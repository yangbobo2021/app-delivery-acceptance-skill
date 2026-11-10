"""Local acceptance records. The calling Skill decides which operation to run."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


NAMESPACE = "native-v1"
MANIFEST_SCHEMA = "acceptance-native-manifest-v1"
DEFINITIONS_SCHEMA = "acceptance-native-definitions-v1"
SCENARIO_LIST_PREFIX = re.compile(r"^(?:(?P<number>[0-9]+)[.)]|[-*+•])\s+")


class Blocked(ValueError):
    pass


def configure_io():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise Blocked(f"{label} must be non-empty text")
    return value


def scenario_scope(value):
    if not isinstance(value, dict) or ("criteria" in value) == ("groups" in value):
        raise Blocked("scenario_scope requires either criteria or groups")
    title = require_text(value.get("title"), "scenario title").strip()
    behavior = require_text(value.get("behavior"), "scenario behavior").strip()
    scope = {"title": title, "behavior": behavior}
    seen = {behavior}

    def criteria(items):
        if not isinstance(items, list) or not items:
            raise Blocked("scenario criteria must be a non-empty list")
        result = []
        for item in items:
            criterion = require_text(item, "scenario criterion").strip()
            if SCENARIO_LIST_PREFIX.match(criterion) or re.match(r"^#{1,6}\s", criterion):
                raise Blocked("write scenario criteria without list or heading markup")
            if criterion in seen:
                raise Blocked("scenario criteria must be distinct and must not repeat the behavior")
            seen.add(criterion)
            result.append(criterion)
        return result

    if "criteria" in value:
        scope["criteria"] = criteria(value["criteria"])
    else:
        if not isinstance(value["groups"], list) or not value["groups"]:
            raise Blocked("scenario groups must be a non-empty list")
        groups = []
        for group in value["groups"]:
            if not isinstance(group, dict):
                raise Blocked("each scenario group requires a title and criteria")
            groups.append({
                "title": require_text(group.get("title"), "scenario group title").strip(),
                "criteria": criteria(group.get("criteria")),
            })
        if len({group["title"] for group in groups}) != len(groups):
            raise Blocked("scenario group titles must be distinct")
        scope["groups"] = groups
    return scope


def token(value, label):
    value = require_text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise Blocked(f"{label} must be a simple identifier")
    return value


def acceptance_id(value):
    value = token(value, "acceptance id")
    if not value.startswith("AT-") or value == "AT-":
        raise Blocked("acceptance id must start with AT-")
    return value


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise Blocked("project root must be a directory")
        self.git_root = next((p for p in (self.root, *self.root.parents) if (p / ".git").exists()), None)
        if self.git_root is not None and self.git_root != self.root:
            raise Blocked("use the Git project root for acceptance records")

    def path(self, *parts):
        relative = Path(*parts)
        if relative.anchor or ".." in relative.parts:
            raise Blocked(f"path is outside the project: {relative}")
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise Blocked(f"linked project path is unsupported: {current.relative_to(self.root)}")
        return current

    def shared(self, identifier, *parts):
        return self.path(".acceptance", NAMESPACE, acceptance_id(identifier), *parts)

    def current(self, legacy=False):
        parts = (".acceptance-local", "current.json") if legacy else (".acceptance-local", NAMESPACE, "current.json")
        pointer = self.path(*parts)
        return acceptance_id(read_json(pointer)["acceptance_id"]) if pointer.exists() else None

    @contextmanager
    def writer(self, attempt=None):
        if self.git_root is not None:
            private = f".acceptance-local/{NAMESPACE}/"
            result = subprocess.run(
                ["git", "check-ignore", "--", private],
                cwd=self.root, capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                raise Blocked(f".acceptance-local/{NAMESPACE}/ must be Git-ignored before writing")
            tracked = subprocess.run(
                ["git", "ls-files", "--", private], cwd=self.root, capture_output=True, timeout=10,
            )
            if tracked.returncode != 0 or tracked.stdout:
                raise Blocked("native local acceptance storage must not contain tracked files")
        lock = self.path(".acceptance-local", NAMESPACE, "writer.lock")
        recovery = self.path(".acceptance-local", NAMESPACE, "recovery.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        if recovery.exists():
            raise Blocked("acceptance recovery is active")
        try:
            lock.mkdir()
        except FileExistsError as error:
            raise Blocked("acceptance storage is busy or has an unreconciled interrupted write") from error
        try:
            if recovery.exists():
                raise Blocked("acceptance recovery is active")
            atomic_write(lock / "owner.json", encoded({"pid": os.getpid(), "attempt": attempt}))
            yield
        finally:
            (lock / "owner.json").unlink(missing_ok=True)
            lock.rmdir()

    def manifest(self, identifier):
        record = read_json(self.shared(identifier, "manifest.json"))
        if record.get("schema") != MANIFEST_SCHEMA or record.get("acceptance_id") != identifier:
            raise Blocked("invalid native acceptance manifest")
        return record

    def activate(self, identifier):
        active = self.current()
        if active not in (None, identifier):
            raise Blocked(f"another native acceptance is active: {active}")
        if active is None:
            atomic_write(self.path(".acceptance-local", NAMESPACE, "current.json"), encoded({"acceptance_id": identifier}))

    def create(self, identifier, request, reference):
        request = require_text(request, "creation request")
        reference = require_text(reference, "request reference")
        with self.writer():
            active = self.current()
            if active not in (None, identifier):
                raise Blocked(f"another native acceptance is active: {active}")
            if self.path(".acceptance", identifier).exists():
                raise Blocked("a legacy acceptance already owns that identifier")
            location = self.shared(identifier, "manifest.json")
            if location.exists():
                record = self.manifest(identifier)
                if (record["request"], record["reference"]) != (request, reference):
                    raise Blocked("acceptance id already binds a different creation request")
                status = "already_created"
            else:
                if location.parent.exists() and any(location.parent.iterdir()):
                    raise Blocked("acceptance directory has no complete manifest")
                record = {
                    "schema": MANIFEST_SCHEMA,
                    "acceptance_id": identifier,
                    "request": request,
                    "reference": reference,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                atomic_write(location, encoded(record))
                status = "created"
            self.activate(identifier)
        return {"status": status, "acceptance_id": identifier, "manifest": str(location.relative_to(self.root))}

    def source(self, name, first, last):
        location = Path(require_text(name, "source path"))
        if location.is_absolute():
            try:
                location = location.relative_to(self.root)
            except ValueError as error:
                raise Blocked("source path is outside the project") from error
        raw = self.path(location).read_bytes()
        lines = raw.splitlines(keepends=True)
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
            raise Blocked(f"invalid inclusive line range in {location}")
        return {
            "kind": "file", "path": location.as_posix(), "first": first, "last": last,
            "sha256": digest(raw), "text": b"".join(lines[first - 1:last]).decode("utf-8"),
        }

    def verified_source(self, source):
        if source["kind"] == "file":
            actual = self.source(source["path"], source["first"], source["last"])
            if (source["sha256"], source["text"]) != (actual["sha256"], actual["text"]):
                raise Blocked(f"source changed since it was read: {actual['path']}")
            require_text(actual["text"], "selected source text")
            return actual
        if source["kind"] == "user":
            return {
                "kind": "user",
                "reference": require_text(source["reference"], "user message reference"),
                "text": require_text(source["text"], "original user words"),
            }
        raise Blocked("source kind must be file or user")

    def versions(self, identifier):
        folder = self.shared(identifier, "definitions")
        if not folder.exists():
            return []
        snapshots = []
        previous = None
        for path in sorted(folder.glob("*.json")):
            version = len(snapshots) + 1
            safe_path = self.shared(identifier, "definitions", path.name)
            raw = safe_path.read_bytes()
            record = json.loads(raw)
            if (
                path.name != f"{version:06}.json"
                or record.get("schema") != DEFINITIONS_SCHEMA
                or record.get("acceptance_id") != identifier
                or record.get("version") != version
                or record.get("base_sha256") != previous
            ):
                raise Blocked("invalid or discontinuous native definition history")
            previous = digest(raw)
            snapshots.append((safe_path, previous, record))
        return snapshots

    def receipt(self, snapshot, status):
        path, sha, record = snapshot
        return {
            "status": status, "acceptance_id": record["acceptance_id"], "version": record["version"],
            "sha256": sha, "path": str(path.relative_to(self.root)),
            "requirement_ids": sorted(record["requirements"]), "scene_ids": sorted(record["scenarios"]),
            "scenes_needing_revalidation": [
                identifier for identifier, scene in record["scenarios"].items()
                if any(
                    requirement not in record["requirements"] or digest(encoded(record["requirements"][requirement])) != expected
                    for requirement, expected in scene["requirement_sha256"].items()
                )
            ],
        }

    def freeze(self, identifier, request):
        request_id = token(request["request_id"], "freeze request id")
        if not request.get("requirements") and not request.get("scenarios"):
            raise Blocked("there is no requirement or scenario to freeze")
        request_hash = digest(encoded(request))
        with self.writer():
            if self.current() != identifier:
                raise Blocked("select the intended native acceptance before freezing")
            self.manifest(identifier)
            versions = self.versions(identifier)
            for snapshot in versions:
                previous = snapshot[2]
                if previous["request_id"] == request_id:
                    if previous["request_sha256"] != request_hash:
                        raise Blocked("request id already binds different inputs")
                    return self.receipt(snapshot, "already_frozen" if snapshot == versions[-1] else "historical_request")
            base = versions[-1] if versions else None
            if request["base_sha256"] != (base[1] if base else None):
                raise Blocked("record snapshot changed; read the current snapshot")
            prior = base[2] if base else {"requirements": {}, "scenarios": {}}
            requirements, scenarios = prior["requirements"].copy(), prior["scenarios"].copy()
            authorizations = {}
            for key, collection in (("requirements", requirements), ("scenarios", scenarios)):
                seen = set()
                for item in request.get(key, []):
                    item_id = token(item["id"], f"{key} id")
                    if item_id in seen:
                        raise Blocked(f"duplicate {key} id: {item_id}")
                    seen.add(item_id)
                    record = {"source": self.verified_source(item["source"])}
                    if key == "scenarios":
                        selected = item["requirements"]
                        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
                            raise Blocked(f"scene {item_id} needs unique requirement IDs")
                        if any(name not in requirements for name in selected):
                            raise Blocked(f"scene {item_id} references a requirement missing from the baseline")
                        record["requirement_sha256"] = {name: digest(encoded(requirements[name])) for name in selected}
                        previous_scene = collection.get(item_id, {})
                        previous_scope = previous_scene.get("scenario_scope")
                        if "scenario_scope" in item:
                            scope = item["scenario_scope"]
                            if previous_scope is not None and scope == previous_scope and record["source"] == previous_scene["source"]:
                                record["scenario_scope"] = previous_scope
                            else:
                                record["scenario_scope"] = scenario_scope(scope)
                        elif previous_scope is not None and record["source"] == previous_scene["source"]:
                            record["scenario_scope"] = previous_scope
                    if item_id in collection and record != collection[item_id]:
                        authorizations[f"{key}:{item_id}"] = require_text(
                            item.get("revision_authorization"), f"explicit authorization to revise {item_id}"
                        )
                    collection[item_id] = record
            version = len(versions) + 1
            record = {
                "schema": DEFINITIONS_SCHEMA, "acceptance_id": identifier, "version": version,
                "request_id": request_id, "request_sha256": request_hash, "base_sha256": request["base_sha256"],
                "requirements": requirements, "scenarios": scenarios, "revision_authorizations": authorizations,
            }
            path = self.shared(identifier, "definitions", f"{version:06}.json")
            data = encoded(record)
            atomic_write(path, data)
            return self.receipt((path, digest(data), record), "frozen")

    def show(self, identifier, legacy=False):
        if legacy:
            manifest = read_json(self.path(".acceptance", identifier, "manifest.json"))
            if manifest.get("schema") != "acceptance-manifest-v1" or manifest.get("acceptance_id") != identifier:
                raise Blocked("unrecognized legacy acceptance manifest")
            result = {"status": "legacy_read_only", "manifest": manifest}
            for name in ("creation", "scope", "scenarios", "tests"):
                path = self.path(".acceptance", identifier, name + ".json")
                result[name] = read_json(path) if path.exists() else None
            records = self.path(".acceptance", identifier, "records")
            result["record_paths"] = sorted(str(p.relative_to(self.root)) for p in records.iterdir()) if records.is_dir() else []
            return result
        manifest = self.manifest(identifier)
        versions = self.versions(identifier)
        return {
            "status": "found", "manifest": manifest,
            "definitions": self.receipt(versions[-1], "current") if versions else None,
        }


def main():
    configure_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    create = subcommands.add_parser("create")
    create.add_argument("id", type=acceptance_id)
    create.add_argument("--request", required=True)
    create.add_argument("--reference", required=True)
    subcommands.add_parser("current")
    show = subcommands.add_parser("show")
    show.add_argument("id", type=acceptance_id)
    show.add_argument("--legacy", action="store_true")
    start = subcommands.add_parser("start")
    start.add_argument("id", type=acceptance_id)
    source = subcommands.add_parser("source")
    source.add_argument("file")
    source.add_argument("first", type=int)
    source.add_argument("last", type=int)
    freeze = subcommands.add_parser("freeze")
    freeze.add_argument("id", type=acceptance_id)
    args = parser.parse_args()
    try:
        project = Project(args.root)
        if args.operation == "create":
            result = project.create(args.id, args.request, args.reference)
        elif args.operation == "current":
            result = {"native": project.current(), "legacy": project.current(legacy=True)}
        elif args.operation == "show":
            result = project.show(args.id, args.legacy)
        elif args.operation == "start":
            with project.writer():
                project.manifest(args.id)
                project.activate(args.id)
            result = {"status": "started", "acceptance_id": args.id}
        elif args.operation == "source":
            result = project.source(args.file, args.first, args.last)
        else:
            result = project.freeze(args.id, json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
