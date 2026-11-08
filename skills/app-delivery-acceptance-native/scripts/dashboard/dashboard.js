"use strict";

const labels = {
  passed: "通过", needs_revision: "需修订", not_fully_validated: "尚未完成验收",
  product_defect: "产品缺陷", pending_review: "待独立复审", definition_changed: "定义有变化",
  not_executed: "未执行", stale: "旧版本", evidence_missing: "证据缺失", historical: "历史记录",
  definition_conflict: "定义冲突", insufficient_evidence: "证据不足", skipped: "跳过",
  measured: "已采集", off: "关闭", unavailable: "未取得覆盖率", unknown: "未核对",
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
  return {snapshot, nodes, groups};
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

function matching(group, query, filter) {
  const data = current.nodes;
  const groupMatches = !query || [...group.parents, group.id].filter(Boolean).some(id => nodeText(data.get(id)).includes(query));
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
  for (const group of current.groups) {
    const matches = matching(group, query, filter);
    if (!matches) continue;
    const tr = element("tr");
    const cells = Array.from({length: 5}, () => tr.appendChild(element("td")));
    for (const parent of group.parents) {
      const node = current.nodes.get(parent);
      cells[0].appendChild(element("div")).append(nodeButton(parent, node.label), element("p", node.title, "muted"));
    }
    if (!group.parents.length) cells[0].textContent = "独立场景";
    if (!group.id) {
      cells[1].textContent = "尚无关联场景";
    } else {
      const node = current.nodes.get(group.id);
      cells[1].append(nodeButton(group.id, node.label), element("p", node.title, "muted"), status(group.status));
    }
    if (!matches.length) {
      cells[2].textContent = "尚无关联用例";
      cells[3].append(status("not_executed"));
      cells[4].textContent = "尚无结论";
    } else {
      const lists = cells.slice(2).map(cell => cell.appendChild(element("ul")));
      const showAll = expanded.has(group.id);
      for (const item of showAll ? matches : matches.slice(0, 3)) {
        const test = current.nodes.get(item.id);
        lists[0].append(element("li"));
        lists[0].lastElementChild.append(nodeButton(item.id, test.label));
        if (test.title !== test.label) lists[0].lastElementChild.append(element("p", test.title, "muted"));
        const run = lists[1].appendChild(element("li"));
        if (item.execution) {
          const node = current.nodes.get(item.execution);
          run.append(nodeButton(item.execution, node.label),
            element("p", node.observed.exit_code == null ? test.label : test.label + " · 退出 " + node.observed.exit_code, "muted"));
        }
        run.append(status(item.outcome));
        const review = lists[2].appendChild(element("li"));
        if (item.review) {
          const opinion = current.nodes.get(item.review);
          review.append(nodeButton(item.review, labels[opinion.status]), element("p", test.label, "muted"));
        } else {
          review.textContent = item.outcome === "pending_review" ? "待独立复审" : "尚无结论";
        }
      }
      if (matches.length > 3) {
        const toggle = element("button", showAll ? "收起" : "查看全部 " + matches.length + " 项");
        toggle.type = "button";
        toggle.dataset.expand = group.id;
        toggle.setAttribute("aria-expanded", String(showAll));
        cells[2].append(toggle);
      }
    }
    rows.push(tr);
  }
  get("count").textContent = "显示 " + rows.length + " / " + current.groups.length + " 组";
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
  get("summary").replaceChildren(...totals.map(([label, count]) => {
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

async function refresh() {
  if (busy) return;
  busy = true;
  const connection = get("connection");
  try {
    const response = await fetch("/snapshot.json", {cache: "no-store", signal: AbortSignal.timeout(4500)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const payload = await response.text();
    if (previousPayload !== payload) {
      current = readSnapshot(JSON.parse(payload));
      previousPayload = payload;
      const snapshot = current.snapshot;
      document.title = snapshot.acceptance_id + " · 验收追踪";
      get("acceptance").textContent = snapshot.acceptance_id;
      get("version").textContent = snapshot.definitions ? "冻结定义 v" + snapshot.definitions.version : "尚未冻结";
      renderSummary();
      renderTable();
      if (detail.open) get("detail-update").hidden = false;
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

get("search").addEventListener("input", renderTable);
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
