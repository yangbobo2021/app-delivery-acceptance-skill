"""Coverage.py subprocess adapter. The tool owns measurement and statistics."""

import os

from evidence import capture
from records import Blocked, atomic_write, read_json


def measure_python(project, folder, executable, script, arguments, roots, limit, environment=None):
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / "coverage.json"
    rcfile = folder / "coverage.ini"
    data = folder / ".coverage"
    source = "\n    ".join(str(project.path(name)) for name in roots)
    if any("\n" in str(project.path(name)) or not project.path(name).is_dir() for name in roots):
        raise Blocked("coverage source roots must be ordinary project directories")
    atomic_write(rcfile, f"[run]\nbranch = true\npatch = subprocess\nparallel = true\nsource =\n    {source}\n".encode())
    env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE_")}
    env.update(COVERAGE_RCFILE=str(rcfile), COVERAGE_FILE=str(data))
    env.update(environment or {})
    tool = [executable, "-m", "coverage"]
    version = capture(project, tool + ["--version"], folder, "version", limit, env)
    if version["exit_code"] != 0:
        return None, {"version": version}
    run = capture(project, tool + ["run", str(project.path(script)), *arguments], folder, "run", limit, env)
    merge = capture(project, tool + ["combine", "--keep"], folder, "combine", limit, env)
    if merge["exit_code"] != 0:
        return None, {"version": version, "run": run, "combine": merge}
    export = capture(project, tool + ["json", "-o", str(report)], folder, "export", limit, env)
    actions = {"version": version, "run": run, "combine": merge, "export": export}
    return read_json(report) if export["exit_code"] == 0 else None, actions
