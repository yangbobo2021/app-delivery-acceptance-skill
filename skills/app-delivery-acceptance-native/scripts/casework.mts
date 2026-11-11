/** Debug, formal execution and review records bound to actual test versions. */
import { mkdirSync, readFileSync, readdirSync } from "node:fs";
import { basename, dirname, extname, join, relative } from "node:path";
import { caseMatches, currentCaseImplementation, requireCurrentDesign } from "./case_design.mts";
import { projectMeasure } from "./coverage_adapter.mts";
import { measurePython } from "./coverage_python.mts";
import { argv, attempts, capture, fileBindings, local, projectRef, requestedTimeout, upstream, verifyBindings, verifyCheckout } from "./evidence.mts";
import {
  Blocked, Project, atomicWrite, compareText, digest, equal, parseJson, readJson, requireText, resolvedFile, stat, token, type RecordData,
} from "./records.mts";

const JUDGMENTS = new Set(["passed", "needs_revision", "product_defect", "definition_conflict", "insufficient_evidence"]);

export function* projectFiles(project: Project, directory: string): Generator<string> {
  for (const name of readdirSync(directory).sort(compareText)) {
    const path = project.path(projectRef(project, join(directory, name)));
    const info = stat(path);
    if (info?.isDirectory()) yield* projectFiles(project, path);
    else if (info?.isFile()) yield path;
    else throw new Blocked("evidence must be an ordinary file");
  }
}
export function evidenceFiles(project: Project, directory: string) {
  const names: string[] = [];
  let size = 0, images = 0;
  for (const path of projectFiles(project, directory)) {
    size += stat(path)!.size;
    if ([".png", ".jpg", ".jpeg", ".webp"].includes(extname(path).toLowerCase())) images++;
    if (size > 25 * 1024 * 1024 || images > 64) throw new Blocked("evidence exceeds the per-attempt byte or image limit");
    names.push(projectRef(project, path));
  }
  return names.length ? fileBindings(project, names) : {};
}
export function coveragePolicy(project: Project, id: string, request: RecordData) {
  const policy = Object.hasOwn(request, "coverage_policy") ? request.coverage_policy : "required";
  if (policy === "off") {
    requireText(request.coverage_basis, "authorization to omit coverage");
    return null;
  }
  if (policy !== "required") throw new Blocked("coverage policy must be required or off");
  const ready = upstream(project, id, request, "coverage", "coverage", "ready");
  verifyBindings(project, { [ready.coverage_report]: ready.coverage_sha256 });
  return ready;
}

export async function runCases(
  project: Project, request: RecordData, path: string, cases: RecordData, coverage: RecordData | null,
  cwd = ".", extraEnv?: NodeJS.ProcessEnv,
) {
  const results = new Map<string, RecordData>();
  for (const [id, item] of Object.entries<RecordData>(cases)) {
    const command = argv(item.command), folder = join(path, id), output = join(folder, "attachments");
    mkdirSync(folder, { recursive: true });
    mkdirSync(output);
    const env = { ...extraEnv, ACCEPTANCE_EVIDENCE_DIR: output };
    const limit = requestedTimeout(request);
    let measured: RecordData = { status: "off" }, actual;
    if (coverage && Object.hasOwn(coverage, "adapter")) {
      const [report, tools] = await projectMeasure(project, join(folder, "coverage"), command, coverage.adapter, coverage.observe, limit, env, cwd);
      actual = tools.run;
      measured = { status: report?.tool === coverage.tool_version ? "measured" : "unavailable",
        commands: tools, report: report?.path ?? null, statistics: report?.metrics ?? null, tool: report?.tool ?? null };
    } else if (coverage) {
      if (command.length < 2 || command[0] !== coverage.python) {
        throw new Blocked("case command must use the actual coverage-ready Python and a project script");
      }
      if (!item.files.includes(projectRef(project, project.path(cwd, command[1])))) {
        throw new Blocked("Python entry must be among this case's bound test files");
      }
      const [report, tools] = await measurePython(project, join(folder, "coverage"), command[0], command[1],
        command.slice(2), coverage.source_roots, limit, env, cwd);
      actual = tools.run;
      const observed = project.path(coverage.observe.path);
      const valid = report && report.meta.version === coverage.tool_version && report.meta.branch_coverage &&
        Object.entries<RecordData>(report.files).some(([name, detail]) => detail.executed_lines.length &&
          resolvedFile(project.path(cwd), name) === observed);
      measured = { status: valid ? "measured" : "unavailable", commands: tools,
        report: report ? projectRef(project, join(folder, "coverage", "coverage.json")) : null };
    } else {
      actual = await capture(project, command, folder, "run", limit, { ...process.env, ...env }, cwd);
    }
    results.set(id, { business_command: command, execution: actual ?? null, coverage: measured, attachments: evidenceFiles(project, output) });
  }
  return Object.fromEntries(results);
}

