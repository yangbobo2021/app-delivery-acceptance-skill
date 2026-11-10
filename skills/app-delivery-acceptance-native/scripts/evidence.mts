/** Shared scope, immutable attempt records and command evidence. */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { closeSync, mkdirSync, openSync, readFileSync, readdirSync } from "node:fs";
import { constants } from "node:os";
import { join, relative } from "node:path";
import {
  Blocked, NAMESPACE, Project, atomicWrite, compareText, digest, encoded, equal, exists, git,
  isIntegerField, message, now, parseJson, portable, posix, readJson, required, stat, token, within, type RecordData,
} from "./records.mts";

import { windowsBatch } from "./windows_batch.mts";

export { now };
export function requestedTimeout(request: RecordData, fallback = 60) {
  if (!Object.hasOwn(request, "timeout")) return fallback;
  return isIntegerField(request, "timeout") ? request.timeout : NaN;
}

export function requirementScope(definitions: RecordData, requirement: string) {
  return {
    requirement_sha256: digest(encoded(required(definitions.requirements, requirement))),
    scenarios: Object.fromEntries(Object.entries<RecordData>(definitions.scenarios)
      .filter(([, scene]) => Object.hasOwn(scene.requirement_sha256, requirement))
      .map(([name, scene]) => [name, digest(encoded(scene))])),
  };
}
export function scoped(project: Project, id: string, request: RecordData) {
  if (project.current() !== id) throw new Blocked("select the intended native acceptance first");
  project.manifest(id);
  const snapshot = project.versions(id).at(-1);
  if (!snapshot || snapshot[1] !== required(request, "definitions_sha256")) throw new Blocked("operation requires the current record snapshot");
  if (Object.hasOwn(request, "scene")) {
    const scene = token(request.scene, "scene id");
    if (!Object.hasOwn(snapshot[2].scenarios, scene)) throw new Blocked("unknown test scenario: " + scene);
    if (project.receipt(snapshot, "current").scenes_needing_revalidation.includes(scene)) {
      throw new Blocked("scene " + scene + " needs revalidation against changed requirements");
    }
  }
  return snapshot[2];
}
export function fileBindings(project: Project, names: string[]) {
  if (!Array.isArray(names) || !names.length || new Set(names).size !== names.length) {
    throw new Blocked("declare a nonempty list of distinct project files");
  }
  return Object.fromEntries(names.map(name => [name, digest(readFileSync(project.path(name)))]));
}
export function verifyBindings(project: Project, bindings: Record<string, string>) {
  if (!equal(fileBindings(project, Object.keys(bindings)), bindings)) throw new Blocked("bound project files have changed");
}
export function verifyCheckout(project: Project, record: RecordData) {
  if (record.revision && Object.hasOwn(record, "cwd")) {
    const revision = git(project.path(record.cwd), ["rev-parse", "--verify", "HEAD"]);
    if (revision.status !== 0 || revision.stdout.trim() !== record.revision) throw new Blocked("bound product revision has changed");
  }
}
export function local(project: Project, id: string, ...parts: string[]) {
  return project.path(".acceptance-local", NAMESPACE, id, ...parts);
}
export function projectRef(project: Project, path: string) {
  if (!within(project.root, path)) throw new Blocked("path is outside the project: " + path);
  return portable(relative(project.root, path)) || ".";
}
export function attempts(project: Project, id: string, section: string): [RecordData, string][] {
  const folder = local(project, id, section), found: [RecordData, string][] = [];
  if (!exists(folder)) return found;
  for (const entry of readdirSync(folder)) {
    const target = join(folder, entry, "result.json");
    if (!stat(target)?.isFile()) continue;
    const raw = readFileSync(project.path(projectRef(project, target))), record = parseJson(raw);
    if (record.acceptance_id !== id || record.operation !== section) throw new Blocked("invalid attempt identity");
    found.push([record, digest(raw)]);
  }
  return found.sort(([a], [b]) => compareText(a.recorded_at ?? "", b.recorded_at ?? "") || compareText(a.request_id, b.request_id));
}
export function argv(value: unknown): string[] {
  if (!Array.isArray(value) || !value.length || value.some(item => typeof item !== "string" || item.includes("\0"))) {
    throw new Blocked("supply a nonempty argv list with string arguments");
  }
  if (!value[0]) throw new Blocked("command executable is empty");
  return value;
}

