/** Local acceptance records and the shared JSON/CLI contracts. */
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync,
  realpathSync, renameSync, rmdirSync, statSync, unlinkSync, writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep, parse as parsePath } from "node:path";

// Requests and receipts are versioned JSON documents. Validate the fields used by each operation.
export type RecordData = Record<string, any>;
export type Snapshot = [string, string, RecordData];
export const NAMESPACE = "native-v1";
const MANIFEST_SCHEMA = "acceptance-native-manifest-v1";
const DEFINITIONS_SCHEMA = "acceptance-native-definitions-v1";
export const SCENARIO_LIST_PREFIX = /^(?:([0-9]+)[.)]|[-*+•])\s+/u;
export const posix = process.platform !== "win32";

export class Blocked extends Error {}
class Usage extends Error {}

export const exists = existsSync;
export function stat(path: string) {
  try { return statSync(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
}
export function removeFile(path: string) {
  try { unlinkSync(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
}
export function within(root: string, path: string) {
  const value = relative(root, path);
  return !isAbsolute(value) && value !== ".." && !value.startsWith(".." + sep);
}
export function resolvedFile(base: string, name: string) {
  const path = resolve(base, name);
  return exists(path) ? realpathSync(path) : path;
}
export function portable(path: string) {
  return path.split(sep).join("/");
}
export function now() {
  return new Date().toISOString().replace(/(\.\d{3})Z$/, "$1000+00:00");
}

const numberSources = new WeakMap<object, Map<string, string>>();
export function parseJson(text: string | Buffer) {
  const utf8 = typeof text === "string" ? text : decodeUtf8(text);
  return JSON.parse(utf8, function (this: object, key: string, value: unknown, context?: { source?: string }) {
    if (typeof value !== "number") return value;
    const source = context?.source ?? String(value);
    if (!/[.eE]/.test(source)) return Number.isSafeInteger(value) ? value : BigInt(source);
    if (!numberSources.has(this)) numberSources.set(this, new Map());
    numberSources.get(this)!.set(key, source);
    return value;
  });
}

export function isIntegerField(object: RecordData, key: string) {
  return Number.isInteger(object[key]) && !numberSources.get(object)?.has(key);
}
export function decodeUtf8(value: Uint8Array) {
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(value);
}
export function readJson(path: string) { return parseJson(readFileSync(path)); }

export function compareText(left: string, right: string) {
  const a = Array.from(left), b = Array.from(right);
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const diff = a[i].codePointAt(0)! - b[i].codePointAt(0)!;
    if (diff) return diff;
  }
  return a.length - b.length;
}
function numberText(value: number, float: boolean) {
  if (!Number.isFinite(value)) throw new Blocked("Out of range float values are not JSON compliant");
  if (Object.is(value, -0)) return float ? "-0.0" : "0";
  if (!float && Number.isInteger(value)) return String(value);
  if (value !== 0 && (Math.abs(value) < 1e-4 || Math.abs(value) >= 1e16)) {
    const [significand, exponent] = value.toExponential().split("e");
    return significand + "e" + exponent[0] + exponent.slice(1).padStart(2, "0");
  }
  return Number.isInteger(value) ? value.toFixed(1) : String(value);
}
function jsonText(value: unknown, depth: number, pretty: boolean, float = false, spaced = false): string {
  if (value === null) return "null";
  if (typeof value === "number") return numberText(value, float);
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value !== "object") throw new Blocked("value is not JSON serializable");
  const array = Array.isArray(value);
  const keys = Object.keys(value);
  if (!array && !spaced) keys.sort(compareText);
  const open = array ? "[" : "{", close = array ? "]" : "}";
  if (!keys.length) return open + close;
  const fields = keys.map(key => {
    const item = jsonText((value as RecordData)[key], depth + 1, pretty, !!numberSources.get(value)?.has(key), spaced);
    return array ? item : JSON.stringify(key) + (pretty || spaced ? ": " : ":") + item;
  });
  if (!pretty) return open + fields.join(spaced ? ", " : ",") + close;
  const padding = "  ".repeat(depth + 1);
  return open + "\n" + padding + fields.join(",\n" + padding) + "\n" + "  ".repeat(depth) + close;
}
// Keep Python float formatting when lifting measured metrics into a receipt.
export function inheritJsonNumbers<T extends object>(target: T, source: object): T {
  const fields = numberSources.get(source);
  if (fields) numberSources.set(target, fields);
  return target;
}
export function encoded(value: unknown) {
  return Buffer.from(jsonText(value, 0, true) + "\n");
}
export function displayJson(value: unknown) { return jsonText(value, 0, false, false, true); }
export function printJson(value: unknown) { console.log(displayJson(value)); }
export function equal(left: unknown, right: unknown) {
  return left === right || (left !== undefined && right !== undefined && encoded(left).equals(encoded(right)));
}
export function digest(value: string | Uint8Array) {
  return createHash("sha256").update(value).digest("hex");
}
export function stdinJson() { return parseJson(readFileSync(0)); }
export function message(error: unknown) { return error instanceof Error ? error.message : String(error); }
export function required(value: RecordData, key: string) {
  if (!Object.hasOwn(value, key)) throw new Blocked("'" + key + "'");
  return value[key];
}
export function requireText(value: unknown, label: string) {
  if (typeof value !== "string" || !value.trim()) throw new Blocked(label + " must be non-empty text");
  return value;
}
export function token(value: unknown, label: string) {
  const text = requireText(value, label);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}(?![\s\S])/.test(text)) throw new Blocked(label + " must be a simple identifier");
  return text;
}
export function acceptanceId(value: unknown) {
  const id = token(value, "acceptance id");
  if (!id.startsWith("AT-") || id === "AT-") throw new Blocked("acceptance id must start with AT-");
  return id;
}

