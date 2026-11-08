import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from booking_fixture import prepare_booking


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/app-delivery-acceptance-native/scripts"
COVERAGE_PYTHON = os.environ.get("ACCEPTANCE_COVERAGE_PYTHON") or (
    sys.executable if importlib.util.find_spec("coverage") else None
)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "project"
        prepare_booking(self.root, "C2")
        self.id = "AT-operations"
        self.record("create", self.id, "--request", "验收 SC1。", "--reference", "test:user")
        requirements = self.record("source", "requirements.md", "3", "3")
        scene = self.record("source", "scenarios.md", "3", "10")
        self.freeze = self.record("freeze", self.id, payload={
            "request_id": "freeze", "base_sha256": None,
            "requirements": [{"id": "R1", "source": requirements}],
            "scenarios": [{"id": "SC1", "requirements": ["R1"], "source": scene}],
        })

    def run_json(self, file, args, payload, expected_code):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / file), "--root", str(self.root), *args],
            input=json.dumps(payload), text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def record(self, *args, payload=None):
        return self.run_json("records.py", args, payload, 0)

    def operation(self, name, request, expected_code=0):
        return self.run_json("operations.py", [name, self.id], {
            "definitions_sha256": self.freeze["sha256"], **request,
        }, expected_code)

    def test_matching_distinguishes_full_partial_and_no_match_without_running(self):
        test_file = "tests/check_s1.py"
        source = self.record("source", test_file, "20", "30")
        assertion = "assert rejected['status'] == 409"
        self.assertIn(assertion, source["text"])
        base = {
            "scene": "SC1", "search_scope": "实际检查 tests/check_s1.py",
            "inspected": {test_file: source["sha256"]},
            "obligations": [{"id": "reject", "quote": "第二次响应 JSON status 必须为 409"}],
            "cases": [{"selector": "main", "status": "active", "source": source, "covers": {"reject": assertion}}],
        }
        full = self.operation("match", {**base, "request_id": "full"})
        self.assertEqual(full["coverage"], "full")
        extra = {"id": "state", "quote": "所有字段和值均不变"}
        partial = self.operation("match", {**base, "request_id": "partial", "obligations": [*base["obligations"], extra]})
        none = self.operation("match", {**base, "request_id": "none", "cases": []})
        self.assertEqual((partial["coverage"], partial["missing"]), ("partial", ["state"]))
        self.assertEqual((none["coverage"], none["missing"]), ("none", ["reject"]))
        self.assertEqual(set(full["cases"]), set(partial["cases"]))
        self.assertEqual(full["execution_status"], "not_executed")
        self.assertFalse(list(self.root.rglob("*.sqlite")))

    def runtime(self, command=None, key="runtime", expected_code=0):
        return self.operation("runtime", {
            "request_id": key, "files": ["app/booking.py", "tools/smoke.py"],
            "command": command or [sys.executable, "tools/smoke.py"],
        }, expected_code)

    def test_runtime_requires_actual_exit_and_reuses_valid_receipt(self):
        runtime = self.runtime()
        self.assertEqual(runtime["status"], "ready")
        self.assertIn("booking subprocess ready", (self.root / next(iter(runtime["smoke"]["output"]))).read_text())
        self.assertEqual(runtime, self.runtime())
        failed = self.runtime([sys.executable, "-c", "raise SystemExit(19)"], "failed", 2)
        self.assertEqual(failed["smoke"]["exit_code"], 19)
        with (self.root / "tools/smoke.py").open("a") as out:
            out.write("\n# Changed")
        self.assertIn("changed", self.runtime(expected_code=2)["reason"])

    @unittest.skipUnless(COVERAGE_PYTHON, "install coverage.py or set ACCEPTANCE_COVERAGE_PYTHON")
    def test_coverage_verifies_product_subprocess_and_rejects_unobserved_line(self):
        self.runtime()
        request = {
            "request_id": "coverage", "runtime": "runtime", "source_roots": ["app"],
            "command": [COVERAGE_PYTHON, "tools/smoke.py"], "observe": {"path": "app/booking.py", "line": 35},
        }
        prepared = self.operation("coverage", request)
        self.assertEqual(prepared["status"], "ready")
        report = json.loads((self.root / prepared["coverage_report"]).read_text())
        self.assertIn(35, report["files"]["app/booking.py"]["executed_lines"])
        self.assertTrue(report["meta"]["branch_coverage"])
        blocked = self.operation("coverage", {**request, "request_id": "unobserved", "observe": {"path": "app/booking.py", "line": 44}}, 2)
        self.assertIn("did not complete successfully and observe", blocked["reason"])
