"""Run native Skill matching and environment-only requests against a frozen fixture."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from booking_fixture import prepare_booking
from e2e_definitions import SKILL, codex, protected


IDENTIFIER = "AT-native-fixture"


def tool(project, script, *args, payload=None):
    process = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / f"{script}.py"), "--root", str(project), *args],
        input=json.dumps(payload), text=True, capture_output=True, timeout=60,
    )
    if process.returncode:
        raise RuntimeError(process.stdout + process.stderr)
    return json.loads(process.stdout)


def setup(output, case):
    project = output / case
    prepare_booking(project, "C2")
    installed = project / ".agents" / "skills" / SKILL.name
    shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns("__pycache__"))
    tool(project, "records", "create", IDENTIFIER, "--request", "冻结 SC1 合成验收。", "--reference", "fixture:user")
    requirement = tool(project, "records", "source", "requirements.md", "3", "3")
    scenario = tool(project, "records", "source", "scenarios.md", "3", "10")
    frozen = tool(project, "records", "freeze", IDENTIFIER, payload={
        "request_id": "freeze", "base_sha256": None,
        "requirements": [{"id": "R1", "source": requirement}],
        "scenarios": [{"id": "SC1", "source": scenario, "requirements": ["R1"]}],
    })
    return project, installed / "SKILL.md", frozen["sha256"]


def outcomes(project, operation):
    root = project / ".acceptance-local" / "native-v1" / IDENTIFIER
    return [json.loads(file.read_text(encoding="utf-8")) for file in sorted((root / operation).glob("*/result.json"))]


def run(output, model, coverage_python, case):
    project, skill, _ = setup(output, case)
    original = protected(project)
    if case == "match":
        prompt = f"只给 {IDENTIFIER} 的 SC1 匹配已有用例。检查 tests/check_s1.py 和 tests/check_s2.py，交付实际匹配与缺口后停止。禁止运行用例或创建新用例。"
    else:
        prompt = (
            f"只为 {IDENTIFIER} 验证测试与覆盖环境。使用已准备的解释器 {coverage_python} "
            "运行 tools/smoke.py。产品是它启动的 app/booking.py；覆盖率探测应观察该文件第 35 行。"
            "绑定这两个文件，准备完成后停止，不跑正式用例。"
        )
    thread, plans, commands, response = codex(project, skill, prompt, model, output / f"{case}.jsonl")
    assert protected(project) == original
    assert len(plans) >= 2, response
    assert all(step["status"] == "completed" for step in plans[-1]), response
    if case == "match":
        matched = outcomes(project, "match")
        assert len(matched) == 1 and matched[0]["coverage"] == "partial", (matched, response)
        assert matched[0]["missing"] and not outcomes(project, "runtime")
        assert {case["source"]["path"] for case in matched[0]["cases"].values()} == {"tests/check_s1.py"}
    else:
        assert [result["status"] for result in outcomes(project, "runtime")] == ["ready"], response
        prepared = outcomes(project, "coverage")
        assert len(prepared) == 1 and prepared[0]["status"] == "ready", response
        report = json.loads((project / prepared[0]["coverage_report"]).read_text(encoding="utf-8"))
        assert 35 in report["files"]["app/booking.py"]["executed_lines"]
    return {"case": case, "status": "passed", "thread": thread, "plan_updates": len(plans)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--coverage-python", required=True)
    parser.add_argument("--case", choices=["match", "environment"], action="append")
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="acceptance-preparation-e2e-")).resolve()
    print(f"Evidence: {output}", flush=True)
    failed = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {case: pool.submit(run, output, args.model, args.coverage_python, case) for case in args.case or ("match", "environment")}
        for case, job in jobs.items():
            try:
                print(json.dumps(job.result(), ensure_ascii=False), flush=True)
            except Exception as error:
                print(json.dumps({"case": case, "status": "failed", "error": str(error)}, ensure_ascii=False), flush=True)
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
