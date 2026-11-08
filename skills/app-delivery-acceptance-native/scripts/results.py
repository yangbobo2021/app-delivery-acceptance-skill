"""Inspect and export acceptance evidence without running project tests."""

import argparse
import html
import json
import mimetypes
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evidence import local, project_ref, verify_bindings, verify_checkout
from records import Blocked, Project, acceptance_id, atomic_write, digest, encoded, read_json, token


def attempts(project, identifier, section):
    folder = local(project, identifier, section)
    found = []
    for location in folder.glob("*/result.json"):
        file = project.path(project_ref(project, location))
        raw = file.read_bytes()
        record = json.loads(raw)
        if record["acceptance_id"] != identifier or record["operation"] != section:
            raise Blocked("invalid attempt identity")
        found.append((record, digest(raw)))
    return sorted(found, key=lambda item: (item[0].get("recorded_at", ""), item[0]["request_id"]))


def intact(project, bindings):
    try:
        if bindings:
            verify_bindings(project, bindings)
        return True
    except (Blocked, OSError):
        return False


def live_attempt(project, run):
    try:
        verify_checkout(project, run)
        return intact(project, run["bindings"])
    except (Blocked, OSError):
        return False


def view(project, identifier, selected=None):
    manifest = project.manifest(identifier)
    versions = project.versions(identifier)
    if not versions:
        return {"acceptance_id": identifier, "manifest": manifest, "definitions": None, "scenes": {}, "evidence": {}}
    _, current_sha, current = versions[-1]
    scenes = current["scenarios"]
    if selected is not None:
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or not set(selected) <= scenes.keys():
            raise Blocked("provide actual, distinct frozen scene IDs")
        scenes = {name: scenes[name] for name in selected}
    result = {
        "acceptance_id": identifier, "manifest": manifest,
        "definitions": {"version": current["version"], "sha256": current_sha},
        "requirements": current["requirements"], "scenes": {}, "evidence": {},
    }
    invalidated = set(project.receipt(versions[-1], "current")["scenes_needing_revalidation"])
    execution = [item for section in ("execute", "manual", "history") for item in attempts(project, identifier, section)]
    execution.sort(key=lambda item: (item[0].get("recorded_at", ""), item[0]["request_id"]))
    opinions = [review for review, _ in attempts(project, identifier, "review") if review["status"] == "reviewed"]
    matched = [item for item, _ in attempts(project, identifier, "match") if item["status"] == "matched"]
    for scene, definition in scenes.items():
        fresh_cases, history, checklist, expected = {}, [], {}, set()
        for match in matched:
            if match["scene"] == scene and match["definitions_sha256"] == current_sha:
                expected = set(match["cases"])
                checklist = match["obligations"]
        for run, sha in execution:
            if run.get("scene") != scene or run["status"] not in {"executed", "observed", "imported"}:
                continue
            applicable = (
                run["operation"] != "history" and scene not in invalidated and run["definitions_sha256"] == current_sha
                and live_attempt(project, run)
            )
            raw = run["artifacts"]
            evidence_valid = intact(project, raw)
            review = next((item for item in reversed(opinions) if item["target_sha256"] == sha and item["target_operation"] == run["operation"]), None)
            review_valid = bool(review and intact(project, review["artifacts"]))
            run_cases = {}
            for identifier_case, item in run["results"].items():
                execution_receipt = item.get("execution")
                verdict = review["judgments"][identifier_case] if review_valid else None
                status = (
                    "evidence_missing" if not evidence_valid else "historical" if run["operation"] == "history" else
                    "stale" if not applicable else
                    verdict["status"] if verdict else "pending_review"
                )
                attachments = dict(item.get("attachments", {}))
                if execution_receipt:
                    attachments.update(execution_receipt.get("output", {}))
                coverage = item.get("coverage", {})
                report = coverage.get("report")
                statistics = None
                if report in raw:
                    attachments[report] = raw[report]
                    if coverage.get("statistics") is not None:
                        statistics = {"version": coverage["tool"], **coverage["statistics"], "adapter": True}
                    elif intact(project, {report: raw[report]}):
                        native = read_json(project.path(report))
                        statistics = {"version": native["meta"]["version"], **native["totals"]}
                if review_valid:
                    response = review["reviewer_response"]
                    attachments[response] = review["artifacts"][response]
                attachments = {name: expected_sha for name, expected_sha in attachments.items() if intact(project, {name: expected_sha})}
                result["evidence"].update(attachments)
                run_cases[identifier_case] = {
                    "status": status, "review": verdict, "evidence": sorted(attachments),
                    "coverage": coverage.get("status", "unavailable"),
                    "exit_code": execution_receipt.get("exit_code") if execution_receipt else None,
                    "coverage_statistics": statistics, "observations": item.get("operations", []),
                }
            history.append({
                "run": run["request_id"], "operation": run["operation"], "definition_sha256": run["definitions_sha256"],
                "selection": run.get("selection", "historical"), "cases": run_cases,
            })
            if applicable:
                fresh_cases.update(run_cases)
                expected.update(run.get("designed_case_ids", run_cases))
                checklist = run.get("obligations") or checklist
        for case in expected - fresh_cases.keys():
            fresh_cases[case] = {"status": "not_executed", "evidence": [], "review": None, "coverage": "unavailable"}
        proved = {
            obligation for item in fresh_cases.values() if item["status"] == "passed"
            for obligation in item["review"].get("covers", [])
        }
        missing = sorted(checklist.keys() - proved)
        complete = bool(checklist and fresh_cases) and not missing and all(item["status"] == "passed" for item in fresh_cases.values())
        result["scenes"][scene] = {
            "definition": definition, "cases": fresh_cases, "history": history, "missing_obligations": missing,
            "status": "passed" if complete else "definition_changed" if scene in invalidated else "not_fully_validated",
        }
    return result


