"""Apriel throughput from the run's own records (no model calls): per-call output tok/s and aggregate tok/s, before
and after 2026-11-14 02:08 (the restart with n-gram speculative decoding and 4 calls at once). Calls are matched to
their start times in calls.log. Run from the repo root: .venv/bin/python eval/apriel/bench/throughput.py"""
import glob
import json
import statistics
from datetime import datetime

RUN = "eval/runs/apriel-budget-20261113"
CUT = "2026-11-14T02:08:00"
start = {}
for line in open(f"{RUN}/calls.log"):
    t, path, att = line.split()
    start[(path.split("/")[-1], int(att.removeprefix("attempt")))] = t
rows = []
for f in glob.glob(f"{RUN}/*/baseline/attempts.jsonl"):
    for line in open(f):
        a = json.loads(line)
        tok = (a.get("usage") or {}).get("completion_tokens")
        if (a["cell"], a["attempt"]) in start and tok and a.get("wall_s"):
            rows.append((start[(a["cell"], a["attempt"])], tok, a["wall_s"]))
for name, sel in (("before (1 call at a time, no speculation)", [r for r in rows if r[0] < CUT]),
                  ("after (4 at once, n-gram x5)", [r for r in rows if r[0] >= CUT])):
    t0 = min(datetime.fromisoformat(r[0]).timestamp() for r in sel)
    t1 = max(datetime.fromisoformat(r[0]).timestamp() + r[2] for r in sel)
    tok = sum(r[1] for r in sel)
    print(f"{name}: {len(sel)} calls, median {statistics.median(r[1] / r[2] for r in sel):.1f} tok/s per call, "
          f"{tok / sum(r[2] for r in sel):.1f} token-weighted, {tok / (t1 - t0):.1f} tok/s aggregate over "
          f"{(t1 - t0) / 3600:.1f} h")
