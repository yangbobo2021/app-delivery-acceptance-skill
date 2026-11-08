"""Debug, formal execution and review records bound to actual test versions."""

import json
import os

from coverage_adapter import project_measure
from coverage_python import measure_python
from evidence import argv, capture, file_bindings, local, project_ref, upstream, verify_bindings, verify_checkout
from records import Blocked, atomic_write, digest, read_json, require_text, token


JUDGMENTS = {"passed", "needs_revision", "product_defect", "definition_conflict", "insufficient_evidence"}


def evidence_files(project, directory):
    names, size, images = [], 0, 0
    for path in sorted(directory.rglob("*")):
        path = project.path(project_ref(project, path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise Blocked("evidence must be an ordinary file")
        size += path.stat().st_size
        images += path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        if size > 25 * 1024 * 1024 or images > 64:
            raise Blocked("evidence exceeds the per-attempt byte or image limit")
        names.append(project_ref(project, path))
    return file_bindings(project, names) if names else {}


def coverage_policy(project, identifier, request):
    policy = request.get("coverage_policy", "required")
    if policy == "off":
        require_text(request.get("coverage_basis"), "authorization to omit coverage")
        return None
    if policy != "required":
        raise Blocked("coverage policy must be required or off")
    ready = upstream(project, identifier, request, "coverage", "coverage", "ready")
    verify_bindings(project, {ready["coverage_report"]: ready["coverage_sha256"]})
    return ready


def run_cases(project, request, path, cases, coverage, *, cwd="."):
    results = {}
    for identifier, case in cases.items():
        command = argv(case["command"])
        folder = path / identifier
        output = folder / "attachments"
        output.mkdir(parents=True)
        environment = {"ACCEPTANCE_EVIDENCE_DIR": str(output)}
        limit = request.get("timeout", 60)
        measured = {"status": "off"}
        if coverage and "adapter" in coverage:
            report, tools = project_measure(
                project, folder / "coverage", command, coverage["adapter"], coverage["observe"], limit, environment, cwd=cwd,
            )
            actual = tools.get("run")
            measured = {
                "status": "measured" if report and report["tool"] == coverage["tool_version"] else "unavailable",
                "commands": tools, "report": report["path"] if report else None,
                "statistics": report["metrics"] if report else None, "tool": report["tool"] if report else None,
            }
        elif coverage:
            if len(command) < 2 or command[0] != coverage["python"]:
                raise Blocked("case command must use the actual coverage-ready Python and a project script")
            if project_ref(project, project.path(cwd, command[1])) not in case["files"]:
                raise Blocked("Python entry must be among this case's bound test files")
            report, tools = measure_python(
                project, folder / "coverage", command[0], command[1], command[2:],
                coverage["source_roots"], limit, environment, cwd=cwd,
            )
            actual = tools.get("run")
            observed = project.path(coverage["observe"]["path"])
            valid = (
                report is not None and report["meta"]["version"] == coverage["tool_version"]
                and report["meta"]["branch_coverage"]
                and any((project.path(cwd) / name).resolve() == observed and detail["executed_lines"] for name, detail in report["files"].items())
            )
            measured = {
                "status": "measured" if valid else "unavailable",
                "commands": tools,
                "report": project_ref(project, folder / "coverage" / "coverage.json") if report is not None else None,
            }
        else:
            actual = capture(project, command, folder, "run", limit, {**os.environ, **environment}, cwd=cwd)
        results[identifier] = {
            "business_command": command, "execution": actual, "coverage": measured,
            "attachments": evidence_files(project, output),
        }
    return results


def debug(project, identifier, request, definitions, path):
    matched = upstream(project, identifier, request, "match", "match", "matched", verify_live=False)
    runtime = upstream(project, identifier, request, "runtime", "runtime", "ready")
    coverage = coverage_policy(project, identifier, request)
    if matched["scene"] != request["scene"]:
        raise Blocked("case matching belongs to a different scene")
    cases = request["cases"]
    if not isinstance(cases, dict) or not cases:
        raise Blocked("supply actual selected case ids and their individual commands")
    for name, case in cases.items():
        if not token(name, "case id").startswith("TC-"):
            raise Blocked("case ids must start with TC-")
        if not isinstance(case["files"], list) or not case["files"]:
            raise Blocked("each case must identify its test files")
        argv(case["command"])
    candidate_files = sorted({file for case in cases.values() for file in case["files"]})
    candidate = file_bindings(project, candidate_files)
    bindings = {**runtime["bindings"], **(coverage["bindings"] if coverage else {}), **candidate}
    for file in candidate:
        atomic_write(path / "source" / file, project.path(file).read_bytes())
    cwd = runtime.get("cwd", ".")
    if coverage and coverage.get("cwd", ".") != cwd:
        raise Blocked("runtime and coverage must bind the same checkout")
    results = run_cases(project, request, path, cases, coverage, cwd=cwd)
    verify_bindings(project, bindings)
    return {
        "status": "debugged", "cases": cases, "results": results, "bindings": bindings,
        "coverage_preparation": coverage["request_id"] if coverage else None,
        "coverage_basis": request.get("coverage_basis"),
        "obligations": matched["obligations"], "cwd": cwd, "revision": runtime.get("revision"),
        "artifacts": evidence_files(project, path),
    }


def bound_attempt(project, identifier, section, key, expected):
    key = token(key, f"{section} attempt id")
    path = local(project, identifier, section, key, "result.json")
    raw = path.read_bytes()
    record = json.loads(raw)
    if record["operation"] != section or record["status"] != expected:
        raise Blocked(f"invalid {section} attempt")
    return record, digest(raw)


def review(project, identifier, request, definitions, path):
    section = request["target_operation"]
    expected = {"debug": "debugged", "execute": "executed", "history": "imported", "manual": "observed",
                "tdd-red": "red_observed", "tdd-green": "green_observed"}
    if section not in expected:
        raise Blocked("review target must be a real execution or debugging attempt")
    target, sha = bound_attempt(project, identifier, section, request["target"], expected[section])
    if (
        sha != request["target_sha256"] or target["scene"] != request["scene"]
        or target["definitions_sha256"] != request["definitions_sha256"]
    ):
        raise Blocked("review does not refer to this scene, current definitions and exact attempt")
    if target["bindings"]:
        verify_bindings(project, target["bindings"])
    verify_checkout(project, target)
    verify_bindings(project, target["artifacts"])
    reviewer = require_text(request["reviewer_session"], "independent Reviewer session id")
    if reviewer == request.get("author_session"):
        raise Blocked("reviewer must have a separate session")
    if "review_file" in request:
        expected_file = local(project, identifier, "review-inbox", f"{token(request['request_id'], 'review id')}.json")
        received = project.path(request["review_file"])
        if received != expected_file or received.stat().st_size > 256 * 1024:
            raise Blocked("review_file must be the bounded output path assigned to this Reviewer")
        raw = received.read_bytes()
    else:
        raw = require_text(request["raw_review"], "verbatim Reviewer response").encode("utf-8")
    atomic_write(path / "reviewer.txt", raw)
    judgments = json.loads(raw)["cases"]
    if set(judgments) != set(target["results"]):
        raise Blocked("Reviewer must return a judgment for every actual case")
    for key, finding in judgments.items():
        require_text(finding["reason"], "review reason")
        if finding["status"] not in JUDGMENTS and not (
            section == "tdd-red" and finding["status"] == "expected_failure"
        ):
            raise Blocked("unrecognized review judgment")
        if any(item not in target.get("obligations", {}) for item in finding.get("covers", [])):
            raise Blocked("review coverage must reference declared scene obligations")
        result = target["results"][key]
        if finding.get("design_status") is not None and (
            section != "debug" or finding["status"] != "product_defect" or finding["design_status"] != "valid"
        ):
            raise Blocked("design_status valid is only used for independently confirmed product defects during debug")
        if finding["status"] == "passed" and section in {"debug", "execute", "tdd-green"}:
            if not result["execution"] or result["execution"]["exit_code"] != 0 or result["coverage"]["status"] == "unavailable":
                raise Blocked("a failed execution or missing required coverage cannot pass")
        if finding["status"] == "passed" and section == "manual":
            if any(item["outcome"] != "observed_success" for item in result["operations"]):
                raise Blocked("failed or uncertain manual observations cannot pass")
    return {
        "status": "reviewed", "judgments": judgments, "reviewer_session": reviewer,
        "target_operation": section, "target": target["request_id"], "target_sha256": sha,
        "bindings": target["bindings"], "artifacts": evidence_files(project, path),
        "cwd": target.get("cwd", "."), "revision": target.get("revision"),
        "reviewer_response": project_ref(project, path / "reviewer.txt"),
    }


def execute(project, identifier, request, definitions, path):
    assessment = upstream(project, identifier, request, "design_review", "review", "reviewed")
    if assessment["target_operation"] != "debug" or assessment["scene"] != request["scene"]:
        raise Blocked("formal execution needs an independent design review of this scene")
    design, sha = bound_attempt(project, identifier, "debug", assessment["target"], "debugged")
    if sha != assessment["target_sha256"]:
        raise Blocked("reviewed design evidence changed")
    verify_bindings(project, design["bindings"])
    verify_bindings(project, design["artifacts"])
    selected = request["selected_case_ids"]
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise Blocked("provide distinct case ids for this run")
    def valid_design(finding):
        return finding.get("status") == "passed" or (
            finding.get("status") == "product_defect" and finding.get("design_status") == "valid"
        )

    if any(not valid_design(assessment["judgments"].get(key, {})) for key in selected):
        raise Blocked("selected cases lack a passing design review")
    coverage = None
    if design["coverage_preparation"]:
        coverage = coverage_policy(project, identifier, {**request, "coverage": design["coverage_preparation"]})
    cases = {key: design["cases"][key] for key in selected}
    results = run_cases(project, request, path, cases, coverage, cwd=design.get("cwd", "."))
    verify_bindings(project, design["bindings"])
    return {
        "status": "executed", "results": results, "design_review": assessment["request_id"],
        "obligations": design.get("obligations", {}), "designed_case_ids": sorted(design["cases"]),
        "selection": "all_designed_cases" if set(selected) == set(design["cases"]) else "selected_cases_only",
        "bindings": design["bindings"], "artifacts": evidence_files(project, path),
        "cwd": design.get("cwd", "."), "revision": design.get("revision"),
    }


def history(project, identifier, request, definitions, path):
    source = project.path(request["receipt"])
    receipt = read_json(source)
    folder = source.parent
    files = {
        folder / "inputs" / name: sha for name, sha in receipt["inputs_sha256"].items()
    }
    files.update({folder / f"{name}.txt": receipt[f"{name}_sha256"] for name in ("stdout", "stderr")})
    attachments = {}
    for original, sha in files.items():
        original = project.path(project_ref(project, original))
        raw = original.read_bytes()
        if digest(raw) != sha:
            raise Blocked("historical evidence does not match its original receipt")
        target = path / "original" / original.relative_to(folder)
        atomic_write(target, raw)
        if original.name in {"stdout.txt", "stderr.txt"}:
            attachments[project_ref(project, target)] = sha
    atomic_write(path / "original" / "receipt.json", source.read_bytes())
    name = token(request["case_id"], "historical case id")
    return {
        "status": "imported", "bindings": {}, "artifacts": evidence_files(project, path),
        "results": {name: {"execution": receipt, "coverage": {"status": "unknown"}, "attachments": attachments}},
        "selection": "historical",
    }


def manual(project, identifier, request, definitions, path):
    result = {}
    cases = request["cases"]
    if not isinstance(cases, dict) or not cases:
        raise Blocked("manual evidence needs actual case observations")
    product_version = require_text(request["product_version"], "observed product version")
    obligations = {}
    if request.get("match"):
        matched = upstream(project, identifier, request, "match", "match", "matched")
        if matched["scene"] != request["scene"]:
            raise Blocked("manual matching belongs to a different scene")
        obligations = matched["obligations"]
    for case_id, observations in cases.items():
        if not token(case_id, "manual case id").startswith("TC-") or not observations:
            raise Blocked("manual case needs a TC id and observed operations")
        attachments = {}
        for operation in observations:
            for key in ("action", "expected", "observed", "performed_by", "performed_at", "receipt_reference"):
                require_text(operation[key], f"manual {key}")
            if operation["outcome"] not in {"observed_success", "observed_failure", "uncertain"}:
                raise Blocked("unknown manual observation outcome")
            for name, expected in operation["evidence"].items():
                raw = project.path(name).read_bytes()
                if digest(raw) != expected:
                    raise Blocked("manual evidence has changed")
                target = path / case_id / "attachments" / digest(name.encode())[:16] / project.path(name).name
                atomic_write(target, raw)
                attachments[project_ref(project, target)] = expected
        if not attachments:
            raise Blocked("manual observations require actual attachments")
        result[case_id] = {
            "execution": None, "coverage": {"status": "unknown"},
            "operations": observations, "attachments": attachments,
        }
    return {
        "status": "observed", "results": result, "bindings": {},
        "product_version": product_version, "selection": "selected_cases_only", "obligations": obligations,
        "artifacts": evidence_files(project, path),
    }
