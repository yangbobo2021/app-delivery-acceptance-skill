"use strict";

const labels = {
  passed: "通过", needs_revision: "需修订", not_fully_validated: "尚未完成验收",
  product_defect: "产品缺陷", pending_review: "待独立复审", definition_changed: "定义有变化",
  pending_delivery_review: "待需求交付判定",
  not_executed: "未执行", no_case_receipt: "无单独执行记录", stale: "旧版本", evidence_missing: "证据缺失", historical: "历史记录",
  definition_conflict: "定义冲突", insufficient_evidence: "证据不足", skipped: "跳过",
  measured: "已采集", off: "关闭", unavailable: "未取得覆盖率", unknown: "未核对",
  expected_failure: "已确认预期红灯", tdd_in_progress: "TDD 进行中", tdd_verified: "TDD 绿灯已复审",
  baseline_passed: "基线通过", baseline_unverified: "基线未验证", tdd_baseline_verified: "TDD 基线已验证",
  verified: "已验证", failed: "失败", recheck: "待复验", untested: "未验证", unconfirmed: "待确认",
  source_missing: "依据缺失", conflict: "依据冲突", blocked: "受阻",
};
const get = id => document.getElementById(id);
const detail = get("detail");
const tabIds = ["overview", "relations", "sources"];
const phase = new URLSearchParams(window.location.search).get("phase");
const snapshotUrl = phase === "development" || phase === "acceptance" ? "/snapshot.json?phase=" + phase : "/snapshot.json";
let current, previousPayload, lastRead, busy = false;
let reading, opener;
const stack = [];

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function status(value) {
  const badge = element("span", labels[value] ?? value, "badge");
  badge.dataset.status = value;
  return badge;
}

function key(...parts) {
  return JSON.stringify(parts);
}

