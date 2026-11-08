"""Shared scope, immutable attempt records and command evidence."""

import json
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


def verify_checkout(project, record):
    if record.get("revision") and "cwd" in record:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=project.path(record.get("cwd", ".")),
            capture_output=True, text=True, timeout=10,
        )
        if revision.returncode or revision.stdout.strip() != record["revision"]:
            raise Blocked("bound product revision has changed")


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


def capture(project, command, folder, name, limit=60, environment=None, *, cwd="."):
    command = argv(command)
    if type(limit) is not int or not 1 <= limit <= 300:
        raise Blocked("timeout must be 1 to 300 seconds")
    folder.mkdir(parents=True, exist_ok=True)
    stdout, stderr = folder / f"{name}.stdout", folder / f"{name}.stderr"
    recorded_env = {
        key: environment[key] for key in ("ACCEPTANCE_EVIDENCE_DIR", "COVERAGE_FILE", "COVERAGE_RCFILE")
        if environment and key in environment
    }
    working = project.path(cwd)
    result = {
        "argv": command, "cwd": str(working), "environment": recorded_env,
        "started_at": now(), "exit_code": None,
    }
    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            process = subprocess.Popen(
                command, cwd=working, env=environment, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, start_new_session=os.name == "posix",
            )
            try:
                atomic_write(folder / f"{name}.process.json", encoded({
                    "pid": process.pid, "group": process.pid if os.name == "posix" else None,
                }))
            except OSError:
                terminate(process)
                raise
            try:
                result["exit_code"] = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                terminate(process)
                result["error"] = "timed out"
        except OSError as error:
            result["error"] = str(error)
    result["finished_at"] = now()
    result["output"] = {project_ref(project, path): digest(path.read_bytes()) for path in (stdout, stderr)}
    return result


def terminate(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif os.name == "nt" and process.poll() is None:
            try:
                killed = subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10,
                )
                needs_kill = killed.returncode != 0
            except (OSError, subprocess.SubprocessError):
                needs_kill = True
            if needs_kill and process.poll() is None:
                process.kill()
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def publish_index(project, identifier, record, raw):
    section = record["operation"]
    if section in {"match", "debug"}:
        root = "cases"
    elif section in {"execute", "manual", "history", "review"}:
        root = "records"
    else:
        return
    if record["status"] == "blocked":
        return
    scene = token(record["scene"], "scene id")
    key = token(record["request_id"], "request id")
    target = project.shared(identifier, root, scene, f"{section}-{key}.json")
    case_ids = record.get("cases", record.get("results", record.get("judgments", {})))
    index = {
        "schema": "acceptance-native-attempt-index-v1", "acceptance_id": identifier,
        "scene": scene, "operation": section, "request_id": key, "status": record["status"],
        "definitions_sha256": record["definitions_sha256"], "result_sha256": digest(raw),
        "record": record["record"], "case_ids": sorted(case_ids),
    }
    data = encoded(index)
    if target.exists():
        if target.read_bytes() != data:
            raise Blocked("published attempt index differs from its committed result")
    else:
        atomic_write(target, data)


def recorded(project, identifier, section, request, action):
    key = token(request["request_id"], "request id")
    path = local(project, identifier, section, key)
    record_file = path / "result.json"
    request_hash = digest(encoded(request))
    with project.writer([identifier, section, key]):
        definitions = scoped(project, identifier, request)
        if record_file.is_file():
            raw = record_file.read_bytes()
            prior = json.loads(raw)
            if prior["request_sha256"] != request_hash:
                raise Blocked("request id already binds different inputs")
            if prior.get("bindings"):
                verify_bindings(project, prior["bindings"])
            verify_checkout(project, prior)
            if prior.get("artifacts"):
                verify_bindings(project, prior["artifacts"])
            publish_index(project, identifier, prior, raw)
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
            "record": project_ref(project, record_file), "recorded_at": now(),
        }
        if "scene" in request:
            record["scene"] = request["scene"]
        data = encoded(record)
        atomic_write(path / "pending-outcome.json", data)
        atomic_write(record_file, data)
        publish_index(project, identifier, record, data)
        return record


def upstream(project, identifier, request, field, section, expected, *, verify_live=True):
    key = token(request[field], f"{section} request id")
    result = read_json(local(project, identifier, section, key, "result.json"))
    if result["operation"] != section or result["status"] != expected or result["definitions_sha256"] != request["definitions_sha256"]:
        raise Blocked(f"missing or stale {section} result")
    if verify_live and result["bindings"]:
        verify_bindings(project, result["bindings"])
    if verify_live:
        verify_checkout(project, result)
    if result.get("artifacts"):
        verify_bindings(project, result["artifacts"])
    return result
