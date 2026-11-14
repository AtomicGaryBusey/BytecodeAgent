"""Regenerate the body of reports/live-model-eval-analytics.md (everything from "## 1." on) from eval/analytics.py,
keeping the hand-written header and key findings. Run from the repo root: .venv/bin/python eval/update_analytics_report.py"""
import subprocess
import sys

REPORT = "reports/live-model-eval-analytics.md"
body = subprocess.run([sys.executable, "eval/analytics.py"], capture_output=True, text=True, check=True).stdout
assert body.startswith("## 1. Statistical confidence"), body[:80]
old = open(REPORT).read()
head = old[:old.index("## 1. Statistical confidence")]
open(REPORT, "w").write(head + body)
print(f"{REPORT}: body regenerated ({body.count(chr(10))} lines)")