export async function debug(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const matched = upstream(project, id, request, "match", "match", "matched", false);
  const runtime = upstream(project, id, request, "runtime", "runtime", "ready");
  const coverage = coveragePolicy(project, id, request);
  if (matched.scene !== request.scene) throw new Blocked("case matching belongs to a different scene");
  const caseDesign = matched.case_design;
  requireCurrentDesign(project, id, request, caseDesign);
  if (caseDesign) verifyBindings(project, matched.bindings);
  const cases = request.cases;
  if (!cases || typeof cases !== "object" || Array.isArray(cases) || !Object.keys(cases).length) {
    throw new Blocked("supply actual selected case ids and their individual commands");
  }
  const baseline = request.purpose === "tdd-baseline";
  if (baseline) {
    verifyBindings(project, matched.bindings);
    if (matched.coverage !== "full" || Object.keys(cases).some(name => matched.cases[name]?.status !== "active")) {
      throw new Blocked("TDD baseline needs full matching and active matched cases for the scene");
    }
  }
  for (const [name, item] of Object.entries<RecordData>(cases)) {
    if (!token(name, "case id").startsWith("TC-")) throw new Blocked("case ids must start with TC-");
    const match = matched.cases[name];
    if (match?.kind === "manual") throw new Blocked("a manual verification item must be recorded as an observation");
    if (!Array.isArray(item.files) || !item.files.length) throw new Blocked("each case must identify its test files");
    if (caseDesign && (match?.status !== "active" || !Object.keys(match.covers).length)) {
      throw new Blocked("each selected implementation must map to its designed checks");
    }
    if ((baseline || caseDesign) && !item.files.includes(match.source.path)) throw new Blocked("bind the matched test source in the candidate");
    argv(item.command);
  }
  const candidate = fileBindings(project, [...new Set(Object.values<RecordData>(cases).flatMap(item => item.files as string[]))].sort(compareText));
  const bindings = { ...runtime.bindings, ...coverage?.bindings, ...candidate };
  for (const file of Object.keys(candidate)) atomicWrite(join(path, "source", file), readFileSync(project.path(file)));
  const cwd = runtime.cwd ?? ".";
  if (coverage && (coverage.cwd ?? ".") !== cwd) throw new Blocked("runtime and coverage must bind the same checkout");
  const results = await runCases(project, request, path, cases, coverage, cwd);
  verifyBindings(project, bindings);
  return { status: "debugged", cases, results, bindings, ...(baseline ? { purpose: "tdd-baseline" } : {}),
    coverage_preparation: coverage?.request_id ?? null, coverage_basis: request.coverage_basis ?? null,
    case_obligations: Object.fromEntries(Object.keys(cases).filter(name => Object.hasOwn(matched.cases, name))
      .map(name => [name, Object.keys(matched.cases[name].covers).sort(compareText)])),
    obligations: matched.obligations, cwd, revision: runtime.revision ?? null, artifacts: evidenceFiles(project, path),
    ...(matched.implementation_sha256 ? { implementation_sha256: matched.implementation_sha256 } : {}),
    ...(caseDesign ? { case_design: caseDesign } : {}) };
}
export function boundAttempt(project: Project, id: string, section: string, key: string, expected: string): [RecordData, string] {
  const raw = readFileSync(local(project, id, section, token(key, section + " attempt id"), "result.json"));
  const record = parseJson(raw);
  if (record.operation !== section || record.status !== expected) throw new Blocked("invalid " + section + " attempt");
  return [record, digest(raw)];
}
export function caseObligations(project: Project, id: string, target: RecordData): RecordData {
  if (Object.hasOwn(target, "case_obligations")) return target.case_obligations;
  if (target.operation === "execute") {
    const design = upstream(project, id, target, "design_review", "review", "reviewed");
    return Object.fromEntries(Object.entries<RecordData>(design.judgments).map(([name, finding]) => [name, finding.covers ?? []]));
  }
  if (["debug", "manual"].includes(target.operation)) {
    const raw = readFileSync(local(project, id, target.operation, target.request_id, "request.json"));
    if (digest(raw) !== target.request_sha256) throw new Blocked("attempt request differs from its recorded inputs");
    const request = parseJson(raw);
    if (request.match) {
      const matched = upstream(project, id, request, "match", "match", "matched", false);
      return Object.fromEntries(Object.entries<RecordData>(matched.cases).map(([name, item]) => [name, Object.keys(item.covers).sort(compareText)]));
    }
  }
  return {};
}
export function coverageWithin(covers: unknown, allowed: RecordData | string[]): covers is string[] {
  return Array.isArray(covers) && covers.every(item => typeof item === "string" &&
    (Array.isArray(allowed) ? allowed.includes(item) : Object.hasOwn(allowed, item)));
}
export function designAllowsExecution(finding: RecordData) {
  return finding.status === "passed" || (finding.status === "product_defect" && finding.design_status === "valid");
}
export function review(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const section = request.target_operation;
  const expected: RecordData = { debug: "debugged", execute: "executed", history: "imported", manual: "observed",
    "tdd-red": "red_observed", "tdd-green": "green_observed" };
  if (!Object.hasOwn(expected, section)) throw new Blocked("review target must be a real execution or debugging attempt");
  const [target, sha] = boundAttempt(project, id, section, request.target, expected[section]);
  if (sha !== request.target_sha256 || target.scene !== request.scene || target.definitions_sha256 !== request.definitions_sha256) {
    throw new Blocked("review does not refer to this scene, current definitions and exact attempt");
  }
  if (section !== "history") requireCurrentDesign(project, id, request, target.case_design);
  if (Object.keys(target.bindings).length) verifyBindings(project, target.bindings);
  verifyCheckout(project, target);
  verifyBindings(project, target.artifacts);
  const reviewer = requireText(request.reviewer_session, "independent Reviewer session id");
  if (reviewer === request.author_session) throw new Blocked("reviewer must have a separate session");
  let raw: Buffer;
  if (Object.hasOwn(request, "review_file")) {
    const expectedFile = local(project, id, "review-inbox", token(request.request_id, "review id") + ".json");
    const received = project.path(request.review_file);
    if (received !== expectedFile || stat(received)!.size > 256 * 1024) throw new Blocked("review_file must be the bounded output path assigned to this Reviewer");
    raw = readFileSync(received);
  } else raw = Buffer.from(requireText(request.raw_review, "verbatim Reviewer response"));
  const reviewFile = join(path, "reviewer.txt");
  atomicWrite(reviewFile, raw);
  const judgments = parseJson(raw).cases;
  if (!equal(Object.keys(judgments).sort(compareText), Object.keys(target.results).sort(compareText))) {
    throw new Blocked("Reviewer must return a judgment for every actual case");
  }
  const assigned = caseObligations(project, id, target);
  for (const [key, finding] of Object.entries<RecordData>(judgments)) {
    requireText(finding.reason, "review reason");
    const status = finding.status, covers = Object.hasOwn(finding, "covers") ? finding.covers : [];
    if (!JUDGMENTS.has(status) && !(section === "tdd-red" && status === "expected_failure")) throw new Blocked("unrecognized review judgment");
    if (!coverageWithin(covers, target.obligations ?? {})) throw new Blocked("review coverage must reference declared scene obligations");
    if (Object.hasOwn(assigned, key) && !coverageWithin(covers, assigned[key])) {
      throw new Blocked("review coverage exceeds assigned obligations for " + key + "; request a corrected independent opinion");
    }
    if (section === "debug" && target.case_design) {
      if (status === "needs_revision" && !["scene", "design", "implementation"].includes(finding.revision_target)) {
        throw new Blocked("name the scene, design or implementation that needs revision");
      }
      if ((status === "passed" || finding.design_status === "valid") &&
          !equal([...new Set(covers)].sort(compareText), [...new Set<string>(assigned[key])].sort(compareText))) {
        throw new Blocked("a passing implementation review must confirm every assigned check");
      }
    }
    if (finding.design_status != null && (section !== "debug" || status !== "product_defect" || finding.design_status !== "valid")) {
      throw new Blocked("design_status valid is only used for independently confirmed product defects during debug");
    }
    const result = target.results[key];
    if (status === "passed" && ["debug", "execute", "tdd-green"].includes(section) &&
        (result.execution?.exit_code !== 0 || result.coverage.status === "unavailable")) {
      throw new Blocked("a failed execution or missing required coverage cannot pass");
    }
    if (status === "passed" && section === "manual" && result.operations.some((item: RecordData) => item.outcome !== "observed_success")) {
      throw new Blocked("failed or uncertain manual observations cannot pass");
    }
  }
  return { status: "reviewed", judgments, reviewer_session: reviewer, target_operation: section, target: target.request_id,
    target_sha256: sha, bindings: target.bindings, artifacts: evidenceFiles(project, path),
    cwd: target.cwd ?? ".", revision: target.revision ?? null, reviewer_response: projectRef(project, reviewFile) };
}

