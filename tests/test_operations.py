import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
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

    def test_scope_review_rejects_omissions_and_survives_only_unrelated_changes(self):
        baseline = self.freeze["sha256"]
        review = {
            "requirement_ids": ["R1"], "analysis": "实际核对已冻结的可观察行为。",
            "scope_review": {"baseline_sha256": baseline, "scenario_ids": ["SC1"]},
        }
        self.operation("analyze", {
            **review, "request_id": "missing",
            "scope_review": {**review["scope_review"], "scenario_ids": []},
        }, expected_code=2)
        result = self.operation("analyze", {**review, "request_id": "review-r1"})
        self.assertEqual(result["scope_review"]["added_scenario_ids"], [])
        source = self.record("source", "scenarios.md", "3", "10")
        self.freeze = self.record("freeze", self.id, payload={
            "request_id": "add-unrelated", "base_sha256": baseline,
            "requirements": [{"id": "R2", "source": self.record("source", "requirements.md", "3", "3")}],
            "scenarios": [{"id": "SC2", "requirements": ["R2"], "source": source}],
        })
        snapshot = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertEqual(snapshot["scope_reviews"]["R1"]["record"], result["record"])
        self.freeze = self.record("freeze", self.id, payload={
            "request_id": "add-related", "base_sha256": self.freeze["sha256"],
            "scenarios": [{"id": "SC3", "requirements": ["R1"], "source": source}],
        })
        snapshot = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertNotIn("R1", snapshot.get("scope_reviews", {}))
        revised = self.operation("analyze", {
            **review, "request_id": "review-r1-updated",
            "scope_review": {**review["scope_review"], "scenario_ids": ["SC1", "SC3"]},
        })
        self.assertEqual(revised["scope_review"]["added_scenario_ids"], ["SC3"])

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
        index = self.root / ".acceptance/native-v1" / self.id / "cases/SC1/match-full.json"
        shared = json.loads(index.read_text())
        self.assertEqual(shared["result_sha256"], hashlib.sha256((self.root / full["record"]).read_bytes()).hexdigest())
        self.assertEqual(shared["case_ids"], sorted(full["cases"]))
        self.assertFalse(list(self.root.rglob("*.sqlite")))

    def runtime(self, command=None, key="runtime", expected_code=0):
        return self.operation("runtime", {
            "request_id": key, "files": ["app/booking.py", "tools/smoke.py"],
            "command": command or [sys.executable, "tools/smoke.py"],
        }, expected_code)

    def review_attempt(self, attempt, key, decision):
        return self.operation("review", {
            "request_id": key, "scene": "SC1",
            "target_operation": attempt["operation"], "target": attempt["request_id"],
            "target_sha256": hashlib.sha256((self.root / attempt["record"]).read_bytes()).hexdigest(),
            "reviewer_session": "separate-fixture-reviewer",
            "raw_review": json.dumps({"cases": {"TC-verified": {"reason": "fixture result", **decision}}}),
        })

    def test_valid_design_with_a_product_defect_runs_formally_and_reports_failure(self):
        self.runtime()
        match = self.operation("match", {
            "request_id": "match", "scene": "SC1", "search_scope": "none",
            "inspected": {}, "cases": [],
            "obligations": [{"id": "reject", "quote": "第二次响应 JSON status 必须为 409"}],
        })
        path = "tests/check_s1.py"
        (self.root / path).write_text("raise SystemExit(1)\n", encoding="utf-8")
        debug = self.operation("debug", {
            "request_id": "debug", "scene": "SC1", "match": match["request_id"], "runtime": "runtime",
            "coverage_policy": "off", "coverage_basis": "isolated fixture",
            "cases": {"TC-verified": {"command": [sys.executable, path], "files": [path]}},
        })
        legacy = self.review_attempt(debug, "legacy", {"status": "product_defect"})
        form = {"request_id": "formal", "scene": "SC1", "selected_case_ids": ["TC-verified"]}
        self.operation("execute", {**form, "design_review": legacy["request_id"]}, 2)
        design = self.review_attempt(debug, "design", {"status": "product_defect", "design_status": "valid"})
        result = self.operation("execute", {**form, "request_id": "formal-valid", "design_review": design["request_id"]})
        self.assertEqual(result["results"]["TC-verified"]["execution"]["exit_code"], 1)
        self.review_attempt(result, "execution-review", {"status": "product_defect"})
        view = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertEqual(view["scenes"]["SC1"]["cases"]["TC-verified"]["status"], "product_defect")

    def test_tdd_requires_reviewed_red_and_preserves_test_bytes_on_green(self):
        product = self.root / "product.py"
        test = self.root / "check_new.py"
        product.write_text("VALUE = 1\n", encoding="utf-8")
        test.write_text("from product import VALUE\nassert VALUE == 2, VALUE\n", encoding="utf-8")
        policy = {"coverage_policy": "off", "coverage_basis": "isolated fixture"}
        probe = {"files": ["product.py"], "command": [sys.executable, "-c", "import product"]}
        self.operation("runtime", {"request_id": "baseline", **probe})
        red = self.operation("tdd-red", {
            "request_id": "red", "scene": "SC1", "runtime": "baseline", "case_id": "TC-verified",
            "product_files": ["product.py"], "test_files": ["check_new.py"],
            "command": [sys.executable, "check_new.py"], **policy,
        })
        self.assertEqual(red["results"]["TC-verified"]["execution"]["exit_code"], 1)
        self.review_attempt(red, "red-review", {"status": "expected_failure"})
        product.write_text("VALUE = 2\n", encoding="utf-8")
        self.operation("runtime", {"request_id": "changed", **probe})
        request = {"scene": "SC1", "runtime": "changed", "red_review": "red-review",
                   "regression_command": [sys.executable, "check_new.py"], **policy}
        self.operation("tdd-green", {**request, "request_id": "missing-review", "red_review": "absent"}, 2)
        original = test.read_bytes()
        test.write_text("assert True\n", encoding="utf-8")
        self.operation("tdd-green", {**request, "request_id": "modified-test"}, 2)
        test.write_bytes(original)
        green = self.operation("tdd-green", {**request, "request_id": "green"})
        self.assertEqual(green["test_bindings"], red["test_bindings"])
        self.assertEqual(green["cases"], red["cases"])
        self.assertEqual(green["regression"]["exit_code"], 0)
        self.review_attempt(green, "green-review", {"status": "passed"})
        source = self.record("source", "check_new.py", "1", "2")
        match_request = {
            "request_id": "formal-match", "scene": "SC1", "search_scope": "reuse the legacy TDD case",
            "inspected": {"check_new.py": source["sha256"]},
            "obligations": [{"id": "reject", "quote": "第二次响应 JSON status 必须为 409"}],
            "cases": [{"selector": "check_new.py", "source": source, "status": "active", "covers": {},
                       "tdd_case": {"green": "green", "case_id": "TC-verified"}}],
        }
        matched = self.operation("match", match_request)
        self.assertEqual(list(matched["cases"]), ["TC-verified"])
        self.assertEqual(matched["cases"]["TC-verified"]["tdd_case"]["record"], green["record"])
        test.write_bytes(original + b"# changed since green\n")
        changed = self.record("source", "check_new.py", "1", "2")
        blocked = self.operation("match", {
            **match_request, "request_id": "formal-match-changed",
            "inspected": {"check_new.py": changed["sha256"]},
            "cases": [{**match_request["cases"][0], "source": changed}],
        }, expected_code=2)
        self.assertIn("current test bytes", blocked["reason"])

    def test_inventory_rejects_partial_scope_without_replacing_saved_report(self):
        report = {"revision": "fixture-head", "scope": ["R1", "R2"], "items": [
            {"id": "SC1", "requirement": "R1", "status": "untested", "sources": ["requirements.md"],
             "cases": [], "evidence": [], "next": "待执行"},
            {"id": "R2", "requirement": "R2", "status": "unconfirmed", "sources": ["scenarios.md"],
             "cases": [], "evidence": [], "next": "待确认"},
        ]}
        saved = self.run_json("results.py", ["save-inventory"], report, 0)
        path = self.root / saved["report"]
        self.assertEqual(json.loads(path.read_bytes()), report)
        original = path.read_bytes()
        missing = self.run_json("results.py", ["save-inventory"], {**report, "items": report["items"][:1]}, 2)
        self.assertIn("missing ['R2']", missing["reason"])
        self.assertEqual(path.read_bytes(), original)

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

    def test_formal_execution_uses_only_reviewed_current_cases_and_preserves_evidence(self):
        self.operation("analyze", {
            "request_id": "scope", "requirement_ids": ["R1"], "analysis": "核对 SC1 范围。",
            "scope_review": {"baseline_sha256": self.freeze["sha256"], "scenario_ids": ["SC1"]},
        })
        self.runtime()
        src = self.record("source", "tests/check_s1.py", "20", "27")
        matched = self.operation("match", {
            "request_id": "match", "scene": "SC1", "search_scope": "actual fixture test",
            "inspected": {"tests/check_s1.py": src["sha256"]},
            "obligations": [{"id": "reject", "quote": "第二次响应 JSON status 必须为 409"}],
            "cases": [{
                "selector": "main", "status": "active", "source": src,
                "covers": {"reject": "assert rejected['status'] == 409"},
            }],
        })
        case_id = next(iter(matched["cases"]))
        selected = {case_id: {"command": [sys.executable, "tests/check_s1.py"], "files": ["tests/check_s1.py"]}}
        debug_request = {
            "request_id": "debug-1", "scene": "SC1", "match": "match", "runtime": "runtime",
            "coverage_policy": "off", "coverage_basis": "Fixture tests the evidence gate independently of coverage.",
            "cases": selected,
        }
        weak = self.operation("debug", debug_request)
        self.assertEqual(weak["results"][case_id]["execution"]["exit_code"], 0)

        def assess(target, decision, key):
            raw = json.dumps({"cases": {case_id: {"status": decision, "reason": "fixture verdict", "covers": ["reject"]}}})
            result = self.operation("review", {
                "request_id": key, "scene": "SC1", "target_operation": target["operation"],
                "target": target["request_id"], "target_sha256": hashlib.sha256((self.root / target["record"]).read_bytes()).hexdigest(),
                "reviewer_session": "fixture-separate-reviewer", "raw_review": raw,
            })
            self.assertEqual((self.root / result["reviewer_response"]).read_text(), raw)
            return result

        assess(weak, "needs_revision", "review-1")
        execution_request = {
            "request_id": "blocked-before-repair", "scene": "SC1",
            "design_review": "review-1", "selected_case_ids": [case_id],
        }
        blocked = self.operation("execute", execution_request, 2)
        self.assertIn("passing design review", blocked["reason"])
        path = self.root / "tests/check_s1.py"
        original = path.read_text()
        path.write_text(original.replace(
            "        assert first['status'] == 201, first",
            "        assert first['status'] == 201, first\n        assert first['booking']['title'] == '原始周会', first",
        ), encoding="utf-8")
        revised = self.operation("debug", {**debug_request, "request_id": "debug-2"})
        self.assertEqual((self.root / weak["record"]).parent.joinpath("source/tests/check_s1.py").read_text(), original)
        assess(revised, "passed", "review-2")
        formal = self.operation("execute", {**execution_request, "request_id": "formal", "design_review": "review-2"})
        self.assertEqual(formal["results"][case_id]["execution"]["exit_code"], 0)
        self.assertEqual(set(formal["results"]), {case_id})
        self.assertEqual(assess(formal, "passed", "formal-review")["target_operation"], "execute")
        view = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertEqual(view["scenes"]["SC1"]["status"], "passed")
        report = self.run_json("results.py", ["report", self.id], {"request_id": "report", "scene_ids": ["SC1"]}, 0)
        self.assertTrue(report["report"].startswith(".acceptance-local/"))
        snapshot_path = self.root / report["snapshot"]
        snapshot = json.loads(snapshot_path.read_text())
        with self.subTest("scenes pass while the delivery judgment is missing"):
            self.assertEqual(snapshot["requirement_results"]["R1"]["status"], "pending_delivery_review")
            blocked = self.run_json("lifecycle.py", ["finish", self.id], {"request_id": "finish-missing"}, 2)
            self.assertIn("missing requirement judgments", blocked["reason"])
        self.assertIn("独立复审", (self.root / report["report"]).read_text())
        for original_name, exported in snapshot["exported"].items():
            self.assertEqual((snapshot_path.parent / exported).read_bytes(), (self.root / original_name).read_bytes())
        inbox = self.root / ".acceptance-local/native-v1" / self.id / "review-inbox"
        inbox.mkdir()
        verdict = inbox / "delivery-pass.json"
        verdict.write_text('{"status":"passed","reason":"All frozen fixture obligations have current evidence"}')
        delivered = self.operation("delivery-judge", {
            "request_id": "delivery-pass", "requirement_ids": ["R1"], "reviewer_session": "fixture-delivery-reviewer",
            "review_file": verdict.relative_to(self.root).as_posix(),
        })
        self.assertEqual(delivered["judgment"]["status"], "passed")
        with subprocess.Popen(
            [sys.executable, str(SCRIPTS / "results.py"), "--root", str(self.root), "serve", self.id],
            stdout=subprocess.PIPE, text=True,
        ) as server:
            try:
                url = json.loads(server.stdout.readline())["url"]
                with urllib.request.urlopen(url, timeout=5) as response:
                    page = response.read().decode()
                    self.assertIn("独立复审", page)
                    self.assertIn('/dashboard.js', page)
                with urllib.request.urlopen(url + "dashboard.js", timeout=5) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertIn(b"setInterval(refresh, 5000)", response.read())
                with urllib.request.urlopen(url + "snapshot.json", timeout=5) as response:
                    live = json.load(response)
                    scene = live["scenes"]["SC1"]
                    self.assertEqual(scene["status"], "passed")
                    self.assertEqual(scene["current_runs"][case_id], formal["request_id"])
                    self.assertEqual(scene["case_design"][case_id]["design_review"]["status"], "passed")
                    self.assertEqual(live["requirement_results"]["R1"]["delivery_judgment"]["record"], delivered["record"])
                first_evidence = next(iter(live["evidence"]))
                with urllib.request.urlopen(url.rstrip("/") + live["evidence_urls"][first_evidence], timeout=5) as response:
                    self.assertEqual(hashlib.sha256(response.read()).hexdigest(), live["evidence"][first_evidence])
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(url + ".acceptance/secret", timeout=5)
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
                failed_opinion = inbox / "delivery-fail.json"
                failed_opinion.write_text('{"status":"failed","reason":"Independent fixture finding"}')
                failed = self.operation("delivery-judge", {
                    "request_id": "delivery-fail", "requirement_ids": ["R1"], "reviewer_session": "fixture-delivery-reviewer",
                    "review_file": failed_opinion.relative_to(self.root).as_posix(),
                })
                with urllib.request.urlopen(url + "snapshot.json", timeout=5) as response:
                    live = json.load(response)
                self.assertEqual(live["scenes"]["SC1"]["status"], "passed")
                self.assertEqual(live["requirement_results"]["R1"]["status"], "failed")
                self.assertEqual(live["requirement_results"]["R1"]["delivery_judgment"]["record"], failed["record"])
            finally:
                server.terminate()
                server.wait(timeout=5)
        evidence_path = self.root / next(iter(formal["results"][case_id]["execution"]["output"]))
        raw_evidence = evidence_path.read_bytes()
        evidence_path.write_bytes(b"changed")
        changed = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertEqual(changed["scenes"]["SC1"]["cases"][case_id]["status"], "evidence_missing")
        self.assertEqual(changed["scenes"]["SC1"]["status"], "not_fully_validated")
        with self.subTest("changed evidence invalidates a prior requirement judgment"):
            self.assertEqual(changed["requirement_results"]["R1"]["status"], "pending_delivery_review")
        self.assertEqual(json.loads(snapshot_path.read_text()), snapshot)
        self.assertIn(
            "changed",
            self.operation("execute", {**execution_request, "request_id": "formal", "design_review": "review-2"}, 2)["reason"],
        )
        evidence_path.write_bytes(raw_evidence)
        failed_report = self.run_json("results.py", ["report", self.id], {"request_id": "failed-report"}, 0)
        failure_snapshot = json.loads((self.root / failed_report["snapshot"]).read_text())
        finished = self.run_json("lifecycle.py", ["finish", self.id], {"request_id": "finish-failed"}, 0)
        archived = json.loads((self.root / finished["record"]).read_text())["snapshot"]
        for surface in (failure_snapshot, archived):
            with self.subTest("failure overrides passing scenes"):
                self.assertEqual(surface["scenes"]["SC1"]["status"], "passed")
                self.assertEqual(surface["requirement_results"]["R1"]["status"], "failed")
                self.assertEqual(surface["requirement_results"]["R1"]["delivery_judgment"]["record"], failed["record"])

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
        product = next(detail for name, detail in report["files"].items() if Path(name).as_posix() == "app/booking.py")
        self.assertIn(35, product["executed_lines"])
        self.assertTrue(report["meta"]["branch_coverage"])
        blocked = self.operation("coverage", {**request, "request_id": "unobserved", "observe": {"path": "app/booking.py", "line": 44}}, 2)
        self.assertIn("did not complete successfully and observe", blocked["reason"])

    def test_archive_finish_and_cleanup_preserve_selected_original_bytes(self):
        ready = self.runtime()
        source, sha = next(iter(ready["smoke"]["output"].items()))
        original = (self.root / source).read_bytes()
        self.assertTrue(original)
        archive = self.run_json("lifecycle.py", ["archive", self.id], {"request_id": "archive", "files": {source: sha}}, 0)
        clean = {
            "request_id": "cleanup", "archive": archive["archive"], "archive_sha256": archive["sha256"],
            "authorization": "I authorize removing this exact archived log.", "files": [source],
        }
        self.assertIn("active", self.run_json("lifecycle.py", ["cleanup", self.id], clean, 2)["reason"])
        self.assertEqual(self.run_json("lifecycle.py", ["finish", self.id], {"request_id": "finish"}, 2)["status"], "blocked")
        finish = self.run_json("lifecycle.py", ["finish", self.id], {
            "request_id": "finish", "allow_unfinished": "Stop and preserve unfinished SC1.",
        }, 0)
        self.assertEqual(self.record("current")["native"], None)
        self.assertTrue((self.root / finish["record"]).is_file())
        self.assertEqual(self.run_json("lifecycle.py", ["cleanup", self.id], clean, 0)["status"], "cleaned")
        self.assertFalse((self.root / source).exists())
        self.assertEqual(self.run_json("lifecycle.py", ["cleanup", self.id], clean, 0)["status"], "cleaned")
        self.assertEqual(self.record("start", self.id)["status"], "started")
        self.assertEqual(self.record("current")["native"], self.id)
        import zipfile
        with zipfile.ZipFile(self.root / archive["archive"]) as bundle:
            self.assertEqual(bundle.read(f"files/{source}"), original)

    def test_reconcile_committed_evidence_without_rerunning_a_command(self):
        ready = self.runtime()
        result_file = self.root / ready["record"]
        original = result_file.read_bytes()
        result_file.unlink()
        lock = self.root / ".acceptance-local/native-v1/writer.lock"
        lock.mkdir()
        previous = subprocess.Popen([sys.executable, "-c", "pass"])
        previous.wait(timeout=5)
        (lock / "owner.json").write_text(json.dumps({"pid": previous.pid, "attempt": [self.id, "runtime", "runtime"]}))
        payload = {"operation": "runtime", "attempt": "runtime"}
        self.assertEqual(self.run_json("lifecycle.py", ["recover", self.id], payload, 2)["status"], "unreconciled")
        reconcile = {
            **payload, "action": "reconcile",
            "external_effects": {"status": "none_proved", "basis": "Original fixture process exited and artifacts were verified."},
        }
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "attempt": [self.id, "runtime", "runtime"]}))
        self.assertIn("active", self.run_json("lifecycle.py", ["recover", self.id], reconcile, 2)["reason"])
        (lock / "owner.json").write_text(json.dumps({"pid": previous.pid, "attempt": [self.id, "runtime", "runtime"]}))
        result = self.run_json("lifecycle.py", ["recover", self.id], reconcile, 0)
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(result_file.read_bytes(), original)
        self.assertFalse(lock.exists())
        self.assertEqual(self.runtime(), ready)

    def test_backfill_is_candidate_only_and_binds_current_document_bytes(self):
        file = self.root / "requirements.md"
        original = file.read_bytes()
        request = {
            "request_id": "backfill", "authorized_paths": ["requirements.md"],
            "files": {"requirements.md": {"base_sha256": hashlib.sha256(original).hexdigest(), "text": "Candidate requirements.\n"}},
        }
        candidate = self.operation("backfill", request)
        self.assertEqual(candidate["status"], "candidate_ready")
        self.assertEqual(file.read_bytes(), original)
        self.assertIn("+Candidate requirements.", candidate["patches"]["requirements.md"]["diff"])
        self.assertEqual(self.operation("backfill", {**request, "request_id": "wrong", "authorized_paths": []}, 2)["status"], "blocked")

    def test_lifecycle_analysis_rebind_and_knowledge_bind_actual_sources(self):
        analyzed = self.operation("analyze", {"request_id": "analyze", "requirement_ids": ["R1"], "analysis": "Observed conflict and complete-state obligation."})
        self.assertIn("409", analyzed["requirements"]["R1"]["source"]["text"])
        changed = self.operation("source-status", {
            "request_id": "source", "reference": "requirements:R1", "source_status": "read_failed",
            "basis": "Fixture external read could not be verified.",
        })
        self.assertEqual(changed["source_status"], "read_failed")
        self.assertIn("409", changed["definition"]["source"]["text"])
        (self.root / ".gitignore").write_text(".acceptance-local/\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "app/booking.py", "tools/smoke.py"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        reverse = self.operation("reverse", {"request_id": "reverse", "revision": revision})
        self.assertIn("app/booking.py", (self.root / reverse["patch"]).read_text())
        bound = self.operation("rebind", {"request_id": "bind", "revision": revision, "files": ["app/booking.py"]})
        self.assertEqual(bound["revision"], revision)
        workspace = self.operation("rebind", {
            "request_id": "workspace", "revision": revision, "files": ["app/booking.py"], "workspace": True,
        })
        self.assertNotEqual(workspace["cwd"], ".")
        working = self.operation("runtime", {
            "request_id": "workspace-runtime", "files": [f"{workspace['cwd']}/tools/smoke.py"],
            "command": [sys.executable, "tools/smoke.py"], "rebind": "workspace",
        })
        self.assertEqual(Path(working["smoke"]["cwd"]), self.root.resolve() / workspace["cwd"])
        self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(), revision)
        ready = self.runtime()
        guide = self.operation("project", {
            "request_id": "project", "runtime": "runtime", "rebind": "bind", "test_roots": ["tests"],
            "coverage_basis": "Only checking environment without coverage", "guide": "Run tools/smoke.py with project Python.",
        })
        self.assertEqual(guide["runtime"], ready["record"])
        learned = self.operation("knowledge", {
            "request_id": "knowledge", "validated_receipt": ready["record"],
            "files": {"INDEX.md": "[Smoke](smoke.md)", "smoke.md": "Verified with the isolated probe."},
        })
        self.assertTrue((self.root / learned["index"]).is_file())
        self.assertEqual(self.operation("change-plan", {
            "request_id": "unchanged", "previous_sha256": self.freeze["sha256"], "intent": "design_only",
        })["affected"], [])
        inbox = self.root / ".acceptance-local/native-v1" / self.id / "review-inbox"
        inbox.mkdir()
        answer = inbox / "scenario.json"
        answer.write_bytes(b'{"scenes":{"SC1":{"status":"needs_revision","reason":"fixture boundary review"}}}\n')
        assessment = self.operation("assess-scenarios", {
            "request_id": "scenario", "scene": "SC1", "reviewer_session": "fixture-reviewer",
            "review_file": answer.relative_to(self.root).as_posix(),
        })
        self.assertEqual(assessment["judgment"]["status"], "needs_revision")

    def test_aggregate_delivery_and_manual_evidence_keep_observation_limits(self):
        path = self.root / "observed.txt"
        raw = b"Actual local fixture observation.\n"
        path.write_bytes(raw)
        observed = self.operation("manual", {
            "request_id": "manual", "scene": "SC1", "product_version": "test fixture",
            "cases": {"TC-observation": [{
                "action": "Inspect result", "expected": "Observes state", "observed": "No conclusion",
                "performed_by": "fixture observer", "performed_at": "2026-11-08T00:00:00Z",
                "receipt_reference": "fixture observation", "outcome": "uncertain",
                "evidence": {"observed.txt": hashlib.sha256(raw).hexdigest()},
            }]},
        })
        self.assertIsNone(observed["results"]["TC-observation"]["execution"])
        recorded = hashlib.sha256((self.root / observed["record"]).read_bytes()).hexdigest()
        review = {
            "request_id": "review-manual", "scene": "SC1", "target_operation": "manual", "target": "manual",
            "target_sha256": recorded, "reviewer_session": "fixture-reviewer",
            "raw_review": json.dumps({"cases": {"TC-observation": {"status": "passed", "reason": "Fixture"}}}),
        }
        self.assertIn("uncertain", self.operation("review", review, 2)["reason"])
        concern = self.operation("review", {
            **review, "request_id": "review-manual-concern",
            "raw_review": json.dumps({"cases": {"TC-observation": {"status": "insufficient_evidence", "reason": "Observation is uncertain"}}}),
        })
        review_bytes = (self.root / concern["record"]).read_bytes()
        decision = self.operation("risk", {
            "request_id": "risk", "scene": "SC1", "finding": "review-manual-concern",
            "finding_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "user_quote": "I accept the limited observation for this fixture only.",
            "source_reference": "fixture:user", "remaining_impact": "No passing result.",
        })
        self.assertEqual(decision["status"], "risk_recorded")
        self.assertEqual((self.root / concern["record"]).read_bytes(), review_bytes)
        inbox = self.root / ".acceptance-local/native-v1" / self.id / "review-inbox"
        inbox.mkdir()
        (inbox / "delivery.json").write_text('{"status":"passed","reason":"Premature claim"}')
        blocked = self.operation("delivery-judge", {
            "request_id": "delivery", "requirement_ids": ["R1"], "reviewer_session": "fixture-reviewer",
            "review_file": str((inbox / "delivery.json").relative_to(self.root)),
        }, 2)
        self.assertIn("lacks required", blocked["reason"])

    def test_manual_item_retains_distinct_runs_and_requires_current_product(self):
        self.operation("analyze", {
            "request_id": "scope", "requirement_ids": ["R1"], "analysis": "核对 SC1 范围。",
            "scope_review": {"baseline_sha256": self.freeze["sha256"], "scenario_ids": ["SC1"]},
        })
        (self.root / "manual-check.md").write_text("# @case-id: TC-manual\n观察返回状态 409。\n")
        source = self.record("source", "manual-check.md", "2", "2")
        match = {
            "request_id": "manual-match", "scene": "SC1", "search_scope": "人工步骤",
            "inspected": {"manual-check.md": source["sha256"]},
            "obligations": [{"id": "reject", "quote": "第二次响应 JSON status 必须为 409"}],
            "cases": [{"selector": "观察返回状态", "source": source, "kind": "manual",
                       "status": "active", "covers": {"reject": "观察返回状态 409。"}}],
        }
        self.operation("match", {
            **match, "obligations": [*match["obligations"], {"id": "state", "quote": "所有字段和值均不变"}],
        })
        product = self.root / "candidate.txt"
        product.write_text("candidate one")
        for run in ("first", "second"):
            evidence = self.root / f"{run}.txt"
            evidence.write_text(f"Observed 409 in the {run} run.")
            result = self.operation("manual", {
                "request_id": run, "scene": "SC1", "match": "manual-match",
                "product_version": "candidate one", "product_files": ["candidate.txt"],
                "cases": {"TC-manual": [{
                    "action": "Inspect result", "expected": "409", "observed": "409",
                    "performed_by": "fixture observer", "performed_at": "2026-11-08T00:00:00Z",
                    "receipt_reference": run, "outcome": "observed_success",
                    "evidence": {evidence.name: hashlib.sha256(evidence.read_bytes()).hexdigest()},
                }]},
            })
            review = {
                "request_id": f"review-{run}", "scene": "SC1", "target_operation": "manual", "target": run,
                "target_sha256": hashlib.sha256((self.root / result["record"]).read_bytes()).hexdigest(),
                "reviewer_session": "fixture-reviewer",
                "raw_review": json.dumps({"cases": {"TC-manual": {
                    "status": "passed", "reason": "409 was observed", "covers": ["reject"],
                }}}),
            }
            if run == "first":
                with self.subTest("reject coverage assigned to another check"):
                    outside = json.loads(review["raw_review"])
                    outside["cases"]["TC-manual"]["covers"].append("state")
                    rejected = self.operation("review", {
                        **review, "request_id": "review-outside", "raw_review": json.dumps(outside),
                    }, 2)
                    self.assertIn("assigned", rejected["reason"])
                self.operation("match", {**match, "request_id": "manual-match-revised"})
            self.operation("review", review)
        view = self.run_json("results.py", ["view", self.id], None, 0)
        scene = view["scenes"]["SC1"]
        self.assertEqual(scene["case_design"]["TC-manual"]["kind"], "manual")
        self.assertEqual([row["run"] for row in scene["history"]], ["first", "second"])
        product.write_text("candidate two")
        view = self.run_json("results.py", ["view", self.id], None, 0)
        self.assertEqual([row["cases"]["TC-manual"]["status"] for row in view["scenes"]["SC1"]["history"]],
                         ["stale", "stale"])

    @unittest.skipUnless(os.environ.get("ACCEPTANCE_C8"), "set ACCEPTANCE_C8 to the tested c8 executable")
    def test_project_coverage_adapter_uses_native_node_child_data(self):
        fixture = Path(__file__).parent / "fixtures/node-coverage"
        shutil.copytree(fixture, self.root, dirs_exist_ok=True)
        node = shutil.which("node")
        c8 = Path(os.environ["ACCEPTANCE_C8"]).absolute()
        library = c8.parent.parent / "istanbul-lib-coverage"
        command = [node, "tools/probe.cjs"]
        self.operation("runtime", {
            "request_id": "node-runtime", "files": ["app/product.cjs", "tools/probe.cjs"], "command": command,
        })
        request = {
            "request_id": "node-coverage", "runtime": "node-runtime", "command": command,
            "observe": {"path": "app/product.cjs", "line": 3},
            "adapter": {
                "files": ["app/product.cjs", "tools/probe.cjs", "tools/verify.cjs"],
                "version": [str(c8), "--version"],
                "prefix": [str(c8), "--all", "--src", "app", "--reporter", "json", "--reports-dir", "{output}", "--"],
                "report": "coverage-final.json",
                "verify": [node, "tools/verify.cjs", str(library), "{report}", "{product}", "{line}"],
            },
        }
        ready = self.operation("coverage", request)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["tool_version"], "10.1.3")
        data = json.loads((self.root / ready["coverage_report"]).read_text())
        self.assertIn(str(self.root.resolve() / "app/product.cjs"), data)
        wrong = self.operation("coverage", {
            **request, "request_id": "node-wrong", "observe": {"path": "app/product.cjs", "line": 5},
        }, 2)
        self.assertEqual(wrong["status"], "blocked")
