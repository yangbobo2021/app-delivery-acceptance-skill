"""Run fixed three-mode tasks using native Codex in isolated Git checkouts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Timer
import time

FIXTURES = Path(__file__).parent / "fixtures/three-modes"
sys.path.insert(0, str(FIXTURES))
from prepare import prepare  # noqa: E402

SKILL = Path(__file__).resolve().parents[1] / "skills/app-delivery-acceptance-native"
MATRIX = json.loads((FIXTURES / "matrix.json").read_text())


def native(project, prompt, model, trace, timeout):
    command = [
        "codex", "app-server", "--stdio", "-c", "mcp_servers={}",
        "-c", "features.apps=false", "-c", "features.plugins=false", "-c", "features.hooks=false",
        "-c", 'web_search="disabled"', "-c", "sandbox_workspace_write.network_access=true",
        "-c", "features.multi_agent=true", "-c", f"agents.default_subagent_model={json.dumps(model)}",
    ]
    installed = project / ".agents/skills" / SKILL.name / "SKILL.md"
    with trace.open("w", encoding="utf-8") as log, trace.with_suffix(".stderr").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(command, cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True)
        timer = Timer(timeout, process.terminate)
        timer.start()

        def send(message):
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def events():
            for line in process.stdout:
                event = json.loads(line)
                log.write(line)
                log.flush()
                yield event
            raise RuntimeError("app-server ended before turn completion")

        stream = events()

        def request(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            for event in stream:
                if event.get("id") == identifier and "method" not in event:
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    return event["result"]

        try:
            request(1, "initialize", {"clientInfo": {"name": "acceptance-three-modes", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
            send({"method": "initialized"})
            catalog = request(2, "skills/list", {"cwds": [str(project)], "forceReload": True})
            if not any(Path(row["path"]) == installed for item in catalog["data"] for row in item["skills"]):
                raise RuntimeError("candidate skill was not discovered")
            thread = request(3, "thread/start", {
                "cwd": str(project), "model": model, "approvalPolicy": "never", "sandbox": "workspace-write",
            })["thread"]["id"]
            request(4, "turn/start", {"threadId": thread, "input": [
                {"type": "text", "text": prompt, "text_elements": []},
                {"type": "skill", "name": SKILL.name, "path": str(installed)},
            ]})
            for event in stream:
                params = event.get("params", {})
                if event.get("method") == "turn/completed" and params.get("threadId") == thread:
                    return thread, params["turn"]["status"]
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


def run_one(output, benchmark, model, case, timeout):
    started = time.monotonic()
    folder = output / model / case
    folder.mkdir(parents=True)
    project = folder / "project"
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SKILL, text=True).strip()
    result = {"model": model, "case": case, "candidate": candidate}
    try:
        result["baseline"] = prepare(project, benchmark, case)
        if case == "A1":
            (project / "build").mkdir()
            with (folder / "preflight.log").open("w", encoding="utf-8") as log:
                subprocess.run(["go", "build", "-o", "build/stock", "."], cwd=project,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        shutil.copytree(SKILL, project / ".agents/skills" / SKILL.name,
                        ignore=shutil.ignore_patterns("__pycache__"))
        prompt = MATRIX["cases"][case]["prompt"]
        (folder / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        result["thread"], result["turn_status"] = native(project, prompt, model, folder / "trace.jsonl", timeout)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    result["elapsed_s"] = round(time.monotonic() - started, 1)
    (folder / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--model", choices=MATRIX["models"], required=True)
    parser.add_argument("--case", action="append", choices=MATRIX["cases"])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=720)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix_hash = hashlib.sha256((FIXTURES / "matrix.json").read_bytes()).hexdigest()
    (output / "matrix.sha256").write_text(matrix_hash + "\n", encoding="ascii")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(run_one, output, args.benchmark.resolve(), args.model, case, args.timeout)
                for case in args.case or MATRIX["models"][args.model]]
        for job in as_completed(jobs):
            print(json.dumps(job.result(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