export async function execute(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const assessment = upstream(project, id, request, "design_review", "review", "reviewed");
  if (assessment.target_operation !== "debug" || assessment.scene !== request.scene) {
    throw new Blocked("formal execution needs an independent design review of this scene");
  }
  const [design, sha] = boundAttempt(project, id, "debug", assessment.target, "debugged");
  if (sha !== assessment.target_sha256) throw new Blocked("reviewed design evidence changed");
  const caseDesign = design.case_design;
  const currentDesign = requireCurrentDesign(project, id, request, caseDesign);
  const selected: string[] = request.selected_case_ids;
  if (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length) {
    throw new Blocked("provide distinct case ids for this run");
  }
  if (selected.some(key => !designAllowsExecution(assessment.judgments[key] ?? {}))) throw new Blocked("selected cases lack a passing design review");
  if (currentDesign?.[0].case_identity === "design") {
    const matches = attempts(project, id, "match").map(([row]) => row);
    const { current } = caseMatches(project, matches, request.scene, request.definitions_sha256, currentDesign);
    const stale = selected.filter(caseId => !currentCaseImplementation(current, design, caseId));
    if (stale.length) {
      throw new Blocked(`no current reviewed implementation for ${stale.join(", ")}; match the current implementation, then debug and review it before executing`);
    }
  }
  verifyBindings(project, design.bindings);
  verifyBindings(project, design.artifacts);
  const coverage = design.coverage_preparation ? coveragePolicy(project, id, { ...request, coverage: design.coverage_preparation }) : null;
  const cases = Object.fromEntries(selected.map(key => [key, design.cases[key]]));
  const results = await runCases(project, request, path, cases, coverage, design.cwd ?? ".");
  verifyBindings(project, design.bindings);
  const designed = Object.keys(design.cases).sort(compareText);
  return { status: "executed", results, design_review: assessment.request_id,
    case_obligations: Object.fromEntries(selected.map(name => [name, assessment.judgments[name].covers ?? []])),
    obligations: design.obligations ?? {}, designed_case_ids: designed,
    selection: selected.length === designed.length ? "all_designed_cases" : "selected_cases_only",
    bindings: design.bindings, artifacts: evidenceFiles(project, path), cwd: design.cwd ?? ".", revision: design.revision ?? null,
    ...(design.implementation_sha256 ? { implementation_sha256: design.implementation_sha256 } : {}),
    ...(caseDesign ? { case_design: caseDesign } : {}) };
}

