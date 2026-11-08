"""User-requested acceptance lifecycle and read-only analysis operations."""

import argparse
import difflib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from evidence import capture, file_bindings, local, now, project_ref, publish_index, scoped, upstream, verify_bindings
from records import Blocked, Project, acceptance_id, atomic_write, configure_io, digest, encoded, read_json, require_text, token
from results import attempts, view


def analyze(project, identifier, request, definitions, path):
    text = require_text(request["analysis"], "analysis with identified gaps")
    selected = request["requirement_ids"]
    if not selected or not set(selected) <= definitions["requirements"].keys():
        raise Blocked("analysis must reference actual frozen requirements")
    return {"status": "analyzed", "analysis": text, "requirements": {item: definitions["requirements"][item] for item in selected}}


def assess_scenarios(project, identifier, request, definitions, path):
    reviewer = require_text(request["reviewer_session"], "independent Reviewer session")
    if reviewer == request.get("author_session"):
        raise Blocked("scenario review requires a separate Reviewer")
    raw = reviewer_output(project, identifier, request)
    result = json.loads(raw)
    if set(result["scenes"]) != {request["scene"]}:
        raise Blocked("scenario assessment must address the selected frozen scene")
    verdict = result["scenes"][request["scene"]]
    if verdict["status"] not in {"passed", "needs_revision", "insufficient_evidence"}:
        raise Blocked("unrecognized scene assessment outcome")
    require_text(verdict["reason"], "independent reason")
    atomic_write(path / "reviewer.json", raw)
    return {
        "status": "assessed", "judgment": verdict, "reviewer_session": reviewer,
        "artifacts": {project_ref(project, path / "reviewer.json"): digest(raw)},
    }


def reverse(project, identifier, request, definitions, path):
    revision = require_text(request["revision"], "local Git revision")
    if revision.startswith("-"):
        raise Blocked("invalid Git revision")
    resolved = capture(project, ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], path, "revision", 20)
    if resolved["exit_code"] != 0:
        raise Blocked("Git revision is unavailable")
    commit = (path / "revision.stdout").read_text().strip()
    proof = capture(project, ["git", "show", "--format=fuller", "--no-ext-diff", "--no-textconv", commit, "--"], path, "changes", 30)
    if proof["exit_code"] != 0:
        raise Blocked("could not materialize the selected commit")
    return {
        "status": "changes_observed", "revision": commit, "command": proof,
        "artifacts": proof["output"], "patch": project_ref(project, path / "changes.stdout"),
    }


def change_plan(project, identifier, request, definitions, path):
    previous = next((record for _, sha, record in project.versions(identifier) if sha == request["previous_sha256"]), None)
    if previous is None:
        raise Blocked("previous definition is unavailable")
    old, new = previous["scenarios"], definitions["scenarios"]
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    invalidated = project.receipt(project.versions(identifier)[-1], "current")["scenes_needing_revalidation"]
    affected = sorted(set(changed + invalidated))
    intent = request["intent"]
    if intent not in {"follow_history", "complete_change_acceptance", "design_only"}:
        raise Blocked("unrecognized change intent")
    historical = {run["scene"] for run, _ in attempts(project, identifier, "execute") if run.get("status") == "executed"}
    selected = [scene for scene in affected if scene in new and (intent != "follow_history" or scene in historical)]
    return {"status": "planned", "affected": affected, "selected": selected, "intent": intent}


def rebind(project, identifier, request, definitions, path):
    requested = require_text(request["revision"], "selected checkout commit")
    if not re.fullmatch(r"[0-9a-f]{40,64}", requested):
        raise Blocked("rebind needs a full Git commit")
    cwd = "."
    created = None
    if request.get("workspace") is True:
        workspace = local(project, identifier, "workspace", token(request["request_id"], "workspace id"))
        if workspace.exists():
            raise Blocked("candidate workspace already exists; inspect its original attempt")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        created = capture(project, ["git", "worktree", "add", "--detach", str(workspace), requested], path, "worktree", 60)
        if created["exit_code"] != 0:
            raise Blocked("Git did not create the selected candidate checkout")
        cwd = project_ref(project, workspace)
    proof = capture(project, ["git", "rev-parse", "--verify", "HEAD"], path, "revision", 20, cwd=cwd)
    if proof["exit_code"] != 0 or (path / "revision.stdout").read_text().strip() != requested:
        raise Blocked("project checkout is not at the selected revision")
    files = [project_ref(project, project.path(cwd, name)) for name in request["files"]]
    bindings = file_bindings(project, files)
    artifacts = {**proof["output"], **(created["output"] if created else {})}
    return {"status": "bound", "revision": requested, "bindings": bindings, "artifacts": artifacts, "cwd": cwd}


