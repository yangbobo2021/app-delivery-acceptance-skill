"""Run each scenario and test case stage in a fresh Codex conversation."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from e2e_case_implementation import run as run_implementation
from e2e_definitions import SKILL, codex
from e2e_preparation import tool


SCENARIO_BASIS = (
    "R1：仓库库位可保留 1 至 5 天，含两端。低于 1 天或高于 5 天的请求必须拒绝，"
    "拒绝时已有保留记录及到期时间保持原样。"
)
ORIGINAL_SCENE = (
    "## SC1 库位保留期限上限\n\n"
    "1. 准备账号 仓库员甲 和库位 A-27，执行 `node cli.js reserve A-27 5`。\n"
    "2. 检查恰好保留 5 天，且保留记录已建立。\n"
)
CASE_GROUPS = [
    {"title": "待处理工单", "criteria": ["待处理工单可以关闭。", "成功关闭后，待处理队列不再包含该工单。"]},
    {"title": "已关闭工单", "criteria": ["再次关闭已关闭工单时，原关闭时间保持不变。"]},
]
CASE_BASIS = "R1：" + "".join(text for group in CASE_GROUPS for text in group["criteria"])


def setup(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("/.acceptance-local/native-v1/\n")
    skill = root / ".agents" / "skills" / SKILL.name
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
    return skill / "SKILL.md"


def run(output, model, stage):
    if stage == "implementation":
        return run_implementation(output, model)
    root = output / stage
    skill = setup(root)
    identifier = f"AT-{stage}"
    if stage == "review":
        return review(output, root, skill, model)
    if stage == "scenarios":
        (root / "requirements.md").write_text(SCENARIO_BASIS + "\n")
        (root / "acceptance-points.md").write_text(ORIGINAL_SCENE)
        requirement = tool(root, "records", "source", "requirements.md", "1", "1")
        original_scene = tool(root, "records", "source", "acceptance-points.md", "1", "4")
        tool(root, "records", "create", identifier, "--request", "核对库位保留期限", "--reference", "fixture")
        tool(root, "records", "freeze", identifier, payload={
            "request_id": "approved-scope", "base_sha256": None,
            "requirements": [{"id": "R1", "source": requirement}],
            "scenarios": [{
                "id": "SC1", "source": original_scene, "requirements": ["R1"],
                "scenario_scope": {"title": "库位保留期限上限", "behavior": "验证库位保留的最大允许期限。",
                                   "criteria": ["保留请求成功，记录已建立且保留期限恰好为 5 天。"]},
            }],
        })
        prompt = (
            f"在已有验收 {identifier} 中只按 requirements.md 的 R1 补齐测试场景并核对覆盖。"
            "现有 SC1 来自已确认的 acceptance-points.md，沿用 SC1。到场景记录和范围核对为止。"
        )
    else:
        (root / "requirements.md").write_text(CASE_BASIS + "\n")
        requirement = tool(root, "records", "source", "requirements.md", "1", "1")
        tool(root, "records", "create", identifier, "--request", "工单关闭", "--reference", "fixture")
        tool(root, "records", "freeze", identifier, payload={
            "request_id": "approved-scene", "base_sha256": None,
            "requirements": [{"id": "R1", "source": requirement}],
            "scenarios": [{
                "id": "SC1", "source": requirement, "requirements": ["R1"],
                "scenario_scope": {"title": "关闭工单", "behavior": "验证工单关闭以及重复关闭的业务行为。",
                                   "groups": CASE_GROUPS},
            }],
        })
        prompt = f"为已有验收 {identifier} 的 SC1 只做自然语言用例设计。保存设计收据后停止。"
    snapshots = root / ".acceptance" / "native-v1" / identifier / "definitions"
    before = {path.name: path.read_bytes() for path in snapshots.glob("*.json")}
    inputs = {path.name: path.read_bytes() for path in root.glob("*.md")}
    trace = output / (stage + ".jsonl")
    thread, plans, commands, response = codex(root, skill, prompt, model, trace, timeout=240)
    (output / (stage + "-outcome.json")).write_text(json.dumps(
        {"thread": thread, "plans": plans, "response": response, "commands": len(commands)},
        ensure_ascii=False, indent=2,
    ) + "\n")
    assert inputs == {path.name: path.read_bytes() for path in root.glob("*.md")}
    assert {path.name for path in root.iterdir()} <= {
        ".git", ".gitignore", ".agents", ".acceptance", ".acceptance-local", *inputs,
    }
    verify_stage(root, identifier, stage, before, response)
    print(f"{stage}: passed ({thread})", flush=True)


def verify_stage(root, identifier, stage, before, response):
    snapshots = root / ".acceptance" / "native-v1" / identifier / "definitions"
    local = root / ".acceptance-local" / "native-v1" / identifier
    scopes = {path.parent.parent.name for path in local.glob("*/*/result.json")}
    versions = sorted(snapshots.glob("*.json"))
    current = json.loads(versions[-1].read_bytes())
    if stage == "scenarios":
        assert scopes == {"analyze"}, (scopes, response)
        assert all((snapshots / name).read_bytes() == data for name, data in before.items()), "initial snapshot changed"
        baseline_bytes, = before.values()
        baseline = json.loads(baseline_bytes)
        assert current["requirements"] == baseline["requirements"]
        assert current["requirements"]["R1"]["source"]["text"].strip() == SCENARIO_BASIS
        assert current["scenarios"]["SC1"]["source"]["text"].strip() == ORIGINAL_SCENE.strip()
        added = set(current["scenarios"]) - set(baseline["scenarios"])
        assert added, "no scenarios were added for the uncovered boundaries"
        visible_lines, added_lines = [], []
        for name, scene in current["scenarios"].items():
            scope = scene["scenario_scope"]
            lines = [scope["behavior"], *(text for group in scope.get("groups", [scope]) for text in group["criteria"])]
            visible_lines.extend(lines)
            if name in added:
                added_lines.extend(lines)
        visible = "\n".join(visible_lines)
        added_text = "\n".join(added_lines)
        assert "1" in added_text and "5" in added_text and "拒绝" in added_text
        assert any(token in added_text for token in ("保持", "不变", "不改变"))
        assert not any(token in visible for token in ("A-27", "仓库员甲", "node cli", "执行命令"))
        scope_review = tool(root, "results", "view", identifier)["scope_reviews"]["R1"]
        assert set(scope_review["scope"]["scenarios"]) == set(current["scenarios"])
        assert scope_review["baseline_sha256"] == hashlib.sha256(baseline_bytes).hexdigest()
        assert set(scope_review["added_scenario_ids"]) == added
    else:
        assert scopes == {"case-design"}, (scopes, response)
        assert before == {path.name: path.read_bytes() for path in versions}
        receipts = [json.loads(path.read_bytes()) for path in local.glob("case-design/*/result.json")]
        design = max(receipts, key=lambda receipt: receipt["recorded_at"])
        assert design["scene"] == "SC1" and design["status"] == "designed" and not design["gaps"]
        quotes = design["obligations"].values()
        assert all(any(quote in text for quote in quotes) for group in CASE_GROUPS for text in group["criteria"])
        assert all(case["preconditions"] and all(check["action"] and check["expected"] for check in case["checks"])
                   for case in design["cases"].values())


def review(output, root, skill, model):
    identifier = "AT-review"
    (root / "requirements.md").write_text(CASE_BASIS + "\n")
    source = tool(root, "records", "source", "requirements.md", "1", "1")
    tool(root, "records", "create", identifier, "--request", "关闭工单", "--reference", "fixture")
    groups = CASE_GROUPS
    procedure = "使用测试账号甲打开管理页，依次执行列表刷新、关闭和再次关闭。"
    scenarios = [
        {"id": name, "requirements": ["R1"], "source": source, "scenario_scope": {
            "title": "关闭工单", "behavior": "验证待处理及已关闭工单的关闭行为。", "groups": scene_groups,
        }}
        for name, scene_groups in (
            ("SC1", groups), ("SC2", [*groups, {"title": "验证流程", "criteria": [procedure]}]),
        )
    ]
    receipt = tool(root, "records", "freeze", identifier, payload={
        "request_id": "review-fixture", "base_sha256": None,
        "requirements": [{"id": "R1", "source": source}], "scenarios": scenarios,
    })
    for scene in scenarios:
        name = scene["id"]
        criteria = [criterion for group in scene["scenario_scope"]["groups"] for criterion in group["criteria"]]
        actions = [
            ("关闭一张待处理工单", "工单状态为已关闭"),
            ("重新查看待处理队列", "队列中已无这张工单"),
            ("记下关闭时间，再次关闭并重新查看", "关闭时间与记下的值相同"),
            (procedure, "工单保持关闭"),
        ]
        ids = [f"C{index}" for index in range(len(criteria))]
        tool(root, "operations", "case-design", identifier, payload={
            "request_id": f"design-{name}", "scene": name, "definitions_sha256": receipt["sha256"],
            "obligations": [{"id": key, "quote": criterion} for key, criterion in zip(ids, criteria)],
            "cases": {"TC-close": {"title": "关闭工单", "kind": "manual", "preconditions": "有一张待处理工单",
                                  "checks": [{"id": key, "action": action, "expected": expected}
                                             for key, (action, expected) in zip(ids, actions)]}},
        })
    prompt = (
        "你是独立 Reviewer。按 Skill 审阅 AT-review 的 SC1、SC2 的场景和已记录的自然语言设计。"
        "只做文档复审，写一份 review.json，分别以场景 ID 为键，写 status（passed 或 needs_revision）、"
        "revision_target（需修订时）及 reason。请指出实际问题原文。评完停止。"
    )
    trace = output / "review.jsonl"
    thread, plans, commands, response = codex(root, skill, prompt, model, trace, timeout=180)
    actual = json.loads((root / "review.json").read_bytes())
    assert actual["SC1"]["status"] == "passed", actual
    assert actual["SC2"]["status"] == "needs_revision" and actual["SC2"]["revision_target"] == "scene", actual
    assert any(term in actual["SC2"]["reason"] for term in ("测试账号", "列表刷新", "验证流程")), actual
    local = root / ".acceptance-local" / "native-v1" / identifier
    assert {p.parent.parent.name for p in local.glob("*/*/result.json")} == {"case-design"}
    print(f"review: passed ({thread})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-luna")
    stages = ("scenarios", "design", "implementation", "review")
    parser.add_argument("--stage", choices=stages, action="append")
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="acceptance-stage-e2e-")).resolve()
    print(output, flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, output, args.model, stage) for stage in args.stage or stages]
        for job in jobs:
            job.result()


if __name__ == "__main__":
    main()
