"""Launch the booking CLI in a fresh directory for environment probes."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


product = Path(__file__).resolve().parents[1] / "app" / "booking.py"
with tempfile.TemporaryDirectory(prefix="booking-smoke-") as folder:
    db = str(Path(folder) / "probe.sqlite")
    run = subprocess.run([sys.executable, str(product), "--db", db, "list"], capture_output=True, check=True)
    assert json.loads(run.stdout) == {"status": 200, "bookings": []}
    print("booking subprocess ready")
