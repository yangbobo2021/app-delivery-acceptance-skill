"""Shared scope, immutable attempt records and command evidence."""

import os
import signal
import subprocess
from datetime import datetime, timezone

from records import Blocked, atomic_write, digest, encoded, read_json, token


def now():
    return datetime.now(timezone.utc).isoformat()


def scoped(project, identifier, request):
    if project.current() != identifier:
        raise Blocked("select the intended native acceptance first")
    project.manifest(identifier)
    versions = project.versions(identifier)
    if not versions or versions[-1][1] != request["definitions_sha256"]:
        raise Blocked("operation requires the current frozen definitions")
    current = versions[-1]
    if "scene" in request:
        scene = token(request["scene"], "scene id")
        if scene not in current[2]["scenarios"]:
            raise Blocked(f"unknown frozen scene: {scene}")
        if scene in project.receipt(current, "current")["scenes_needing_revalidation"]:
            raise Blocked(f"scene {scene} needs revalidation against changed requirements")
    return current[2]


def file_bindings(project, names):
    if not isinstance(names, list) or not names or len(set(names)) != len(names):
        raise Blocked("declare a nonempty list of distinct project files")
    return {name: digest(project.path(name).read_bytes()) for name in names}


def verify_bindings(project, bindings):
    if file_bindings(project, list(bindings)) != bindings:
        raise Blocked("bound project files have changed")


def local(project, identifier, *parts):
    return project.path(".acceptance-local", "native-v1", identifier, *parts)


def project_ref(project, path):
    return path.relative_to(project.root).as_posix()


def argv(value):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or "\x00" in item for item in value):
        raise Blocked("supply a nonempty argv list with string arguments")
    if not value[0]:
        raise Blocked("command executable is empty")
    return value


def capture(project, command, folder, name, limit=60, environment=None):
    command = argv(command)
    if type(limit) is not int or not 1 <= limit <= 300:
        raise Blocked("timeout must be 1 to 300 seconds")
    folder.mkdir(parents=True, exist_ok=True)
    stdout, stderr = folder / f"{name}.stdout", folder / f"{name}.stderr"
    result = {"argv": command, "started_at": now(), "exit_code": None}
    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            process = subprocess.Popen(
                command, cwd=project.root, env=environment, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, start_new_session=os.name == "posix",
            )
            try:
                result["exit_code"] = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                result["error"] = "timed out"
        except OSError as error:
            result["error"] = str(error)
    result["finished_at"] = now()
    result["output"] = {project_ref(project, path): digest(path.read_bytes()) for path in (stdout, stderr)}
    return result


def recorded(project, identifier, section, request, action):
    key = token(request["request_id"], "request id")
    path = local(project, identifier, section, key)
    record_file = path / "result.json"
    request_hash = digest(encoded(request))
    with project.writer():
        definitions = scoped(project, identifier, request)
        if record_file.is_file():
            prior = read_json(record_file)
            if prior["request_sha256"] != request_hash:
                raise Blocked("request id already binds different inputs")
            if prior.get("bindings"):
                verify_bindings(project, prior["bindings"])
            if prior.get("artifacts"):
                verify_bindings(project, prior["artifacts"])
            return prior
        if path.exists():
            raise Blocked("an unfinished attempt exists; inspect the original evidence before retrying")
        path.mkdir(parents=True)
        atomic_write(path / "request.json", encoded(request))
        try:
            outcome = action(project, identifier, request, definitions, path)
        except (Blocked, KeyError, TypeError, ValueError, OSError) as error:
            outcome = {"status": "blocked", "reason": str(error)}
        record = {
            **outcome, "operation": section, "acceptance_id": identifier, "request_id": key,
            "request_sha256": request_hash, "definitions_sha256": request["definitions_sha256"],
            "record": project_ref(project, record_file),
        }
        if "scene" in request:
            record["scene"] = request["scene"]
        atomic_write(record_file, encoded(record))
        return record


def upstream(project, identifier, request, field, section, expected):
    key = token(request[field], f"{section} request id")
    result = read_json(local(project, identifier, section, key, "result.json"))
    if result["operation"] != section or result["status"] != expected or result["definitions_sha256"] != request["definitions_sha256"]:
        raise Blocked(f"missing or stale {section} result")
    verify_bindings(project, result["bindings"])
    if result.get("artifacts"):
        verify_bindings(project, result["artifacts"])
    return result
