"""Run each backend and replay the other backend's real records without migration."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from booking_fixture import prepare_booking
from skill_command import SCRIPTS

IDENTIFIER = "AT-parity"
PYTHON_COMMIT = "a5c9637728956239e9e7975802fbd240d9338605"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, original, writer):
    root = output / writer
    prepare_booking(root, "C2")

    def invoke(language, script, command, payload=None, *args):
        entry = [sys.executable, original / f"{script}.py"] if language == "python" else ["node", SCRIPTS / f"{script}.mts"]
        raw = subprocess.run(
            [*entry, "--root", root, command, IDENTIFIER, *args], input=json.dumps(payload), encoding="utf-8",
            capture_output=True, check=True, timeout=60,
        )
        return json.loads(raw.stdout)

    reader = "typescript" if writer == "python" else "python"
    assert invoke(writer, "records", "create", None, "--request", "验证重叠预约。", "--reference", "fixture")["status"] == "created"
    assert invoke(reader, "records", "create", None, "--request", "验证重叠预约。", "--reference", "fixture")["status"] == "already_created"
    basis = {"kind": "user", "reference": "fixture", "text": "同一会议室的重叠预约返回 409。"}
    freeze = {
        "request_id": "freeze", "base_sha256": None,
        "requirements": [{"id": "R1", "source": basis}],
        "scenarios": [{
            "id": "SC1", "source": basis, "requirements": ["R1"],
            "scenario_scope": {
                "title": "重叠预约", "behavior": "验证同一会议室已有预约的冲突处理。",
                "groups": [{"title": "拒绝", "criteria": ["同一会议室的重叠预约返回 409。"]}],
            },
        }],
        "encoding_probe": {"10": 5, "2": 2, "numeric_forms": [1.0, -0.0, 1e-5, 1e16, 9007199254740993]},
    }
    frozen = invoke(writer, "records", "freeze", freeze)
    replay = invoke(reader, "records", "freeze", freeze)
    assert replay == {**frozen, "status": "already_frozen"}
    assert sha(root / frozen["path"]) == frozen["sha256"]

    operations = []
    def operation(name, request):
        payload = {"definitions_sha256": frozen["sha256"], **request}
        receipt = invoke(writer, "operations", name, payload)
        before = (root / receipt["record"]).read_bytes()
        assert invoke(reader, "operations", name, payload) == receipt, name
        assert (root / receipt["record"]).read_bytes() == before, name
        operations.append(name)
        return receipt

    operation("analyze", {"request_id": "scope", "requirement_ids": ["R1"], "analysis": "按场景核对已确认的范围。",
                         "scope_review": {"baseline_sha256": frozen["sha256"], "scenario_ids": ["SC1"]}})
    scene = {"scene": "SC1"}
    stable_id = "TC-" + str(uuid.uuid5(uuid.NAMESPACE_URL, "tests/check_s1.py:main"))
    designed_id = "D-reject" if writer == "python" else stable_id
    operation("case-design", {
        **scene, "request_id": "design", "obligations": [{"id": "reject", "quote": "同一会议室的重叠预约返回 409。"}],
        "cases": {designed_id: {"title": "拒绝重叠预约", "kind": "automated", "preconditions": "已有预约",
                              "checks": [{"id": "reject", "action": "提交重叠时段", "expected": "返回 409"}]}},
    })
    records = [sys.executable, original / "records.py"] if writer == "python" else ["node", SCRIPTS / "records.mts"]
    test = "tests/check_s1.py"
    source = json.loads(subprocess.check_output([*records, "--root", root, "source", test, "20", "27"]))
    match = operation("match", {
        **scene, "request_id": "match", "case_design": "design", "search_scope": "现有预约集成用例",
        "inspected": {test: source["sha256"]},
        "cases": [{"selector": "main", "status": "active", "source": source,
                   **({"case_id": stable_id} if writer == "typescript" else {}),
                   "covers": {"reject": "assert rejected['status'] == 409"}}],
    })
    case_id, = match["cases"]
    command = [sys.executable, "tools/smoke.py"]
    operation("runtime", {"request_id": "runtime", "files": ["app/booking.py", "tools/smoke.py"], "command": command})
    operation("coverage", {"request_id": "coverage", "runtime": "runtime", "source_roots": ["app"], "command": command,
                           "observe": {"path": "app/booking.py", "line": 35}})
    debug = operation("debug", {
        **scene, "request_id": "debug", "runtime": "runtime", "coverage": "coverage", "match": "match",
        "cases": {case_id: {"command": [sys.executable, test], "files": [test]}},
    })

    def review(target, key):
        return operation("review", {
            **scene, "request_id": key, "target_operation": target["operation"], "target": target["request_id"],
            "target_sha256": sha(root / target["record"]), "reviewer_session": "fixture-independent-" + key,
            "raw_review": json.dumps({"cases": {case_id: {"status": "passed", "reason": "符合断言", "covers": ["reject"]}}}),
        })

    review(debug, "design-review")
    formal = operation("execute", {**scene, "request_id": "formal", "design_review": "design-review", "selected_case_ids": [case_id]})
    observed = formal["results"][case_id]
    assert observed["execution"]["exit_code"] == 0 and observed["coverage"]["status"] == "measured"
    review(formal, "execution-review")
    operation("coverage-recompute", {"request_id": "recompute", "reports": [{"path": observed["coverage"]["report"], "receipt": formal["record"]}]})
    inbox = root / ".acceptance-local/native-v1" / IDENTIFIER / "review-inbox/delivery.json"
    inbox.parent.mkdir()
    inbox.write_text('{"status":"passed","reason":"同版正式执行和复审通过"}', encoding="utf-8")
    operation("delivery-judge", {"request_id": "delivery", "requirement_ids": ["R1"], "reviewer_session": "fixture-delivery",
                                 "review_file": inbox.relative_to(root).as_posix()})

    current = invoke(writer, "results", "view")
    assert current == invoke(reader, "results", "view")
    assert current["requirement_results"]["R1"]["status"] == "passed"
    exported = invoke(writer, "results", "report", {"request_id": "first"})
    assert invoke(reader, "results", "report", {"request_id": "first"}) == exported
    other = invoke(reader, "results", "report", {"request_id": "second"})
    for field in ("report", "snapshot"):
        assert (root / exported[field]).read_bytes() == (root / other[field]).read_bytes(), field
    evidence, = [name for name in observed["execution"]["output"] if name.endswith("stdout")]
    request = {"request_id": "archive", "files": {evidence: sha(root / evidence)}}
    archive = invoke(writer, "lifecycle", "archive", request)
    assert invoke(reader, "lifecycle", "archive", request) == archive
    assert invoke(writer, "lifecycle", "finish", {"request_id": "finish"}) == invoke(reader, "lifecycle", "finish", {"request_id": "finish"})
    removed = invoke(reader, "lifecycle", "cleanup", {
        "request_id": "cleanup", "archive": archive["archive"], "archive_sha256": archive["sha256"],
        "authorization": "fixture approves removal of the exact archived log", "files": [evidence],
    })
    assert removed["status"] == "cleaned" and not (root / evidence).exists()
    return {"writer": writer, "reader": reader, "operations": operations, "case_id": case_id,
            "definitions_sha256": frozen["sha256"], "evidence": str(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-scripts", type=Path, required=True,
                        help="scripts directory from Python baseline " + PYTHON_COMMIT)
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix="acceptance-runtime-parity-")).resolve()
    results = [run(output, args.python_scripts.resolve(), writer) for writer in ("python", "typescript")]
    assert results[0]["case_id"] == results[1]["case_id"]
    assert results[0]["definitions_sha256"] == results[1]["definitions_sha256"]
    audit = output / "comparison.json"
    audit.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(audit)


if __name__ == "__main__":
    main()
