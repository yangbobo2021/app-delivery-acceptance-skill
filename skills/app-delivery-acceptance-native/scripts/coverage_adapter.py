"""Run a project-owned mature coverage tool and a project-owned native-report verifier."""

import json
import os
from pathlib import Path

from evidence import argv, capture, project_ref
from records import Blocked, digest


def project_measure(project, folder, command, adapter, observable, limit, environment=None, *, cwd="."):
    name = Path(adapter["report"])
    if name.anchor or ".." in name.parts or not name.name:
        raise Blocked("adapter report must be within the new evidence directory")
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / name
    product = project.path(observable["path"])
    values = {
        "{output}": str(folder), "{report}": str(report),
        "{product}": str(product), "{line}": str(observable["line"]),
    }
    version_argv, prefix, verify = map(argv, (adapter["version"], adapter["prefix"], adapter["verify"]))
    if "{output}" not in prefix or "{report}" not in verify or "{product}" not in verify:
        raise Blocked("adapter must run into a fresh output directory and verify the native report and product")
    env = {**os.environ, **(environment or {})}
    version = capture(project, version_argv, folder, "version", limit, env, cwd=cwd)
    if version["exit_code"] != 0:
        return None, {"version": version}
    run = capture(project, [values.get(arg, arg) for arg in prefix] + argv(command), folder, "run", limit, env, cwd=cwd)
    commands = {"version": version, "run": run}
    if not report.is_file():
        return None, commands
    report = project.path(project_ref(project, report))
    raw = report.read_bytes()
    if not raw:
        return None, commands
    verified = capture(project, [values.get(arg, arg) for arg in verify], folder, "verify", limit, env, cwd=cwd)
    commands["verify"] = verified
    if verified["exit_code"] != 0 or report.read_bytes() != raw:
        return None, commands
    try:
        proof = json.loads((folder / "verify.stdout").read_bytes())
    except ValueError:
        return None, commands
    if proof.get("observed") is not True or not isinstance(proof.get("metrics"), dict):
        return None, commands
    return {
        "tool": (folder / "version.stdout").read_text(encoding="utf-8").strip(),
        "metrics": proof["metrics"], "path": project_ref(project, report), "sha256": digest(raw),
    }, commands
