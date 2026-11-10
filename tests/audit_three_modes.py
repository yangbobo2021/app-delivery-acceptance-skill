"""Score the predeclared tasks from actual events and retained files."""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from e2e_three_modes import MATRIX


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def events(path):
    for line in path.read_bytes().splitlines() if path.is_file() else ():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def rollout_files():
    sessions = Path.home() / ".codex/sessions"
    for today in (date.today(), date.today() - timedelta(days=1)):
        yield from (sessions / today.strftime("%Y/%m/%d")).glob("rollout-*.jsonl")


def parent_calls(path):
    calls = {}
    for event in events(path):
        item = event.get("payload", {})
        if event.get("type") == "response_item" and item.get("name") == "spawn_agent":
            try:
                request = json.loads(item["arguments"])
                calls[request["task_name"]] = request.get("fork_turns", "default")
            except (KeyError, TypeError, ValueError):
                continue
    return calls


def review_facts(records, project, thread):
    files = list(rollout_files())
    parent = next((path for path in files if path.stem.endswith(thread or "!!")), None)
    calls = parent_calls(parent) if parent else {}
    child = {}
    if thread:
        for path in files:
            with path.open("rb") as stream:
                try:
                    meta = json.loads(next(stream)).get("payload", {})
                except (StopIteration, ValueError):
                    continue
            source = meta.get("source")
            if not isinstance(source, dict):
                continue
            spawn = source.get("subagent", {}).get("thread_spawn", {})
            if spawn.get("parent_thread_id") == thread:
                child[meta["id"]] = child[spawn["agent_path"]] = (meta["id"], spawn["agent_path"], path)
    facts = []
    for row in records:
        if row["operation"] != "review" or row["status"] != "reviewed":
            continue
        path = project / row["record"]
        request = load(path.with_name("request.json"))
        target = path.parents[2] / row["target_operation"] / row["target"] / "result.json"
        receipt = project / request["review_file"] if "review_file" in request else None
        session, agent, rollout = child.get(row["reviewer_session"], (None, None, None))
        observed_writer = False
        if receipt and rollout:
            for event in events(rollout):
                item = event.get("payload", {})
                if event.get("type") == "response_item" and item.get("type") in {"function_call", "custom_tool_call"}:
                    call = item.get("input") or item.get("arguments") or ""
                    if receipt.name in call and re.search(r"cat\s*>|write_(?:text|bytes)|\.open\(|\*\*\* (?:Add|Update) File|apply_patch|\bcp\s", call):
                        observed_writer = True
                        break
        facts.append({
            "request": row["request_id"], "operation": row["target_operation"], "target": row["target"],
            "case_verdicts": row["judgments"], "native_child": session,
            "fork_turns": calls.get(agent.split("/")[-1]) if agent else None,
            "exact_target": digest(target) == row["target_sha256"],
            "child_write_seen": observed_writer,
            "raw_bytes": bool(receipt and receipt.is_file() and digest(receipt) == digest(project / row["reviewer_response"])),
        })
    return facts