function terminate(child: ChildProcess) {
  if (!child.pid) return;
  try {
    if (posix) process.kill(-child.pid, "SIGKILL");
    else if (child.exitCode === null && child.signalCode === null) {
      const result = spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { timeout: 10_000, windowsHide: true });
      if (result.status !== 0) child.kill("SIGKILL");
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
  }
}
export async function capture(
  project: Project, input: unknown, folder: string, name: string, limit = 60, environment?: NodeJS.ProcessEnv, cwd = ".",
) {
  const command = argv(input);
  if (!Number.isInteger(limit) || limit < 1 || limit > 300) throw new Blocked("timeout must be 1 to 300 seconds");
  mkdirSync(folder, { recursive: true });
  const stdout = join(folder, name + ".stdout"), stderr = join(folder, name + ".stderr");
  const recordedEnv = Object.fromEntries(["ACCEPTANCE_EVIDENCE_DIR", "COVERAGE_FILE", "COVERAGE_RCFILE"]
    .filter(key => environment?.[key] !== undefined).map(key => [key, environment![key]]));
  const working = project.path(cwd);
  const result: RecordData = { argv: command, cwd: working, environment: recordedEnv, started_at: now(), exit_code: null };
  const out = openSync(stdout, "wx");
  try {
    const err = openSync(stderr, "wx");
    try {
      const batch = windowsBatch(command, working, environment);
      const child = spawn(batch?.executable ?? command[0], batch?.args ?? command.slice(1), {
        cwd: working, env: environment, stdio: ["ignore", out, err], detached: posix, windowsHide: true,
        windowsVerbatimArguments: !!batch,
      });
      const completed = new Promise<{ code?: number | null; error?: Error }>(resolve => {
        child.once("error", error => resolve({ error }));
        child.once("exit", (code, signal) => resolve({ code: code ?? (signal ? -constants.signals[signal] : null) }));
      });
      if (child.pid) {
        try {
          atomicWrite(join(folder, name + ".process.json"), encoded({ pid: child.pid, group: posix ? child.pid : null }));
        } catch (error) { terminate(child); await completed; throw error; }
      }
      let timer: NodeJS.Timeout | undefined;
      const deadline = new Promise<null>(resolve => { timer = setTimeout(() => resolve(null), limit * 1000); });
      try {
        const finished = await Promise.race([completed, deadline]);
        if (!finished) {
          terminate(child);
          await completed;
          result.error = "timed out";
        } else if (finished.error) result.error = message(finished.error);
        else result.exit_code = finished.code;
      } finally { clearTimeout(timer); }
    } catch (error) { result.error = message(error); }
    finally { closeSync(err); }
  } finally { closeSync(out); }
  result.finished_at = now();
  result.output = Object.fromEntries([stdout, stderr].map(path => [projectRef(project, path), digest(readFileSync(path))]));
  return result;
}

export function publishIndex(project: Project, id: string, record: RecordData, raw: Buffer) {
  const section = record.operation;
  const root = ["case-design", "match", "debug"].includes(section) ? "cases" :
    ["execute", "manual", "history", "review"].includes(section) ? "records" : null;
  if (!root || record.status === "blocked") return;
  const scene = token(record.scene, "scene id"), key = token(record.request_id, "request id");
  const target = project.shared(id, root, scene, section + "-" + key + ".json");
  const data = encoded({
    schema: "acceptance-native-attempt-index-v1", acceptance_id: id, scene, operation: section, request_id: key,
    status: record.status, definitions_sha256: record.definitions_sha256, result_sha256: digest(raw),
    record: record.record, case_ids: Object.keys(record.cases ?? record.results ?? record.judgments ?? {}).sort(compareText),
  });
  if (!exists(target)) atomicWrite(target, data);
  else if (!readFileSync(target).equals(data)) throw new Blocked("published attempt index differs from its committed result");
}

export type Operation = (
  project: Project, id: string, request: RecordData, definitions: RecordData, path: string,
) => RecordData | Promise<RecordData>;

export async function recorded(project: Project, id: string, section: string, request: RecordData, action: Operation) {
  const key = token(required(request, "request_id"), "request id");
  const path = local(project, id, section, key), recordFile = join(path, "result.json"), requestHash = digest(encoded(request));
  const release = project.lock([id, section, key]);
  try {
    const definitions = scoped(project, id, request);
    if (stat(recordFile)?.isFile()) {
      const raw = readFileSync(recordFile), prior = parseJson(raw);
      if (prior.request_sha256 !== requestHash) throw new Blocked("request id already binds different inputs");
      if (prior.bindings && Object.keys(prior.bindings).length) verifyBindings(project, prior.bindings);
      verifyCheckout(project, prior);
      if (prior.artifacts && Object.keys(prior.artifacts).length) verifyBindings(project, prior.artifacts);
      publishIndex(project, id, prior, raw);
      return prior;
    }
    if (exists(path)) throw new Blocked("an unfinished attempt exists; inspect the original evidence before retrying");
    mkdirSync(path, { recursive: true });
    atomicWrite(join(path, "request.json"), encoded(request));
    let outcome: RecordData;
    try { outcome = await action(project, id, request, definitions, path); }
    catch (error) { outcome = { status: "blocked", reason: message(error) }; }
    const record = { ...outcome, operation: section, acceptance_id: id, request_id: key,
      request_sha256: requestHash, definitions_sha256: request.definitions_sha256, record: projectRef(project, recordFile),
      recorded_at: now(), ...(Object.hasOwn(request, "scene") ? { scene: request.scene } : {}) };
    const data = encoded(record);
    atomicWrite(join(path, "pending-outcome.json"), data);
    atomicWrite(recordFile, data);
    publishIndex(project, id, record, data);
    return record;
  } finally { release(); }
}
export function upstream(
  project: Project, id: string, request: RecordData, field: string, section: string, expected: string, verifyLive = true,
): RecordData {
  const key = token(required(request, field), section + " request id");
  const result = readJson(local(project, id, section, key, "result.json"));
  if (result.operation !== section || result.status !== expected || result.definitions_sha256 !== request.definitions_sha256) {
    throw new Blocked("missing or stale " + section + " result");
  }
  if (verifyLive && result.bindings && Object.keys(result.bindings).length) verifyBindings(project, result.bindings);
  if (verifyLive) verifyCheckout(project, result);
  if (result.artifacts && Object.keys(result.artifacts).length) verifyBindings(project, result.artifacts);
  return result;
}