export function history(project: Project, _id: string, request: RecordData, _definitions: RecordData, path: string) {
  const source = project.path(request.receipt), receipt = readJson(source), folder = dirname(source);
  const files = new Map(Object.entries<string>(receipt.inputs_sha256).map(([name, sha]) =>
    [project.path(projectRef(project, folder), "inputs", name), sha]));
  for (const name of ["stdout", "stderr"]) files.set(join(folder, name + ".txt"), receipt[name + "_sha256"]);
  const attachments: RecordData = {};
  for (const [original, sha] of files) {
    const raw = readFileSync(project.path(projectRef(project, original)));
    if (digest(raw) !== sha) throw new Blocked("historical evidence does not match its original receipt");
    const target = join(path, "original", relative(folder, original));
    atomicWrite(target, raw);
    if (["stdout.txt", "stderr.txt"].includes(basename(original))) attachments[projectRef(project, target)] = sha;
  }
  atomicWrite(join(path, "original", "receipt.json"), readFileSync(source));
  const name = token(request.case_id, "historical case id");
  return { status: "imported", bindings: {}, artifacts: evidenceFiles(project, path),
    results: { [name]: { execution: receipt, coverage: { status: "unknown" }, attachments } }, selection: "historical" };
}

export function manual(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const cases = request.cases;
  if (!cases || typeof cases !== "object" || Array.isArray(cases) || !Object.keys(cases).length) {
    throw new Blocked("manual evidence needs actual case observations");
  }
  const productVersion = requireText(request.product_version, "observed product version");
  let obligations = {}, planned: RecordData = {}, caseDesign, implementationSha;
  if (request.match) {
    const matched = upstream(project, id, request, "match", "match", "matched");
    if (matched.scene !== request.scene) throw new Blocked("manual matching belongs to a different scene");
    obligations = matched.obligations;
    planned = Object.fromEntries(Object.entries<RecordData>(matched.cases).filter(([, item]) => item.kind === "manual"));
    caseDesign = matched.case_design;
    implementationSha = matched.implementation_sha256;
  }
  requireCurrentDesign(project, id, request, caseDesign);
  if (request.match && Object.keys(cases).some(name => !Object.hasOwn(planned, name) || planned[name].status !== "active")) {
    throw new Blocked("manual observation must use an active verification item from the scene");
  }
  if (Object.keys(planned).length && !request.product_files?.length) throw new Blocked("manual verification needs the files of the observed product version");
  const bindings = request.product_files?.length ? fileBindings(project, request.product_files) : {};
  const results = Object.fromEntries(Object.entries<RecordData[]>(cases).map(([caseId, observations]) => {
    if (!token(caseId, "manual case id").startsWith("TC-") || !observations?.length) throw new Blocked("manual case needs a TC id and observed operations");
    const attachments: RecordData = {};
    for (const operation of observations) {
      for (const key of ["action", "expected", "observed", "performed_by", "performed_at", "receipt_reference"]) {
        requireText(operation[key], "manual " + key);
      }
      if (!["observed_success", "observed_failure", "uncertain"].includes(operation.outcome)) throw new Blocked("unknown manual observation outcome");
      for (const [name, expected] of Object.entries(operation.evidence)) {
        const source = project.path(name), raw = readFileSync(source);
        if (digest(raw) !== expected) throw new Blocked("manual evidence has changed");
        const target = join(path, caseId, "attachments", digest(name).slice(0, 16), basename(source));
        atomicWrite(target, raw);
        attachments[projectRef(project, target)] = expected;
      }
    }
    if (!Object.keys(attachments).length) throw new Blocked("manual observations require actual attachments");
    return [caseId, { execution: null, coverage: { status: "unknown" }, operations: observations, attachments }];
  }));
  if (Object.keys(bindings).length) verifyBindings(project, bindings);
  return { status: "observed", results, bindings, product_version: productVersion, selection: "selected_cases_only",
    obligations, case_obligations: Object.fromEntries(Object.keys(cases).filter(name => Object.hasOwn(planned, name))
      .map(name => [name, Object.keys(planned[name].covers).sort(compareText)])),
    artifacts: evidenceFiles(project, path),
    ...(implementationSha ? { implementation_sha256: implementationSha } : {}),
    ...(caseDesign ? { case_design: caseDesign } : {}) };
}