def project_prepare(project, identifier, request, definitions, path):
    runtime = upstream(project, identifier, request, "runtime", "runtime", "ready")
    coverage = None
    if request.get("coverage"):
        coverage = upstream(project, identifier, request, "coverage", "coverage", "ready")
    else:
        require_text(request.get("coverage_basis"), "project coverage disposition")
    revision = None
    if request.get("rebind"):
        revision = upstream(project, identifier, request, "rebind", "rebind", "bound")["revision"]
    return {
        "status": "ready", "runtime": runtime["record"], "coverage": coverage["record"] if coverage else None,
        "revision": revision, "test_roots": request["test_roots"],
        "guide": require_text(request["guide"], "verified project operating guide"),
        "bindings": {**runtime["bindings"], **(coverage["bindings"] if coverage else {})},
    }


def source_status(project, identifier, request, definitions, path):
    collection, separator, key = request["reference"].partition(":")
    if not separator or collection not in {"requirements", "scenarios"} or key not in definitions[collection]:
        raise Blocked("source status must refer to a frozen definition")
    status = request["source_status"]
    if status not in {"read_failed", "deletion_confirmed", "available"}:
        raise Blocked("source observation must distinguish failure from confirmed deletion")
    basis = require_text(request["basis"], "actual source observation")
    return {"status": "observed", "source_status": status, "basis": basis, "definition": definitions[collection][key]}


def reviewer_output(project, identifier, request):
    key = token(request["request_id"], "review request id")
    source = project.path(request["review_file"])
    if source != local(project, identifier, "review-inbox", f"{key}.json") or source.stat().st_size > 256 * 1024:
        raise Blocked("use the assigned independent Reviewer file")
    return source.read_bytes()


def delivery_judge(project, identifier, request, definitions, path):
    reviewer = require_text(request["reviewer_session"], "independent delivery Reviewer session")
    if reviewer == request.get("author_session"):
        raise Blocked("delivery review requires a separate Reviewer")
    selected = request["requirement_ids"]
    if not selected or not set(selected) <= definitions["requirements"].keys():
        raise Blocked("select actual frozen requirements")
    snapshot = view(project, identifier)
    selected_scenes = {
        key: scene for key, scene in snapshot["scenes"].items()
        if set(scene["definition"]["requirement_sha256"]) & set(selected)
    }
    covered = {req for scene in selected_scenes.values() for req in scene["definition"]["requirement_sha256"]}
    raw = reviewer_output(project, identifier, request)
    judgment = json.loads(raw)
    require_text(judgment["reason"], "independent delivery judgment")
    if judgment["status"] not in {"passed", "blocked", "failed"}:
        raise Blocked("unknown delivery judgment")
    complete = bool(selected_scenes) and set(selected) <= covered and all(s["status"] == "passed" for s in selected_scenes.values())
    if judgment["status"] == "passed" and not complete:
        raise Blocked("selected delivery scope lacks required passing scene evidence")
    atomic_write(path / "reviewer.json", raw)
    return {
        "status": "judged", "judgment": judgment, "requirement_ids": selected, "reviewer_session": reviewer,
        "scene_status": {name: scene["status"] for name, scene in selected_scenes.items()},
        "artifacts": {project_ref(project, path / "reviewer.json"): digest(raw)},
    }


