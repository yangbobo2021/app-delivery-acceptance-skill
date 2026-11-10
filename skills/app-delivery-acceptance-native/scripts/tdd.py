"""Record a reviewed red test and rerun exactly that candidate on changed product."""
import os

from case_design import require_current_design
from casework import bound_attempt, coverage_policy, evidence_files, run_cases
from evidence import argv, capture, file_bindings, upstream, verify_bindings
from records import Blocked, acceptance_id, atomic_write, token


def match_tdd_case(project, identifier, definitions, scene, source, reference):
    """Reuse a TDD identity only when its recorded test and scenario still match."""
    origin = acceptance_id(reference.get("acceptance_id", identifier))
    case_id = token(reference["case_id"], "TDD case id")
    receipt, sha = bound_attempt(project, origin, "tdd-green", reference["green"], "green_observed")
    prior_scene = next((version["scenarios"].get(scene) for _, digest, version in project.versions(origin)
                        if digest == receipt["definitions_sha256"]), None)
    case = receipt["cases"].get(case_id)
    if (receipt["acceptance_id"] != origin or receipt["scene"] != scene
            or prior_scene != definitions["scenarios"][scene] or case is None
            or source["path"] not in case["files"]
            or receipt["test_bindings"].get(source["path"]) != source["sha256"]):
        raise Blocked("TDD case needs a matching scene, test file, and current test bytes")
    if case.get("source") and case["source"] != source:
        raise Blocked("selected declaration differs from the TDD case source")
    verify_bindings(project, receipt["artifacts"])
    return case_id, {"record": receipt["record"], "sha256": sha}


def fresh_python_environment(path):
    # The interpreter can otherwise reuse a same-size, same-second prior source revision.
    return {"PYTHONPYCACHEPREFIX": str(path / "python-cache"), "PYTHONDONTWRITEBYTECODE": "1"}


def run(project, request, path, runtime, coverage, product, cases, test_bindings):
    bindings = {**runtime["bindings"], **(coverage["bindings"] if coverage else {}), **product, **test_bindings}
    cwd = runtime.get("cwd", ".")
    if coverage and coverage.get("cwd", ".") != cwd:
        raise Blocked("runtime and coverage must bind the same checkout")
    for name in test_bindings:
        atomic_write(path / "source" / name, project.path(name).read_bytes())
    results = run_cases(project, request, path, cases, coverage, cwd=cwd, extra_env=fresh_python_environment(path))
    verify_bindings(project, bindings)
    return {
        "bindings": bindings, "product_bindings": product, "test_bindings": test_bindings,
        "cases": cases, "results": results, "obligations": {}, "cwd": cwd,
        "revision": runtime.get("revision"),
    }


def red(project, identifier, request, definitions, path):
    runtime = upstream(project, identifier, request, "runtime", "runtime", "ready")
    coverage = coverage_policy(project, identifier, request)
    product = file_bindings(project, request["product_files"])
    case_id = token(request["case_id"], "case id")
    if not case_id.startswith("TC-"):
        raise Blocked("case ids must start with TC-")
    case = {"command": argv(request["command"]), "files": request["test_files"]}
    tests = file_bindings(project, case["files"])
    case_design = None
    if request.get("match"):
        matched = upstream(project, identifier, request, "match", "match", "matched")
        implementation = matched["cases"].get(case_id, {})
        if (matched["scene"] != request["scene"] or implementation.get("status") != "active"
                or implementation.get("kind") == "manual" or not implementation["covers"]
                or implementation["source"]["path"] not in tests):
            raise Blocked("TDD red needs a matched automated implementation of the scene checks")
        case.update(source=implementation["source"], covers=implementation["covers"])
        case_design = matched.get("case_design")
    require_current_design(project, identifier, request, case_design)
    if "source" in request:
        source = project.verified_source(request["source"])
        if (source["kind"] != "file" or tests.get(source["path"]) != source["sha256"]
                or source != case.get("source", source)):
            raise Blocked("TDD case source must be among the bound test files")
        case["source"] = source
    if product.keys() & tests.keys():
        raise Blocked("product and test files must be distinct")
    result = run(project, request, path, runtime, coverage, product, {case_id: case}, tests)
    observed = result["results"][case_id]
    execution = observed["execution"] or {}
    code = execution.get("exit_code")
    failed = type(code) is int and code > 0 and observed["coverage"]["status"] != "unavailable"
    return {
        **result, "status": "red_observed" if failed else "blocked",
        "reason": None if failed else "expected a completed failing test with required evidence",
        "artifacts": evidence_files(project, path),
        **({"case_design": case_design} if case_design else {}),
    }


def green(project, identifier, request, definitions, path):
    review = upstream(project, identifier, request, "red_review", "review", "reviewed", verify_live=False)
    if review["target_operation"] != "tdd-red" or review["scene"] != request["scene"]:
        raise Blocked("green needs a reviewed red attempt for the same scene")
    original, sha = bound_attempt(project, identifier, "tdd-red", review["target"], "red_observed")
    if sha != review["target_sha256"] or any(row["status"] != "expected_failure" for row in review["judgments"].values()):
        raise Blocked("green needs a Reviewer-confirmed behavioral failure")
    case_design = original.get("case_design")
    require_current_design(project, identifier, request, case_design)
    verify_bindings(project, original["artifacts"])
    verify_bindings(project, original["test_bindings"])
    runtime = upstream(project, identifier, request, "runtime", "runtime", "ready")
    coverage = coverage_policy(project, identifier, request)
    if runtime.get("cwd", ".") != original["cwd"]:
        raise Blocked("red and green must run in the same candidate workspace")
    product = file_bindings(project, list(original["product_bindings"]))
    if product == original["product_bindings"]:
        raise Blocked("product files have not changed since red")
    result = run(project, request, path, runtime, coverage, product, original["cases"], original["test_bindings"])
    regression = capture(project, argv(request["regression_command"]), path, "regression",
                         request.get("timeout", 60), {**os.environ, **fresh_python_environment(path)}, cwd=result["cwd"])
    verify_bindings(project, result["bindings"])
    passed = regression["exit_code"] == 0 and all(
        row["execution"] and row["execution"]["exit_code"] == 0 and row["coverage"]["status"] != "unavailable"
        for row in result["results"].values()
    )
    return {
        **result, "status": "green_observed" if passed else "blocked",
        "reason": None if passed else "the original test or project regression did not pass",
        "red": original["request_id"], "red_review": review["request_id"],
        "regression": regression, "artifacts": evidence_files(project, path),
        **({"case_design": case_design} if case_design else {}),
    }
