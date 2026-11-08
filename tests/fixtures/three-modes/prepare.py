"""Create isolated task repos. Never copy the scoring matrix into a task repo."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
BASE = json.loads((HERE / "matrix.json").read_text())["benchmark"]


def write(project, name, body):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def git(project, *args):
    return subprocess.check_output(["git", "-C", str(project), *args], text=True).strip()


def commit(project, message):
    git(project, "add", "-A")
    git(project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", message)
    return git(project, "rev-parse", "HEAD")


def stock(project, benchmark, case):
    for name in ("main.go", "go.mod", "docs/requirements.md", "README.md"):
        write(project, name, git(benchmark, "show", f"{BASE}:projects/go-api/{name}") + "\n")
    write(project, "docs/scenario.md", "# STOCK-03 / STOCK-06\n\nSC-STOCK：同一进程内同参重试返回 200 与首次响应，同 ID 改数量返回 409，库存保持 8。正常停止并用同一目录重启后同参重试仍返回 200 与首次响应，库存仍为 8。\n")
    shutil.copy(HERE / "stock_check.py", project / "stock_check.py")
    if case == "A1":
        shutil.copy(HERE / "stock_restart.py", project / "stock_restart.py")
    with (project / "README.md").open("a", encoding="utf-8") as stream:
        stream.write("\nExisting tests run from this root with Python 3. Run `python3 stock_check.py` for same-process retries. "
                     "If present, run `python3 stock_restart.py` for persistence across a restart.\n")


def development(project, case):
    write(project, "docs/requirements.md", "# Pricing\n\nPRICE-01：总价为单价（分）乘数量。\nPRICE-02：购买数量达到 10 时，在总价上打九折，结果向下取整为整数分。数量少于 10 时原价。\n")
    write(project, "price.py", "def total(cents, quantity):\n    return cents * quantity\n")
    write(project, "tests/test_price.py", "import unittest\nfrom price import total\n\n\nclass BasePrice(unittest.TestCase):\n    def test_regular(self):\n        self.assertEqual(total(125, 3), 375)\n")
    if case == "B3":
        write(project, "tools/environment.py", """import sys
from pathlib import Path

config = Path(".local-settings")
if sys.argv[1:] == ["--init"]:
    config.write_text("development\\n")
if not config.exists():
    sys.exit("Missing .local-settings; follow README")
from price import total
assert total(100, 1) == 100
print("environment ready")
""")
        probe = "Before other test commands, run `PYTHONPATH=. python3 tools/environment.py` and keep its output. If it reports missing local settings, run the offline initializer `PYTHONPATH=. python3 tools/environment.py --init`, then rerun the original probe.\n"
    else:
        probe = "Environment probe is `python3 -c 'from price import total; assert total(100, 1) == 100'`.\n"
    write(project, "README.md", "# Pricing\n\n" + probe + "Existing regression: `python3 -m unittest discover -s tests -v`.\n")


def archive(project, item, revision):
    run = subprocess.run([sys.executable, "checks.py", item], cwd=project, capture_output=True, check=True, text=True)
    log = f"evidence/{item}.log"
    write(project, log, run.stdout)
    hashes = {p: hashlib.sha256((project / p).read_bytes()).hexdigest() for p in ("pricing.py", "checks.py", log)}
    write(project, f"evidence/{item}.json", json.dumps({
        "requirement": item, "revision": revision, "exit_code": run.returncode, "log": log, "sha256": hashes,
    }, indent=2) + "\n")


def inventory(project, case):
    if case == "C3":
        write(project, "README.md", "# Delivery sources\n\nINV-E was defined in docs/archived-policy.md. The archive path is authoritative when accessible. INV-F has current requirements and a conflicting operator guide.\n")
        write(project, "docs/requirements.md", "INV-F：导出保留 30 天历史。\n")
        write(project, "docs/operator.md", "INV-F：部署运行说明规定只保留 7 天。两份文件均未标记替代关系。\n")
        write(project, "policy.py", "retention_days = 7\n")
        return
    write(project, "README.md", "# Delivery sources\n\nConfirmed obligations are in docs/requirements.md. INV-D has only docs/notes.md. Historical receipts are in evidence/. Each receipt identifies the product and check hashes and raw log.\n\nExisting checks (run only when requested): `python3 checks.py INV-A`, `python3 checks.py INV-B`, `python3 checks.py INV-C`.\n")
    write(project, "docs/requirements.md", "INV-A：基础费用 100 分。\nINV-B：加急费在基础费用上加 25 分。\nINV-C：返还金额为 30 分。\n")
    write(project, "docs/notes.md", "INV-D：讨论是否增加取消费。尚无正式决定。\n")
    write(project, "pricing.py", "def base(): return 100\ndef rush(): return 125\ndef refund(): return 20\n")
    write(project, "checks.py", """import sys
from pricing import base, rush, refund

item = sys.argv[1]
handler, expected = {"INV-A": (base, 100), "INV-B": (rush, 125), "INV-C": (refund, 30)}[item]
observed = handler()
print(f"{item}: expected={expected}; observed={observed}", flush=True)
assert observed == expected, (expected, observed)
""")
    old = commit(project, "Historical implementation")
    archive(project, "INV-B", old)
    with (project / "pricing.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# Updated implementation package.\n")
    current = commit(project, "Current implementation")
    archive(project, "INV-A", current)


def prepare(project, benchmark, case):
    project.mkdir(parents=True)
    git(project, "init", "-q")
    write(project, ".gitignore", ".acceptance-local/\n.agents/\nbuild/\n__pycache__/\n.local-settings\n")
    if case[0] == "A":
        stock(project, benchmark, case)
    elif case[0] == "B":
        development(project, case)
    else:
        inventory(project, case)
    return commit(project, "Freeze acceptance inputs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument("output", type=Path)
    parser.add_argument("benchmark", type=Path)
    args = parser.parse_args()
    print(prepare(args.output, args.benchmark, args.case))
