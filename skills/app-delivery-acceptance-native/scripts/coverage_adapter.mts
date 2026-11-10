/** Run the project's coverage tool and its native report verifier. */
import { mkdirSync, readFileSync } from "node:fs";
import { join, parse as parsePath } from "node:path";
import { argv, capture, projectRef } from "./evidence.mts";
import { Blocked, Project, digest, parseJson, posix, stat, type RecordData } from "./records.mts";

export async function projectMeasure(
  project: Project, folder: string, command: unknown, adapter: RecordData, observable: RecordData,
  limit: number, environment?: NodeJS.ProcessEnv, cwd = ".",
): Promise<[RecordData | null, RecordData]> {
  const name = adapter.report as string;
  const parts = name.split(posix ? /\// : /[\\/]/).filter(part => part && part !== ".");
  if (parsePath(name).root || parts.includes("..") || !parts.length) {
    throw new Blocked("adapter report must be within the new evidence directory");
  }
  mkdirSync(folder, { recursive: true });
  let report = join(folder, name);
  const product = project.path(observable.path);
  const values = new Map([["{output}", folder], ["{report}", report], ["{product}", product], ["{line}", String(observable.line)]]);
  const versionArgv = argv(adapter.version), prefix = argv(adapter.prefix), verify = argv(adapter.verify);
  if (!prefix.includes("{output}") || !verify.includes("{report}") || !verify.includes("{product}")) {
    throw new Blocked("adapter must run into a fresh output directory and verify the native report and product");
  }
  const env = { ...process.env, ...environment };
  const version = await capture(project, versionArgv, folder, "version", limit, env, cwd);
  if (version.exit_code !== 0) return [null, { version }];
  const run = await capture(project, [...prefix.map(arg => values.get(arg) ?? arg), ...argv(command)], folder, "run", limit, env, cwd);
  const commands: RecordData = { version, run };
  if (!stat(report)?.isFile()) return [null, commands];
  report = project.path(projectRef(project, report));
  const raw = readFileSync(report);
  if (!raw.length) return [null, commands];
  const verified = await capture(project, verify.map(arg => values.get(arg) ?? arg), folder, "verify", limit, env, cwd);
  commands.verify = verified;
  if (verified.exit_code !== 0 || !readFileSync(report).equals(raw)) return [null, commands];
  let proof;
  try { proof = parseJson(readFileSync(join(folder, "verify.stdout"))); }
  catch (error) { if (error instanceof SyntaxError) return [null, commands]; throw error; }
  if (proof?.observed !== true || !proof.metrics || typeof proof.metrics !== "object" || Array.isArray(proof.metrics)) {
    return [null, commands];
  }
  return [{ tool: readFileSync(join(folder, "version.stdout"), "utf8").trim(),
    metrics: proof.metrics, path: projectRef(project, report), sha256: digest(raw) }, commands];
}
