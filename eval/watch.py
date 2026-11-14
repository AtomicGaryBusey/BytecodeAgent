"""Block until something in the live eval needs attention, then print what happened and the status report, and exit.

The loop's wake signal (2026-11-14, replacing 30-minute polling): start it in the background; it exits on the first
event, which re-invokes the session. Events:
  - a run stopped making calls (finished, aborted, killed): its last problem lines are printed;
  - a run started (a parked chain woke up), or a chain / resumer script ended;
  - a new abort / isolation / usage-limit line in a live run's log;
  - a memory guard tripped, or MemAvailable fell under --mem-warn MB (default 6000; the guards pause at 4096);
  - a live run made no call for --stall minutes (default 150: a token-budget call can take 70);
  - nothing for --max-hours (default 6): a heartbeat, so a dead watcher is never mistaken for a quiet eval.
Read-only. Run from the repo root: .venv/bin/python eval/watch.py [--poll 20] [--max-hours 6]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import status  # noqa: E402


def events(old: dict, new: dict, a) -> list[str]:
    ev = []
    o, n = {r["run"]: r for r in old["runs"]}, {r["run"]: r for r in new["runs"]}
    for name in o.keys() - n.keys():
        r = status.run_info(status.RUNS / name, None, None)
        tot = f"/{r['planned']}" if r["planned"] else ""
        ev.append(f"run stopped: {name} at {r['cells']}{tot} cells ({r['first']} first, {r['final']} within four)"
                  + "".join(f"\n    ! {p}" for p in r["problems"]))
    ev += [f"run started: {name}" for name in n.keys() - o.keys()]
    for name in o.keys() & n.keys():
        fresh = [p for p in n[name]["problems"] if p not in o[name]["problems"]]
        ev += [f"problem in {name}: {p}" for p in fresh]
        if (n[name].get("idle_min") or 0) >= a.stall > (o[name].get("idle_min") or 0):
            ev.append(f"stall: {name} made no call for {n[name]['idle_min']} min")
    op, np_ = {p["script"] for p in old["parked"]}, {p["script"] for p in new["parked"]}
    live_scripts = np_ | {s for s in op if any(s in " ".join(av) for _, av in status.procs())}
    ev += [f"script ended: {s}" for s in op - live_scripts]
    ev += [f"guard trip: {t}" for t in new["guards"]["trips"] if t not in old["guards"]["trips"]]
    ev += [f"guard stopped: {g}" for g in set(old["guards"]["live"]) - set(new["guards"]["live"])]
    if new["memory"]["available_mb"] < a.mem_warn <= old["memory"]["available_mb"]:
        ev.append(f"memory low: {new['memory']['available_mb']} MB available")
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=float, default=20)
    ap.add_argument("--max-hours", type=float, default=6)
    ap.add_argument("--stall", type=int, default=150)
    ap.add_argument("--mem-warn", type=int, default=6000)
    a = ap.parse_args()
    os.chdir(status.ROOT)
    old, t0 = status.collect(), time.time()
    while True:
        time.sleep(a.poll)
        new = status.collect()
        ev = events(old, new, a)
        if not ev and time.time() - t0 > a.max_hours * 3600:
            ev = [f"heartbeat: no event for {a.max_hours:g} h"]
        if ev:
            print("EVENTS:\n  " + "\n  ".join(ev) + "\n\n" + status.render(new))
            return
        # keep the first snapshot's problem and guard lists (so nothing is reported twice), track the rest
        for r in new["runs"]:
            prev = next((x for x in old["runs"] if x["run"] == r["run"]), None)
            if prev:
                r["problems"] = prev["problems"]
        new["guards"]["trips"] = old["guards"]["trips"]
        old = new


if __name__ == "__main__":
    main()