function scenarioScope(value: RecordData) {
  if (!value || typeof value !== "object" || Array.isArray(value) || ("criteria" in value) === ("groups" in value)) {
    throw new Blocked("scenario_scope requires either criteria or groups");
  }
  const title = requireText(value.title, "scenario title").trim();
  const behavior = requireText(value.behavior, "scenario behavior").trim();
  const seen = new Set([behavior]);
  function criteria(items: unknown) {
    if (!Array.isArray(items) || !items.length) throw new Blocked("scenario criteria must be a non-empty list");
    return items.map(item => {
      const text = requireText(item, "scenario criterion").trim();
      if (SCENARIO_LIST_PREFIX.test(text) || /^#{1,6}\s/.test(text)) {
        throw new Blocked("write scenario criteria without list or heading markup");
      }
      if (seen.has(text)) throw new Blocked("scenario criteria must be distinct and must not repeat the behavior");
      seen.add(text);
      return text;
    });
  }
  if ("criteria" in value) return { title, behavior, criteria: criteria(value.criteria) };
  if (!Array.isArray(value.groups) || !value.groups.length) throw new Blocked("scenario groups must be a non-empty list");
  const groups = value.groups.map((group: RecordData) => {
    if (!group || typeof group !== "object" || Array.isArray(group)) throw new Blocked("each scenario group requires a title and criteria");
    return { title: requireText(group.title, "scenario group title").trim(), criteria: criteria(group.criteria) };
  });
  if (new Set(groups.map(group => group.title)).size !== groups.length) throw new Blocked("scenario group titles must be distinct");
  return { title, behavior, groups };
}

