/** Invoke coverage.py in the interpreter used by the project under test. */
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { capture, projectRef, verifyBindings } from "./evidence.mts";
import { Blocked, Project, atomicWrite, digest, equal, readJson, stat, type RecordData } from "./records.mts";

function coverageEnv(folder: string) {
  return { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("COVERAGE_"))),
    COVERAGE_RCFILE: join(folder, "coverage.ini"), COVERAGE_FILE: join(folder, ".coverage") };
}
export async function measurePython(
  project: Project, folder: string, executable: string, script: string, args: string[], roots: string[],
  limit: number, environment?: NodeJS.ProcessEnv, cwd = ".",
): Promise<[RecordData | null, RecordData]> {
  mkdirSync(folder, { recursive: true });
  const source = roots.map(name => project.path(name));
  if (source.some(path => path.includes("\n") || !stat(path)?.isDirectory())) {
    throw new Blocked("coverage source roots must be ordinary project directories");
  }
  const report = join(folder, "coverage.json");
  atomicWrite(join(folder, "coverage.ini"), "[run]\nbranch = true\npatch = subprocess\nparallel = true\nsource =\n    " + source.join("\n    ") + "\n");
  const env = { ...coverageEnv(folder), ...environment }, tool = [executable, "-m", "coverage"];
  const version = await capture(project, [...tool, "--version"], folder, "version", limit, env, cwd);
  if (version.exit_code !== 0) return [null, { version }];
  const run = await capture(project, [...tool, "run", project.path(cwd, script), ...args], folder, "run", limit, env, cwd);
  const combine = await capture(project, [...tool, "combine", "--keep"], folder, "combine", limit, env, cwd);
  if (combine.exit_code !== 0) return [null, { version, run, combine }];
  const output = await capture(project, [...tool, "json", "-o", report], folder, "export", limit, env, cwd);
  return [output.exit_code === 0 ? readJson(report) : null, { version, run, combine, export: output }];
}

export async function recompute(project: Project, id: string, request: RecordData, _definitions: RecordData, path: string) {
  const reports: RecordData[] = request.reports;
  if (!Array.isArray(reports) || !reports.length || new Set(reports.map(entry => entry.path)).size !== reports.length) {
    throw new Blocked("provide distinct original coverage reports");
  }
  let fingerprint: { version: string; config: Buffer; interpreter: string; cwd: string } | undefined;
  const allBindings = {}, selected: string[] = [];
  for (const [index, entry] of reports.entries()) {
    const receipt = readJson(project.path(entry.receipt));
    if (receipt.acceptance_id !== id || receipt.definitions_sha256 !== request.definitions_sha256 || receipt.operation !== "execute") {
      throw new Blocked("coverage recomputation needs selected executions of the same definition");
    }
    const item = Object.values<RecordData>(receipt.results).find(row => row.coverage.report === entry.path && row.coverage.status === "measured");
    if (!item || item.coverage.statistics !== undefined) throw new Blocked("report must belong to an actual measured execution");
    const cwd = item.execution.cwd ? projectRef(project, item.execution.cwd) : ".";
    verifyBindings(project, receipt.bindings);
    verifyBindings(project, receipt.artifacts);
    const file = project.path(entry.path), raw = readJson(file);
    const config = join(dirname(file), "coverage.ini"), data = join(dirname(file), ".coverage");
    const proof = { version: raw.meta.version, config: readFileSync(config), interpreter: item.coverage.commands.version.argv[0], cwd };
    if (fingerprint && (!fingerprint.config.equals(proof.config) ||
        !equal([fingerprint.version, fingerprint.interpreter, fingerprint.cwd], [proof.version, proof.interpreter, proof.cwd]))) {
      throw new Blocked("coverage tool, configuration, or interpreter differs");
    }
    fingerprint = proof;
    Object.assign(allBindings, receipt.bindings);
    for (const artifact of [file, config, data]) {
      if (digest(readFileSync(artifact)) !== receipt.artifacts[projectRef(project, artifact)]) throw new Blocked("original coverage artifact changed");
    }
    atomicWrite(join(path, ".coverage.input-" + index), readFileSync(data));
    selected.push(entry.path);
  }
  const { version, config, interpreter, cwd } = fingerprint!;
  atomicWrite(join(path, "coverage.ini"), config);
  const env = coverageEnv(path), tool = [interpreter, "-m", "coverage"];
  const check = await capture(project, [...tool, "--version"], path, "version", 30, env, cwd);
  if (check.exit_code || !readFileSync(join(path, "version.stdout"), "utf8").includes("version " + version)) {
    throw new Blocked("the original coverage.py version is unavailable");
  }
  const combine = await capture(project, [...tool, "combine", "--keep"], path, "combine", 60, env, cwd);
  if (combine.exit_code !== 0) throw new Blocked("coverage.py could not combine these data files");
  const output = join(path, "coverage.json");
  const exported = await capture(project, [...tool, "json", "-o", output], path, "export", 60, env, cwd);
  if (exported.exit_code !== 0) throw new Blocked("coverage.py did not export a native report");
  const report = projectRef(project, output);
  return { status: "recomputed", sources: selected, bindings: allBindings,
    commands: { version: check, combine, export: exported }, coverage_report: report, artifacts: { [report]: digest(readFileSync(output)) } };
}
