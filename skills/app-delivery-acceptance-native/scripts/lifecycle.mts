/** User-requested lifecycle, recovery and read-only analysis. */
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, rmdirSync, unlinkSync } from "node:fs";
import { basename, dirname, extname, join, parse as parsePath } from "node:path";
import { ARCHIVE_BYTE_LIMIT, readZip, writeZip } from "./archive.mts";
import { projectFiles } from "./casework.mts";
import { documentDiff } from "./document_diff.mts";
import {
  attempts, capture, fileBindings, local, projectRef, publishIndex, requirementScope, scoped, upstream, verifyBindings, type Operation,
} from "./evidence.mts";
import {
  Blocked, NAMESPACE, Project, acceptanceId, argsFor, atomicWrite, compareText, decodeUtf8, digest, encoded, equal, exists,
  now, parseJson, posix, readJson, requireText, runCli, stat, stdinJson, token, within, type RecordData,
} from "./records.mts";
import { deliveryBasis, view } from "./results.mts";

function analyze(project: Project, id: string, request: RecordData, definitions: RecordData) {
  const analysis = requireText(request.analysis, "analysis with identified gaps"), selected: string[] = request.requirement_ids;
  if (!selected?.length || selected.some(name => !Object.hasOwn(definitions.requirements, name))) {
    throw new Blocked("analysis must reference requirements in the baseline");
  }
  const result: RecordData = { status: "analyzed", analysis,
    requirements: Object.fromEntries(selected.map(name => [name, definitions.requirements[name]])) };
  if (Object.hasOwn(request, "scope_review")) {
    if (selected.length !== 1) throw new Blocked("scene scope review must address exactly one requirement");
    const requirement = selected[0], review = request.scope_review, baselineSha = review.baseline_sha256;
    const baseline = project.versions(id).find(([, sha]) => sha === baselineSha)?.[2];
    if (!baseline || !Object.hasOwn(baseline.requirements, requirement)) {
      throw new Blocked("scene scope review needs the starting record snapshot for this requirement");
    }
    const original = requirementScope(baseline, requirement), current = requirementScope(definitions, requirement);
    const scenes = Object.keys(current.scenarios).sort(compareText);
    if (scenes.some(name => definitions.scenarios[name].requirement_sha256[requirement] !== current.requirement_sha256)) {
      throw new Blocked("scene scope review must use scenes bound to the current requirement");
    }
    if (original.requirement_sha256 !== current.requirement_sha256 ||
        Object.entries(original.scenarios).some(([name, sha]) => current.scenarios[name] !== sha)) {
      throw new Blocked("scene scope review must preserve the baseline requirement and the starting scenarios");
    }
    if (!Array.isArray(review.scenario_ids) || !equal([...review.scenario_ids].sort(compareText), scenes)) {
      throw new Blocked("scene scope review must list every current scene for the requirement");
    }
    result.scope_review = { requirement_id: requirement, baseline_sha256: baselineSha, scope: current,
      added_scenario_ids: scenes.filter(name => !Object.hasOwn(original.scenarios, name)) };
  }
  return result;
}
function reviewerOutput(project: Project, id: string, request: RecordData) {
  const key = token(request.request_id, "review request id"), source = project.path(request.review_file);
  if (source !== local(project, id, "review-inbox", key + ".json") || stat(source)!.size > 256 * 1024) {
    throw new Blocked("use the assigned independent Reviewer file");
  }
  return readFileSync(source);
}
function assessScenarios(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const reviewer = requireText(request.reviewer_session, "independent Reviewer session");
  if (reviewer === request.author_session) throw new Blocked("scenario review requires a separate Reviewer");
  const raw = reviewerOutput(project, id, request), result = parseJson(raw);
  if (!equal(Object.keys(result.scenes), [request.scene])) throw new Blocked("scenario assessment must address the selected test scenario");
  const verdict = result.scenes[request.scene];
  if (!["passed", "needs_revision", "insufficient_evidence"].includes(verdict.status)) throw new Blocked("unrecognized scene assessment outcome");
  requireText(verdict.reason, "independent reason");
  const file = join(path, "reviewer.json");
  atomicWrite(file, raw);
  return { status: "assessed", judgment: verdict, reviewer_session: reviewer, artifacts: { [projectRef(project, file)]: digest(raw) } };
}
async function reverse(project: Project, _id: string, request: RecordData, _definitions: RecordData, path: string) {
  const revision = requireText(request.revision, "local Git revision");
  if (revision.startsWith("-")) throw new Blocked("invalid Git revision");
  const resolved = await capture(project, ["git", "rev-parse", "--verify", revision + "^{commit}"], path, "revision", 20);
  if (resolved.exit_code !== 0) throw new Blocked("Git revision is unavailable");
  const commit = readFileSync(join(path, "revision.stdout"), "utf8").trim();
  const proof = await capture(project, ["git", "show", "--format=fuller", "--no-ext-diff", "--no-textconv", commit, "--"], path, "changes", 30);
  if (proof.exit_code !== 0) throw new Blocked("could not materialize the selected commit");
  return { status: "changes_observed", revision: commit, command: proof, artifacts: proof.output,
    patch: projectRef(project, join(path, "changes.stdout")) };
}
function changePlan(project: Project, id: string, request: RecordData, definitions: RecordData) {
  const versions = project.versions(id), previous = versions.find(([, sha]) => sha === request.previous_sha256)?.[2];
  if (!previous) throw new Blocked("previous definition is unavailable");
  const old = previous.scenarios, current = definitions.scenarios;
  const changed = [...new Set([...Object.keys(old), ...Object.keys(current)])].filter(name => !equal(old[name], current[name]));
  const invalidated = project.receipt(versions.at(-1)!, "current").scenes_needing_revalidation;
  const affected = [...new Set([...changed, ...invalidated])].sort(compareText), intent = request.intent;
  if (!["follow_history", "complete_change_acceptance", "design_only"].includes(intent)) throw new Blocked("unrecognized change intent");
  const historical = new Set(attempts(project, id, "execute").filter(([run]) => run.status === "executed").map(([run]) => run.scene));
  const selected = affected.filter(scene => Object.hasOwn(current, scene) && (intent !== "follow_history" || historical.has(scene)));
  return { status: "planned", affected, selected, intent };
}
async function rebind(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const requested = requireText(request.revision, "selected checkout commit");
  if (!/^[0-9a-f]{40,64}(?![\s\S])/.test(requested)) throw new Blocked("rebind needs a full Git commit");
  let cwd = ".", created;
  if (request.workspace === true) {
    const workspace = local(project, id, "workspace", token(request.request_id, "workspace id"));
    if (exists(workspace)) throw new Blocked("candidate workspace already exists; inspect its original attempt");
    mkdirSync(dirname(workspace), { recursive: true });
    created = await capture(project, ["git", "worktree", "add", "--detach", workspace, requested], path, "worktree", 60);
    if (created.exit_code !== 0) throw new Blocked("Git did not create the selected candidate checkout");
    cwd = projectRef(project, workspace);
  }
  const proof = await capture(project, ["git", "rev-parse", "--verify", "HEAD"], path, "revision", 20, undefined, cwd);
  if (proof.exit_code !== 0 || readFileSync(join(path, "revision.stdout"), "utf8").trim() !== requested) {
    throw new Blocked("project checkout is not at the selected revision");
  }
  const bindings = fileBindings(project, request.files.map((name: string) => projectRef(project, project.path(cwd, name))));
  return { status: "bound", revision: requested, bindings, artifacts: { ...proof.output, ...created?.output }, cwd };
}
function projectPrepare(project: Project, id: string, request: RecordData) {
  const runtime = upstream(project, id, request, "runtime", "runtime", "ready");
  const coverage = request.coverage ? upstream(project, id, request, "coverage", "coverage", "ready") : null;
  if (!coverage) requireText(request.coverage_basis, "project coverage disposition");
  const revision = request.rebind ? upstream(project, id, request, "rebind", "rebind", "bound").revision : null;
  return { status: "ready", runtime: runtime.record, coverage: coverage?.record ?? null, revision, test_roots: request.test_roots,
    guide: requireText(request.guide, "verified project operating guide"), bindings: { ...runtime.bindings, ...coverage?.bindings } };
}
function sourceStatus(_project: Project, _id: string, request: RecordData, definitions: RecordData) {
  const [collection, key] = request.reference.split(/:(.*)/s, 2);
  if (!["requirements", "scenarios"].includes(collection) || !Object.hasOwn(definitions[collection], key)) {
    throw new Blocked("source status must refer to a recorded requirement or test scenario");
  }
  const status = request.source_status;
  if (!["read_failed", "deletion_confirmed", "available"].includes(status)) throw new Blocked("source observation must distinguish failure from confirmed deletion");
  return { status: "observed", source_status: status, basis: requireText(request.basis, "actual source observation"), definition: definitions[collection][key] };
}
function deliveryJudge(project: Project, id: string, request: RecordData, definitions: RecordData, path: string) {
  const reviewer = requireText(request.reviewer_session, "independent delivery Reviewer session");
  if (reviewer === request.author_session) throw new Blocked("delivery review requires a separate Reviewer");
  const selected: string[] = request.requirement_ids;
  if (!selected?.length || selected.some(name => !Object.hasOwn(definitions.requirements, name))) throw new Blocked("select requirements in the baseline");
  const snapshot = view(project, id), selectedScenes = Object.fromEntries(Object.entries<RecordData>(snapshot.scenes)
    .filter(([, scene]) => selected.some(req => Object.hasOwn(scene.definition.requirement_sha256, req))));
  const covered = new Set(Object.values(selectedScenes).flatMap(scene => Object.keys(scene.definition.requirement_sha256)));
  const raw = reviewerOutput(project, id, request), judgment = parseJson(raw);
  requireText(judgment.reason, "independent delivery judgment");
  if (!["passed", "blocked", "failed"].includes(judgment.status)) throw new Blocked("unknown delivery judgment");
  const complete = covered.size && selected.every(req => covered.has(req)) && Object.values(selectedScenes).every(scene => scene.status === "passed");
  if (judgment.status === "passed" && !complete) throw new Blocked("selected delivery scope lacks required passing scene evidence");
  const file = join(path, "reviewer.json");
  atomicWrite(file, raw);
  return { status: "judged", judgment, requirement_ids: selected, reviewer_session: reviewer,
    scene_status: Object.fromEntries(Object.entries(selectedScenes).map(([name, scene]) => [name, scene.status])),
    scene_evidence: deliveryBasis(selectedScenes), artifacts: { [projectRef(project, file)]: digest(raw) } };
}
function risk(project: Project, id: string, request: RecordData) {
  const file = local(project, id, "review", token(request.finding, "review id"), "result.json");
  const raw = readFileSync(file), finding = parseJson(raw);
  if (request.finding_sha256 !== digest(raw) || finding.scene !== request.scene ||
      finding.definitions_sha256 !== request.definitions_sha256 || finding.status !== "reviewed" ||
      Object.values<RecordData>(finding.judgments).every(item => item.status === "passed")) {
    throw new Blocked("risk must bind a current nonpassing finding");
  }
  verifyBindings(project, finding.artifacts);
  return { status: "risk_recorded", finding: request.finding, finding_sha256: request.finding_sha256,
    user_quote: requireText(request.user_quote, "actual user risk acceptance"),
    source_reference: requireText(request.source_reference, "actual user message reference"),
    remaining_impact: requireText(request.remaining_impact, "remaining impact") };
}
function pathParts(name: string) { return name.split(posix ? /\// : /[\\/]/).filter(part => part && part !== "."); }
function backfill(project: Project, _id: string, request: RecordData, _definitions: RecordData, path: string) {
  const allowed = new Set(request.authorized_paths), changes = Object.entries<RecordData>(request.files);
  if (!changes.length || changes.length !== allowed.size || changes.some(([name]) => !allowed.has(name))) {
    throw new Blocked("backfill must exactly name the allowed document paths");
  }
  const patches = Object.fromEntries(changes.map(([name, spec]) => {
    const file = project.path(name);
    if (![".md", ".txt", ".rst"].includes(extname(file).toLowerCase()) || pathParts(name)[0]?.startsWith(".")) {
      throw new Blocked("backfill supports ordinary project documents");
    }
    const before = exists(file) ? readFileSync(file) : null;
    if (spec.base_sha256 !== (before ? digest(before) : null)) throw new Blocked("the source document changed since backfill was prepared");
    const after = requireText(spec.text, "candidate document"), candidate = join(path, "documents", name);
    const diff = documentDiff(before ? decodeUtf8(before) : "", after, name);
    atomicWrite(candidate, after);
    return [name, { candidate: projectRef(project, candidate), diff }];
  }));
  return { status: "candidate_ready", patches };
}
function knowledge(project: Project, id: string, request: RecordData) {
  const source = project.path(request.validated_receipt), receipt = readJson(source);
  if (receipt.acceptance_id !== id || !["ready", "reviewed", "executed"].includes(receipt.status)) {
    throw new Blocked("knowledge needs a validated receipt from this acceptance");
  }
  if (receipt.artifacts && Object.keys(receipt.artifacts).length) verifyBindings(project, receipt.artifacts);
  const files = request.files, names = Object.keys(files);
  if (!Object.hasOwn(files, "INDEX.md") || names.some(name => !name.endsWith(".md"))) throw new Blocked("include the Markdown knowledge index and actual methods");
  for (const [name, text] of Object.entries(files)) {
    if (parsePath(name).root || pathParts(name).includes("..")) throw new Blocked("knowledge file is outside the package");
    requireText(text, "knowledge Markdown");
  }
  const visited = new Set<string>(), pending = ["INDEX.md"];
  while (pending.length) {
    const name = pending.pop()!;
    if (visited.has(name)) continue;
    visited.add(name);
    for (const match of files[name].matchAll(/\]\(([^)]+)\)/g)) {
      const link = match[1].split("#")[0];
      if (!link || link.startsWith("https://")) continue;
      const resolved = pathParts(dirname(name) + "/" + link).join("/");
      if (parsePath(link).root || !Object.hasOwn(files, resolved)) throw new Blocked("knowledge index links to missing method: " + resolved);
      pending.push(resolved);
    }
  }
  if (visited.size !== names.length) throw new Blocked("some methods are unreachable from the index");
  const target = project.shared(id, "records", "knowledge-" + token(request.request_id, "knowledge id"));
  if (exists(target)) throw new Blocked("knowledge revision already exists");
  mkdirSync(dirname(target), { recursive: true });
  const staged = mkdtempSync(join(dirname(target), ".pending-knowledge-"));
  try {
    for (const [name, text] of Object.entries<string>(files)) atomicWrite(join(staged, name), text);
    atomicWrite(join(staged, "manifest.json"), encoded({ receipt: request.validated_receipt, sha256: digest(readFileSync(source)) }));
    renameSync(staged, target);
  } finally { rmSync(staged, { recursive: true, force: true }); }
  return { status: "published", index: projectRef(project, join(target, "INDEX.md")) };
}