export function atomicWrite(path: string, data: string | Uint8Array) {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = join(dirname(path), ".pending-" + randomUUID());
  const fd = openSync(temporary, "wx", 0o600);
  try {
    try {
      writeFileSync(fd, data);
      fsyncSync(fd);
    } finally { closeSync(fd); }
    renameSync(temporary, path);
  } finally { removeFile(temporary); }
}
export function git(root: string, args: string[], timeout = 10_000) {
  const result = spawnSync("git", args, { cwd: root, timeout, encoding: "utf8", windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
  if (result.error) throw result.error;
  return result;
}

export class Project {
  readonly root: string;
  readonly gitRoot: string | null;
  constructor(root: string) {
    this.root = realpathSync(root);
    if (!statSync(this.root).isDirectory()) throw new Blocked("project root must be a directory");
    let dir = this.root;
    while (!exists(join(dir, ".git")) && dir !== dirname(dir)) dir = dirname(dir);
    this.gitRoot = exists(join(dir, ".git")) ? dir : null;
    if (this.gitRoot && this.gitRoot !== this.root) throw new Blocked("use the Git project root for acceptance records");
  }
  path(...parts: string[]) {
    const names = parts.flatMap(part => {
      if (parsePath(part).root) throw new Blocked("path is outside the project: " + part);
      return part.split(posix ? /\// : /[\\/]/).filter(name => name && name !== ".");
    });
    if (names.includes("..")) throw new Blocked("path is outside the project: " + parts.join(sep));
    let current = this.root;
    for (const name of names) {
      current = join(current, name);
      try {
        if (lstatSync(current).isSymbolicLink()) throw new Blocked("linked project path is unsupported: " + relative(this.root, current));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
    }
    return current;
  }
  shared(id: string, ...parts: string[]) { return this.path(".acceptance", NAMESPACE, acceptanceId(id), ...parts); }
  current(legacy = false) {
    const pointer = legacy ? this.path(".acceptance-local", "current.json") : this.path(".acceptance-local", NAMESPACE, "current.json");
    return exists(pointer) ? acceptanceId(required(readJson(pointer), "acceptance_id")) : null;
  }
  lock(attempt: string[] | null = null) {
    if (this.gitRoot) {
      const local = ".acceptance-local/" + NAMESPACE + "/";
      if (git(this.root, ["check-ignore", "--", local]).status !== 0) {
        throw new Blocked(local + " must be Git-ignored before writing");
      }
      const tracked = git(this.root, ["ls-files", "--", local]);
      if (tracked.status !== 0 || tracked.stdout) throw new Blocked("native local acceptance storage must not contain tracked files");
    }
    const lock = this.path(".acceptance-local", NAMESPACE, "writer.lock");
    const recovery = this.path(".acceptance-local", NAMESPACE, "recovery.lock");
    mkdirSync(dirname(lock), { recursive: true });
    if (exists(recovery)) throw new Blocked("acceptance recovery is active");
    try { mkdirSync(lock); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") {
        throw new Blocked("acceptance storage is busy or has an unreconciled interrupted write");
      }
      throw error;
    }
    const release = () => {
      removeFile(join(lock, "owner.json"));
      rmdirSync(lock);
    };
    try {
      if (exists(recovery)) throw new Blocked("acceptance recovery is active");
      atomicWrite(join(lock, "owner.json"), encoded({ pid: process.pid, attempt }));
    } catch (error) { release(); throw error; }
    return release;
  }
  write<T>(action: () => T) {
    const release = this.lock();
    try { return action(); } finally { release(); }
  }
  manifest(id: string) {
    const record = readJson(this.shared(id, "manifest.json"));
    if (record.schema !== MANIFEST_SCHEMA || record.acceptance_id !== id) throw new Blocked("invalid native acceptance manifest");
    return record;
  }
  activate(id: string) {
    const active = this.current();
    if (active && active !== id) throw new Blocked("another native acceptance is active: " + active);
    if (active === null) atomicWrite(this.path(".acceptance-local", NAMESPACE, "current.json"), encoded({ acceptance_id: id }));
  }
  create(id: string, request: string, reference: string) {
    requireText(request, "creation request");
    requireText(reference, "request reference");
    return this.write(() => {
      const active = this.current();
      if (active && active !== id) throw new Blocked("another native acceptance is active: " + active);
      if (exists(this.path(".acceptance", id))) throw new Blocked("a legacy acceptance already owns that identifier");
      const location = this.shared(id, "manifest.json");
      let status = "created";
      if (exists(location)) {
        const record = this.manifest(id);
        if (record.request !== request || record.reference !== reference) throw new Blocked("acceptance id already binds a different creation request");
        status = "already_created";
      } else {
        if (exists(dirname(location)) && readdirSync(dirname(location)).length) throw new Blocked("acceptance directory has no complete manifest");
        atomicWrite(location, encoded({ schema: MANIFEST_SCHEMA, acceptance_id: id, request, reference, created_at: now() }));
      }
      this.activate(id);
      return { status, acceptance_id: id, manifest: relative(this.root, location) };
    });
  }
  source(name: string, first: number, last: number) {
    let location = requireText(name, "source path");
    if (isAbsolute(location)) {
      if (!within(this.root, location)) throw new Blocked("source path is outside the project");
      location = relative(this.root, location);
    }
    const file = this.path(location);
    const raw = readFileSync(file);
    // Split bytes only on CR, LF and CRLF, then decode the selected range.
    const lines = raw.toString("latin1").match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g) ?? [];
    const path = portable(relative(this.root, file));
    if (!Number.isInteger(first) || !Number.isInteger(last) || first < 1 || first > last || last > lines.length) {
      throw new Blocked("invalid inclusive line range in " + path);
    }
    return { kind: "file" as const, path, first, last, sha256: digest(raw),
      text: decodeUtf8(Buffer.from(lines.slice(first - 1, last).join(""), "latin1")) };
  }
  verifiedSource(source: RecordData) {
    if (source.kind === "file") {
      const actual = this.source(source.path, isIntegerField(source, "first") ? source.first : NaN,
        isIntegerField(source, "last") ? source.last : NaN);
      if (source.sha256 !== actual.sha256 || source.text !== actual.text) throw new Blocked("source changed since it was read: " + actual.path);
      requireText(actual.text, "selected source text");
      return actual;
    }
    if (source.kind === "user") return { kind: "user" as const, reference: requireText(source.reference, "user message reference"), text: requireText(source.text, "original user words") };
    throw new Blocked("source kind must be file or user");
  }
  versions(id: string): Snapshot[] {
    const folder = this.shared(id, "definitions");
    if (!exists(folder)) return [];
    let previous: string | null = null;
    return readdirSync(folder).filter(name => name.endsWith(".json")).sort(compareText).map((name, index) => {
      const version = index + 1;
      const path = this.shared(id, "definitions", name);
      const raw = readFileSync(path), record = parseJson(raw);
      if (name !== String(version).padStart(6, "0") + ".json" || record.schema !== DEFINITIONS_SCHEMA ||
          record.acceptance_id !== id || record.version !== version || record.base_sha256 !== previous) {
        throw new Blocked("invalid or discontinuous native definition history");
      }
      previous = digest(raw);
      return [path, previous, record];
    });
  }
  receipt([path, sha, record]: Snapshot, status: string) {
    return {
      status, acceptance_id: record.acceptance_id, version: record.version, sha256: sha, path: relative(this.root, path),
      requirement_ids: Object.keys(record.requirements).sort(compareText), scene_ids: Object.keys(record.scenarios).sort(compareText),
      scenes_needing_revalidation: Object.keys(record.scenarios).sort(compareText).filter(id =>
        Object.entries(record.scenarios[id].requirement_sha256).some(([requirement, expected]) =>
          !Object.hasOwn(record.requirements, requirement) || digest(encoded(record.requirements[requirement])) !== expected)),
    };
  }
  freeze(id: string, request: RecordData) {
    const requestId = token(required(request, "request_id"), "freeze request id");
    if (!request.requirements?.length && !request.scenarios?.length) throw new Blocked("there is no requirement or scenario to freeze");
    const requestHash = digest(encoded(request));
    return this.write(() => {
      if (this.current() !== id) throw new Blocked("select the intended native acceptance before freezing");
      this.manifest(id);
      const versions = this.versions(id), base = versions.at(-1);
      for (const snapshot of versions) {
        if (snapshot[2].request_id !== requestId) continue;
        if (snapshot[2].request_sha256 !== requestHash) throw new Blocked("request id already binds different inputs");
        return this.receipt(snapshot, snapshot === base ? "already_frozen" : "historical_request");
      }
      if (required(request, "base_sha256") !== (base?.[1] ?? null)) throw new Blocked("record snapshot changed; read the current snapshot");
      const prior = base?.[2] ?? { requirements: {}, scenarios: {} };
      const requirements = { ...prior.requirements }, scenarios = { ...prior.scenarios };
      const authorizations: RecordData = {};
      for (const [key, collection] of Object.entries({ requirements, scenarios })) {
        const seen = new Set<string>();
        for (const item of request[key] ?? []) {
          const name = token(item.id, key + " id");
          if (seen.has(name)) throw new Blocked("duplicate " + key + " id: " + name);
          seen.add(name);
          const record: RecordData = { source: this.verifiedSource(item.source) };
          const previous = Object.hasOwn(collection, name) ? collection[name] : undefined;
          if (key === "scenarios") {
            const selected = item.requirements;
            if (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length) {
              throw new Blocked("scene " + name + " needs unique requirement IDs");
            }
            if (selected.some(req => !Object.hasOwn(requirements, req))) throw new Blocked("scene " + name + " references a requirement missing from the baseline");
            record.requirement_sha256 = Object.fromEntries(selected.map(req => [req, digest(encoded(requirements[req]))]));
            const sameSource = equal(record.source, previous?.source), oldScope = previous?.scenario_scope;
            if (Object.hasOwn(item, "scenario_scope")) {
              record.scenario_scope = oldScope && sameSource && equal(item.scenario_scope, oldScope) ? oldScope : scenarioScope(item.scenario_scope);
            } else if (oldScope && sameSource) record.scenario_scope = oldScope;
          }
          if (previous && !equal(record, previous)) {
            authorizations[key + ":" + name] = requireText(item.revision_authorization, "explicit authorization to revise " + name);
          }
          Object.defineProperty(collection, name, { value: record, enumerable: true, writable: true, configurable: true });
        }
      }
      const version = versions.length + 1;
      const record = { schema: DEFINITIONS_SCHEMA, acceptance_id: id, version, request_id: requestId,
        request_sha256: requestHash, base_sha256: request.base_sha256, requirements, scenarios, revision_authorizations: authorizations };
      const path = this.shared(id, "definitions", String(version).padStart(6, "0") + ".json"), data = encoded(record);
      atomicWrite(path, data);
      return this.receipt([path, digest(data), record], "frozen");
    });
  }
  show(id: string, legacy = false) {
    if (legacy) {
      const manifest = readJson(this.path(".acceptance", id, "manifest.json"));
      if (manifest.schema !== "acceptance-manifest-v1" || manifest.acceptance_id !== id) throw new Blocked("unrecognized legacy acceptance manifest");
      const result: RecordData = { status: "legacy_read_only", manifest };
      for (const name of ["creation", "scope", "scenarios", "tests"]) {
        const path = this.path(".acceptance", id, name + ".json");
        result[name] = exists(path) ? readJson(path) : null;
      }
      const records = this.path(".acceptance", id, "records");
      result.record_paths = stat(records)?.isDirectory() ? readdirSync(records).map(name => relative(this.root, join(records, name))).sort(compareText) : [];
      return result;
    }
    const manifest = this.manifest(id), last = this.versions(id).at(-1);
    return { status: "found", manifest, definitions: last ? this.receipt(last, "current") : null };
  }
}

type Options = Record<string, "required" | "value" | "flag">;
type CommandSpec = { args: string[]; options?: Options };
export function argsFor(commands: Record<string, CommandSpec>, common: Options = {}) {
  const argv = process.argv.slice(2);
  const sharedFlags = Object.keys(common).map(name => " [--" + name + " <value>]").join("");
  const usage = "Usage: node " + basename(process.argv[1]) + " --root <project>" + sharedFlags + " <command>\n" +
    Object.entries(commands).map(([name, spec]) => "  " + [name, ...spec.args, ...Object.keys(spec.options ?? {}).map(key => "--" + key)].join(" ")).join("\n");
  if (argv.includes("--help") || argv.includes("-h")) { console.log(usage); return null; }
  const args: string[] = [], flags: RecordData = Object.create(null);
  let spec: CommandSpec | undefined;
  let positionalsOnly = false;
  for (let i = 0; i < argv.length; i++) {
    const word = argv[i];
    if (!positionalsOnly && word === "--") { positionalsOnly = true; continue; }
    if (!positionalsOnly && word.startsWith("--")) {
      const [flag, inline] = word.slice(2).split(/=(.*)/s, 2);
      const kind = flag === "root" ? "required" : spec?.options?.[flag] ?? common[flag];
      if (!kind) throw new Usage("unrecognized argument: " + word + "\n" + usage);
      if (kind === "flag") {
        if (inline !== undefined) throw new Usage("unexpected value for " + word);
        flags[flag] = true;
      } else {
        const value = inline ?? argv[++i];
        if (value === undefined || (inline === undefined && value.startsWith("--"))) throw new Usage("missing value for --" + flag);
        flags[flag] = value;
      }
    } else {
      args.push(word);
      if (args.length === 1 && Object.hasOwn(commands, word)) spec = commands[word];
    }
  }
  const [operation, ...values] = args;
  const requiredArgs = spec?.args.filter(name => !name.endsWith("?")).length ?? 0;
  if (!spec || values.length < requiredArgs || values.length > spec.args.length || flags.root === undefined ||
      Object.entries({ ...common, ...spec.options }).some(([flag, kind]) => kind === "required" && flags[flag] === undefined)) {
    throw new Usage(usage);
  }
  return { operation, values, flags, root: flags.root as string };
}
export function integer(value: string, label: string) {
  if (!/^[+-]?[0-9]+$/.test(value)) throw new Usage("invalid integer for " + label);
  return Number(value);
}
export async function runCli(action: () => unknown | Promise<unknown>) {
  try {
    const result = await action();
    if (result !== undefined) {
      printJson(result);
      if ((result as RecordData)?.status === "blocked") process.exitCode = 2;
    }
  } catch (error) {
    if (error instanceof Usage) console.error(message(error));
    else printJson({ status: "blocked", reason: message(error) });
    process.exitCode = 2;
  }
}
if (import.meta.main) await runCli(() => {
  const args = argsFor({
    create: { args: ["id"], options: { request: "required", reference: "required" } },
    current: { args: [] }, show: { args: ["id"], options: { legacy: "flag" } }, start: { args: ["id"] },
    source: { args: ["file", "first", "last"] }, freeze: { args: ["id"] },
  });
  if (!args) return;
  const { operation, values: [first, second, third], flags, root } = args;
  const project = new Project(root);
  if (operation === "current") return { native: project.current(), legacy: project.current(true) };
  if (operation === "source") return project.source(first, integer(second, "first"), integer(third, "last"));
  const id = acceptanceId(first);
  if (operation === "create") return project.create(id, flags.request, flags.reference);
  if (operation === "show") return project.show(id, flags.legacy);
  if (operation === "freeze") return project.freeze(id, stdinJson());
  return project.write(() => {
    project.manifest(id);
    project.activate(id);
    return { status: "started", acceptance_id: id };
  });
});