function title(source, fallback) {
  const first = source?.text?.split("\n").map(line => line.trim()).find(Boolean);
  return first ? first.replace(/^(?:#{1,6}|[-*])\s+/, "").slice(0, 68) : fallback;
}

function summary(source) {
  return source?.text?.split("\n").map(line => line.trim())
    .find(line => line && !/^[#|]/.test(line))?.replace(/^[-*]\s+/, "");
}

function readSnapshot(snapshot) {
  const nodes = new Map();
  const groups = [];
  const requirements = new Map();
  function add(id, data) {
    nodes.set(id, {relations: new Set(), ...data});
    return id;
  }
  function link(left, right) {
    nodes.get(left).relations.add(right);
    nodes.get(right).relations.add(left);
  }
  for (const [id, item] of Object.entries(snapshot.requirements ?? {})) {
    const scope = snapshot.scope_reviews?.[id];
    const outcome = snapshot.requirement_results?.[id];
    const judgment = outcome?.delivery_judgment;
    requirements.set(id, add(key("requirement", id), {
      label: id, kind: "需求 / 规则", type: "requirement", title: title(item.source, id), source: item.source, scope,
      definition: snapshot.definitions,
      status: outcome?.status, reason: judgment?.reason, record: judgment?.record,
      evidence: judgment ? [judgment.record] : [],
    }));
  }
  const connected = new Set();
  for (const [id, scene] of Object.entries(snapshot.scenes)) {
    const sceneKey = add(key("scene", id), {
      label: id, kind: "测试场景", type: "scene", title: title(scene.definition.source, id), definition: snapshot.definitions,
      source: scene.definition.source, status: scene.status, missing: scene.missing_obligations,
    });
    const parents = Object.keys(scene.definition.requirement_sha256).map(r => requirements.get(r)).filter(Boolean);
    for (const parent of parents) {
      connected.add(parent);
      link(parent, sceneKey);
    }
    const cases = [];
    const caseIds = new Set([...Object.keys(scene.cases), ...scene.history.flatMap(r => Object.keys(r.cases))]);
    for (const caseId of [...caseIds].sort()) {
      const item = scene.cases[caseId];
      const design = scene.case_design?.[caseId];
      const history = scene.history.filter(r => r.cases[caseId]);
      const manual = design?.kind === "manual" || history.some(run => run.operation === "manual");
      const caseKey = add(key("case", id, caseId), {
        label: caseId, kind: manual ? "人工验证项" : "用例 / 操作", type: "case",
        title: design?.selector?.trim() || caseId,
        source: design?.source, design, status: design?.status === "skipped" ? "skipped" : item?.status,
      });
      link(sceneKey, caseKey);
      let execution, review, outcome;
      const currentRun = scene.current_runs?.[caseId];
      const currentAttempt = scene.current_attempts?.[caseId];
      for (const run of history) {
        const observed = run.cases[caseId];
        const identity = run.record ?? run.run;
        const details = scene.run_details?.[identity]?.[caseId];
        const runKey = add(key("run", id, caseId, identity), {
          label: run.run, kind: "实际执行", type: "run", title: caseId, status: observed.status, observed,
          stage: {"tdd-red": "红灯", "tdd-green": "绿灯", "tdd-baseline": "基线"}[run.operation],
          version: run.definition_sha256, product: run.product_version, bindings: run.bindings,
          record: run.record, recordedAt: run.recorded_at, evidence: observed.evidence, command: details?.command,
        });
        link(caseKey, runKey);
        let reviewKey;
        if (observed.review) {
          reviewKey = add(key("review", id, caseId, identity), {
            label: caseId, kind: "独立复审", type: "review", title: run.run, status: observed.review.status,
            reason: observed.review.reason, covers: observed.review.covers,
            record: details?.review?.record, recordedAt: details?.review?.recorded_at, reviewer: details?.review?.session,
            evidence: observed.evidence.filter(name => name.endsWith("/reviewer.txt")),
          });
          link(runKey, reviewKey);
        }
        if (currentAttempt ? identity === currentAttempt : run.run === currentRun || (!currentRun && run === history.at(-1))) {
          execution = runKey;
          review = reviewKey;
          outcome = observed.status;
        }
      }
      cases.push({id: caseKey, execution, review, attempts: history.length,
                  outcome: outcome ?? (design?.status === "skipped" ? "skipped" : "not_executed")});
    }
    groups.push({id: sceneKey, parents, cases, executed: scene.history.length > 0, status: scene.status});
  }
  for (const parent of requirements.values()) {
    if (!connected.has(parent)) groups.push({id: null, parents: [parent], cases: []});
  }
  return {snapshot, nodes, groups, requirements};
}

function nodeButton(id, name) {
  const button = element("button", name, "node");
  button.type = "button";
  button.dataset.node = id;
  return button;
}

function nodeText(node) {
  return [node.label, node.title, node.source?.text].filter(Boolean).join(" ").toLowerCase();
}

function matching(group, parent, query, filter) {
  const data = current.nodes;
  const groupMatches = !query || [parent, group.id].filter(Boolean).some(id => nodeText(data.get(id)).includes(query));
  if (!group.cases.length) {
    return groupMatches && (!filter || (filter === "not_executed" && group.id)) ? [] : null;
  }
  const matches = group.cases.filter(item => {
    const category = ["not_executed", "pending_review", "passed", "product_defect"].includes(item.outcome) ? item.outcome : "other";
    return (!filter || filter === category) && (groupMatches || [item.id, item.execution, item.review].filter(Boolean)
      .some(id => nodeText(data.get(id)).includes(query)));
  });
  return matches.length ? matches : null;
}

function renderTable() {
  if (!current) return;
  const query = get("search").value.trim().toLowerCase();
  const filter = get("filter").value;
  const rows = [];
  const focused = document.activeElement?.dataset.node;
  const sections = new Map([...current.requirements.values(), null].map(parent => [parent, []]));
  for (const group of current.groups) {
    for (const parent of group.parents.length ? group.parents : [null]) {
      sections.get(parent).push(group);
    }
  }
  const total = [...sections.values()].reduce((count, groups) => count
    + groups.reduce((subtotal, group) => subtotal + Math.max(1, group.cases.length), 0), 0);
  for (const [parent, groups] of sections) {
    const visible = groups.map(group => ({group, matches: matching(group, parent, query, filter)}))
      .filter(item => item.matches !== null);
    const requirementRows = visible.reduce((count, item) => count + Math.max(1, item.matches.length), 0);
    const requirement = parent ? current.nodes.get(parent) : null;
    for (const [index, {group, matches}] of visible.entries()) {
      const scene = group.id ? current.nodes.get(group.id) : null;
      for (const [caseIndex, item] of (matches.length ? matches : [null]).entries()) {
        const row = element("tr", undefined, "verification-row");
        row.dataset.requirement = requirement?.label ?? "";
        row.dataset.scene = scene?.label ?? "";
        if (index === 0 && caseIndex === 0) {
          const cell = requirementCell(row, requirementRows);
          if (requirement) {
            cell.append(nodeButton(parent, requirement.label));
            cell.append(element("p", summary(requirement.source) ?? requirement.title, "row-summary muted"));
            if (requirement.status) cell.append(status(requirement.status));
          } else {
            cell.textContent = "独立场景";
          }
        }
        if (caseIndex === 0) {
          row.classList.add("scene-start");
          const cell = row.appendChild(element("td", undefined, "scene"));
          cell.rowSpan = Math.max(1, matches.length);
          if (scene) {
            cell.append(nodeButton(group.id, scene.label));
            cell.append(element("p", summary(scene.source) ?? scene.title, "row-summary muted"), status(group.status));
          } else {
            cell.textContent = "尚无关联场景";
          }
        }
        const [caseCell, runCell, reviewCell] = ["case", "run", "review"]
          .map(name => row.appendChild(element("td", undefined, name)));
        if (item) {
          const test = current.nodes.get(item.id);
          row.dataset.case = test.label;
          caseCell.append(nodeButton(item.id, test.label));
          if (test.title !== test.label) caseCell.append(element("p", test.title, "row-summary muted"));
          caseCell.append(element("p", test.kind + " · " + item.attempts + " 次执行", "muted"));
          if (item.execution) {
            const node = current.nodes.get(item.execution);
            runCell.append(nodeButton(item.execution, node.stage ? node.stage + " · " + node.label : node.label));
            if (node.observed.exit_code != null) runCell.append(element("p", "退出码 " + node.observed.exit_code, "muted"));
          }
          runCell.append(status(current.snapshot.mode === "development" && item.outcome === "not_executed"
            ? "no_case_receipt" : item.outcome));
          if (item.review) {
            const opinion = current.nodes.get(item.review);
            const verdict = opinion.status === "passed" && current.snapshot.mode === "development"
              ? "绿灯复审通过" : labels[opinion.status] ?? opinion.status;
            reviewCell.append(nodeButton(item.review, verdict));
          } else {
            reviewCell.textContent = item.outcome === "pending_review" ? "待独立复审" : "尚无结论";
          }
        } else {
          caseCell.textContent = "尚无关联用例";
          runCell.append(status(current.snapshot.mode === "development" ? "no_case_receipt" : "not_executed"));
          reviewCell.textContent = "尚无结论";
        }
        rows.push(row);
      }
    }
  }
  get("count").textContent = "显示 " + rows.length + " / " + total + " 行";
  if (!rows.length) {
    const row = element("tr");
    const cell = row.appendChild(element("td", current.snapshot.definitions ? "没有匹配记录" : "尚无冻结定义"));
    cell.colSpan = 5;
    rows.push(row);
  }
  get("rows").replaceChildren(...rows);
  if (focused && !detail.open) {
    [...get("rows").querySelectorAll("[data-node]")].find(b => b.dataset.node === focused)?.focus({preventScroll: true});
  }
}

function renderSummary() {
  const scenes = current.groups.filter(group => group.id);
  const executed = scenes.filter(group => group.executed);
  const totals = [
    ["冻结需求", Object.keys(current.snapshot.requirements ?? {}).length],
    ["冻结场景", scenes.length],
    ["已执行", executed.length],
    ["未执行", scenes.length - executed.length],
    ["通过场景", scenes.filter(group => group.status === "passed").length],
    ["有产品缺陷", scenes.filter(group => group.cases.some(item => item.outcome === "product_defect")).length],
  ];
  const shown = current.snapshot.mode === "development"
    ? [...totals.slice(0, 4), ["绿灯已复审", scenes.filter(group => group.status === "tdd_verified").length],
      ["基线已验证", scenes.filter(group => group.status === "tdd_baseline_verified").length]] : totals;
  if (current.snapshot.scope_reviews) {
    shown.splice(1, 0, ["已核对范围", Object.keys(current.snapshot.scope_reviews).length]);
  }
  get("summary").replaceChildren(...shown.map(([label, count]) => {
    const card = element("div", undefined, "stat");
    card.append(element("strong", count), element("span", label));
    return card;
  }));
}

function activeDetail() {
  return stack.at(-1);
}

function sourceLocation(source) {
  return source.kind === "file"
    ? source.path + " · 第 " + source.first + "–" + source.last + " 行"
    : source.reference ?? "已冻结内容";
}

function prose(tag, value) {
  const node = element(tag);
  const tokens = /\[\[([^\]]+)\]\([^)]+\)\]|\[([^\]]+)\]\([^)]+\)|`([^`]+)`|\*\*([^*]+)\*\*/g;
  let cursor = 0;
  for (const match of value.matchAll(tokens)) {
    node.append(value.slice(cursor, match.index));
    if (match[3]) node.append(element("code", match[3]));
    else if (match[4]) node.append(element("strong", match[4]));
    else node.append(element("span", match[1] ?? match[2], "source-reference"));
    cursor = match.index + match[0].length;
  }
  node.append(value.slice(cursor));
  return node;
}

function frozenText(source) {
  const result = element("div", undefined, "frozen-text");
  const lines = source.text.split("\n").map(line => line.trim());
  const heading = /^#{1,6}\s+(.+)/;
  const bullet = /^[-*]\s+(.+)/;
  const tableRule = /^\|(?:\s*:?-+:?\s*\|)+$/;
  const cells = line => line.slice(1).replace(/\|$/, "").split("|").map(cell => cell.trim());
  let list;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const item = line.match(bullet);
    if (item) {
      if (!list) list = result.appendChild(element("ul"));
      list.append(prose("li", item[1]));
      continue;
    }
    list = null;
    if (!line) continue;
    const label = line.match(heading);
    if (label) {
      result.append(prose("h4", label[1]));
    } else if (line.startsWith("|") && tableRule.test(lines[i + 1] ?? "")) {
      const scroller = result.appendChild(element("div", undefined, "spec-table"));
      const table = scroller.appendChild(element("table"));
      const header = table.createTHead().insertRow();
      header.append(...cells(line).map(value => {
        const th = prose("th", value);
        th.scope = "col";
        return th;
      }));
      const body = table.createTBody();
      i++;
      while (lines[i + 1]?.startsWith("|")) {
        body.insertRow().append(...cells(lines[++i]).map(value => prose("td", value)));
      }
    } else {
      result.append(prose("p", line));
    }
  }
  return result;
}

function detailSection(body, heading) {
  const section = body.appendChild(element("section", undefined, "detail-section"));
  section.append(element("h3", heading));
  return section;
}

function evidenceLinks(body, node) {
  const items = (node.evidence ?? []).filter(name => reading.snapshot.evidence_urls?.[name]);
  if (!items.length) return;
  const section = detailSection(body, "原始证据");
  const list = section.appendChild(element("ul", undefined, "evidence-list"));
  for (const name of items) {
    const link = element("a", name.split("/").at(-1));
    link.href = reading.snapshot.evidence_urls[name];
    link.target = "_blank";
    link.rel = "noopener";
    link.title = name;
    list.appendChild(element("li")).append(link);
  }
}

function relationList(node) {
  const list = element("ul", undefined, "relation-list");
  for (const id of node.relations) {
    const related = reading.nodes.get(id);
    const item = list.appendChild(element("li"));
    const name = [related.kind, related.stage, related.label].filter(Boolean).join(" · ");
    item.append(nodeButton(id, name));
    if (related.status) item.append(status(related.status));
  }
  return list.childElementCount ? list : element("p", "尚无关联记录");
}

function renderDetail() {
  const frame = activeDetail();
  const node = reading.nodes.get(frame.id);
  const body = get("detail-body");
  get("detail-title").textContent = node.kind + " · " + node.label;
  get("back").hidden = stack.length < 2;
  get("detail-update").hidden = reading === current;
  for (const id of tabIds) {
    get(id).setAttribute("aria-selected", String(id === frame.tab));
    get(id).tabIndex = id === frame.tab ? 0 : -1;
  }
  body.setAttribute("aria-labelledby", frame.tab);
  body.replaceChildren();
  if (frame.tab === "relations") {
    body.append(relationList(node));
  } else if (frame.tab === "sources") {
    if (node.source) {
      const source = node.source;
      body.append(element("p", sourceLocation(source)), element("pre", source.text));
      if (source.sha256) body.append(element("p", "SHA-256 " + source.sha256, "muted"));
    }
    if (node.design?.files) body.append(element("p", node.design.files.join("、")));
    if (node.design?.command) body.append(element("pre", JSON.stringify(node.design.command)));
    if (node.version) body.append(element("p", "执行绑定定义 " + node.version, "muted"));
    if (node.product) body.append(element("p", "被测版本 " + node.product));
    if (node.record) body.append(element("p", node.record, "muted"));
    for (const [path, sha] of Object.entries(node.bindings ?? {})) {
      body.append(element("p", path + " · SHA-256 " + sha, "muted"));
    }
    evidenceLinks(body, node);
    if (!body.childElementCount) body.append(element("p", "尚无依据或原始记录"));
  } else {
    if (node.status) body.append(status(node.status));
    if (node.type === "requirement" || node.type === "scene") {
      if (node.source) {
        const section = detailSection(body, node.type === "requirement" ? "需求原文" : "场景原文");
        section.append(element("p", sourceLocation(node.source) + (node.definition ? " · 冻结 v" + node.definition.version : ""), "muted"),
          frozenText(node.source));
      }
    } else if (node.type === "case") {
      if (node.title !== node.label) body.append(element("p", node.title));
      if (node.source) {
        const section = detailSection(body, node.kind === "人工验证项" ? "操作步骤与预期" : "用例源码");
        section.append(element("p", sourceLocation(node.source), "muted"),
          node.kind === "人工验证项" ? frozenText(node.source) : element("pre", node.source.text));
      }
      if (node.design?.covers && Object.keys(node.design.covers).length) {
        const list = detailSection(body, "用例检查项").appendChild(element("dl", undefined, "checks"));
        for (const [id, assertion] of Object.entries(node.design.covers)) {
          list.append(element("dt", id), element("dd", assertion));
        }
      }
    } else {
      body.append(element("p", (node.stage ? node.stage + " · " : "") + node.title));
    }
    if (node.reason) detailSection(body, node.type === "requirement" ? "交付判定依据" : "复审意见").append(element("p", node.reason));
    if (node.scope) {
      detailSection(body, "场景范围核对").append(element("p", node.scope.analysis), element("p", node.scope.record, "muted"));
    }
    if (node.missing?.length) body.append(element("p", "未取得通过证据的检查项 " + node.missing.join("、")));
    if (node.design?.design_review) {
      const review = node.design.design_review;
      detailSection(body, "设计复审").append(status(review.status), element("p", review.reason));
    }
    if (node.reviewer) body.append(element("p", "Reviewer · " + node.reviewer, "muted"));
    if (node.recordedAt) body.append(element("p", "记录时间 · " + node.recordedAt, "muted"));
    if (node.product) body.append(element("p", "被测版本 · " + node.product, "muted"));
    if (node.command) detailSection(body, "实际运行命令").append(element("pre", JSON.stringify(node.command)));
    if (node.observed) {
      if (node.observed.exit_code != null) body.append(element("p", "退出码 " + node.observed.exit_code + " · 覆盖率 " + (labels[node.observed.coverage] ?? node.observed.coverage)));
      for (const item of node.observed.observations ?? []) {
        const section = detailSection(body, "人工观察");
        section.append(element("p", item.action), element("p", "预期 · " + item.expected),
          element("p", "实际 · " + item.observed), element("p", [item.performed_at, item.performed_by].filter(Boolean).join(" · "), "muted"));
      }
    }
    if (node.covers?.length) body.append(element("p", "已复审检查项 " + node.covers.join("、")));
    evidenceLinks(body, node);
    if (node.relations.size) detailSection(body, node.type === "requirement" ? "关联场景" : "关联记录").append(relationList(node));
  }
  body.scrollTop = frame.scroll;
}

function show(id) {
  if (!detail.open) {
    reading = current;
    opener = id;
    stack.length = 0;
    detail.showModal();
  }
  if (activeDetail()) activeDetail().scroll = get("detail-body").scrollTop;
  stack.push({id, tab: "overview", scroll: 0});
  renderDetail();
}

function renderInventory() {
  const inventory = current.snapshot.inventory;
  const query = get("search").value.trim().toLowerCase();
  const items = (inventory?.items ?? []).filter(item => JSON.stringify(item).toLowerCase().includes(query));
  get("count").textContent = inventory ? "显示 " + items.length + " / " + inventory.items.length + " 项" : "等待盘点记录";
  const groups = new Map();
  for (const item of items) {
    const requirement = item.requirement ?? item.id;
    if (!groups.has(requirement)) groups.set(requirement, []);
    groups.get(requirement).push(item);
  }
  const rows = [];
  for (const [requirement, group] of groups) {
    for (const [index, item] of group.entries()) {
      const row = element("tr");
      row.dataset.requirement = requirement;
      if (index === 0) requirementCell(row, group.length).textContent = requirement;
      const source = row.appendChild(element("td"));
      if (item.requirement) {
        source.append(element("strong", item.id), element("p", item.origin ?? "", "muted"));
      }
      source.append(element("p", (item.sources ?? []).join("、")));
      if (item.static_findings?.length) {
        source.append(element("p", "静态发现 · " + item.static_findings.join("、"), "muted"));
      }
      const cases = row.appendChild(element("td"));
      if (item.cases?.length) {
        const list = cases.appendChild(element("ul"));
        for (const test of item.cases) {
          const entry = list.appendChild(element("li"));
          entry.append(element("strong", test.selector), element("p", test.source, "muted"));
          if (test.id) entry.append(element("p", test.id, "muted"));
        }
      } else {
        cases.textContent = item.cases ? "未找到关联用例" : "未记录用例关联";
      }
      const evidence = row.appendChild(element("td"));
      if (item.command) evidence.append(element("p", item.command), element("p", "退出码 " + item.exit_code, "muted"));
      if (item.evidence?.length) {
        const list = evidence.appendChild(element("ul"));
        for (const path of item.evidence) list.append(element("li", path));
      } else {
        evidence.append(element("p", "无适用执行证据"));
      }
      const state = row.appendChild(element("td"));
      state.append(status(item.status));
      if (item.reason) state.append(element("p", item.reason, "muted"));
      row.append(element("td", item.next));
      rows.push(row);
    }
  }
  if (!rows.length) {
    const row = element("tr");
    row.appendChild(element("td", inventory ? "没有匹配记录" : "尚未生成盘点报告")).colSpan = 6;
    rows.push(row);
  }
  get("rows").replaceChildren(...rows);
}

function requirementCell(row, count) {
  row.classList.add("requirement-start");
  const cell = row.appendChild(element("td", undefined, "requirement"));
  cell.rowSpan = count;
  return cell;
}

async function refresh() {
  if (busy) return;
  busy = true;
  const connection = get("connection");
  try {
    const response = await fetch(snapshotUrl, {cache: "no-store", signal: AbortSignal.timeout(4500)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const payload = await response.text();
    if (previousPayload !== payload) {
      const next = JSON.parse(payload);
      current = next.mode === "inventory" ? {snapshot: next} : readSnapshot(next);
      previousPayload = payload;
      const snapshot = current.snapshot;
      if (snapshot.mode === "inventory") {
        document.title = "维护项目 · 交付盘点";
        get("acceptance").textContent = "维护项目 · 交付盘点";
        get("version").textContent = snapshot.inventory ? "项目版本 " + snapshot.inventory.revision.slice(0, 12) : "尚未生成盘点报告";
        get("filter").closest("label").hidden = true;
        const names = ["需求", "场景 / 来源", "关联用例", "执行证据", "交付状态", "下一步"];
        const headers = document.querySelector("thead tr");
        headers.replaceChildren(...names.map(name => {
          const heading = element("th", name);
          heading.scope = "col";
          return heading;
        }));
        headers.closest(".table-scroll").setAttribute("aria-label", "维护盘点追踪表");
        renderInventory();
      } else {
        document.title = snapshot.acceptance_id + " · 验收追踪";
        get("acceptance").textContent = snapshot.acceptance_id;
        const prefix = snapshot.mode === "development" ? "测试驱动开发 · " : "正式验收 · ";
        const phaseNav = get("phase-nav");
        const currentPhase = snapshot.mode === "development" ? "development" : "acceptance";
        phaseNav.hidden = false;
        phaseNav.querySelector("[aria-current]")?.removeAttribute("aria-current");
        phaseNav.querySelector("[data-phase=" + currentPhase + "]").setAttribute("aria-current", "page");
        get("version").textContent = snapshot.definitions ? prefix + "冻结定义 v" + snapshot.definitions.version : "尚未冻结";
        renderSummary();
        renderTable();
        if (detail.open) get("detail-update").hidden = false;
      }
    }
    lastRead = new Date().toLocaleTimeString("zh-CN");
    connection.removeAttribute("data-error");
    connection.textContent = "● 自动刷新 · " + lastRead;
  } catch {
    connection.dataset.error = "";
    connection.textContent = lastRead ? "读取失败 · 保留 " + lastRead + " 的结果" : "暂时无法读取结果";
  } finally {
    busy = false;
  }
}

get("search").addEventListener("input", () => {
  if (current?.snapshot.mode === "inventory") renderInventory();
  else renderTable();
});
get("filter").addEventListener("change", renderTable);
get("refresh").addEventListener("click", refresh);
get("rows").addEventListener("click", event => {
  const button = event.target.closest("button");
  if (button?.dataset.node) show(button.dataset.node);
});
get("detail-body").addEventListener("click", event => {
  const button = event.target.closest("[data-node]");
  if (button) show(button.dataset.node);
});
get("back").addEventListener("click", () => {
  stack.pop();
  renderDetail();
  get(activeDetail().tab).focus();
});
get("refresh-detail").addEventListener("click", () => {
  if (!current.nodes.has(activeDetail().id)) {
    detail.close();
    return;
  }
  reading = current;
  const frames = stack.filter(frame => current.nodes.has(frame.id));
  stack.splice(0, stack.length, ...frames);
  renderDetail();
});
get("tabs").addEventListener("click", event => {
  if (!tabIds.includes(event.target.id)) return;
  Object.assign(activeDetail(), {tab: event.target.id, scroll: 0});
  renderDetail();
});
get("tabs").addEventListener("keydown", event => {
  const index = tabIds.indexOf(event.target.id);
  if (index < 0) return;
  const next = {ArrowLeft: (index + 2) % 3, ArrowRight: (index + 1) % 3, Home: 0, End: 2}[event.key];
  if (next === undefined) return;
  event.preventDefault();
  const tab = get(tabIds[next]);
  tab.click();
  tab.focus();
});
detail.addEventListener("close", () => {
  [...get("rows").querySelectorAll("[data-node]")].find(b => b.dataset.node === opener)?.focus();
});
refresh();
setInterval(refresh, 5000);
