"""Natural-language case design, recorded independently of test implementation."""

from evidence import attempts
from records import Blocked, require_text, token


def scene_obligations(definitions, scene, items):
    scenario = definitions["scenarios"][scene]
    scope = scenario.get("scenario_scope")
    sources = ([criterion for group in scope.get("groups", [scope]) for criterion in group["criteria"]]
               if scope else [scenario["source"]["text"]])
    label = "a recorded scenario criterion" if scope else "the recorded test scenario"
    obligations = {}
    for item in items:
        key = token(item["id"], "obligation id")
        quote = require_text(item["quote"], "test scenario quote")
        if key in obligations or not any(quote in source for source in sources):
            raise Blocked(f"obligations must have unique ids and quote {label} exactly")
        obligations[key] = quote
    if not obligations:
        raise Blocked("enumerate the checks supported by the recorded test scenario")
    return obligations


def plan(project, identifier, request, definitions, path):
    obligations = scene_obligations(definitions, request["scene"], request["obligations"])
    cases, assigned = {}, set()
    for name, case in request["cases"].items():
        token(name, "case design id")
        kind = case["kind"]
        if kind not in {"automated", "manual"}:
            raise Blocked("design kind must be automated or manual")
        checks = case["checks"]
        if not isinstance(checks, list) or not checks:
            raise Blocked("supply the checks in their intended order")
        ids = {item["id"] for item in checks}
        if len(ids) != len(checks) or not ids <= obligations.keys() or assigned & ids:
            raise Blocked("assign each designed check to one case using a declared obligation id")
        steps = []
        for item in checks:
            step = {"id": item["id"], "action": require_text(item["action"], "check action and input"),
                    "expected": require_text(item["expected"], "check expected result")}
            if "title" in item:
                step["title"] = require_text(item["title"], "check title")
            if "setup" in item:
                if not isinstance(item["setup"], list):
                    raise Blocked("supply check setup as an ordered list")
                step["setup"] = [require_text(action, "check setup action") for action in item["setup"]]
            steps.append(step)
        cases[name] = {
            "title": require_text(case["title"], "case title"), "kind": kind,
            "preconditions": require_text(case["preconditions"], "case preconditions and test data"),
            "checks": steps,
        }
        assigned.update(ids)
    missing = sorted(obligations.keys() - assigned)
    gaps = request.get("gaps", {})
    if set(gaps) != set(missing):
        raise Blocked("give a reason for each obligation without a case design")
    return {"status": "designed", "obligations": obligations, "cases": cases,
            "missing": missing, "gaps": {key: require_text(gaps[key], "design gap reason") for key in missing}}


def current_designs(project, identifier, definitions_sha256):
    return {
        row["scene"]: (row, {"id": row["request_id"], "record": row["record"], "sha256": sha})
        for row, sha in attempts(project, identifier, "case-design")
        if row["status"] == "designed" and row["definitions_sha256"] == definitions_sha256
    }


def require_current_design(project, identifier, request, reference):
    current = current_designs(project, identifier, request["definitions_sha256"]).get(request["scene"])
    if reference != (current[1] if current else None):
        raise Blocked("use the current case design for this test scenario")