def risk(project, identifier, request, definitions, path):
    file = local(project, identifier, "review", token(request["finding"], "review id"), "result.json")
    raw = file.read_bytes()
    finding = json.loads(raw)
    if (
        request["finding_sha256"] != digest(raw) or finding["scene"] != request["scene"]
        or finding["definitions_sha256"] != request["definitions_sha256"]
        or finding["status"] != "reviewed" or all(j["status"] == "passed" for j in finding["judgments"].values())
    ):
        raise Blocked("risk must bind a current nonpassing finding")
    verify_bindings(project, finding["artifacts"])
    return {
        "status": "risk_recorded", "finding": request["finding"], "finding_sha256": request["finding_sha256"],
        "user_quote": require_text(request["user_quote"], "actual user risk acceptance"),
        "source_reference": require_text(request["source_reference"], "actual user message reference"),
        "remaining_impact": require_text(request["remaining_impact"], "remaining impact"),
    }


def backfill(project, identifier, request, definitions, path):
    allowed = set(request["authorized_paths"])
    changes = request["files"]
    if not changes or set(changes) != allowed:
        raise Blocked("backfill must exactly name the allowed document paths")
    patches = {}
    for name, spec in changes.items():
        file = project.path(name)
        if file.suffix.lower() not in {".md", ".txt", ".rst"} or Path(name).parts[0].startswith("."):
            raise Blocked("backfill supports ordinary project documents")
        before = file.read_bytes() if file.exists() else None
        if spec["base_sha256"] != (digest(before) if before is not None else None):
            raise Blocked("backfill document baseline changed")
        after = require_text(spec["text"], "candidate document")
        difference = difflib.unified_diff(
            (before or b"").decode("utf-8").splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{name}", tofile=f"b/{name}",
        )
        candidate = path / "documents" / name
        atomic_write(candidate, after.encode("utf-8"))
        patches[name] = {"candidate": project_ref(project, candidate), "diff": "".join(difference)}
    return {"status": "candidate_ready", "patches": patches}


