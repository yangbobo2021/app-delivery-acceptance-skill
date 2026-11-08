"""Evidence-producing operations for the native acceptance Skill."""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from coverage_python import measure_python
from evidence import argv, capture, file_bindings, project_ref, recorded, upstream, verify_bindings
from records import Blocked, Project, acceptance_id, digest, require_text, token


def match(project, identifier, request, definitions, path):
    scene = definitions["scenarios"][request["scene"]]["source"]["text"]
    inspected = request["inspected"]
    bindings = file_bindings(project, list(inspected)) if inspected else {}
    if inspected != bindings:
        raise Blocked("inspected test files changed before publishing")
    obligations = {}
    for item in request["obligations"]:
        key = token(item["id"], "obligation id")
        quote = require_text(item["quote"], "frozen scene quote")
        if key in obligations or quote not in scene:
            raise Blocked("obligations must have unique ids and exact frozen scene quotes")
        obligations[key] = quote
    if not obligations:
        raise Blocked("enumerate the checks supported by the frozen scene")
    cases, supported = {}, set()
    for entry in request["cases"]:
        source = project.verified_source(entry["source"])
        if source["kind"] != "file" or bindings.get(source["path"]) != source["sha256"]:
            raise Blocked("case source must be among the inspected files")
        selector = require_text(entry["selector"], "test selector")
        status = entry["status"]
        if status not in {"active", "skipped"}:
            raise Blocked("case status must be active or skipped")
        references = {}
        for obligation, assertion in entry["covers"].items():
            if obligation not in obligations or require_text(assertion, "case assertion quote") not in source["text"]:
                raise Blocked("case coverage must point to exact source and known obligation")
            references[obligation] = assertion
        before = source["first"] - 1
        marker_text = source["text"]
        if before:
            marker_text = project.source(source["path"], before, before)["text"] + marker_text
        markers = set(re.findall(r"@case-id:\s*(TC-[A-Za-z0-9_-]+)", marker_text))
        if len(markers) > 1:
            raise Blocked("case selection spans multiple permanent case ids")
        identity = next(iter(markers), None)
        if identity is None:
            identity = f"TC-{uuid.uuid5(uuid.NAMESPACE_URL, source['path'] + ':' + selector)}"
        if identity in cases:
            raise Blocked(f"duplicate case id: {identity}")
        cases[identity] = {"source": source, "selector": selector, "status": status, "covers": references}
        if status == "active":
            supported.update(references)
    missing = sorted(obligations.keys() - supported)
    return {
        "status": "matched", "coverage": "full" if not missing else "partial" if supported else "none",
        "execution_status": "not_executed", "obligations": obligations, "cases": cases, "missing": missing,
        "bindings": bindings, "search_scope": require_text(request["search_scope"], "search scope"),
    }


def runtime(project, identifier, request, definitions, path):
    bindings = file_bindings(project, request["files"])
    run = capture(project, request["command"], path, "smoke", request.get("timeout", 60))
    verify_bindings(project, bindings)
    return {"status": "ready" if run["exit_code"] == 0 else "blocked", "bindings": bindings, "smoke": run}


def coverage(project, identifier, request, definitions, path):
    preparation = upstream(project, identifier, request, "runtime", "runtime", "ready")
    roots = request["source_roots"]
    if not isinstance(roots, list) or not roots:
        raise Blocked("declare project source directories")
    observable = request["observe"]
    product = project.path(observable["path"])
    files = sorted({file.relative_to(project.root).as_posix() for root in roots for file in project.path(root).rglob("*.py")})
    if observable["path"] not in files:
        raise Blocked("probe observation must name a product file within the bound scope")
    command = argv(request["command"])
    if len(command) < 2 or Path(command[0]).name.split(".")[0] not in {"python", "python3"}:
        raise Blocked("this Python coverage adapter requires python <script> [args]")
    bindings = {**preparation["bindings"], **file_bindings(project, sorted(set(files + [command[1]])))}
    report, actions = measure_python(project, path / "coverage", command[0], command[1], command[2:], roots, request.get("timeout", 60))
    verify_bindings(project, bindings)
    if report is None:
        return {"status": "blocked", "reason": "coverage.py could not produce a native report", "commands": actions, "bindings": bindings}
    observed = next(
        (detail for name, detail in report["files"].items() if (project.root / name).resolve() == product),
        {},
    )
    ready = (
        report["meta"]["branch_coverage"] and actions["run"]["exit_code"] == 0
        and observable["line"] in observed.get("executed_lines", [])
    )
    return {
        "status": "ready" if ready else "blocked",
        "reason": None if ready else "probe did not complete successfully and observe the required product line",
        "bindings": bindings, "commands": actions, "observe": observable, "source_roots": roots,
        "python": command[0], "tool_version": report["meta"]["version"], "probe": command,
        "coverage_report": project_ref(project, path / "coverage" / "coverage.json"),
        "coverage_sha256": digest((path / "coverage" / "coverage.json").read_bytes()),
    }


OPERATIONS = {"match": match, "runtime": runtime, "coverage": coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    parser.add_argument("id", type=acceptance_id)
    args = parser.parse_args()
    try:
        result = recorded(Project(args.root), args.id, args.operation, json.load(sys.stdin), OPERATIONS[args.operation])
    except (Blocked, KeyError, TypeError, ValueError, OSError) as error:
        result = {"status": "blocked", "reason": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
