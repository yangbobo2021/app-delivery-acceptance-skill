"""Opt-in Chrome and native Codex check of a single open live dashboard."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright

from e2e_three_modes import MATRIX, SKILL, native, prepare
from skill_command import skill_command


def run(output, benchmark, model):
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    revision = prepare(project, benchmark, "A1")
    (project / "build").mkdir()
    subprocess.run(["go", "build", "-o", "build/stock", "."], cwd=project, check=True, timeout=120)
    skill = project / ".agents/skills" / SKILL.name
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
    prompt = MATRIX["cases"]["A1"]["prompt"]
    (output / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    subprocess.run(skill_command("records", project, "create", "AT-delivery", "--request", prompt,
                                 "--reference", "当前用户消息", scripts=skill / "scripts"), check=True)
    server = subprocess.Popen(
        skill_command("results", project, "serve", "AT-delivery", scripts=skill / "scripts"),
        stdout=subprocess.PIPE, text=True,
    )
    timeline = []
    navigations, page_errors = [], []
    report = None
    try:
        url = json.loads(server.stdout.readline())["url"]
        with sync_playwright() as playwright, ThreadPoolExecutor(max_workers=1) as pool:
            browser = playwright.chromium.launch(channel="chrome")
            page = browser.new_page(viewport={"width": 1420, "height": 910})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
            page.goto(url)
            expect(page.locator("#acceptance")).to_have_text("AT-delivery")
            expect(page.locator("#version")).to_have_text("尚未记录需求基线")
            expect(page.get_by_role("columnheader")).to_have_text(
                ["需求 / 规则", "测试场景", "用例", "实际执行", "独立复审"]
            )
            job = pool.submit(native, project, prompt, model, output / "trace.jsonl", 720)

            def capture(name, observed):
                page.screenshot(path=str(output / f"{name}.png"))
                timeline.append({"stage": name, "observed": observed, "captured_at": datetime.now(timezone.utc).isoformat()})

            target = page.locator('[data-case="TC-after-restart"] .case button')
            expect(target).to_be_visible(timeout=360000)
            expect(page.locator('#rows [data-status="not_executed"]')).to_have_count(2)
            capture("01-unexecuted", "两个真实既有用例尚未执行")
            search = page.get_by_role("searchbox")
            search.fill("TC-after-restart")
            page.locator("#filter").select_option("not_executed")
            expect(page.locator('[data-case="TC-same-process"]')).to_have_count(0)
            expect(page.get_by_role("button", name="STOCK-03", exact=True)).to_be_visible()
            page.locator("#filter").select_option("")
            target.click()
            page.get_by_role("tab", name="关联关系").click()
            page.get_by_role("button", name="测试场景 · SC-STOCK", exact=True).click()
            expect(page.locator("#detail-meta")).to_have_text("测试场景 · SC-STOCK")
            page.get_by_role("button", name="← 返回").click()
            expect(page.get_by_role("tab", name="关联关系")).to_have_attribute("aria-selected", "true")
            expect(page.locator("#detail-meta")).to_have_text("自动用例 · TC-after-restart")

            expect(page.locator('#rows [data-status="pending_review"]')).to_have_count(1, timeout=360000)
            expect(page.locator("#detail-update")).to_be_visible()
            expect(page.locator("#detail-meta")).to_have_text("自动用例 · TC-after-restart")
            expect(page.get_by_role("tab", name="关联关系")).to_have_attribute("aria-selected", "true")
            capture("02-review-pending", "正式执行已落盘，打开的详情保留原阅读快照并提示更新")
            page.get_by_role("button", name="更新详情").click()
            expect(page.locator("#detail-update")).to_be_hidden()
            expect(page.locator("#detail-body").get_by_role("button", name="实际执行 · ", exact=False)).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator("#detail")).not_to_be_visible()
            expect(target).to_be_focused()
            expect(search).to_have_value("TC-after-restart")

            defect = page.locator("#rows").get_by_role("button", name="产品缺陷", exact=True)
            expect(defect).to_be_visible(timeout=360000)
            page.locator("#filter").select_option("product_defect")
            expect(page.get_by_role("button", name="STOCK-06", exact=True)).to_be_visible()
            capture("03-reviewed", "同一页面显示重启重试缺陷")
            defect.click()
            expect(page.locator("#detail-body")).to_contain_text("201")
            page.get_by_role("tab", name="依据与记录").click()
            attachment = page.locator("#detail-body").get_by_role("link", name="reviewer.txt")
            assert page.request.get(urljoin(url, attachment.get_attribute("href"))).ok
            capture("04-review-detail", "独立复审可跳转并打开原始意见")
            page.keyboard.press("Escape")
            assert navigations == [url] and not page_errors, (navigations, page_errors)

            thread, turn = job.result(timeout=90)
            assert turn == "completed", turn
            local = project / ".acceptance-local/native-v1/AT-delivery"
            report = next(local.glob("reports/*/report.html"))
            snapshot = json.loads(report.with_name("snapshot.json").read_text())
            for name, sha in snapshot["exported_sha256"].items():
                assert hashlib.sha256((report.parent / name).read_bytes()).hexdigest() == sha
            server.terminate()
            server.wait(timeout=5)
            offline = browser.new_page()
            offline.goto(report.as_uri())
            expect(offline.get_by_role("cell", name="产品缺陷", exact=False)).to_be_visible()
            offline.screenshot(path=str(output / "05-offline.png"))
            browser.close()
            (output / "result.json").write_text(json.dumps({
                "model": model, "thread": thread, "baseline": revision, "navigations": navigations,
                "timeline": timeline, "page_errors": page_errors, "offline_report": str(report),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--model", default="gpt-6-luna")
    args = parser.parse_args()
    run(args.output.resolve(), args.benchmark.resolve(), args.model)