def render(snapshot, files):
    escape = lambda value: html.escape(str(value))
    labels = {
        "passed": "通过", "needs_revision": "需修订", "not_fully_validated": "尚未完成验收",
        "product_defect": "产品缺陷", "pending_review": "待独立复审", "definition_changed": "定义有变化",
        "not_executed": "未执行", "stale": "旧版本", "evidence_missing": "证据缺失",
        "historical": "导入的历史执行",
        "definition_conflict": "定义冲突", "insufficient_evidence": "证据不足",
        "measured": "已采集", "off": "关闭", "unavailable": "未取得覆盖率", "unknown": "覆盖率未知",
    }

    def evidence_links(item):
        return " ".join(
            f'<a href="{escape(files[name])}">{escape(Path(name).name)}</a>' for name in item["evidence"]
        )

    rows = []
    for scene, detail in snapshot["scenes"].items():
        title = f"{scene} · {labels.get(detail['status'], detail['status'])}"
        requirement_ids = ", ".join(detail["definition"]["requirement_sha256"])
        content = [f"<h2>{escape(title)}</h2><p>关联需求 {escape(requirement_ids)}</p>"]
        definition_text = detail["definition"]["source"]["text"]
        content.append(f"<details><summary>冻结场景原文</summary><pre>{escape(definition_text)}</pre></details>")
        content.append("<table><thead><tr><th>用例</th><th>执行</th><th>覆盖率</th><th>独立复审</th><th>证据</th></tr></thead><tbody>")
        for case, item in sorted(detail["cases"].items()):
            verdict = labels.get(item["status"], item["status"])
            reason = (item["review"] or {}).get("reason", "")
            code = item.get("exit_code")
            execution = "无退出码" if code is None else f"退出码 {code}"
            coverage = labels.get(item["coverage"], item["coverage"])
            stats = item.get("coverage_statistics")
            if stats and stats.get("adapter"):
                coverage = f"{stats['version']} · {json.dumps({k: v for k, v in stats.items() if k not in {'version', 'adapter'}}, ensure_ascii=False)}"
            elif stats:
                coverage = (
                    f"coverage.py {stats['version']} · 行 {stats['covered_lines']}/{stats['num_statements']}"
                    f" · 分支 {stats['covered_branches']}/{stats['num_branches']}"
                )
            if item.get("observations"):
                execution = "；".join(f"{op['action']}：{op['observed']}" for op in item["observations"])
            content.append(
                f"<tr><td>{escape(case)}</td><td>{escape(execution)}</td><td>{escape(coverage)}</td>"
                f"<td>{escape(verdict)}<br>{escape(reason)}</td><td>{evidence_links(item)}</td></tr>"
            )
        content.append("</tbody></table>")
        if detail["missing_obligations"]:
            content.append(f"<p>未取得通过证据的检查项：{escape(', '.join(detail['missing_obligations']))}</p>")
        content.append("<details><summary>历史尝试</summary><ul>")
        for old in detail["history"]:
            for case, item in old["cases"].items():
                verdict = labels.get(item["status"], item["status"])
                if item["review"]:
                    review_status = item["review"]["status"]
                    verdict += f" · 复审 {labels.get(review_status, review_status)}"
                content.append(f"<li>{escape(old['run'])} · {escape(case)} · {escape(verdict)} {evidence_links(item)}</li>")
        content.append("</ul></details>")
        rows.append("<section>" + "".join(content) + "</section>")
    requirements = "".join(
        f"<details><summary>{escape(name)}</summary><pre>{escape(item['source']['text'])}</pre></details>"
        for name, item in sorted(snapshot.get("requirements", {}).items())
    )
    identifier = escape(snapshot["acceptance_id"])
    version = escape((snapshot["definitions"] or {}).get("version", "未冻结"))
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>交付验收 · {identifier}</title><style>"
        "body{font:16px/1.55 system-ui,sans-serif;background:#f4f6fa;color:#172135;margin:0}"
        "main{max-width:1080px;margin:auto;padding:32px}section{background:#fff;border:1px solid #d7dfed;padding:24px;margin:24px 0}"
        "table{width:100%;border-collapse:collapse;margin:16px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid #d7dfed;overflow-wrap:anywhere}"
        "a{color:#164ba5;margin-right:8px}pre{white-space:pre-wrap}summary{cursor:pointer}h1,h2{line-height:1.25}"
        "</style>"
        f"<main><h1>交付验收 · {identifier}</h1><p>冻结定义 v{version} · <a href=\"snapshot.json\">结果快照</a></p>"
        f"<h2>冻结需求</h2>{requirements}{''.join(rows)}</main></html>\n"
    )


