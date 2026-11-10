/** Natural-language case design, independent of implementation. */
import { attempts } from "./evidence.mts";
import { Blocked, Project, compareText, equal, requireText, token, type RecordData } from "./records.mts";

export function sceneObligations(definitions: RecordData, scene: string, items: RecordData[]) {
  const scenario = definitions.scenarios[scene], scope = scenario.scenario_scope;
  const sources: string[] = scope ? (scope.groups ?? [scope]).flatMap((group: RecordData) => group.criteria) : [scenario.source.text];
  const label = scope ? "a recorded scenario criterion" : "the recorded test scenario";
  const obligations = new Map<string, string>();
  for (const item of items) {
    const key = token(item.id, "obligation id"), quote = requireText(item.quote, "test scenario quote");
    if (obligations.has(key) || !sources.some(source => source.includes(quote))) {
      throw new Blocked("obligations must have unique ids and quote " + label + " exactly");
    }
    obligations.set(key, quote);
  }
  if (!obligations.size) throw new Blocked("enumerate the checks supported by the recorded test scenario");
  return Object.fromEntries(obligations);
}
export function plan(_project: Project, _id: string, request: RecordData, definitions: RecordData) {
  const obligations = sceneObligations(definitions, request.scene, request.obligations);
  const assigned = new Set<string>();
  const cases = Object.fromEntries(Object.entries<RecordData>(request.cases).map(([name, item]) => {
    token(name, "case design id");
    const { kind, checks } = item;
    if (!["automated", "manual"].includes(kind)) throw new Blocked("design kind must be automated or manual");
    if (!Array.isArray(checks) || !checks.length) throw new Blocked("supply the checks in their intended order");
    const steps = checks.map(check => {
      const id = check.id;
      if (!Object.hasOwn(obligations, id) || assigned.has(id)) {
        throw new Blocked("assign each designed check to one case using a declared obligation id");
      }
      assigned.add(id);
      const step: RecordData = { id, action: requireText(check.action, "check action and input"),
        expected: requireText(check.expected, "check expected result") };
      if (Object.hasOwn(check, "title")) step.title = requireText(check.title, "check title");
      if (Object.hasOwn(check, "setup")) {
        if (!Array.isArray(check.setup)) throw new Blocked("supply check setup as an ordered list");
        step.setup = check.setup.map((action: unknown) => requireText(action, "check setup action"));
      }
      return step;
    });
    return [name, { title: requireText(item.title, "case title"), kind,
      preconditions: requireText(item.preconditions, "case preconditions and test data"), checks: steps }];
  }));
  const missing = Object.keys(obligations).filter(key => !assigned.has(key)).sort(compareText);
  const gaps = request.gaps ?? {};
  if (!equal(Object.keys(gaps).sort(compareText), missing)) throw new Blocked("give a reason for each obligation without a case design");
  return { status: "designed", obligations, cases, missing,
    gaps: Object.fromEntries(missing.map(key => [key, requireText(gaps[key], "design gap reason")])) };
}
export function currentDesigns(project: Project, id: string, definitionsSha: string) {
  return new Map(attempts(project, id, "case-design").filter(([row]) =>
    row.status === "designed" && row.definitions_sha256 === definitionsSha).map(([row, sha]) =>
    [row.scene, [row, { id: row.request_id, record: row.record, sha256: sha }] as const]));
}
export function requireCurrentDesign(project: Project, id: string, request: RecordData, reference: RecordData | null | undefined) {
  const current = currentDesigns(project, id, request.definitions_sha256).get(request.scene);
  if (!equal(reference ?? null, current?.[1] ?? null)) throw new Blocked("use the current case design for this test scenario");
}
