import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from booking_fixture import prepare_booking


BASELINE = Path(__file__).parent / "baseline" / "behaviors.json"


class BaselineTests(unittest.TestCase):
    def test_all_entries_have_unique_user_tasks_and_observable_stops(self):
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        cases = data["cases"]
        self.assertEqual(len(cases), 31)
        self.assertEqual(len({case["entry"] for case in cases}), 31)
        for case in cases:
            with self.subTest(entry=case["entry"]):
                self.assertIn(case["stage"], range(1, 7))
                for field in ("task", "outcome", "stop"):
                    self.assertTrue(case[field].strip())

    @unittest.skipUnless(os.environ.get("ACCEPTANCE_REFERENCE"), "set ACCEPTANCE_REFERENCE to the pinned source")
    def test_inventory_matches_reference_registry(self):
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        root = Path(os.environ["ACCEPTANCE_REFERENCE"])
        package = root / "skills/en/app-delivery-acceptance/playbooks/acceptance"
        entries = {file.parent.name for file in (package / "workflows").glob("*/registry.mjs")}
        self.assertEqual(entries, {case["entry"] for case in data["cases"]})
        self.assertEqual(json.loads((package / "package.json").read_text())["version"], data["reference"]["version"])

    def test_historical_pass_and_failure_are_real_and_bound_to_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            for case, expected_exit in (("C3", 0), ("C4", 1)):
                with self.subTest(case=case):
                    project = Path(directory) / case
                    prepare_booking(project, case)
                    history = project / "history"
                    receipt = json.loads((history / "receipt.json").read_text())
                    self.assertEqual(receipt["exit_code"], expected_exit)
                    for file, expected in receipt["inputs_sha256"].items():
                        self.assertEqual(hashlib.sha256((history / "inputs" / file).read_bytes()).hexdigest(), expected)
                    for name in ("stdout", "stderr"):
                        self.assertEqual(
                            hashlib.sha256((history / f"{name}.txt").read_bytes()).hexdigest(),
                            receipt[f"{name}_sha256"],
                        )

    def test_weak_case_misses_a_consistently_wrong_first_title(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "C2"
            prepare_booking(project, "C2")
            product = project / "app" / "booking.py"
            source = product.read_text(encoding="utf-8")
            original = "args.room, args.start, args.end, args.title, datetime"
            self.assertEqual(source.count(original), 1)
            product.write_text(source.replace(original, 'args.room, args.start, args.end, "错误标题", datetime'), encoding="utf-8")
            result = subprocess.run([sys.executable, "tests/check_s1.py"], cwd=project, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = json.loads(result.stdout.splitlines()[0])
            self.assertEqual(json.loads(first["stdout"])["booking"]["title"], "错误标题")


if __name__ == "__main__":
    unittest.main()
