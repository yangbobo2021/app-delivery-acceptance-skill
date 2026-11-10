import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


from skill_command import skill_command


class Blocked(ValueError):
    pass


class Project:
    def __init__(self, root):
        self.root = root

    def call(self, *args, payload=None):
        result = subprocess.run(
            skill_command("records", self.root, *args), input=json.dumps(payload), text=True,
            capture_output=True, timeout=15,
        )
        reply = json.loads(result.stdout)
        if result.returncode == 2:
            raise Blocked(reply["reason"])
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return reply

    def create(self, identifier, request, reference):
        return self.call("create", identifier, "--request", request, "--reference", reference)

    def show(self, identifier, legacy=False):
        return self.call("show", identifier, *(["--legacy"] if legacy else []))

    def source(self, name, first, last):
        return self.call("source", name, str(first), str(last))

    def freeze(self, identifier, request):
        return self.call("freeze", identifier, payload=request)

    def current(self, legacy=False):
        return self.call("current")["legacy" if legacy else "native"]

    def shared(self, identifier, *parts):
        return self.root.joinpath(".acceptance", "native-v1", identifier, *parts)


def frozen_request(request_id="initial", base=None, requirements=(), scenarios=()):
    return {"request_id": request_id, "base_sha256": base, "requirements": list(requirements), "scenarios": list(scenarios)}


def oral(identifier, text):
    return {"id": identifier, "source": {"kind": "user", "reference": "fixture:user", "text": text}}


class RecordsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project = Project(self.root)
        self.identifier = "AT-native"
        self.quote = "只新建交付验收，稍后再提供要求。"

    def create(self):
        return self.project.create(self.identifier, self.quote, "fixture:create")

    def test_create_only_and_idempotent_retry(self):
        first = self.create()
        self.assertEqual(first["status"], "created")
        path = self.root / first["manifest"]
        raw = path.read_bytes()
        self.assertEqual(json.loads(raw)["request"], self.quote)
        self.assertIsNone(self.project.show(self.identifier)["definitions"])
        self.assertFalse(self.project.shared(self.identifier, "definitions").exists())
        self.assertEqual(self.create()["status"], "already_created")
        self.assertEqual(path.read_bytes(), raw)
        with self.assertRaisesRegex(Blocked, "another native acceptance"):
            self.project.create("AT-other", "新建", "fixture:other")

    def test_retry_after_manifest_commits_but_pointer_write_fails(self):
        self.create()
        # A committed manifest with no pointer is the state left by an interrupted activation.
        (self.root / ".acceptance-local/native-v1/current.json").unlink()
        manifest = self.project.shared(self.identifier, "manifest.json")
        original = manifest.read_bytes()
        self.assertIsNone(self.project.current())
        self.assertEqual(self.create()["status"], "already_created")
        self.assertEqual(self.project.current(), self.identifier)
        self.assertEqual(manifest.read_bytes(), original)

    def test_freezes_exact_oral_text_and_specified_crlf_lines(self):
        self.create()
        quote = "管理员可以关闭预约。"
        (self.root / "scenes.md").write_bytes("# 场景\r\n\r\nSC1 关闭预约。\r\nSC2 范围之外。\r\n".encode())
        source = self.project.source("scenes.md", 3, 3)
        result = self.project.freeze(
            self.identifier,
            frozen_request(requirements=[oral("R1", quote)], scenarios=[{"id": "SC1", "source": source, "requirements": ["R1"]}]),
        )
        snapshot = json.loads((self.root / result["path"]).read_bytes())
        self.assertEqual(snapshot["requirements"]["R1"]["source"]["text"], quote)
        self.assertEqual(snapshot["scenarios"]["SC1"]["source"]["text"], "SC1 关闭预约。\r\n")
        self.assertEqual(result["scene_ids"], ["SC1"])

    def test_source_drift_and_unknown_scene_link_block_without_version(self):
        self.create()
        source_path = self.root / "requirements.md"
        source_path.write_text("R1 原始要求。\n", encoding="utf-8")
        old_source = self.project.source("requirements.md", 1, 1)
        source_path.write_text("R1 新版要求。\n", encoding="utf-8")
        with self.assertRaisesRegex(Blocked, "source changed"):
            self.project.freeze(self.identifier, frozen_request(requirements=[{"id": "R1", "source": old_source}]))
        scene = {**oral("SC1", "使用指定环境。"), "requirements": ["R2"]}
        with self.assertRaisesRegex(Blocked, "requirement missing from the baseline"):
            self.project.freeze(self.identifier, frozen_request(scenarios=[scene]))
        self.assertIsNone(self.project.show(self.identifier)["definitions"])

    def test_duplicate_ids_and_different_reused_request_are_rejected(self):
        self.create()
        initial = oral("R1", "保留输入记录。")
        with self.assertRaisesRegex(Blocked, "duplicate"):
            self.project.freeze(self.identifier, frozen_request(requirements=[initial, initial]))
        original = self.project.freeze(self.identifier, frozen_request(requirements=[initial]))
        with self.assertRaisesRegex(Blocked, "request id"):
            self.project.freeze(self.identifier, frozen_request(requirements=[oral("R1", "不同要求。")]))
        self.assertEqual(self.project.show(self.identifier)["definitions"]["sha256"], original["sha256"])

    def test_refreezing_identical_text_binds_the_new_request(self):
        self.create()
        requirement = oral("R1", "保留原话。")
        first = self.project.freeze(self.identifier, frozen_request(requirements=[requirement]))
        repeat = frozen_request("repeat", first["sha256"], [requirement])
        second = self.project.freeze(self.identifier, repeat)
        self.assertEqual(self.project.freeze(self.identifier, repeat)["status"], "already_frozen")
        self.assertEqual(second["version"], 2)
        with self.assertRaisesRegex(Blocked, "request id"):
            self.project.freeze(self.identifier, frozen_request("repeat", second["sha256"], [oral("R2", "新内容。")]))

    def test_revisions_preserve_history_and_invalidate_only_dependent_scenes(self):
        self.create()
        initial = frozen_request(
            requirements=[oral("R1", "使用原版本。"), oral("R2", "其他要求。")],
            scenarios=[{**oral("SC1", "核对 R1。"), "requirements": ["R1"]}, {**oral("SC2", "核对 R2。"), "requirements": ["R2"]}],
        )
        first = self.project.freeze(self.identifier, initial)
        history = (self.root / first["path"]).read_bytes()
        changed = oral("R1", "使用指定新版本。")
        with self.assertRaisesRegex(Blocked, "explicit authorization"):
            self.project.freeze(self.identifier, frozen_request("revision", first["sha256"], [changed]))
        authorization = "我授权修改 R1 为指定新版本。"
        updated = frozen_request("revision", first["sha256"], [{**changed, "revision_authorization": authorization}])
        second = self.project.freeze(self.identifier, updated)
        self.assertEqual(second["scenes_needing_revalidation"], ["SC1"])
        self.assertEqual(json.loads((self.root / second["path"]).read_bytes())["revision_authorizations"]["requirements:R1"], authorization)
        self.assertEqual(self.project.freeze(self.identifier, updated)["status"], "already_frozen")
        self.assertEqual(self.project.freeze(self.identifier, initial)["status"], "historical_request")
        self.assertEqual((self.root / first["path"]).read_bytes(), history)
        with self.assertRaisesRegex(Blocked, "record snapshot changed"):
            self.project.freeze(self.identifier, frozen_request("stale", first["sha256"], [oral("R3", "追加要求。")]))
        self.assertEqual(self.project.show(self.identifier)["definitions"]["sha256"], second["sha256"])

    def test_interrupted_write_lock_blocks_another_writer(self):
        self.create()
        lock = self.root / ".acceptance-local/native-v1/writer.lock"
        lock.mkdir()
        with self.assertRaisesRegex(Blocked, "busy or has an unreconciled"):
            self.project.freeze(self.identifier, frozen_request(requirements=[oral("R1", "追加要求。")]))
        self.assertTrue(lock.is_dir())
        self.assertIsNone(self.project.show(self.identifier)["definitions"])

    def test_concurrent_writers_cannot_publish_against_same_baseline(self):
        self.create()
        command = skill_command("records", self.root, "freeze", self.identifier)
        processes = []
        for identifier in ("R1", "R2"):
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            child.stdin.write(json.dumps(frozen_request(identifier, requirements=[oral(identifier, "并发要求。")])).encode())
            child.stdin.close()
            processes.append(child)
        outcomes = []
        for child in processes:
            with child.stdout:
                outcomes.append(json.loads(child.stdout.read())["status"])
            child.wait(timeout=5)
        self.assertCountEqual(outcomes, ["frozen", "blocked"])
        self.assertEqual(len(list(self.project.shared(self.identifier, "definitions").glob("*.json"))), 1)

    def test_legacy_files_and_pointers_coexist_without_writes(self):
        manifest = self.root / ".acceptance/AT-old/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b'{"schema":"acceptance-manifest-v1","acceptance_id":"AT-old"}\n')
        pointer = self.root / ".acceptance-local/current.json"
        pointer.parent.mkdir()
        pointer.write_bytes(b'{"acceptance_id":"AT-old"}\n')
        self.create()
        self.assertEqual(self.project.current(legacy=True), "AT-old")
        self.assertEqual(self.project.current(), self.identifier)
        self.assertEqual(self.project.show("AT-old", legacy=True)["status"], "legacy_read_only")
        self.assertEqual(pointer.read_bytes(), b'{"acceptance_id":"AT-old"}\n')
        self.assertEqual(manifest.read_bytes(), b'{"schema":"acceptance-manifest-v1","acceptance_id":"AT-old"}\n')

    def test_git_project_requires_real_ignore(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.project = Project(self.root)
        with self.assertRaisesRegex(Blocked, "must be Git-ignored"):
            self.create()
        self.assertFalse((self.root / ".acceptance").exists())
        (self.root / ".gitignore").write_text("/.acceptance-local/native-v1/current.json\n")
        with self.assertRaisesRegex(Blocked, "must be Git-ignored"):
            self.create()
        (self.root / ".gitignore").write_text("/.acceptance-local/\n")
        self.assertEqual(self.create()["status"], "created")

    def test_symlink_storage_and_source_traversal_block(self):
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.root / ".acceptance-local").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                if sys.platform == "win32" and getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows runner has no symlink privilege")
                raise
            with self.assertRaisesRegex(Blocked, "linked project path"):
                self.create()
            self.assertEqual(list(Path(outside).iterdir()), [])
        with self.assertRaisesRegex(Blocked, "outside the project"):
            self.project.source("../requirements.md", 1, 1)
