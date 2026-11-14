"""Regenerate every generated table, the analytics report and the dashboard from the recorded runs, in one command.

  .venv/bin/python eval/regenerate.py              tables -> reports, analytics, dashboard
  .venv/bin/python eval/regenerate.py --quality    also rebuild eval/code_quality.jsonl first (re-runs every passing
                                                   solution, ~20 min; needed when new passing cells were added)
  .venv/bin/python eval/regenerate.py --check      only the guard below; change nothing
  --force   regenerate even though the guard found unfinished runs that are not excluded

Guard: a run directory that is unfinished (fewer cell records than its plan, or a live process) and is neither a
smoke nor listed in eval/model_names.IN_PROGRESS (or KNOWN_PARTIAL, the old runs reported as they stand) would put a partial battery into the tables. The command stops and
names them; add them to IN_PROGRESS (or let them finish), then run again.

What it does not do: prose. Findings, per-model notes and the summaries in the reports are written by hand after
reading the regenerated tables (the diff printed at the end shows which tables moved). Publishing the dashboard
artifact is a separate, deliberate step (eval/out/dashboard-artifact.html is the file to publish).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import status  # noqa: E402
from model_names import IN_PROGRESS, KNOWN_PARTIAL  # noqa: E402

PY = sys.executable
OUT = ROOT / "eval" / "out"
# generator -> the reports whose generated tables it fills (matched by table header; prose is left alone)
TABLES = [
    ("xv_tables", ["reports/live-model-eval-cross-vendor.md", "README.md"]),
    ("app_tables", ["reports/live-model-eval-apps.md"]),
    ("hex_tables", ["reports/live-model-eval-hex.md"]),
    ("harness_tables", ["reports/live-model-eval-agent-harness.md"]),
    ("effort_tables", ["reports/live-model-eval-effort.md"]),
]


def unfinished() -> list[str]:
    live = {r["run"] for r in status.collect()["runs"]}
    bad = []
    for rd in sorted(p for p in status.RUNS.iterdir() if p.is_dir()):
        if "smoke" in rd.name or rd.name in IN_PROGRESS or rd.name in KNOWN_PARTIAL:
            continue
        total = status.planned(rd)
        if total is None:
            continue
        n = len(status.cells_done(rd))
        if rd.name in live or n < total:
            bad.append(f"{rd.name}: {n}/{total} cells" + (" (running)" if rd.name in live else ""))
    return bad


def run(argv: list[str], out: Path | None = None) -> None:
    print("+", " ".join(argv) + (f" > {out.relative_to(ROOT)}" if out else ""), flush=True)
    r = subprocess.run(argv, cwd=ROOT, capture_output=bool(out), text=True)
    if out:
        out.write_text(r.stdout)
        out.with_suffix(".err").write_text(r.stderr)
    if r.returncode != 0:
        raise SystemExit(f"FAILED ({r.returncode}): {' '.join(argv)}\n{(r.stderr or '')[-800:] if out else ''}")
    if out and not r.stdout.strip():
        raise SystemExit(f"FAILED: {' '.join(argv)} printed nothing")


def main() -> None:
    os.chdir(ROOT)
    bad = unfinished()
    if bad:
        print("Unfinished runs that are NOT excluded (eval/model_names.IN_PROGRESS):\n  " + "\n  ".join(bad))
        if "--force" not in sys.argv:
            raise SystemExit("stopped: exclude them or let them finish (or --force)")
    else:
        print(f"guard: every run outside IN_PROGRESS ({len(IN_PROGRESS)} excluded) is complete")
    if "--check" in sys.argv:
        return
    OUT.mkdir(exist_ok=True)
    for gen, reports in TABLES:
        out = OUT / f"{gen}.md"
        run([PY, f"eval/{gen}.py"], out)
        for rep in reports:
            run([PY, "eval/update_report_tables.py", rep, str(out.relative_to(ROOT))])
    if "--quality" in sys.argv:
        run([PY, "eval/code_quality.py"])
    run([PY, "eval/update_analytics_report.py"])
    run([PY, "eval/dashboard.py", "--artifact", "eval/out/dashboard-artifact.html"])
    print("\nChanged files (tables only; write the prose by hand):")
    subprocess.run(["git", "--no-pager", "diff", "--stat", "--", "reports", "README.md", "eval/code_quality.jsonl"], cwd=ROOT)


if __name__ == "__main__":
    main()
