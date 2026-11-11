/** Record a reviewed red test and rerun exactly that test on changed product. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireCurrentDesign } from "./case_design.mts";
import { boundAttempt, coveragePolicy, evidenceFiles, runCases } from "./casework.mts";
import { argv, capture, fileBindings, requestedTimeout, upstream, verifyBindings } from "./evidence.mts";
import { Blocked, Project, acceptanceId, atomicWrite, equal, token, type RecordData } from "./records.mts";

export function matchTddCase(project: Project, id: string, definitions: RecordData, scene: string, source: RecordData, reference: RecordData) {
  const origin = acceptanceId(reference.acceptance_id ?? id), caseId = token(reference.case_id, "TDD case id");
  const [receipt, sha] = boundAttempt(project, origin, "tdd-green", reference.green, "green_observed");
  const priorScene = project.versions(origin).find(([, digest]) => digest === receipt.definitions_sha256)?.[2].scenarios[scene];
  const item = receipt.cases[caseId];
  if (receipt.acceptance_id !== origin || receipt.scene !== scene || !equal(priorScene, definitions.scenarios[scene]) ||
      !item || !item.files.includes(source.path) || receipt.test_bindings[source.path] !== source.sha256) {
    throw new Blocked("TDD case needs a matching scene, test file, and current test bytes");
  }
  if (item.source && !equal(item.source, source)) throw new Blocked("selected declaration differs from the TDD case source");
  verifyBindings(project, receipt.artifacts);
  return [caseId, { record: receipt.record, sha256: sha }] as const;
}
function freshPythonEnvironment(path: string) {
  // A test interpreter must not reuse a same-size, same-second product revision.
  return { PYTHONPYCACHEPREFIX: join(path, "python-cache"), PYTHONDONTWRITEBYTECODE: "1" };
}
async function run(project: Project, request: RecordData, path: string, runtime: RecordData, coverage: RecordData | null,
    product: RecordData, cases: RecordData, tests: RecordData) {
  const bindings = { ...runtime.bindings, ...coverage?.bindings, ...product, ...tests }, cwd = runtime.cwd ?? ".";
  if (coverage && (coverage.cwd ?? ".") !== cwd) throw new Blocked("runtime and coverage must bind the same checkout");
  for (const name of Object.keys(tests)) atomicWrite(join(path, "source", name), readFileSync(project.path(name)));
  const results = await runCases(project, request, path, cases, coverage, cwd, freshPythonEnvironment(path));
  verifyBindings(project, bindings);
  return { bindings, product_bindings: product, test_bindings: tests, cases, results, obligations: {}, cwd, revision: runtime.revision ?? null };
}
export async function red(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const runtime = upstream(project, id, request, "runtime", "runtime", "ready"), coverage = coveragePolicy(project, id, request);
  const product = fileBindings(project, request.product_files), caseId = token(request.case_id, "case id");
  if (!caseId.startsWith("TC-")) throw new Blocked("case ids must start with TC-");
  const item: RecordData = { command: argv(request.command), files: request.test_files };
  const tests = fileBindings(project, item.files);
  let caseDesign, implementationSha;
  if (request.match) {
    const matched = upstream(project, id, request, "match", "match", "matched"), implementation = matched.cases[caseId];
    if (matched.scene !== request.scene || implementation?.status !== "active" || implementation.kind === "manual" ||
        !Object.keys(implementation.covers).length || !Object.hasOwn(tests, implementation.source.path)) {
      throw new Blocked("TDD red needs a matched automated implementation of the scene checks");
    }
    Object.assign(item, { source: implementation.source, covers: implementation.covers });
    caseDesign = matched.case_design;
    implementationSha = matched.implementation_sha256;
  }
  requireCurrentDesign(project, id, request, caseDesign);
  if (Object.hasOwn(request, "source")) {
    const source = project.verifiedSource(request.source);
    if (source.kind !== "file" || tests[source.path] !== source.sha256 || !equal(source, item.source ?? source)) {
      throw new Blocked("TDD case source must be among the bound test files");
    }
    item.source = source;
  }
  if (Object.keys(product).some(name => Object.hasOwn(tests, name))) throw new Blocked("product and test files must be distinct");
  const result = await run(project, request, path, runtime, coverage, product, { [caseId]: item }, tests);
  const observed = result.results[caseId], code = observed.execution?.exit_code;
  const failed = Number.isInteger(code) && code > 0 && observed.coverage.status !== "unavailable";
  return { ...result, status: failed ? "red_observed" : "blocked",
    reason: failed ? null : "expected a completed failing test with required evidence", artifacts: evidenceFiles(project, path),
    ...(implementationSha ? { implementation_sha256: implementationSha } : {}),
    ...(caseDesign ? { case_design: caseDesign } : {}) };
}
export async function green(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const review = upstream(project, id, request, "red_review", "review", "reviewed", false);
  if (review.target_operation !== "tdd-red" || review.scene !== request.scene) throw new Blocked("green needs a reviewed red attempt for the same scene");
  const [original, sha] = boundAttempt(project, id, "tdd-red", review.target, "red_observed");
  if (sha !== review.target_sha256 || Object.values<RecordData>(review.judgments).some(row => row.status !== "expected_failure")) {
    throw new Blocked("green needs a Reviewer-confirmed behavioral failure");
  }
  const caseDesign = original.case_design;
  requireCurrentDesign(project, id, request, caseDesign);
  verifyBindings(project, original.artifacts);
  verifyBindings(project, original.test_bindings);
  const runtime = upstream(project, id, request, "runtime", "runtime", "ready"), coverage = coveragePolicy(project, id, request);
  if ((runtime.cwd ?? ".") !== original.cwd) throw new Blocked("red and green must run in the same candidate workspace");
  const product = fileBindings(project, Object.keys(original.product_bindings));
  if (equal(product, original.product_bindings)) throw new Blocked("product files have not changed since red");
  const result = await run(project, request, path, runtime, coverage, product, original.cases, original.test_bindings);
  const regression = await capture(project, argv(request.regression_command), path, "regression", requestedTimeout(request),
    { ...process.env, ...freshPythonEnvironment(path) }, result.cwd);
  verifyBindings(project, result.bindings);
  const passed = regression.exit_code === 0 && Object.values<RecordData>(result.results).every(row =>
    row.execution?.exit_code === 0 && row.coverage.status !== "unavailable");
  return { ...result, status: passed ? "green_observed" : "blocked", reason: passed ? null : "the original test or project regression did not pass",
    red: original.request_id, red_review: review.request_id, regression, artifacts: evidenceFiles(project, path),
    ...(original.implementation_sha256 ? { implementation_sha256: original.implementation_sha256 } : {}),
    ...(caseDesign ? { case_design: caseDesign } : {}) };
}
