"""Verify that Codex implements and debugs an approved design in isolation."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from e2e_definitions import codex, protected
from e2e_preparation import IDENTIFIER, outcomes, setup, tool


CASE_FILE = "tests/check_s1.py"
PRODUCT = "app/booking.py"
CHECKS = (
    ("created", "在 A 会议室创建 2026-11-10 10:00–11:00 的“原始周会”",
     "在空数据库按场景创建首条预约，读取创建响应。", "业务状态为 201。"),
    ("initial-title", "在 A 会议室创建 2026-11-10 10:00–11:00 的“原始周会”",
     "分别读取创建响应与拒绝前的独立列表查询。", "两处的预约标题各自等于输入原始周会。"),
    ("reject", "第二次响应 JSON status 必须为 409",
     "按场景提交同室重叠请求。", "业务状态为 409。"),
    ("preserve", "所有字段和值均不变",
     "分别在重叠请求前后独立查询完整预约列表。", "恰有原来的一条记录，所有字段及值与拒绝前一致。"),
)


def seed_design(project, definitions_sha):
    return tool(project, "operations", "case-design", IDENTIFIER, payload={
        "request_id": "approved-design", "scene": "SC1", "definitions_sha256": definitions_sha,
        "obligations": [{"id": key, "quote": quote} for key, quote, _, _ in CHECKS],
        "cases": {"D-conflict": {
            "title": "同室重叠拒绝且保留原预约", "kind": "automated",
            "preconditions": "用独立临时数据库，通过真实 CLI 操作。",
            "checks": [{"id": key, "action": action, "expected": expected} for key, _, action, expected in CHECKS],
        }},
    })


def verify_behavior(project, output):
    """Run the generated test on a normal product and single-defect copies."""
    source = (project / PRODUCT).read_text(encoding="utf-8")
    mutations = {
        "wrong-title": ("args.room, args.start, args.end, args.title, datetime",
                        'args.room, args.start, args.end, "错误标题", datetime'),
        "wrong-status": ('emit(409, error="room conflict"', 'emit(422, error="room conflict"'),
        "changed-state": ("        if existing:\n            pass\n",
                          "        if existing:\n            db.execute('UPDATE bookings SET title = ?', ('被篡改',))\n"),
    }
    for name, mutation in [("normal", None), *mutations.items()]:
        replica = output / name
        for folder in ("app", "tests"):
            shutil.copytree(project / folder, replica / folder)
        if mutation:
            original, changed = mutation
            assert source.count(original) == 1, f"{name}: fixture no longer exposes the expected mutation point"
            (replica / PRODUCT).write_text(source.replace(original, changed), encoding="utf-8")
        result = subprocess.run([sys.executable, CASE_FILE], cwd=replica, capture_output=True, timeout=20)
        (replica / "stdout.log").write_bytes(result.stdout)
        (replica / "stderr.log").write_bytes(result.stderr)
        (replica / "exit-code.txt").write_text(f"{result.returncode}\n", encoding="ascii")
        assert (result.returncode == 0) == (mutation is None), f"{name}: unexpected exit {result.returncode}, see {replica}"


def run(output, model):
    project, skill, definitions_sha = setup(output, "implementation")
    design = seed_design(project, definitions_sha)
    tool(project, "operations", "runtime", IDENTIFIER, payload={
        "request_id": "prepared-runtime", "definitions_sha256": definitions_sha,
        "files": [PRODUCT, "tools/smoke.py"], "command": [sys.executable, "tools/smoke.py"],
    })
    before = protected(project)
    shared = project / ".acceptance" / "native-v1" / IDENTIFIER
    definitions = {path: path.read_bytes() for path in (shared / "definitions").glob("*.json")}
    design_path = project / design["record"]
    design_bytes = design_path.read_bytes()
    prompt = (
        f"只实现并调试 {IDENTIFIER} 的 SC1 已确认用例设计 approved-design。"
        f"沿用 prepared-runtime 和 Python {sys.executable}。只允许修改 {CASE_FILE}，保留其他资料。"
        "本轮只验证用例实现，明确关闭覆盖率采集。保存匹配和真实调试结果后停止，不做独立复审或正式执行。"
    )
    thread, plans, _, response = codex(project, skill, prompt, model, output / "implementation.jsonl", timeout=300)
    (output / "implementation-outcome.json").write_text(json.dumps(
        {"thread": thread, "plans": plans, "response": response}, ensure_ascii=False, indent=2,
    ) + "\n", encoding="utf-8")
    after = protected(project)
    assert {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)} == {CASE_FILE}
    assert {path: path.read_bytes() for path in (shared / "definitions").glob("*.json")} == definitions
    assert design_path.read_bytes() == design_bytes
    local = project / ".acceptance-local" / "native-v1" / IDENTIFIER
    recorded = {path.parent.parent.name for path in local.glob("*/*/result.json")}
    assert recorded == {"case-design", "runtime", "match", "debug"}, recorded
    assert outcomes(project, "case-design") == [design]
    assert len(plans) >= 2 and all(step["status"] == "completed" for step in plans[-1]), response

    debug = max(outcomes(project, "debug"), key=lambda row: row["recorded_at"])
    assert debug["status"] == "debugged" and debug["definitions_sha256"] == definitions_sha, debug
    assert debug["case_design"]["id"] == design["request_id"]
    assert debug["case_design"]["sha256"] == hashlib.sha256(design_bytes).hexdigest()
    request = json.loads((project / debug["record"]).with_name("request.json").read_bytes())
    matched = next(row for row in outcomes(project, "match") if row["request_id"] == request["match"])
    assert matched["coverage"] == "full" and matched["case_design"] == debug["case_design"], matched
    case_id, = debug["cases"]
    assert matched["cases"][case_id]["source"]["path"] == CASE_FILE
    assert set(debug["case_obligations"][case_id]) == set(design["obligations"])
    result = debug["results"][case_id]
    command = result["business_command"]
    assert len(command) == 2 and Path(command[0]).resolve() == Path(sys.executable).resolve(), command
    assert (project / command[1]).resolve() == project / CASE_FILE, command
    assert result["execution"]["exit_code"] == 0 and result["coverage"]["status"] == "off", result
    source = (project / debug["record"]).parent / "source" / CASE_FILE
    assert source.read_bytes() == (project / CASE_FILE).read_bytes()
    assert all(hashlib.sha256((project / path).read_bytes()).hexdigest() == digest
               for path, digest in result["execution"]["output"].items())
    verify_behavior(project, output / "implementation-probes")
    print(f"implementation: passed ({thread})", flush=True)
