"""Run bounded native Agent acceptance tasks with actual independent review."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from e2e_definitions import codex, protected
from e2e_preparation import IDENTIFIER, outcomes, setup, tool


def prerequisites(project, sha, executable, seed_debug):
    def operation(name, request):
        return tool(project, "operations", name, IDENTIFIER, payload={"definitions_sha256": sha, **request})

    files = ["app/booking.py", "tools/smoke.py"]
    operation("runtime", {"request_id": "runtime-1", "files": files, "command": [executable, "tools/smoke.py"]})
    operation("coverage", {
        "request_id": "coverage-1", "runtime": "runtime-1", "source_roots": ["app"],
        "command": [executable, "tools/smoke.py"], "observe": {"path": "app/booking.py", "line": 35},
    })
    if not seed_debug:
        return None
    path = "tests/check_s1.py"
    source = tool(project, "records", "source", path, "20", "27")
    match = operation("match", {
        "request_id": "match-1", "scene": "SC1", "search_scope": "既有 SC1 用例声明",
        "inspected": {path: source["sha256"]},
        "obligations": [
            {"id": "first-input", "quote": "在 A 会议室创建 2026-11-10 10:00–11:00 的“原始周会”"},
            {"id": "reject", "quote": "第二次响应 JSON status 必须为 409"},
            {"id": "preserve", "quote": "所有字段和值均不变"},
        ],
        "cases": [{
            "selector": "main", "status": "active", "source": source,
            "covers": {"reject": "assert rejected['status'] == 409"},
        }],
    })
    identifier = next(iter(match["cases"]))
    operation("debug", {
        "request_id": "debug-1", "scene": "SC1", "runtime": "runtime-1", "coverage": "coverage-1",
        "match": "match-1", "cases": {identifier: {"command": [executable, path], "files": [path]}},
    })
    return identifier


def reject_consistent_wrong_title(project):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for folder in ("tests", "app"):
            shutil.copytree(project / folder, target / folder)
        before = subprocess.run([sys.executable, "tests/check_s1.py"], cwd=target, capture_output=True, timeout=20)
        assert before.returncode == 0, before.stderr
        product = target / "app/booking.py"
        source = product.read_text(encoding="utf-8")
        original = "args.room, args.start, args.end, args.title, datetime"
        assert source.count(original) == 1
        product.write_text(source.replace(original, 'args.room, args.start, args.end, "错误标题", datetime'), encoding="utf-8")
        mutant = subprocess.run([sys.executable, "tests/check_s1.py"], cwd=target, capture_output=True, timeout=20)
        assert mutant.returncode != 0, "generated test missed a consistently wrong first title"


def run(output, model, executable, case):
    fixture = {"history": "C3", "conflict": "C4"}.get(case, "C2")
    project, skill, sha = setup(output, case, fixture)
    identifier = prerequisites(project, sha, executable, case == "design") if case != "conflict" else None
    original = protected(project)
    config = f"验收 ID 是 {IDENTIFIER}。已知可用解释器为 {executable}，环境探测用 tools/smoke.py，覆盖目标为 app/booking.py 第 35 行。"
    tasks = json.loads((Path(__file__).parent / "fixtures/booking/tasks.json").read_text(encoding="utf-8"))
    prompts = {
        "design": config + f" 只准备 SC1。当前候选 {identifier} 已有调试 debug-1，请先取得独立设计意见；如有问题，允许仅修改 tests/check_s1.py。用例准备完成就停止。",
        "full": config + tasks["C1"],
        "history": config + tasks["C3"],
        "conflict": config + tasks["C4"],
    }

    def authorization(response):
        assert "201" in response and "409" in response and not outcomes(project, "execute"), response
        assert protected(project) == original, "first turn edited frozen materials"
        return "以冻结需求 R1 为准。我授权仅修订 SC1 与 R1 冲突的判据（拒绝状态码和完整状态保护）与对应测试用例。使用本项目现有探测准备环境和覆盖；保持产品、R1、SC2、测试规范及历史原样。完成 SC1 正式执行和独立复审。"

    trace = output / f"{case}.jsonl"
    thread, plans, _, response = codex(
        project, skill, prompts[case], model, trace, timeout=720 if case in {"conflict", "history"} else 420, review=True,
        followup=authorization if case == "conflict" else None,
    )
    current = protected(project)
    allowed = {"tests/check_s1.py", "scenarios.md"} if case == "conflict" else {"tests/check_s1.py"}
    assert {key for key in current.keys() | original.keys() if current.get(key) != original.get(key)} <= allowed
    records = outcomes(project, "review")
    for record in records:
        if record["status"] != "reviewed":
            continue
        request = json.loads((project / record["record"]).with_name("request.json").read_text(encoding="utf-8"))
        assert (project / request["review_file"]).read_bytes() == (project / record["reviewer_response"]).read_bytes()
    judgments = [(record["target_operation"], [item["status"] for item in record["judgments"].values()]) for record in records if record["status"] == "reviewed"]
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    calls = [event["params"].get("item", {}) for event in events if event["method"] == "item/completed"]
    assert any(call.get("type") == "collabAgentToolCall" for call in calls), "no observable native reviewer call"
    assert len(plans) >= 3 and all(step["status"] == "completed" for step in plans[-1]), (plans, response)
    if case == "design":
        assert not any("修订" in item["step"] or "新版" in item["step"] for item in plans[0]), plans[0]
        assert any(len(plan) > len(plans[0]) and any("修订" in item["step"] for item in plan) for plan in plans[1:]), plans
        assert any(kind == "debug" and "needs_revision" in findings for kind, findings in judgments), judgments
        assert any(kind == "debug" and set(findings) == {"passed"} for kind, findings in judgments), judgments
        assert not outcomes(project, "execute"), "design-only task entered formal execution"
    else:
        assert any(kind == "execute" and set(findings) == {"passed"} for kind, findings in judgments), (judgments, response)
        assert any(result["status"] == "executed" for result in outcomes(project, "execute"))
        if case == "history":
            assert any(kind == "history" and "needs_revision" in findings for kind, findings in judgments), judgments
    reject_consistent_wrong_title(project)
    return {"case": case, "model": model, "status": "passed", "thread": thread, "plan_updates": len(plans), "reviews": judgments}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--case", choices=["design", "full", "history", "conflict"], required=True)
    parser.add_argument("--coverage-python", required=True)
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix=f"acceptance-{args.case}-e2e-")).resolve()
    print(f"Evidence: {output}", flush=True)
    print(json.dumps(run(output, args.model, args.coverage_python, args.case), ensure_ascii=False))


if __name__ == "__main__":
    main()
