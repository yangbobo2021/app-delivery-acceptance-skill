"""Opt-in native Codex checks for the definitions candidate."""

import argparse
import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Timer

from booking_fixture import prepare_booking


SKILL = Path(__file__).resolve().parents[1] / "skills" / "app-delivery-acceptance-native"
ORAL = "预约取消后应保留原预约的审计记录。"
TASKS = {
    "create": "请在这个项目只新建验收 AT-create。现在没有需求，创建后就停止。",
    "oral": f"在这个项目新建 AT-oral，需求 R4 的原话是「{ORAL}」。请冻结这条需求后停止。",
    "file": "在这个项目新建 AT-file，仅冻结 requirements.md 的 R1 和 scenarios.md 的 SC1，冻结后停止。",
    "conflict": "从 requirements.md 和 scenarios.md 冻结 R1 与 SC1。遇到冻结依据冲突，请说明并停止；禁止修改原文。",
}


def protected(project):
    return {
        file.relative_to(project).as_posix(): file.read_bytes()
        for file in project.rglob("*") if file.is_file()
        and file.relative_to(project).parts[0] not in {".acceptance", ".acceptance-local"}
    }


def codex(project, skill, prompt, model, evidence, *, timeout=150, review=False, followup=None):
    command = [
        "codex", "app-server", "--stdio", "-c", "mcp_servers={}",
        "-c", "features.apps=false", "-c", "features.plugins=false", "-c", "features.hooks=false",
        "-c", 'web_search="disabled"',
    ]
    if review:
        command.extend(["-c", "features.multi_agent=true", "-c", f"agents.default_subagent_model={json.dumps(model)}"])
    with evidence.with_suffix(".stderr").open("w", encoding="utf-8") as stderr, evidence.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True)
        timer = Timer(timeout, process.terminate)
        timer.start()

        def send(message):
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def messages():
            for line in process.stdout:
                yield json.loads(line)
            raise RuntimeError(f"Codex app-server closed before completion: {evidence}")

        stream = messages()

        def request(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            for event in stream:
                if event.get("id") == identifier and "method" not in event:
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    return event["result"]

        try:
            request(1, "initialize", {"clientInfo": {"name": "acceptance-definitions-test", "version": "1"}, "capabilities": {"experimentalApi": True}})
            send({"method": "initialized"})
            catalog = request(2, "skills/list", {"cwds": [str(project)], "forceReload": True})
            assert any(Path(s["path"]) == skill for entry in catalog["data"] for s in entry["skills"])
            thread = request(3, "thread/start", {
                "cwd": str(project), "model": model, "approvalPolicy": "never", "sandbox": "workspace-write",
            })["thread"]["id"]
            skill_input = {"type": "skill", "name": SKILL.name, "path": str(skill)}
            request(4, "turn/start", {"threadId": thread, "input": [
                {"type": "text", "text": prompt, "text_elements": []},
                skill_input,
            ]})
            plans, commands, responses = [], [], []
            for event in stream:
                method, params = event.get("method"), event.get("params", {})
                if params.get("threadId") != thread:
                    continue
                item = params.get("item", {})
                if method == "turn/plan/updated":
                    plans.append(params["plan"])
                elif method == "item/completed" and item.get("type") == "commandExecution":
                    commands.append(item)
                elif method == "item/completed" and item.get("type") == "agentMessage":
                    responses.append(item["text"])
                elif method == "item/completed" and item.get("type") == "collabAgentToolCall":
                    pass
                elif method == "turn/completed":
                    assert params["turn"]["status"] == "completed", params["turn"]["status"]
                    if followup is None:
                        break
                    text = followup(responses[-1])
                    followup = None
                    send({"id": 5, "method": "turn/start", "params": {"threadId": thread, "input": [
                        {"type": "text", "text": text, "text_elements": []}, skill_input,
                    ]}})
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                else:
                    continue
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
            return thread, plans, commands, responses[-1]
        finally:
            timer.cancel()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()


def run(output, model, case):
    project = output / case
    prepare_booking(project, "C4" if case == "conflict" else "C2")
    if case == "conflict":
        shutil.rmtree(project / "history")
    installed = project / ".agents" / "skills" / SKILL.name
    shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns("__pycache__"))
    original = protected(project)
    thread, plans, commands, response = codex(project, installed / "SKILL.md", TASKS[case], model, output / f"{case}.jsonl")
    assert protected(project) == original, f"{case}: project input changed"
    assert len(plans) >= 2 and plans[0], f"{case}: native plan missing"
    assert all(s["status"] != "completed" for s in plans[0]), f"{case}: plan began already complete"
    assert not any("tests/check_" in c["command"] for c in commands), f"{case}: ran a test after definition request"
    native = project / ".acceptance" / "native-v1"
    snapshots = sorted(native.glob("*/definitions/*.json"))
    if case == "conflict":
        assert not snapshots and "409" in response and "201" in response, response
    else:
        manifests = list(native.glob("*/manifest.json"))
        assert len(manifests) == 1, (case, manifests)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["acceptance_id"] == f"AT-{case}", f"{case}: wrong acceptance ID {manifest['acceptance_id']}"
        assert manifest["request"] == TASKS[case], f"{case}: creation request changed"
        assert all(s["status"] == "completed" for s in plans[-1]), plans[-1]
        if case == "create":
            assert not snapshots
        else:
            assert len(snapshots) == 1, snapshots
            definition = json.loads(snapshots[0].read_text(encoding="utf-8"))
            if case == "oral":
                assert set(definition["requirements"]) == {"R4"}
                assert definition["requirements"]["R4"]["source"]["text"] == ORAL
                assert not definition["scenarios"]
            else:
                assert set(definition["requirements"]) == {"R1"} and set(definition["scenarios"]) == {"SC1"}
                requirement = definition["requirements"]["R1"]["source"]
                scenario = definition["scenarios"]["SC1"]["source"]
                assert requirement["path"] == "requirements.md" and "409" in requirement["text"]
                assert scenario["path"] == "scenarios.md" and "SC2" not in scenario["text"]
    assert not list(native.glob("*/records")), f"{case}: unexpected execution records"
    return {"case": case, "thread": thread, "plan_updates": len(plans), "snapshots": len(snapshots), "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--case", action="append", choices=TASKS)
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="acceptance-native-e2e-")).resolve()
    print(f"Evidence: {output}", flush=True)
    failed = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {case: pool.submit(run, output, args.model, case) for case in args.case or TASKS}
        for case, job in jobs.items():
            try:
                print(json.dumps(job.result(), ensure_ascii=False), flush=True)
            except Exception as error:
                print(json.dumps({"case": case, "status": "failed", "error": str(error)}, ensure_ascii=False), flush=True)
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
