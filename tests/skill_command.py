"""Invoke the installed Skill or the pinned Python baseline through the same contracts."""

import os
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/app-delivery-acceptance-native/scripts"


def skill_command(script, root, *args, scripts=SCRIPTS):
    name = Path(script).stem
    baseline = os.environ.get("ACCEPTANCE_PYTHON_BASELINE")
    runtime, entry = (sys.executable, Path(baseline) / f"{name}.py") if baseline else ("node", scripts / f"{name}.mts")
    return [runtime, str(entry), "--root", str(root), *args]
