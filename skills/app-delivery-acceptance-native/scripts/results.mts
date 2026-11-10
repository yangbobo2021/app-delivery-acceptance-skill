/** Inspect and export acceptance evidence without running project tests. */
import { mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { basename, dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { currentDesigns } from "./case_design.mts";
import { caseObligations, coverageWithin, designAllowsExecution } from "./casework.mts";
import { attempts, local, projectRef, requirementScope, verifyBindings, verifyCheckout } from "./evidence.mts";
import {
  Blocked, NAMESPACE, Project, SCENARIO_LIST_PREFIX, acceptanceId, argsFor, atomicWrite, compareText, digest, displayJson, encoded,
  equal, exists, inheritJsonNumbers, integer, isIntegerField, printJson, readJson, requireText, runCli, stat, stdinJson, token, type RecordData,
} from "./records.mts";

function intact(project: Project, bindings?: Record<string, string>) {
  try {
    if (bindings && Object.keys(bindings).length) verifyBindings(project, bindings);
    return true;
  } catch (error) {
    if (error instanceof Blocked || (error as NodeJS.ErrnoException).code) return false;
    throw error;
  }
}
function liveAttempt(project: Project, run: RecordData) {
  try {
    verifyCheckout(project, run);
    return intact(project, run.bindings);
  } catch (error) {
    if (error instanceof Blocked || (error as NodeJS.ErrnoException).code) return false;
    throw error;
  }
}
export function deliveryBasis(scenes: RecordData) {
  return Object.fromEntries(Object.entries<RecordData>(scenes).map(([name, { status, history, missing_obligations }]) =>
    [name, digest(encoded({ status, history, missing_obligations }))]));
}
const incompleteCase = () => ({ status: "not_executed", evidence: [], review: null, coverage: "unavailable" });

export function view(project: Project, id: string, selected: string[] | null = null, dashboard = false, phase?: string) {
  const manifest = project.manifest(id), snapshot = project.versions(id).at(-1);
  if (!snapshot) return { acceptance_id: id, manifest, definitions: null, scenes: {}, evidence: {} } as RecordData;
  const [, currentSha, current] = snapshot;
  const scenes = current.scenarios;
  if (selected !== null && (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length ||
      selected.some(name => !Object.hasOwn(scenes, name)))) {
    throw new Blocked("provide distinct IDs of recorded test scenarios");
  }
  const result: RecordData = { acceptance_id: id, manifest, definitions: { version: current.version, sha256: currentSha },
    requirements: current.requirements, scenes: {}, evidence: {} };
  const invalidated = new Set(project.receipt(snapshot, "current").scenes_needing_revalidation);
  const reviews = new Map<string, RecordData>();
  for (const [row] of attempts(project, id, "analyze")) {
    const review = row.scope_review;
    if (row.status !== "analyzed" || !review) continue;
    const requirement = review.requirement_id;
    if (Object.hasOwn(current.requirements, requirement) && equal(review.scope, requirementScope(current, requirement))) {
      reviews.set(requirement, { ...review, analysis: row.analysis, record: row.record });
    }
  }
  if (reviews.size) result.scope_reviews = Object.fromEntries(reviews);
  let execution = ["execute", "manual", "history"].flatMap(section => attempts(project, id, section));
  const debugged = dashboard ? attempts(project, id, "debug") : [];
  const development = dashboard ? ["tdd-red", "tdd-green"].flatMap(section => attempts(project, id, section)) : [];
  development.push(...debugged.filter(([run]) => run.purpose === "tdd-baseline"));
  const developmentMode = phase === "development" || (!phase && development.length > 0 && !execution.length);
  if (developmentMode) {
    result.mode = "development";
    execution = development;
  }
  execution.sort(([a], [b]) => compareText(a.recorded_at ?? "", b.recorded_at ?? "") || compareText(a.request_id, b.request_id));
  const opinions = attempts(project, id, "review").map(([row]) => row).filter(row => row.status === "reviewed");
  const matched = attempts(project, id, "match").map(([row]) => row).filter(row => row.status === "matched");
  const designs = currentDesigns(project, id, currentSha);
  for (const [scene, definition] of Object.entries<RecordData>(scenes)) {
    const fresh = new Map<string, RecordData>(), history: RecordData[] = [], preview = new Set<string>();
    const currentRuns = new Map<string, string>(), currentAttempts = new Map<string, string>(), runDetails = new Map<string, RecordData>();
    const [planned, reference] = designs.get(scene) ?? [null, null];
    let checklist = planned?.obligations ?? {}, expected = new Set<string>(), caseDesign = new Map<string, RecordData>();
    for (const match of matched) {
      if (match.scene === scene && match.definitions_sha256 === currentSha &&
          equal(match.case_design ?? null, reference) && (!planned || intact(project, match.bindings))) {
        expected = new Set(Object.keys(match.cases));
        checklist = match.obligations;
        caseDesign = new Map(Object.entries(match.cases));
      }
    }
    if (dashboard) {
      for (const [candidate, sha] of debugged.toReversed()) {
        if (candidate.scene !== scene || candidate.status !== "debugged" ||
            (candidate.purpose === "tdd-baseline") !== developmentMode || candidate.definitions_sha256 !== currentSha ||
            invalidated.has(scene) || !equal(candidate.case_design ?? null, reference) ||
            Object.keys(candidate.cases).every(name => preview.has(name)) ||
            !liveAttempt(project, candidate) || !intact(project, candidate.artifacts)) continue;
        const review = opinions.findLast(item => item.target_operation === "debug" && item.target_sha256 === sha && intact(project, item.artifacts));
        for (const [name, details] of Object.entries<RecordData>(candidate.cases)) {
          if (preview.has(name)) continue;
          const finding = review?.judgments[name] ?? null, observed = candidate.results[name].execution;
          const preparation = !finding ? "debug_pending_review" : designAllowsExecution(finding) ? "ready_for_execution" : "design_blocked";
          caseDesign.set(name, { ...caseDesign.get(name), ...details, debug: candidate.request_id, design_review: finding,
            preparation, debug_exit_code: observed?.exit_code ?? null });
          preview.add(name);
        }
      }
    }
    for (const [run, sha] of execution) {
      const baseline = run.purpose === "tdd-baseline";
      if (run.scene !== scene || !(baseline && run.status === "debugged" ||
          ["executed", "observed", "imported", "red_observed", "green_observed"].includes(run.status))) continue;
      const applicable = run.operation !== "history" && !invalidated.has(scene) && run.definitions_sha256 === currentSha &&
        equal(run.case_design ?? null, reference) && liveAttempt(project, run);
      const raw = run.artifacts, evidenceValid = intact(project, raw);
      const review = opinions.findLast(item => item.target_sha256 === sha && item.target_operation === run.operation);
      let reviewValid = !!review && intact(project, review.artifacts);
      if (applicable && reviewValid) {
        try {
          const assigned = caseObligations(project, id, run);
          reviewValid = Object.entries<RecordData>(review!.judgments).every(([name, finding]) =>
            coverageWithin(finding.covers ?? [], assigned[name] ?? run.obligations ?? {}));
        } catch { reviewValid = false; }
      }
      const runCases = new Map<string, RecordData>();
      for (const [name, item] of Object.entries<RecordData>(run.results)) {
        const receipt = item.execution, verdict = reviewValid ? review!.judgments[name] : null;
        let status = !evidenceValid ? "evidence_missing" : run.operation === "history" ? "historical" :
          !applicable ? "stale" : verdict?.status ?? "pending_review";
        if (baseline && evidenceValid && applicable) {
          status = receipt?.exit_code === 0 && item.coverage.status !== "unavailable" ? "baseline_passed" : "baseline_unverified";
        }
        const attachments = { ...item.attachments, ...receipt?.output }, coverage = item.coverage ?? {}, report = coverage.report;
        let statistics = null;
        if (report != null && Object.hasOwn(raw, report)) {
          attachments[report] = raw[report];
          if (coverage.statistics != null) {
            statistics = inheritJsonNumbers({ version: coverage.tool, ...coverage.statistics, adapter: true }, coverage.statistics);
          } else if (intact(project, { [report]: raw[report] })) {
            const native = readJson(project.path(report));
            statistics = inheritJsonNumbers({ version: native.meta.version, ...native.totals }, native.totals);
          }
        }
        if (reviewValid) {
          const response = review!.reviewer_response;
          attachments[response] = review!.artifacts[response];
        }
        const validAttachments = Object.fromEntries(Object.entries<string>(attachments).filter(([path, hash]) =>
          intact(project, { [path]: hash })));
        if (selected === null || selected.includes(scene)) Object.assign(result.evidence, validAttachments);
        runCases.set(name, { status, review: verdict, evidence: Object.keys(validAttachments).sort(compareText),
          coverage: coverage.status ?? "unavailable", exit_code: receipt?.exit_code ?? null,
          coverage_statistics: statistics, observations: item.operations ?? [] });
        if (dashboard) {
          const details: RecordData = {};
          if (receipt?.argv?.length) details.command = receipt.argv;
          if (verdict) details.review = { record: review!.record, session: review!.reviewer_session ?? null, recorded_at: review!.recorded_at ?? null };
          if (Object.keys(details).length) {
            if (!runDetails.has(run.record)) runDetails.set(run.record, {});
            runDetails.get(run.record)![name] = details;
          }
        }
      }
      history.push({ run: run.request_id, operation: baseline ? "tdd-baseline" : run.operation,
        record: run.record, recorded_at: run.recorded_at, product_version: run.product_version || run.revision || null,
        bindings: run.bindings, definition_sha256: run.definitions_sha256, selection: run.selection ?? "historical",
        cases: Object.fromEntries(runCases) });
      if (developmentMode && (!planned || applicable)) {
        for (const [name, details] of Object.entries<RecordData>(run.cases)) {
          expected.add(name);
          caseDesign.set(name, { ...caseDesign.get(name), ...details });
        }
      }
      if (applicable) {
        for (const [name, value] of runCases) {
          fresh.set(name, value);
          if (dashboard) {
            currentRuns.set(name, run.request_id);
            currentAttempts.set(name, run.record);
          }
        }
        for (const name of run.designed_case_ids ?? runCases.keys()) expected.add(name);
        if (run.obligations && Object.keys(run.obligations).length) checklist = run.obligations;
      }
    }
    for (const name of expected) if (!fresh.has(name)) fresh.set(name, incompleteCase());
    const proved = new Set([...fresh.values()].filter(item => item.status === "passed").flatMap(item => item.review.covers ?? []));
    const missing = Object.keys(checklist).filter(name => !proved.has(name)).sort(compareText);
    const complete = Object.keys(checklist).length && fresh.size && !missing.length && [...fresh.values()].every(item => item.status === "passed");
    let status = complete ? "passed" : invalidated.has(scene) ? "definition_changed" : "not_fully_validated";
    if (developmentMode) {
      const outcomes = new Set([...fresh.values()].map(item => item.status));
      status = outcomes.size === 1 && outcomes.has("baseline_passed") ? "tdd_baseline_verified" :
        outcomes.has("passed") && [...outcomes].every(value => ["passed", "baseline_passed"].includes(value)) ? "tdd_verified" : "tdd_in_progress";
    }
    for (const name of preview) if (!fresh.has(name)) fresh.set(name, incompleteCase());
    result.scenes[scene] = { definition, cases: Object.fromEntries(fresh), history, missing_obligations: missing,
      status, case_design: Object.fromEntries(caseDesign),
      ...(planned ? { case_plan: { ...reference, cases: planned.cases, gaps: planned.gaps } } : {}),
      ...(dashboard ? { current_runs: Object.fromEntries(currentRuns), current_attempts: Object.fromEntries(currentAttempts),
        run_details: Object.fromEntries(runDetails) } : {}) };
  }
  const delivery = new Map<string, [RecordData, string]>();
  if (!developmentMode) {
    const basis = deliveryBasis(result.scenes);
    for (const [row, sha] of attempts(project, id, "delivery-judge")) {
      if (row.status !== "judged" || row.definitions_sha256 !== currentSha) continue;
      const valid = row.scene_evidence != null && Object.entries(row.scene_evidence).every(([name, value]) => basis[name] === value) &&
        intact(project, row.artifacts);
      for (const req of row.requirement_ids) {
        if (valid) delivery.set(req, [row, sha]);
        else delivery.delete(req);
      }
    }
  }
  result.requirement_results = Object.fromEntries(Object.keys(current.requirements).map(requirement => {
    const ids = Object.keys(scenes).filter(name => Object.hasOwn(scenes[name].requirement_sha256, requirement));
    const states = Object.fromEntries(ids.map(name => [name, result.scenes[name]?.status ?? null]));
    const detail: RecordData = { scenes: states, scope_reviewed: reviews.has(requirement) };
    let status = "pending_delivery_review";
    const judgment = delivery.get(requirement);
    if (developmentMode) {
      status = reviews.has(requirement) && ids.length &&
        Object.values(states).every(state => ["tdd_verified", "tdd_baseline_verified"].includes(state)) ? "tdd_verified" : "not_fully_validated";
    } else if (judgment) {
      const [row, sha] = judgment;
      status = row.judgment.status;
      detail.delivery_judgment = { ...row.judgment, record: row.record, sha256: sha };
      if (selected === null || ids.some(name => selected.includes(name))) result.evidence[row.record] = sha;
    }
    return [requirement, { status, ...detail }];
  }));
  if (selected !== null) result.scenes = Object.fromEntries(selected.map(name => [name, result.scenes[name]]));
  return result;
}

const escape = (value: unknown) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#x27;");
const labels = new Map(Object.entries({
  passed: "通过", needs_revision: "需修订", not_fully_validated: "尚未完成验收", failed: "失败", blocked: "受阻",
  pending_delivery_review: "待需求交付判定", product_defect: "产品缺陷", pending_review: "待独立复审",
  definition_changed: "定义有变化", not_executed: "未执行", stale: "旧版本", evidence_missing: "证据缺失",
  historical: "导入的历史执行", definition_conflict: "定义冲突", insufficient_evidence: "证据不足", measured: "已采集",
  off: "关闭", unavailable: "未取得覆盖率", unknown: "覆盖率未知",
}));
const label = (value: string) => labels.get(value) ?? value;

function renderScenarioCriteria(criteria: string[], behavior: string) {
  const content: string[] = [];
  let currentTag;
  for (const criterion of criteria) {
    const text = criterion.trim(), marker = SCENARIO_LIST_PREFIX.exec(text);
    const value = text.slice(marker?.[0].length ?? 0);
    if (value === behavior.trim()) continue;
    const number = marker?.[1], tag = number ? "ol" : "ul";
    if (tag !== currentTag) {
      if (currentTag) content.push("</" + currentTag + ">");
      content.push("<" + tag + ">");
      currentTag = tag;
    }
    const attr = number ? ' value="' + number + '"' : "";
    content.push("<li" + attr + ">" + escape(value) + "</li>");
  }
  if (currentTag) content.push("</" + currentTag + ">");
  return content;
}
function render(snapshot: RecordData, files: Record<string, string>) {
  function evidenceLinks(item: RecordData) {
    return item.evidence.map((name: string) => '<a href="' + escape(files[name]) + '">' + escape(basename(name)) + "</a>").join(" ");
  }
  const rows = Object.entries<RecordData>(snapshot.scenes).map(([scene, detail]) => {
    const definition = detail.definition, scope = definition.scenario_scope;
    const name = scope ? scene + " " + scope.title : scene;
    const title = name + " · " + label(detail.status), reqs = Object.keys(definition.requirement_sha256).join(", ");
    const content = ["<h2>" + escape(title) + "</h2><p>关联需求 " + escape(reqs) + "</p>"];
    if (scope) {
      content.push("<h3>验证范围</h3><p>" + escape(scope.behavior) + "</p><h3>验收判据</h3>");
      for (const group of scope.groups ?? [scope]) {
        if (scope.groups) content.push("<h4>" + escape(group.title) + "</h4>");
        content.push(...renderScenarioCriteria(group.criteria, scope.behavior));
      }
    }
    content.push("<details><summary>测试场景原文</summary><pre>" + escape(definition.source.text) + "</pre></details>");
    for (const [id, item] of Object.entries<RecordData>(detail.case_plan?.cases ?? {})) {
      content.push("<details><summary>" + escape(id) + " · " + escape(item.title) + "</summary>" +
        "<p>前置条件与数据　" + escape(item.preconditions) + "</p><ul>");
      for (const step of item.checks) content.push("<li>" + escape(step.id) + "　操作 " + escape(step.action) + "　预期 " + escape(step.expected) + "</li>");
      content.push("</ul></details>");
    }
    for (const [check, reason] of Object.entries(detail.case_plan?.gaps ?? {})) {
      content.push("<p>设计缺口　" + escape(check) + " · " + escape(reason) + "</p>");
    }
    content.push("<table><thead><tr><th>用例</th><th>执行</th><th>覆盖率</th><th>独立复审</th><th>证据</th></tr></thead><tbody>");
    for (const id of Object.keys(detail.cases).sort(compareText)) {
      const item = detail.cases[id], verdict = label(item.status), reason = item.review?.reason ?? "";
      let execution = item.exit_code == null ? "无退出码" : "退出码 " + item.exit_code;
      let coverage = label(item.coverage);
      const stats = item.coverage_statistics;
      if (stats?.adapter) {
        const metrics = inheritJsonNumbers(Object.fromEntries(Object.entries(stats).filter(([key]) => !["version", "adapter"].includes(key))), stats);
        coverage = stats.version + " · " + displayJson(metrics);
      } else if (stats) {
        coverage = "coverage.py " + stats.version + " · 行 " + stats.covered_lines + "/" + stats.num_statements +
          " · 分支 " + stats.covered_branches + "/" + stats.num_branches;
      }
      if (item.observations?.length) execution = item.observations.map((op: RecordData) => op.action + "：" + op.observed).join("；");
      content.push("<tr><td>" + escape(id) + "</td><td>" + escape(execution) + "</td><td>" + escape(coverage) +
        "</td><td>" + escape(verdict) + "<br>" + escape(reason) + "</td><td>" + evidenceLinks(item) + "</td></tr>");
    }
    content.push("</tbody></table>");
    if (detail.missing_obligations.length) content.push("<p>未取得通过证据的检查项：" + escape(detail.missing_obligations.join(", ")) + "</p>");
    content.push("<details><summary>历史尝试</summary><ul>");
    for (const old of detail.history) {
      for (const [id, item] of Object.entries<RecordData>(old.cases)) {
        const verdict = label(item.status) + (item.review ? " · 复审 " + label(item.review.status) : "");
        content.push("<li>" + escape(old.run) + " · " + escape(id) + " · " + escape(verdict) + " " + evidenceLinks(item) + "</li>");
      }
    }
    content.push("</ul></details>");
    return "<section>" + content.join("") + "</section>";
  });
  const requirements = Object.keys(snapshot.requirements ?? {}).sort(compareText).map(name => {
    const item = snapshot.requirements[name], detail = snapshot.requirement_results?.[name];
    const verdict = labels.get(detail?.status) ?? "尚未完成验收", judgment = detail?.delivery_judgment;
    let review = "";
    if (judgment) {
      const record = judgment.record, link = Object.hasOwn(files, record) ?
        '<a href="' + escape(files[record]) + '">判定收据</a>' : escape(record);
      review = "<p>" + escape(judgment.reason) + " · " + link + "</p>";
    }
    return "<details><summary>" + escape(name) + " · " + escape(verdict) + "</summary><pre>" +
      escape(item.source.text) + "</pre>" + review + "</details>";
  });
  const id = escape(snapshot.acceptance_id);
  const version = snapshot.definitions ? "记录快照 v" + escape(snapshot.definitions.version) : "尚未记录需求基线";
  return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
    "<title>交付验收 · " + id + "</title><style>" +
    "body{font:16px/1.55 system-ui,sans-serif;background:#f4f6fa;color:#172135;margin:0}" +
    "main{max-width:1080px;margin:auto;padding:32px}section{background:#fff;border:1px solid #d7dfed;padding:24px;margin:24px 0}" +
    "table{width:100%;border-collapse:collapse;margin:16px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid #d7dfed;overflow-wrap:anywhere}" +
    "a{color:#164ba5;margin-right:8px}pre{white-space:pre-wrap}summary{cursor:pointer}h1,h2{line-height:1.25}" +
    "</style><main><h1>交付验收 · " + id + "</h1><p>" + version + ' · <a href="snapshot.json">结果快照</a></p>' +
    "<h2>需求基线</h2>" + requirements.join("") + rows.join("") + "</main></html>\n";
}

function exportReport(project: Project, id: string, request: RecordData) {
  const key = token(request.request_id, "report request id"), target = local(project, id, "reports", key), selection = request.scene_ids ?? null;
  return project.write(() => {
    if (exists(target)) {
      const stored = readJson(project.path(projectRef(project, join(target, "snapshot.json"))));
      if (!equal(stored.selection, selection)) throw new Blocked("report request already binds a different selection");
      const exported = Object.fromEntries(Object.entries(stored.exported_sha256).map(([name, sha]) => [projectRef(project, join(target, name)), sha]));
      if (!intact(project, exported as Record<string, string>)) throw new Blocked("saved report files changed");
    } else {
      const snapshot = view(project, id, selection);
      mkdirSync(dirname(target), { recursive: true });
      const staged = mkdtempSync(join(dirname(target), ".pending-report-"));
      try {
        const files = Object.fromEntries(Object.entries<string>(snapshot.evidence).map(([source, sha]) => {
          const raw = readFileSync(project.path(source));
          if (digest(raw) !== sha) throw new Blocked("evidence changed while exporting");
          const relative = "evidence/" + digest(source).slice(0, 16) + "/" + basename(source);
          atomicWrite(join(staged, relative), raw);
          return [source, relative];
        }));
        const report = render(snapshot, files);
        atomicWrite(join(staged, "report.html"), report);
        snapshot.selection = selection;
        snapshot.exported = files;
        snapshot.exported_sha256 = { "report.html": digest(report),
          ...Object.fromEntries(Object.entries(files).map(([source, relative]) => [relative, snapshot.evidence[source]])) };
        atomicWrite(join(staged, "snapshot.json"), encoded(snapshot));
        renameSync(staged, target);
      } finally { rmSync(staged, { recursive: true, force: true }); }
    }
    return { status: "reported", report: projectRef(project, join(target, "report.html")), snapshot: projectRef(project, join(target, "snapshot.json")) };
  });
}

const inventoryStatuses = new Set(["verified", "failed", "recheck", "untested", "unconfirmed", "source_missing", "conflict", "blocked"]);
function saveInventory(project: Project, report: RecordData) {
  const path = project.path(".acceptance-local", NAMESPACE, "inventory.json");
  requireText(report.revision, "inventory revision");
  const { scope, items } = report;
  if (!Array.isArray(scope) || !Array.isArray(items)) throw new Blocked("inventory needs a scope list and an items list");
  const expected = new Set(scope.map(item => requireText(item, "inventory requirement id")));
  if (expected.size !== scope.length || report.candidates?.length) {
    throw new Blocked("declare unique scope IDs and place all findings, including candidates, in items");
  }
  return project.write(() => {
    const ids = new Set<string>(), covered = new Set<string>();
    for (const item of items) {
      const key = requireText(item.id, "inventory item id");
      if (ids.has(key)) throw new Blocked("duplicate inventory item: " + key);
      ids.add(key);
      covered.add(requireText(item.requirement, "inventory requirement id"));
      if (!inventoryStatuses.has(item.status)) throw new Blocked("invalid inventory status: " + key);
      if (!Array.isArray(item.sources) || !item.sources.length) throw new Blocked("inventory item needs sources: " + key);
      for (const source of item.sources) requireText(source, "inventory source");
      requireText(item.next, "inventory next step");
      if (!Array.isArray(item.cases) || !Array.isArray(item.evidence)) throw new Blocked("inventory item needs cases and evidence lists: " + key);
      for (const test of item.cases) {
        requireText(test.selector, "inventory case selector");
        requireText(test.source, "inventory case source");
      }
      for (const name of item.evidence) {
        if (!stat(project.path(requireText(name, "inventory evidence path")))?.isFile() || name === projectRef(project, path)) {
          throw new Blocked("inventory evidence must be a separate existing file: " + name);
        }
      }
      if (Object.hasOwn(item, "command") || Object.hasOwn(item, "exit_code")) {
        requireText(item.command, "inventory recheck command");
        if (!isIntegerField(item, "exit_code") || !item.evidence.length) throw new Blocked("inventory recheck needs its exit code and evidence: " + key);
      }
    }
    if (!equal([...expected].sort(compareText), [...covered].sort(compareText))) {
      const describe = (names: string[]) => "[" + names.sort(compareText).map(name => "'" + name + "'").join(", ") + "]";
      throw new Blocked("inventory scope mismatch: missing " + describe([...expected].filter(name => !covered.has(name))) +
        ", extra " + describe([...covered].filter(name => !expected.has(name))));
    }
    const data = encoded(report);
    atomicWrite(path, data);
    return { status: "inventory_saved", report: projectRef(project, path), sha256: digest(data), items: items.length, scope };
  });
}
function inventorySnapshot(project: Project): RecordData {
  const path = project.path(".acceptance-local", NAMESPACE, "inventory.json");
  const inventory = stat(path)?.isFile() ? readJson(path) : null;
  if (inventory?.candidates?.length) {
    const ids = new Set(inventory.items.map((item: RecordData) => item.id));
    inventory.items = [...inventory.items, ...inventory.candidates.filter((item: RecordData) => !ids.has(item.id))
      .map((item: RecordData) => ({ ...item, sources: item.sources?.length ? item.sources : item.source ? [item.source] : [] }))];
  }
  return { mode: "inventory", inventory, evidence: {} };
}

const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/dashboard.js", ["dashboard.js", "text/javascript; charset=utf-8"]],
  ["/dashboard.css", ["dashboard.css", "text/css; charset=utf-8"]],
]);
const evidenceTypes = new Map([
  [".png", "image/png"], [".jpg", "image/jpeg"], [".jpeg", "image/jpeg"], [".webp", "image/webp"], [".json", "application/json"],
]);
const phases = ["development", "acceptance"];
async function serve(project: Project, id: string | null, port: number, phase?: string) {
  if (id !== null) project.manifest(id);
  const directory = fileURLToPath(new URL("./dashboard/", import.meta.url));
  const server = createServer((request, response) => {
    const fail = (code: number, text = "") => {
      response.writeHead(code, { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff" }).end(text);
    };
    if (request.method !== "GET") { fail(501); return; }
    try {
      const [path, query] = (request.url ?? "/").split(/\?(.*)/s, 2), asset = assets.get(path);
      let body: Buffer, content: string;
      if (asset) {
        body = readFileSync(join(directory, asset[0]));
        content = asset[1];
      } else {
        const requested = new URLSearchParams(query).getAll("phase").filter(Boolean);
        if (requested.length && (requested.length !== 1 || !phases.includes(requested[0]))) { fail(400, "Invalid acceptance phase"); return; }
        const snapshot = id === null ? inventorySnapshot(project) : view(project, id, null, true, requested[0] ?? phase);
        const endpoint = (name: string) => "/evidence/" + digest(name);
        if (path === "/snapshot.json") {
          const suffix = requested.length ? "?phase=" + requested[0] : "";
          snapshot.evidence_urls = Object.fromEntries(Object.keys(snapshot.evidence).map(name => [name, endpoint(name) + suffix]));
          body = encoded(snapshot);
          content = "application/json; charset=utf-8";
        } else {
          const original = Object.keys(snapshot.evidence).find(name => path === endpoint(name));
          if (original === undefined) { fail(404); return; }
          body = readFileSync(project.path(original));
          if (digest(body) !== snapshot.evidence[original]) { fail(409); return; }
          content = evidenceTypes.get(extname(original).toLowerCase()) ?? "text/plain; charset=utf-8";
        }
      }
      response.writeHead(200, { "Content-Type": content, "Content-Length": body.length, "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff" }).end(body);
    } catch { fail(409, "Acceptance evidence is unavailable"); }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") { reject(new Blocked("dashboard did not open a local port")); return; }
      printJson({ status: "viewing", url: "http://127.0.0.1:" + address.port + "/" });
      resolve();
    });
  });
}
if (import.meta.main) await runCli(async () => {
  const args = argsFor({
    view: { args: ["id"] }, report: { args: ["id"] }, serve: { args: ["id"] },
    "serve-inventory": { args: [] }, "save-inventory": { args: [] },
  }, { port: "value", phase: "value" });
  if (!args) return;
  const { operation, values: [id], flags } = args;
  const port = flags.port === undefined ? 0 : integer(flags.port, "port");
  if (flags.phase !== undefined && !phases.includes(flags.phase)) throw new Blocked("Invalid acceptance phase");
  if (flags.phase !== undefined && operation !== "serve") throw new Blocked("--phase is for the acceptance dashboard");
  const project = new Project(args.root);
  if (operation === "save-inventory") return saveInventory(project, stdinJson());
  if (operation === "serve-inventory") return serve(project, null, port);
  const identifier = acceptanceId(id);
  if (operation === "view") return view(project, identifier);
  if (operation === "report") return exportReport(project, identifier, stdinJson());
  return serve(project, identifier, port, flags.phase);
});
