"use strict";

const labels = {
  passed: "通过", needs_revision: "需修订", not_fully_validated: "尚未完成验收",
  product_defect: "产品缺陷", pending_review: "待独立复审", definition_changed: "定义有变化",
  not_executed: "未执行", stale: "旧版本", evidence_missing: "证据缺失", historical: "历史记录",
  definition_conflict: "定义冲突", insufficient_evidence: "证据不足", skipped: "跳过",
  measured: "已采集", off: "关闭", unavailable: "未取得覆盖率", unknown: "未核对",
  expected_failure: "已确认预期红灯", tdd_in_progress: "TDD 进行中", tdd_verified: "TDD 绿灯已复审",
  verified: "已验证", failed: "失败", recheck: "待复验", untested: "未验证", unconfirmed: "待确认",
  source_missing: "依据缺失", conflict: "依据冲突", blocked: "受阻",
};
const get = id => document.getElementById(id);
const detail = get("detail");
const tabIds = ["overview", "relations", "sources"];
const expanded = new Set();
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
    requirements.set(id, add(key("requirement", id), {
      label: id, kind: "需求 / 规则", title: title(item.source, id), source: item.source,
    }));
  }
  const connected = new Set();
  for (const [id, scene] of Object.entries(snapshot.scenes)) {
    const sceneKey = add(key("scene", id), {
      label: id, kind: "测试场景", title: title(scene.definition.source, id),
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
      const caseKey = add(key("case", id, caseId), {
        label: caseId, kind: "用例 / 操作", title: design?.selector ?? caseId,
        source: design?.source, design, status: design?.status === "skipped" ? "skipped" : item?.status,
      });
      link(sceneKey, caseKey);
      let execution, review, outcome;
      const history = scene.history.filter(r => r.cases[caseId]);
      const currentRun = scene.current_runs?.[caseId];
      for (const run of history) {
        const observed = run.cases[caseId];
        const runKey = add(key("run", id, caseId, run.run), {
          label: run.run, kind: "实际执行", title: caseId, status: observed.status, observed,
          stage: {"tdd-red": "红灯", "tdd-green": "绿灯"}[run.operation],
          version: run.definition_sha256, evidence: observed.evidence,
        });
        link(caseKey, runKey);
        let reviewKey;
        if (observed.review) {
          reviewKey = add(key("review", id, caseId, run.run), {
            label: caseId, kind: "独立复审", title: run.run, status: observed.review.status,
            reason: observed.review.reason, covers: observed.review.covers,
            evidence: observed.evidence.filter(name => name.endsWith("/reviewer.txt")),
          });
          link(runKey, reviewKey);
          link(caseKey, reviewKey);
        }
        if (run.run === currentRun || (!currentRun && run === history.at(-1))) {
          execution = runKey;
          review = reviewKey;
          outcome = observed.status;
        }
      }
      cases.push({id: caseKey, execution, review, outcome: outcome ?? (design?.status === "skipped" ? "skipped" : "not_executed")});
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
  const total = [...sections.values()].reduce((count, groups) => count + groups.length, 0);
  for (const [parent, groups] of sections) {
    const visible = groups.map(group => ({group, matches: matching(group, parent, query, filter)}))
      .filter(item => item.matches !== null);
    for (const [index, {group, matches}] of visible.entries()) {
      const tr = element("tr");
      const requirement = parent ? current.nodes.get(parent) : null;
      tr.dataset.requirement = requirement?.label ?? "";
      if (index === 0) {
        const cell = requirementCell(tr, visible.length);
        if (requirement) {
          cell.append(nodeButton(parent, requirement.label), element("p", requirement.title, "muted"));
        } else {
          cell.textContent = "独立场景";
        }
      }
      const [sceneCell, casesCell, runsCell, reviewsCell] = Array.from({length: 4}, () => tr.appendChild(element("td")));
      if (!group.id) {
        sceneCell.textContent = "尚无关联场景";
      } else {
        const node = current.nodes.get(group.id);
        sceneCell.append(nodeButton(group.id, node.label), element("p", node.title, "muted"), status(group.status));
      }
      if (!matches.length) {
        casesCell.textContent = "尚无关联用例";
        runsCell.append(status("not_executed"));
        reviewsCell.textContent = "尚无结论";
      } else {
        const lists = [casesCell, runsCell, reviewsCell].map(cell => cell.appendChild(element("ul")));
        const showAll = expanded.has(group.id);
        for (const item of showAll ? matches : matches.slice(0, 3)) {
          const test = current.nodes.get(item.id);
          lists[0].append(element("li"));
          lists[0].lastElementChild.append(nodeButton(item.id, test.label));
          if (test.title !== test.label) lists[0].lastElementChild.append(element("p", test.title, "muted"));
          const run = lists[1].appendChild(element("li"));
          if (item.execution) {
            const node = current.nodes.get(item.execution);
            run.append(nodeButton(item.execution, node.stage ? node.stage + " · " + node.label : node.label),
              element("p", node.observed.exit_code == null ? test.label : test.label + " · 退出 " + node.observed.exit_code, "muted"));
          }
          run.append(status(item.outcome));
          const review = lists[2].appendChild(element("li"));
          if (item.review) {
            const opinion = current.nodes.get(item.review);
            const verdict = opinion.status === "passed" && current.snapshot.mode === "development"
              ? "绿灯复审通过" : labels[opinion.status];
            review.append(nodeButton(item.review, verdict), element("p", test.label, "muted"));
          } else {
            review.textContent = item.outcome === "pending_review" ? "待独立复审" : "尚无结论";
          }
        }
        if (matches.length > 3) {
          const toggle = element("button", showAll ? "收起" : "查看全部 " + matches.length + " 项");
          toggle.type = "button";
          toggle.dataset.expand = group.id;
          toggle.setAttribute("aria-expanded", String(showAll));
          casesCell.append(toggle);
        }
      }
      rows.push(tr);
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
    ? [...totals.slice(0, 4), ["绿灯已复审", scenes.filter(group => group.status === "tdd_verified").length]] : totals;
  get("summary").replaceChildren(...shown.map(([label, count]) => {
    const card = element("div", undefined, "stat");
    card.append(element("strong", count), element("span", label));
    return card;
  }));
}

function activeDetail() {
  return stack.at(-1);
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
    const list = element("ul", undefined, "relation-list");
    for (const id of node.relations) {
      const related = reading.nodes.get(id);
      list.append(element("li"));
      list.lastElementChild.append(nodeButton(id, related.kind + " · " + related.label));
    }
    body.append(list.childElementCount ? list : element("p", "尚无关联记录"));
  } else if (frame.tab === "sources") {
    if (node.source) {
      const source = node.source;
      body.append(element("p", source.kind === "file"
        ? source.path + " · 第 " + source.first + "–" + source.last + " 行" : source.reference ?? "已冻结内容"),
      element("pre", source.text));
      if (source.sha256) body.append(element("p", "SHA-256 " + source.sha256, "muted"));
    }
    if (node.design?.files) body.append(element("p", node.design.files.join("、")));
    if (node.design?.command) body.append(element("pre", JSON.stringify(node.design.command)));
    if (node.version) body.append(element("p", "执行绑定定义 " + node.version, "muted"));
    for (const name of node.evidence ?? []) {
      const url = reading.snapshot.evidence_urls?.[name];
      if (!url) continue;
      const link = element("a", name.split("/").at(-1));
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener";
      body.append(element("p"));
      body.lastElementChild.append(link);
    }
    if (!body.childElementCount) body.append(element("p", "尚无依据或原始记录"));
  } else {
    body.append(element("p", node.title));
    if (node.status) body.append(status(node.status));
    if (node.reason) body.append(element("p", node.reason));
    if (node.missing?.length) body.append(element("p", "未取得通过证据的检查项 " + node.missing.join("、")));
    if (node.design?.design_review) {
      const review = node.design.design_review;
      body.append(element("p", "设计复审 · " + (labels[review.status] ?? review.status) + " · " + review.reason));
    }
    if (node.observed) {
      body.append(element("p", "退出码 " + (node.observed.exit_code ?? "无") + " · 覆盖率 " + labels[node.observed.coverage]));
      for (const item of node.observed.observations ?? []) {
        body.append(element("p", item.action + " · " + item.observed));
      }
    }
    if (node.covers?.length) body.append(element("p", "已复审检查项 " + node.covers.join("、")));
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
      row.append(element("td", (item.evidence ?? []).join("、") || "无适用执行证据"));
      const state = row.appendChild(element("td"));
      state.append(status(item.status));
      if (item.reason) state.append(element("p", item.reason, "muted"));
      row.append(element("td", item.next));
      rows.push(row);
    }
  }
  if (!rows.length) {
    const row = element("tr");
    row.appendChild(element("td", inventory ? "没有匹配记录" : "尚未生成盘点报告")).colSpan = 5;
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
    const response = await fetch("/snapshot.json", {cache: "no-store", signal: AbortSignal.timeout(4500)});
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
        const names = ["需求", "场景 / 来源", "执行证据", "交付状态", "下一步"];
        document.querySelectorAll("th").forEach((heading, index) => { heading.textContent = names[index]; });
        renderInventory();
      } else {
        document.title = snapshot.acceptance_id + " · 验收追踪";
        get("acceptance").textContent = snapshot.acceptance_id;
        const prefix = snapshot.mode === "development" ? "测试驱动开发 · " : "";
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
  if (button?.dataset.expand) {
    const id = button.dataset.expand;
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    renderTable();
    [...get("rows").querySelectorAll("[data-expand]")].find(b => b.dataset.expand === id)?.focus();
  }
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