def export_report(project, identifier, request):
    key = token(request["request_id"], "report request id")
    target = project.shared(identifier, "records", f"report-{key}")
    selection = request.get("scene_ids")
    with project.writer():
        if target.exists():
            stored = read_json(project.path(project_ref(project, target / "snapshot.json")))
            if stored["selection"] != selection:
                raise Blocked("report request already binds a different selection")
            exported = {project_ref(project, target / name): sha for name, sha in stored["exported_sha256"].items()}
            if not intact(project, exported):
                raise Blocked("frozen report files changed")
        else:
            snapshot = view(project, identifier, selection)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix=".pending-report-", dir=target.parent))
            try:
                files = {}
                for source, sha in snapshot["evidence"].items():
                    raw = project.path(source).read_bytes()
                    if digest(raw) != sha:
                        raise Blocked("evidence changed while exporting")
                    key_name = digest(source.encode())[:16]
                    basename = Path(source).name
                    rel = f"evidence/{key_name}/{basename}"
                    atomic_write(staged / rel, raw)
                    files[source] = rel
                report = render(snapshot, files).encode("utf-8")
                atomic_write(staged / "report.html", report)
                snapshot["selection"] = selection
                snapshot["exported"] = files
                snapshot["exported_sha256"] = {
                    "report.html": digest(report),
                    **{rel: snapshot["evidence"][source] for source, rel in files.items()},
                }
                atomic_write(staged / "snapshot.json", encoded(snapshot))
                staged.rename(target)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
    return {
        "status": "reported", "report": project_ref(project, target / "report.html"),
        "snapshot": project_ref(project, target / "snapshot.json"),
    }


def serve(project, identifier, port=0):
    project.manifest(identifier)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                snapshot = view(project, identifier)
                files = {name: f"evidence/{digest(name.encode())}" for name in snapshot["evidence"]}
                if self.path == "/":
                    response = render(snapshot, files).encode("utf-8")
                    content = "text/html; charset=utf-8"
                elif self.path == "/snapshot.json":
                    response = encoded(snapshot)
                    content = "application/json; charset=utf-8"
                else:
                    original = next((name for name, route in files.items() if "/" + route == self.path), None)
                    if original is None:
                        self.send_error(404)
                        return
                    response = project.path(original).read_bytes()
                    if digest(response) != snapshot["evidence"][original]:
                        self.send_error(409)
                        return
                    content = mimetypes.guess_type(original)[0]
                    if content not in {"image/png", "image/jpeg", "image/webp", "application/json"}:
                        content = "text/plain; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(response)))
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(response)
            except (Blocked, KeyError, OSError, ValueError):
                self.send_error(409, "Acceptance evidence is unavailable")

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(json.dumps({"status": "viewing", "url": f"http://127.0.0.1:{server.server_port}/"}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("operation", choices=["view", "report", "serve"])
    parser.add_argument("id", type=acceptance_id)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    try:
        project = Project(args.root)
        if args.operation == "serve":
            serve(project, args.id, args.port)
            return 0
        result = view(project, args.id) if args.operation == "view" else export_report(project, args.id, json.load(sys.stdin))
    except (Blocked, KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
