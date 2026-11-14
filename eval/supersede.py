"""Move named cells of a run into <run>/superseded/<subject>/, with their attempts, results and infra records, so the
next run of the same command re-runs them from scratch; and append the reason to <run>/superseded/README.md.
usage: .venv/bin/python eval/supersede.py <run dir> <subject> <reason> <cell> [<cell> ...]"""
import json
import os
import shutil
import sys

run, subj, reason, cells = sys.argv[1], sys.argv[2], sys.argv[3], set(sys.argv[4:])
base = os.path.join(run, subj, "baseline")
dest = os.path.join(run, "superseded", subj)
os.makedirs(dest, exist_ok=True)
moved = 0
for c in sorted(cells):
    src = os.path.join(base, "cells", c)
    if os.path.isdir(src):
        target = os.path.join(dest, c)
        if os.path.exists(target):  # superseded before: keep both
            target += ".2"
        shutil.move(src, target)
        moved += 1
for log in ("attempts", "results", "infra"):
    f = os.path.join(base, f"{log}.jsonl")
    if not os.path.exists(f):
        continue
    keep, out = [], {}
    for line in open(f):
        c = json.loads(line).get("cell")
        (out.setdefault(c, []) if c in cells else keep).append(line)
    open(f, "w").writelines(keep)
    for c, lines in out.items():
        with open(os.path.join(dest, f"{c}.{log}.jsonl"), "a") as fh:
            fh.writelines(lines)
with open(os.path.join(run, "superseded", "README.md"), "a") as fh:
    fh.write(f"\n{subj}: {len(cells)} cell(s) superseded and re-run from scratch. {reason}\nCells: {', '.join(sorted(cells))}\n")
print(f"{run} {subj}: moved {moved} cell dirs, {len(cells)} cells")
