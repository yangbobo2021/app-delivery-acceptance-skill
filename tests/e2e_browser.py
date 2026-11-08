"""Exercise manual acceptance evidence with a real browser and native Reviewer."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from e2e_definitions import SKILL, codex
from e2e_preparation import IDENTIFIER, outcomes, tool


def run(chrome, model):
    output = Path(tempfile.mkdtemp(prefix="acceptance-browser-e2e-")).resolve()
    print(f"Evidence: {output}", flush=True)
    project = output / "project"
    project.mkdir()
    installed = project / ".agents/skills" / SKILL.name
    shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns("__pycache__"))
    page = project / "index.html"
    page.write_text('<meta charset="utf-8"><title>预约</title><h1>预约确认</h1><p>A 会议室</p>', encoding="utf-8")
    requirements = project / "requirements.md"
    requirements.write_text("R1：确认页应显示「预约确认」和「A 会议室」。\nSC1：打开确认页，观察标题和会议室。\n", encoding="utf-8")
    tool(project, "records", "create", IDENTIFIER, "--request", "冻结浏览器合成验收。", "--reference", "fixture:user")
    r1 = tool(project, "records", "source", "requirements.md", "1", "1")
    sc1 = tool(project, "records", "source", "requirements.md", "2", "2")
    frozen = tool(project, "records", "freeze", IDENTIFIER, payload={
        "request_id": "freeze", "base_sha256": None,
        "requirements": [{"id": "R1", "source": r1}],
        "scenarios": [{"id": "SC1", "requirements": ["R1"], "source": sc1}],
    })
    profile = output / "chrome-profile"
    screenshot = project / "confirmation.png"
    command = [
        chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
        "--window-size=900,550", f"--screenshot={screenshot}", page.as_uri(),
    ]
    observed = subprocess.run(command, capture_output=True, timeout=35)
    assert observed.returncode == 0 and screenshot.read_bytes().startswith(b"\x89PNG"), observed.stderr
    sha = hashlib.sha256(screenshot.read_bytes()).hexdigest()
    browser = tool(project, "operations", "manual", IDENTIFIER, payload={
        "request_id": "browser-1", "definitions_sha256": frozen["sha256"], "scene": "SC1",
        "product_version": hashlib.sha256(page.read_bytes()).hexdigest(),
        "cases": {"TC-browser": [{
            "action": "用 Chrome 打开本地确认页并截取实际画面",
            "expected": "显示预约确认及 A 会议室",
            "observed": "Chrome 产生了实际截图，内容交给独立 Reviewer 查看",
            "performed_by": "fixture Chrome process", "performed_at": datetime.now(timezone.utc).isoformat(),
            "receipt_reference": "actual headless Chrome command and PNG", "outcome": "observed_success",
            "evidence": {"confirmation.png": sha},
        }]},
    })
    prompt = f"请对 {IDENTIFIER} 的 SC1 已有手工观察 browser-1 做独立复审。请核对实际图像里的确认标题和房间，交付复审后停止。"
    thread, plans, _, response = codex(project, installed / "SKILL.md", prompt, model, output / "browser.jsonl", timeout=300, review=True)
    reviews = [item for item in outcomes(project, "review") if item["status"] == "reviewed"]
    assert len(reviews) == 1 and reviews[0]["target"] == "browser-1", response
    assert reviews[0]["judgments"]["TC-browser"]["status"] == "passed", response
    sessions = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    child_saw_image = False
    for logfile in sessions.rglob("*.jsonl"):
        with logfile.open() as events:
            try:
                metadata = json.loads(events.readline()).get("payload", {})
            except ValueError:
                continue
            if metadata.get("parent_thread_id") != thread:
                continue
            child_saw_image |= any("view_image" in line and "confirmation.png" in line for line in events)
    assert child_saw_image, "independent Reviewer did not observably open the actual PNG"
    assert (project / next(iter(browser["results"]["TC-browser"]["attachments"]))).read_bytes() == screenshot.read_bytes()
    print(json.dumps({"status": "passed", "thread": thread, "plan_updates": len(plans), "image_sha256": sha}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    args = parser.parse_args()
    run(args.chrome, args.model)
