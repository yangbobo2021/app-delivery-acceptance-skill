import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "booking"
INPUTS = (
    "app/booking.py",
    "requirements.md",
    "scenarios.md",
    "test-design.md",
    "tests/check_s1.py",
)


def prepare_booking(project, case):
    tasks = json.loads((FIXTURES / "tasks.json").read_text(encoding="utf-8"))
    task = tasks[case]
    shutil.copytree(FIXTURES / "project", project)
    if case == "C4":
        for relative in ("scenarios.md", "tests/check_s1.py"):
            shutil.copyfile(FIXTURES / "conflict" / relative, project / relative)
    if case in {"C3", "C4"}:
        history = project / "history"
        inputs = history / "inputs"
        hashes = {}
        for relative in INPUTS:
            snapshot = inputs / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / relative, snapshot)
            hashes[relative] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        command = [sys.executable, "tests/check_s1.py"]
        started = datetime.now(timezone.utc).isoformat()
        execution = subprocess.run(command, cwd=project, capture_output=True, timeout=15)
        (history / "stdout.txt").write_bytes(execution.stdout)
        (history / "stderr.txt").write_bytes(execution.stderr)
        receipt = {
            "command": command,
            "cwd": str(project.resolve()),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": execution.returncode,
            "inputs_sha256": hashes,
            "stdout_sha256": hashlib.sha256(execution.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(execution.stderr).hexdigest(),
        }
        (history / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return task
