import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [reference, python] = process.argv.slice(2);
const workflows = path.join(reference, "skills/en/app-delivery-acceptance/playbooks/acceptance/workflows");
const baseline = process.env.ACCEPTANCE_PYTHON_BASELINE;
const script = baseline ? path.join(baseline, "records.py")
  : fileURLToPath(new URL("../skills/app-delivery-acceptance-native/scripts/records.mts", import.meta.url));
const { createAcceptance } = await import(pathToFileURL(path.join(workflows, "create/initialize.mjs")));
const { freezeAcceptance } = await import(pathToFileURL(path.join(workflows, "freeze/freeze.mjs")));
const { inventory } = await import(pathToFileURL(path.join(workflows, "view/data.mjs")));
const root = fs.mkdtempSync(path.join(os.tmpdir(), "acceptance-coexistence-"));
function native(args) {
  const result = spawnSync(baseline ? python : process.execPath, [script, "--root", root, ...args], { encoding: "utf8", timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}
function legacyFiles(folder) {
  return fs.readdirSync(folder, { withFileTypes: true })
    .filter(entry => entry.name !== "native-v1")
    .flatMap(entry => {
      const file = path.join(folder, entry.name);
      return entry.isDirectory() ? legacyFiles(file) : [[file, fs.readFileSync(file)]];
    });
}
try {
  const acceptance_id = "AT-legacy-fixture";
  const common = { project_root: root, acceptance_id };
  createAcceptance({ ...common, request_id: "create", user_request: "仅创建旧版合成验收。" });
  freezeAcceptance({
    ...common, request_id: "freeze", base_definitions_sha256: null, basis: "合成夹具明确授权冻结",
    definitions: {
      context: { business_goal: "验证原版冻结资料与新版写入目录共存。" },
      requirements: [{ id: "R1", text: "旧验收的原始要求。", origin: { kind: "integration-fixture", reference: "fixture:user" }, external_refs: [] }],
    },
  });
  const snapshot = [".acceptance", ".acceptance-local"].flatMap(name => legacyFiles(path.join(root, name)));
  assert.deepEqual(inventory(root).map(item => item.id), [acceptance_id]);
  native(["create", "AT-native-fixture", "--request", "仅创建新版合成验收。", "--reference", "fixture:user"]);
  assert.deepEqual(native(["current"]), { legacy: acceptance_id, native: "AT-native-fixture" });
  const legacy = native(["show", acceptance_id, "--legacy"]);
  assert.equal(legacy.scenarios.requirements[0].text, "旧验收的原始要求。");
  assert.ok(legacy.record_paths.length > 0);
  assert.deepEqual(inventory(root).map(item => item.id), [acceptance_id]);
  for (const [file, raw] of snapshot) assert.deepEqual(fs.readFileSync(file), raw, file);
  console.log(JSON.stringify({ status: "passed", preserved_files: snapshot.length, legacy_version: legacy.scope.version }));
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