function archive(project: Project, id: string, request: RecordData) {
  const key = token(request.request_id, "archive id"), files = request.files;
  if (!files || typeof files !== "object" || Array.isArray(files) || !Object.keys(files).length) {
    throw new Blocked("archive explicit file paths and SHA-256 values");
  }
  return project.write(() => {
    project.manifest(id);
    const target = project.shared(id, "records", "archive-" + key + ".zip");
    const allowed = [project.shared(id), local(project, id)];
    if (exists(target)) {
      const read = readZip(readFileSync(target));
      if (!equal(parseJson(read("manifest.json")).files, files)) throw new Blocked("archive request already binds different files");
      for (const [name, sha] of Object.entries(files)) {
        if (digest(read("files/" + name)) !== sha) throw new Blocked("archived bytes changed");
      }
    } else {
      const entries = new Map([["manifest.json", encoded({ acceptance_id: id, files })]]);
      let size = 0;
      for (const [name, sha] of Object.entries(files)) {
        const file = project.path(name);
        if (!allowed.some(root => within(root, file))) throw new Blocked("archive file is outside this acceptance");
        if (pathParts(name).some(part => part === ".git" || part.startsWith(".env")) || ["agent.jsonl", "native-events.jsonl"].includes(basename(file))) {
          throw new Blocked("archive selection contains private environment or session material");
        }
        size += stat(file)!.size;
        if (size > ARCHIVE_BYTE_LIMIT) throw new Blocked("archive exceeds the byte limit");
        const raw = readFileSync(file);
        if (digest(raw) !== sha) throw new Blocked("archive file changed");
        entries.set("files/" + name, raw);
      }
      atomicWrite(target, writeZip(entries));
    }
    return { status: "archived", archive: projectRef(project, target), sha256: digest(readFileSync(target)) };
  });
}
function finish(project: Project, id: string, request: RecordData) {
  const target = project.shared(id, "records", "finish-" + token(request.request_id, "finish id") + ".json");
  return project.write(() => {
    if (exists(target)) {
      if (!equal(readJson(target).request, request)) throw new Blocked("finish request already binds different inputs");
    } else {
      if (project.current() !== id) throw new Blocked("finish only the selected active acceptance");
      const snapshot = view(project, id), unfinished = Object.entries<RecordData>(snapshot.scenes)
        .filter(([, item]) => item.status !== "passed").map(([name]) => name).sort(compareText);
      const requirements = Object.entries<RecordData>(snapshot.requirement_results ?? {});
      const pending = requirements.filter(([, item]) => !Object.hasOwn(item, "delivery_judgment")).map(([name]) => name).sort(compareText);
      if (!requirements.length || pending.length) requireText(request.allow_unfinished, "explicit authorization for missing requirement judgments");
      atomicWrite(target, encoded({ request, snapshot, unfinished, pending_requirements: pending }));
    }
    if (project.current() === id) unlinkSync(project.path(".acceptance-local", NAMESPACE, "current.json"));
    return { status: "finished", record: projectRef(project, target) };
  });
}
function cleanup(project: Project, id: string, request: RecordData) {
  requireText(request.authorization, "explicit cleanup authorization");
  const target = project.shared(id, "records", "cleanup-" + token(request.request_id, "cleanup id") + ".json");
  return project.write(() => {
    if (project.current() === id) throw new Blocked("active acceptance cannot be cleaned");
    project.manifest(id);
    const source = project.path(request.archive);
    if (!within(project.shared(id, "records"), source)) throw new Blocked("cleanup requires the verified archive for this acceptance");
    const raw = readFileSync(source);
    if (digest(raw) !== request.archive_sha256) throw new Blocked("cleanup requires the verified archive for this acceptance");
    const files: string[] = request.files;
    if (!files?.length) throw new Blocked("select actual archived local files");
    const state = exists(target) ? readJson(target) : { request, removed: [], pending: null };
    if (!equal(state.request, request)) throw new Blocked("cleanup request already binds different inputs");
    const remove: [string, string][] = [], read = readZip(raw), manifest = parseJson(read("manifest.json"));
    if (manifest.acceptance_id !== id) throw new Blocked("archive belongs to another acceptance");
    function recordRemoval(name: string) {
      state.removed.push(name);
      state.pending = null;
      atomicWrite(target, encoded(state));
    }
    for (const name of files) {
      const file = project.path(name);
      if (!within(local(project, id), file)) throw new Blocked("cleanup is restricted to selected local acceptance files");
      const expected = manifest.files[name];
      if (digest(read("files/" + name)) !== expected) throw new Blocked("archived bytes are not recoverable");
      if (exists(file)) {
        if (digest(readFileSync(file)) !== expected) throw new Blocked("local file differs from archived bytes");
        remove.push([name, file]);
      } else if (state.pending === name) recordRemoval(name);
      else if (!state.removed.includes(name)) throw new Blocked("local file is already missing");
    }
    for (const [name, file] of remove) {
      state.pending = name;
      atomicWrite(target, encoded(state));
      unlinkSync(file);
      recordRemoval(name);
    }
    return { status: "cleaned", record: projectRef(project, target), files };
  });
}
function windowsProcesses(): RecordData[] {
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"],
  { encoding: "utf8", timeout: 20_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true });
  if (result.error || result.status !== 0) throw new Blocked("could not verify Windows process identities");
  const processes = parseJson(result.stdout);
  if (!Array.isArray(processes) || !processes.length) throw new Blocked("Windows process information is unavailable");
  return processes;
}
function verifyProcessFinished(pid: number, group: boolean, windows: RecordData[] | null) {
  if (!Number.isInteger(pid) || pid <= 0) throw new Blocked("missing original process identity");
  if (windows) {
    if (windows.some(item => item.ProcessId === pid || item.ParentProcessId === pid)) {
      throw new Blocked("original Windows process or descendant is still active");
    }
    return;
  }
  try { process.kill(group ? -pid : pid, 0); }
  catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ESRCH") return;
    if (code === "EPERM" || code === "EACCES") throw new Blocked("cannot verify the original process has stopped");
    throw error;
  }
  throw new Blocked("original process or process group is still active");
}
function recover(project: Project, id: string, request: RecordData) {
  project.manifest(id);
  const section = token(request.operation, "operation"), key = token(request.attempt, "attempt id");
  const folder = local(project, id, section, key), result = join(folder, "result.json");
  if (!stat(folder)?.isDirectory() || !stat(join(folder, "request.json"))?.isFile()) throw new Blocked("recovery requires an existing recorded attempt");
  const action = request.action ?? "inspect", lock = project.path(".acceptance-local", NAMESPACE, "writer.lock");
  if (exists(result)) {
    const record = readJson(result);
    if (record.artifacts && Object.keys(record.artifacts).length) verifyBindings(project, record.artifacts);
    if (action === "inspect" || !exists(lock)) return { status: "settled", result: record.record };
  }
  const paths = [...projectFiles(project, folder)];
  const receipts = paths.map(file => projectRef(project, file)).sort(compareText);
  if (action === "inspect") return { status: "unreconciled", evidence: receipts,
    reason: "original attempt has no committed result; preserve evidence and resolve any active process or side effect before resuming" };
  if (!["reconcile", "supersede"].includes(action)) throw new Blocked("unknown recovery action");
  const effects = request.external_effects;
  if (!["none_proved", "reconciled"].includes(effects.status)) throw new Blocked("recovery requires an explicit assessment of external effects");
  requireText(effects.basis, "verified effects assessment");
  const guard = project.path(".acceptance-local", NAMESPACE, "recovery.lock");
  mkdirSync(guard);
  try {
    const windows = posix ? null : windowsProcesses();
    const owner = exists(lock) ? readJson(join(lock, "owner.json")) : null;
    if (owner) {
      if (!equal(owner.attempt, [id, section, key])) throw new Blocked("writer lock belongs to another attempt");
      verifyProcessFinished(owner.pid, false, windows);
    }
    for (const location of paths.filter(file => file.endsWith(".process.json"))) {
      const receipt = readJson(location);
      verifyProcessFinished(posix ? receipt.group : receipt.pid, posix, windows);
    }
    if (action === "reconcile") {
      const outcome = exists(result) ? result : join(folder, "pending-outcome.json"), raw = readFileSync(outcome), data = parseJson(raw);
      if (!equal([data.acceptance_id, data.operation, data.request_id], [id, section, key])) throw new Blocked("invalid saved outcome identity");
      scoped(project, id, data);
      for (const field of ["bindings", "artifacts"]) {
        if (data[field] && Object.keys(data[field]).length) verifyBindings(project, data[field]);
      }
      atomicWrite(result, raw);
      publishIndex(project, id, data, raw);
    }
    const audit = join(folder, "recovery.json");
    atomicWrite(audit, encoded({ request, recovered_at: now(), original_owner: owner, original_evidence: receipts }));
    if (owner) {
      const quarantine = project.path(".acceptance-local", NAMESPACE, ["recovered", id, section, key].join("-"));
      if (exists(quarantine)) throw new Blocked("recovered lock record already exists");
      renameSync(lock, quarantine);
    }
    return { status: action === "reconcile" ? "reconciled" : "superseded", record: projectRef(project, audit) };
  } finally { rmdirSync(guard); }
}
export const RECORDED: Record<string, Operation> = {
  analyze, "assess-scenarios": assessScenarios, reverse, "change-plan": changePlan, rebind, "source-status": sourceStatus,
  "delivery-judge": deliveryJudge, risk, knowledge, backfill, project: projectPrepare,
};
const DIRECT = { archive, finish, cleanup, recover };
if (import.meta.main) await runCli(() => {
  const args = argsFor(Object.fromEntries(Object.keys(DIRECT).sort(compareText).map(name => [name, { args: ["id"] }])));
  if (!args) return;
  const result = DIRECT[args.operation as keyof typeof DIRECT](new Project(args.root), acceptanceId(args.values[0]), stdinJson());
  if (result.status === "unreconciled") process.exitCode = 2;
  return result;
});