def knowledge(project, identifier, request, definitions, path):
    source = project.path(request["validated_receipt"])
    receipt = read_json(source)
    if receipt["acceptance_id"] != identifier or receipt["status"] not in {"ready", "reviewed", "executed"}:
        raise Blocked("knowledge needs a validated receipt from this acceptance")
    if receipt.get("artifacts"):
        verify_bindings(project, receipt["artifacts"])
    files = request["files"]
    if "INDEX.md" not in files or not all(name.endswith(".md") for name in files):
        raise Blocked("include the Markdown knowledge index and actual methods")
    for name, text in files.items():
        relative = Path(name)
        if relative.anchor or ".." in relative.parts:
            raise Blocked("knowledge file is outside the package")
        require_text(text, "knowledge Markdown")
    visited = set()
    pending = ["INDEX.md"]
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for link in re.findall(r"\]\(([^)]+)\)", files[name]):
            link = link.split("#", 1)[0]
            if not link or link.startswith("https://"):
                continue
            resolved = (Path(name).parent / link).as_posix()
            if resolved not in files:
                raise Blocked(f"knowledge index links to missing method: {resolved}")
            pending.append(resolved)
    if visited != files.keys():
        raise Blocked("some methods are unreachable from the index")
    target = project.shared(identifier, "records", f"knowledge-{token(request['request_id'], 'knowledge id')}")
    if target.exists():
        raise Blocked("knowledge revision already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".pending-knowledge-", dir=target.parent))
    try:
        for name, text in files.items():
            atomic_write(staged / name, text.encode("utf-8"))
        atomic_write(staged / "manifest.json", encoded({"receipt": request["validated_receipt"], "sha256": digest(source.read_bytes())}))
        staged.rename(target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {"status": "published", "index": project_ref(project, target / "INDEX.md")}


def archive(project, identifier, request):
    key = token(request["request_id"], "archive id")
    files = request["files"]
    if not isinstance(files, dict) or not files:
        raise Blocked("archive explicit file paths and SHA-256 values")
    with project.writer():
        project.manifest(identifier)
        target = project.shared(identifier, "records", f"archive-{key}.zip")
        allowed = (project.shared(identifier), local(project, identifier))
        if target.exists():
            with zipfile.ZipFile(target) as bundle:
                if json.loads(bundle.read("manifest.json"))["files"] != files:
                    raise Blocked("archive request already binds different files")
                for name, sha in files.items():
                    if digest(bundle.read(f"files/{name}")) != sha:
                        raise Blocked("archived bytes changed")
        else:
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("manifest.json", encoded({"acceptance_id": identifier, "files": files}))
                size = 0
                for name, sha in files.items():
                    file = project.path(name)
                    if not any(file.is_relative_to(root) for root in allowed):
                        raise Blocked("archive file is outside this acceptance")
                    if any(part == ".git" or part.startswith(".env") for part in Path(name).parts) or file.name in {
                        "agent.jsonl", "native-events.jsonl",
                    }:
                        raise Blocked("archive selection contains private environment or session material")
                    size += file.stat().st_size
                    if size > 50 * 1024 * 1024:
                        raise Blocked("archive exceeds the byte limit")
                    raw = file.read_bytes()
                    if digest(raw) != sha:
                        raise Blocked("archive file changed")
                    bundle.writestr(f"files/{name}", raw)
            atomic_write(target, output.getvalue())
    return {"status": "archived", "archive": project_ref(project, target), "sha256": digest(target.read_bytes())}


def finish(project, identifier, request):
    key = token(request["request_id"], "finish id")
    target = project.shared(identifier, "records", f"finish-{key}.json")
    with project.writer():
        if target.exists():
            old = read_json(target)
            if old["request"] != request:
                raise Blocked("finish request already binds different inputs")
        else:
            if project.current() != identifier:
                raise Blocked("finish only the selected active acceptance")
            snapshot = view(project, identifier)
            unfinished = sorted(name for name, data in snapshot["scenes"].items() if data["status"] != "passed")
            if not snapshot["scenes"] or unfinished:
                require_text(request.get("allow_unfinished"), "explicit authorization for unfinished acceptance")
            atomic_write(target, encoded({"request": request, "snapshot": snapshot, "unfinished": unfinished}))
        if project.current() == identifier:
            project.path(".acceptance-local", "native-v1", "current.json").unlink()
    return {"status": "finished", "record": project_ref(project, target)}


def cleanup(project, identifier, request):
    require_text(request["authorization"], "explicit cleanup authorization")
    key = token(request["request_id"], "cleanup id")
    target = project.shared(identifier, "records", f"cleanup-{key}.json")
    with project.writer():
        if project.current() == identifier:
            raise Blocked("active acceptance cannot be cleaned")
        project.manifest(identifier)
        source = project.path(request["archive"])
        if not source.is_relative_to(project.shared(identifier, "records")) or digest(source.read_bytes()) != request["archive_sha256"]:
            raise Blocked("cleanup requires the verified archive for this acceptance")
        files = request["files"]
        if not files:
            raise Blocked("select actual archived local files")
        state = read_json(target) if target.exists() else {"request": request, "removed": [], "pending": None}
        if state["request"] != request:
            raise Blocked("cleanup request already binds different inputs")
        to_remove = []
        with zipfile.ZipFile(source) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest["acceptance_id"] != identifier:
                raise Blocked("archive belongs to another acceptance")
            for name in files:
                file = project.path(name)
                if not file.is_relative_to(local(project, identifier)):
                    raise Blocked("cleanup is restricted to selected local acceptance files")
                expected = manifest["files"][name]
                if digest(bundle.read(f"files/{name}")) != expected:
                    raise Blocked("archived bytes are not recoverable")
                if file.exists():
                    if digest(file.read_bytes()) != expected:
                        raise Blocked("local file differs from archived bytes")
                    to_remove.append((name, file))
                elif state["pending"] == name:
                    state["removed"].append(name)
                    state["pending"] = None
                    atomic_write(target, encoded(state))
                elif name not in state["removed"]:
                    raise Blocked("local file is already missing")
        for name, file in to_remove:
            state["pending"] = name
            atomic_write(target, encoded(state))
            file.unlink()
            state["removed"].append(name)
            state["pending"] = None
            atomic_write(target, encoded(state))
    return {"status": "cleaned", "record": project_ref(project, target), "files": files}


def recover(project, identifier, request):
    project.manifest(identifier)
    section = token(request["operation"], "operation")
    key = token(request["attempt"], "attempt id")
    folder = local(project, identifier, section, key)
    if not folder.is_dir() or not (folder / "request.json").is_file():
        raise Blocked("recovery requires an existing recorded attempt")
    action = request.get("action", "inspect")
    result_file = folder / "result.json"
    if result_file.exists():
        record = read_json(result_file)
        if record.get("artifacts"):
            verify_bindings(project, record["artifacts"])
        if action == "inspect" or not project.path(".acceptance-local", "native-v1", "writer.lock").exists():
            return {"status": "settled", "result": record["record"]}
    receipts = sorted(project_ref(project, file) for file in folder.rglob("*") if file.is_file())
    pending = {
        "status": "unreconciled", "evidence": receipts,
        "reason": "original attempt has no committed result; preserve evidence and resolve any active process or side effect before resuming",
    }
    if action == "inspect":
        return pending
    if action not in {"reconcile", "supersede"}:
        raise Blocked("unknown recovery action")
    effects = request["external_effects"]
    if effects["status"] not in {"none_proved", "reconciled"}:
        raise Blocked("recovery requires an explicit assessment of external effects")
    require_text(effects["basis"], "verified effects assessment")
    if os.name != "posix":
        raise Blocked("automatic interrupted-process recovery is currently verified only on POSIX")
    guard = project.path(".acceptance-local", "native-v1", "recovery.lock")
    guard.mkdir()
    try:
        lock = project.path(".acceptance-local", "native-v1", "writer.lock")
        owner = None
        if lock.exists():
            owner = read_json(lock / "owner.json")
            if owner["attempt"] != [identifier, section, key]:
                raise Blocked("writer lock belongs to another attempt")
            verify_process_finished(owner["pid"], os.kill)
        for location in folder.rglob("*.process.json"):
            process = read_json(project.path(project_ref(project, location)))
            verify_process_finished(process["group"], os.killpg)
        if action == "reconcile":
            outcome = result_file if result_file.exists() else folder / "pending-outcome.json"
            data = read_json(outcome)
            if (data["acceptance_id"], data["operation"], data["request_id"]) != (identifier, section, key):
                raise Blocked("invalid saved outcome identity")
            scoped(project, identifier, data)
            for field in ("bindings", "artifacts"):
                if data.get(field):
                    verify_bindings(project, data[field])
            original = outcome.read_bytes()
            atomic_write(result_file, original)
            publish_index(project, identifier, data, original)
        audit = folder / "recovery.json"
        atomic_write(audit, encoded({"request": request, "recovered_at": now(), "original_owner": owner, "original_evidence": receipts}))
        if owner is not None:
            quarantine = project.path(".acceptance-local", "native-v1", f"recovered-{identifier}-{section}-{key}")
            if quarantine.exists():
                raise Blocked("recovered lock record already exists")
            lock.rename(quarantine)
        return {
            "status": "reconciled" if action == "reconcile" else "superseded",
            "record": project_ref(project, audit),
        }
    finally:
        guard.rmdir()


def verify_process_finished(pid, check):
    if type(pid) is not int or pid <= 0:
        raise Blocked("missing original process identity")
    try:
        check(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as error:
        raise Blocked("cannot verify the original process has stopped") from error
    raise Blocked("original process or process group is still active")


RECORDED = {
    "analyze": analyze, "assess-scenarios": assess_scenarios, "reverse": reverse,
    "change-plan": change_plan, "rebind": rebind, "source-status": source_status,
    "delivery-judge": delivery_judge, "risk": risk, "knowledge": knowledge, "backfill": backfill,
    "project": project_prepare,
}
DIRECT = {"archive": archive, "finish": finish, "cleanup": cleanup, "recover": recover}


def main():
    configure_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("operation", choices=sorted(DIRECT))
    parser.add_argument("id", type=acceptance_id)
    args = parser.parse_args()
    try:
        result = DIRECT[args.operation](Project(args.root), args.id, json.load(sys.stdin))
    except (Blocked, KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as error:
        result = {"status": "blocked", "reason": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] in {"blocked", "unreconciled"} else 0


if __name__ == "__main__":
    sys.exit(main())
