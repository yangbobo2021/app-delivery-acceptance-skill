"""Coverage.py subprocess adapter. The tool owns measurement and statistics."""

import os
from pathlib import Path

from evidence import capture, project_ref, verify_bindings
from records import Blocked, atomic_write, digest, read_json


def measure_python(project, folder, executable, script, arguments, roots, limit, environment=None, *, cwd="."):
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
    version = capture(project, tool + ["--version"], folder, "version", limit, env, cwd=cwd)
    if version["exit_code"] != 0:
        return None, {"version": version}
    run = capture(project, tool + ["run", str(project.path(cwd, script)), *arguments], folder, "run", limit, env, cwd=cwd)
    merge = capture(project, tool + ["combine", "--keep"], folder, "combine", limit, env, cwd=cwd)
    if merge["exit_code"] != 0:
        return None, {"version": version, "run": run, "combine": merge}
    export = capture(project, tool + ["json", "-o", str(report)], folder, "export", limit, env, cwd=cwd)
    actions = {"version": version, "run": run, "combine": merge, "export": export}
    return read_json(report) if export["exit_code"] == 0 else None, actions


def recompute(project, identifier, request, definitions, path):
    reports = request["reports"]
    if not isinstance(reports, list) or len(reports) == 0 or len(reports) != len({r["path"] for r in reports}):
        raise Blocked("provide distinct original coverage reports")
    fingerprint = None
    all_bindings, selected = {}, []
    for index, entry in enumerate(reports):
        receipt_path = project.path(entry["receipt"])
        receipt = read_json(receipt_path)
        if (
            receipt["acceptance_id"] != identifier
            or receipt["definitions_sha256"] != request["definitions_sha256"]
            or receipt["operation"] != "execute"
        ):
            raise Blocked("coverage recomputation needs selected executions of the same definition")
        case = next((
            item for item in receipt["results"].values()
            if item["coverage"].get("report") == entry["path"] and item["coverage"]["status"] == "measured"
        ), None)
        if case is None or case["coverage"].get("statistics") is not None:
            raise Blocked("report must belong to an actual measured execution")
        current = case["coverage"]
        cwd = project_ref(project, Path(case["execution"]["cwd"])) if case["execution"].get("cwd") else "."
        verify_bindings(project, receipt["bindings"])
        verify_bindings(project, receipt["artifacts"])
        file = project.path(entry["path"])
        raw = read_json(file)
        config, data = file.with_name("coverage.ini"), file.with_name(".coverage")
        proof = (
            raw["meta"]["version"], config.read_bytes(), receipt["bindings"],
            current["commands"]["version"]["argv"][0], cwd,
        )
        if fingerprint is not None and (fingerprint[0], fingerprint[1], fingerprint[3:]) != (proof[0], proof[1], proof[3:]):
            raise Blocked("coverage tool, configuration, or interpreter differs")
        fingerprint = proof
        all_bindings.update(receipt["bindings"])
        for artifact in (file, config, data):
            location = project_ref(project, artifact)
            if digest(artifact.read_bytes()) != receipt["artifacts"][location]:
                raise Blocked("original coverage artifact changed")
        atomic_write(path / f".coverage.input-{index}", data.read_bytes())
        selected.append(entry["path"])
    version, config, _, interpreter, cwd = fingerprint
    rcfile = path / "coverage.ini"
    atomic_write(rcfile, config)
    env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE_")}
    env.update(COVERAGE_RCFILE=str(rcfile), COVERAGE_FILE=str(path / ".coverage"))
    tool = [interpreter, "-m", "coverage"]
    check = capture(project, tool + ["--version"], path, "version", 30, env, cwd=cwd)
    if check["exit_code"] or f"version {version}" not in (path / "version.stdout").read_text():
        raise Blocked("the original coverage.py version is unavailable")
    merge = capture(project, tool + ["combine", "--keep"], path, "combine", 60, env, cwd=cwd)
    if merge["exit_code"] != 0:
        raise Blocked("coverage.py could not combine these data files")
    export = capture(project, tool + ["json", "-o", str(path / "coverage.json")], path, "export", 60, env, cwd=cwd)
    if export["exit_code"] != 0:
        raise Blocked("coverage.py did not export a native report")
    output = path / "coverage.json"
    return {
        "status": "recomputed", "sources": selected, "bindings": all_bindings,
        "commands": {"version": check, "combine": merge, "export": export},
        "coverage_report": project_ref(project, output),
        "artifacts": {project_ref(project, output): digest(output.read_bytes())},
    }
