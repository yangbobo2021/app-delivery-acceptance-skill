/** Evidence-producing operations for the native acceptance Skill. */
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { basename, extname, join } from "node:path";
import { currentDesigns, plan, sceneObligations } from "./case_design.mts";
import { debug, execute, history, manual, review } from "./casework.mts";
import { projectMeasure } from "./coverage_adapter.mts";
import { measurePython, recompute } from "./coverage_python.mts";
import { argv, capture, fileBindings, projectRef, recorded, requestedTimeout, upstream, verifyBindings, type Operation } from "./evidence.mts";
import { green, matchTddCase, red } from "./tdd.mts";
import { Blocked, Project, acceptanceId, argsFor, compareText, digest, encoded, equal, requireText, resolvedFile, runCli, stdinJson, token, type RecordData } from "./records.mts";

function stableCaseId(name: string) {
  const urlNamespace = Buffer.from("6ba7b8119dad11d180b400c04fd430c8", "hex");
  const hash = createHash("sha1").update(urlNamespace).update(name).digest();
  hash[6] = (hash[6] & 0x0f) | 0x50;
  hash[8] = (hash[8] & 0x3f) | 0x80;
  const hex = hash.subarray(0, 16).toString("hex");
  return "TC-" + [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}
export function match(project: Project, id: string, request: RecordData, definitions: RecordData) {
  const design = currentDesigns(project, id, request.definitions_sha256).get(request.scene);
  const identified = design?.[0].case_identity === "design" ? design[0].cases : null;
  let obligations: RecordData, kinds: Map<string, string> | undefined;
  if (design) {
    const [planned, reference] = design;
    if (request.case_design !== reference.id || Object.hasOwn(request, "obligations")) {
      throw new Blocked("match against the current case design instead of redefining its obligations");
    }
    obligations = planned.obligations;
    kinds = new Map(Object.values<RecordData>(planned.cases).flatMap(item =>
      item.checks.map((check: RecordData) => [check.id, item.kind] as const)));
  } else {
    if (request.case_design) throw new Blocked("no current case design for this test scenario");
    obligations = sceneObligations(definitions, request.scene, request.obligations);
  }
  const inspected = request.inspected, names = Object.keys(inspected);
  const bindings = names.length ? fileBindings(project, names) : {};
  if (!equal(inspected, bindings)) throw new Blocked("inspected test files changed before publishing");
  const cases = new Map<string, RecordData>(), supported = new Set<string>();
  for (const entry of request.cases) {
    const source = project.verifiedSource(entry.source);
    if (source.kind !== "file" || bindings[source.path] !== source.sha256) throw new Blocked("case source must be among the inspected files");
    const selector = requireText(entry.selector, "test selector"), status = entry.status, kind = entry.kind ?? "automated";
    if (!["active", "skipped"].includes(status)) throw new Blocked("case status must be active or skipped");
    if (!["automated", "manual"].includes(kind)) throw new Blocked("case kind must be automated or manual");
    const designedId = identified ? token(entry.case_id, "designed case id") : undefined;
    const planned = identified && designedId && Object.hasOwn(identified, designedId) ? identified[designedId] : undefined;
    if (identified && planned?.kind !== kind) throw new Blocked("use a current designed case id and its verification kind");
    const assigned = new Set(planned?.checks.map((check: RecordData) => check.id));
    const references = Object.fromEntries(Object.entries(entry.covers).map(([obligation, assertion]) => {
      if (!Object.hasOwn(obligations, obligation) || !source.text.includes(requireText(assertion, "case assertion quote"))) {
        throw new Blocked("case coverage must point to exact source and known obligation");
      }
      if (kinds && kinds.get(obligation) !== kind) throw new Blocked("implementation must match an assigned check and its verification kind");
      if (planned && !assigned.has(obligation)) throw new Blocked("implementation can only cover checks assigned to its designed case id");
      return [obligation, assertion];
    }));
    const before = source.first - 1;
    const markerText = (before ? project.source(source.path, before, before).text : "") + source.text;
    const markers = new Set(Array.from(markerText.matchAll(/@case-id:\s*(TC-[A-Za-z0-9_-]+)/g), match => match[1]));
    if (markers.size > 1) throw new Blocked("case selection spans multiple permanent case ids");
    let identity = markers.values().next().value, provenance;
    if (Object.hasOwn(entry, "tdd_case")) {
      if (kind !== "automated") throw new Blocked("TDD identity applies to an automated test");
      const [priorId, receipt] = matchTddCase(project, id, definitions, request.scene, source, entry.tdd_case);
      if (identity && identity !== priorId) throw new Blocked("test source marker conflicts with the TDD case id");
      if (designedId && designedId !== priorId) throw new Blocked("use the original TDD case id in the case design");
      identity = priorId;
      provenance = receipt;
    }
    identity = designedId ?? identity ?? stableCaseId(source.path + ":" + selector);
    if (cases.has(identity)) throw new Blocked("duplicate case id: " + identity);
    cases.set(identity, { source, selector, status, covers: references,
      ...(provenance ? { tdd_case: provenance } : {}), ...(kind === "manual" ? { kind } : {}) });
    if (status === "active") for (const name of Object.keys(references)) supported.add(name);
  }
  const missing = Object.keys(obligations).filter(key => !supported.has(key)).sort(compareText);
  return { status: "matched", coverage: !missing.length ? "full" : supported.size ? "partial" : "none", execution_status: "not_executed",
    obligations, cases: Object.fromEntries(cases), missing, bindings, search_scope: requireText(request.search_scope, "search scope"),
    ...(identified ? { implementation_sha256: Object.fromEntries([...cases].map(([key, item]) =>
      [key, digest(encoded(item))])) } : {}),
    ...(design ? { case_design: design[1] } : {}) };
}
export async function runtime(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const rebind = request.rebind ? upstream(project, id, request, "rebind", "rebind", "bound") : null;
  const cwd = rebind?.cwd ?? ".", bindings = { ...rebind?.bindings, ...fileBindings(project, request.files) };
  const run = await capture(project, request.command, path, "smoke", requestedTimeout(request), undefined, cwd);
  verifyBindings(project, bindings);
  return { status: run.exit_code === 0 ? "ready" : "blocked", bindings, artifacts: run.output,
    smoke: run, cwd, revision: rebind?.revision ?? null };
}
function* pythonFiles(directory: string): Generator<string> {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (extname(path) === ".py") yield path;
    if (entry.isDirectory()) yield* pythonFiles(path);
  }
}
export async function coverage(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const preparation = upstream(project, id, request, "runtime", "runtime", "ready"), cwd = preparation.cwd ?? ".";
  const observable = request.observe, folder = join(path, "coverage");
  if (Object.hasOwn(request, "adapter")) {
    const adapter = request.adapter, bindings = { ...preparation.bindings, ...fileBindings(project, adapter.files) };
    if (!Object.hasOwn(bindings, observable.path)) throw new Blocked("product probe must point to a bound project file");
    const [result, commands] = await projectMeasure(project, folder, request.command, adapter, observable, requestedTimeout(request), undefined, cwd);
    verifyBindings(project, bindings);
    const ready = result && commands.run.exit_code === 0;
    return { status: ready ? "ready" : "blocked", reason: ready ? null : "project coverage tool did not verify actual product execution",
      bindings, commands, observe: observable, adapter, cwd,
      ...(result ? { coverage_report: result.path, coverage_sha256: result.sha256, tool_version: result.tool } : {}) };
  }
  const roots: string[] = request.source_roots;
  if (!Array.isArray(roots) || !roots.length) throw new Blocked("declare project source directories");
  const product = project.path(observable.path);
  const files = roots.flatMap(root => [...pythonFiles(project.path(root))]
    .map(file => projectRef(project, file)));
  if (!files.includes(observable.path)) throw new Blocked("probe observation must name a product file within the bound scope");
  const command = argv(request.command);
  if (command.length < 2 || !["python", "python3"].includes(basename(command[0]).split(".")[0])) {
    throw new Blocked("this Python coverage adapter requires python <script> [args]");
  }
  const probe = projectRef(project, project.path(cwd, command[1]));
  const bindings = { ...preparation.bindings, ...fileBindings(project, [...new Set([...files, probe])].sort(compareText)) };
  const [report, commands] = await measurePython(project, folder, command[0], command[1], command.slice(2), roots, requestedTimeout(request), undefined, cwd);
  verifyBindings(project, bindings);
  if (!report) return { status: "blocked", reason: "coverage.py could not produce a native report", commands, bindings };
  const observed = Object.entries<RecordData>(report.files).find(([name]) => resolvedFile(project.path(cwd), name) === product)?.[1];
  const ready = report.meta.branch_coverage && commands.run.exit_code === 0 && observed?.executed_lines.includes(observable.line);
  const output = join(folder, "coverage.json");
  return { status: ready ? "ready" : "blocked",
    reason: ready ? null : "probe did not complete successfully and observe the required product line",
    bindings, commands, observe: observable, source_roots: roots, python: command[0], tool_version: report.meta.version,
    probe: command, cwd, coverage_report: projectRef(project, output), coverage_sha256: digest(readFileSync(output)) };
}

if (import.meta.main) await runCli(async () => {
  const { RECORDED } = await import("./lifecycle.mts");
  const operations: Record<string, Operation> = { "case-design": plan, match, runtime, coverage, debug, execute, review,
    history, manual, "coverage-recompute": recompute, "tdd-red": red, "tdd-green": green, ...RECORDED };
  const args = argsFor(Object.fromEntries(Object.keys(operations).sort(compareText).map(name => [name, { args: ["id"] }])));
  if (!args) return;
  return recorded(new Project(args.root), acceptanceId(args.values[0]), args.operation, stdinJson(), operations[args.operation]);
});