def capture(folder, case):
    project = folder / "project"
    trace = list(events(folder / "trace.jsonl"))
    finished = (folder / "run.json").is_file()
    run = load(folder / "run.json") if finished else {}
    thread = run.get("thread") or next((e["params"]["threadId"] for e in trace if e.get("params", {}).get("threadId")), None)
    task_events = [e for e in trace if e.get("params", {}).get("threadId") == thread]
    plans = [e["params"]["plan"] for e in task_events if e.get("method") == "turn/plan/updated"]
    commands = [e["params"]["item"] for e in task_events if e.get("method") == "item/completed"
                and e.get("params", {}).get("item", {}).get("type") == "commandExecution"]
    stored = project / ".acceptance-local/native-v1"
    records = sorted((load(p) for p in stored.glob("AT-*/*/*/result.json")), key=lambda row: row["recorded_at"])
    output = {
        "case": case, "run": run, "plan_updates": len(plans), "final_plan": plans[-1] if plans else [],
        "operations": [{k: v for k, v in r.items() if k in {
            "operation", "request_id", "status", "reason", "record", "recorded_at", "scene",
        }} for r in records],
    }
    if not finished:
        return {**output, "status": "running"}
    if case == "B3":
        probe = subprocess.check_output(["git", "show", f"{run['baseline']}:tools/environment.py"], cwd=project)
        try:
            compile(probe, "tools/environment.py", "exec")
        except SyntaxError:
            return {**output, "status": "invalid_fixture", "failures": ["the frozen fixture contains a malformed environment probe"]}
    facts = review_facts(records, project, thread)
    output["reviews"] = facts
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    def records_of(operation, status=None):
        return [r for r in records if r["operation"] == operation and (status is None or r["status"] == status)]

    def review_of(operation, target, verdicts):
        return any(f["operation"] == operation and f["target"] == target["request_id"]
                   and set(v["status"] for v in f["case_verdicts"].values()) == set(verdicts) for f in facts)

    def business_commands():
        executable = re.compile(r"(?:^|[;&])\s*(?:\S+=\S+\s+)*python(?:3(?:\.\d+)?)?\s+(?:\./)?"
                                r"(?:checks\.py|stock_(?:check|restart)\.py)(?:\s|$)", re.MULTILINE)
        observed = []
        for row in commands:
            shell = shlex.split(row["command"])[-1]
            statements, until = [], None
            for line in shell.splitlines():
                if until:
                    if line.strip() == until:
                        until = None
                    continue
                marker = re.search(r"<<-?\s*['\"]?(\w+)", line)
                until = marker.group(1) if marker else None
                statements.append(line)
            if executable.search("\n".join(statements)):
                observed.append(row)
        return observed

    changed = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project, text=True).splitlines()
    changed = [row for row in changed if not row[3:].startswith(".acceptance/")]
    output["project_diff"] = changed
    check(run.get("turn_status") == "completed", "native turn did not complete")
    check(len(plans) >= 2 and any(step["status"] != "completed" for step in plans[0]), "native PLAN missing")
    if facts:
        check(all(f["native_child"] and f["fork_turns"] == "none" and f["exact_target"] and f["raw_bytes"] for f in facts),
              "one or more reviews lack a confirmed new context, exact target, or original bytes")

    if case[0] in "AB":
        definitions = [load(p) for p in (project / ".acceptance/native-v1").glob("*/definitions/*.json")]
        expected_id = re.search(r"\bAT-[a-z0-9-]+", MATRIX["cases"][case]["prompt"]).group()
        output["acceptance_ids"] = sorted({d["acceptance_id"] for d in definitions})
        check(output["acceptance_ids"] == [expected_id], "the acceptance record did not use the ID specified in the task")
        expected = "STOCK-03" if case[0] == "A" else "PRICE-02"
        check(any(r["source"]["kind"] == "file" and expected in r["source"]["text"]
                  for d in definitions for r in d["requirements"].values()), "requirement text was not frozen from source")

    if case == "A1":
        check(not changed, "product or existing cases changed")
        runs = records_of("execute", "executed")
        observed = {key: row["execution"]["exit_code"] for r in runs for key, row in r["results"].items()}
        output["formal_exit_codes"] = observed
        check(observed == {"TC-same-process": 0, "TC-after-restart": 1}, "both expected formal outcomes were not observed")
        check(any(f["operation"] == "debug" for f in facts) and any(
            f["operation"] == "execute" and f["case_verdicts"].get("TC-after-restart", {}).get("status") == "product_defect"
            for f in facts), "valid design or formal defect review missing")
        snapshots = list(stored.glob("AT-*/reports/*/snapshot.json"))
        check(any(row["status"] == "product_defect" for p in snapshots for s in load(p)["scenes"].values()
                  for row in s["cases"].values()), "native report does not show the product defect")
    elif case == "A2":
        matched = records_of("match", "matched")
        output["matches"] = [{k: r[k] for k in ("coverage", "cases", "missing")} for r in matched]
        check(any(r["coverage"] == "partial" and r["missing"] and set(r["cases"]) == {"TC-same-process"}
                  for r in matched), "no partial result with stable case identity")
        check(not changed and not business_commands() and all(r["operation"] == "match" for r in records),
              "match-only task edited input or moved into execution")
    elif case[0] == "B":
        reds = records_of("tdd-red", "red_observed")
        greens = records_of("tdd-green", "green_observed")
        base = subprocess.check_output(["git", "show", "HEAD:price.py"], cwd=project)
        red_ok = [r for r in reds if r["product_bindings"].get("price.py") == hashlib.sha256(base).hexdigest()
                  and review_of("tdd-red", r, ["expected_failure"])]
        check(bool(red_ok), "missing baseline behavioral red with exact independent review")
        if case == "B2":
            check(not greens and (project / "price.py").read_bytes() == base, "test-only path modified product or ran green")
            check(all(row[3:].startswith("tests/") for row in changed), "test-only path edited an unauthorized file")
        else:
            check(all(row[3:] == "price.py" or row[3:].startswith("tests/") for row in changed),
                  "TDD modified files outside the authorized product and test scope")
            check(any(g["red"] == r["request_id"] and g["test_bindings"] == r["test_bindings"]
                      and g["cases"] == r["cases"] and g["regression"]["exit_code"] == 0
                      and review_of("tdd-green", g, ["passed"]) for g in greens for r in red_ok),
                  "missing reviewed same-test green and successful regression")
            oracle = subprocess.run(
                ["python3", "-B", "-c", "from price import total; assert (total(111, 9), total(111, 10), total(111, 11)) == (999, 999, 1098)"],
                cwd=project, capture_output=True, text=True,
            )
            output["post_run_behavior_oracle"] = oracle.returncode
            check(oracle.returncode == 0, "updated implementation fails the independent threshold/rounding oracle")
        if case == "B3":
            blocked, ready = records_of("runtime", "blocked"), records_of("runtime", "ready")
            missing_config = [
                row for row in blocked if any(
                    "Missing .local-settings" in (project / name).read_text(errors="replace")
                    for name in (row.get("smoke") or {}).get("output", {})
                )
            ]
            recorded_failure = any(b["recorded_at"] < x["recorded_at"] < r["recorded_at"]
                                   for b in missing_config for x in ready for r in red_ok)
            direct_failure = next((i for i, row in enumerate(commands)
                                   if re.search(r"python3\s+tools/environment\.py\s*>", row["command"])
                                   and "--init" not in row["command"]
                                   and "Missing .local-settings; follow README\nexit_code=1" in row.get("aggregatedOutput", "")
                                   and any(any(entry in later["command"] for entry in ("operations.mts", "operations.py")) and "runtime" in later["command"]
                                           and '"status": "ready"' in later.get("aggregatedOutput", "")
                                           for later in commands[i+1:])), None)
            retained = [path for path in stored.glob("AT-*/environment/*.log")
                        if path.read_text(errors="replace").strip() == "Missing .local-settings; follow README"]
            direct_proof = direct_failure is not None and bool(retained) and any(
                x["recorded_at"] < r["recorded_at"] for x in ready for r in red_ok
            )
            output["missing_configuration_log"] = [p.relative_to(project).as_posix() for p in retained]
            check(recorded_failure or direct_proof, "the planned missing-configuration failure and recovery before red were not proven")
    else:
        report = stored / "inventory.json"
        expected = {
            "C1": {"INV-A": "verified", "INV-B": "recheck", "INV-C": "untested", "INV-D": "unconfirmed"},
            "C2": {"INV-A": "verified", "INV-B": "verified", "INV-C": "failed", "INV-D": "unconfirmed"},
            "C3": {"INV-E": "source_missing", "INV-F": "conflict"},
        }[case]
        try:
            value = load(report)
            items = {row["id"]: row for row in value["items"]}
            actual = {key: row["status"] for key, row in items.items()}
            output["inventory"] = actual
            check(actual == expected, "inventory states differ from frozen oracle")
            check(value["revision"] == run["baseline"], "inventory report is not bound to actual HEAD")
            check(all(row["sources"] and row["next"] for row in items.values()), "inventory misses a source or next step")
        except (OSError, KeyError, TypeError, ValueError):
            items = {}
            check(False, "missing or invalid inventory.json")
        check(not changed and not records, "inventory wrote tracked material or began an acceptance workflow")
        business = business_commands()
        output["check_commands"] = [{"command": c["command"], "exit_code": c.get("exitCode")} for c in business]
        if case == "C2":
            check(set(re.findall(r"checks\.py\s+(INV-[A-Z])", "\n".join(c["command"] for c in business))) == {"INV-B", "INV-C"},
                  "exact authorized checks were not observed")
            for key, expected_code in {"INV-B": 0, "INV-C": 1}.items():
                row = items.get(key, {})
                existing = [project / p for p in row.get("evidence", []) if isinstance(p, str)]
                check(any(p.is_file() and key in p.read_text(errors="replace") for p in existing),
                      f"{key} raw new evidence was not linked")
                check(row.get("exit_code", (row.get("execution") or row.get("run") or {}).get("exit_code")) == expected_code, f"{key} recorded wrong exit code")
        else:
            check(not business, "read-only inventory executed a check")
    output["status"] = "passed" if not failures else "failed"
    output["failures"] = failures
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = []
    for model, cases in MATRIX["models"].items():
        for case in cases:
            folder = args.output / model / case
            if folder.is_dir():
                row = capture(folder, case)
                rows.append(row)
                print(model, case, row["status"], " | ".join(row.get("failures", [])))
    (args.output / "audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
