"""One status report for the live eval: every run making calls (progress, rate, ETA, errors), every chain or
resumer that is parked (and until when), the local-model servers and memory guards, and stray subject processes.

Read-only. Run from the repo root:
  .venv/bin/python eval/status.py            the report
  .venv/bin/python eval/status.py --json     the same facts as JSON (eval/watch.py reads this)

Sources: /proc (processes, memory), each run directory (plan.json, cell records outside superseded/, calls.log,
the current segment of <run>.out after its last "== resumed" line), and the chain / guard logs under eval/.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "eval" / "runs"
BAD = re.compile(r"run aborted|Traceback|isolation:|usage limit|quota exhausted|limit reached", re.I)
SLEEP = re.compile(r"sleeping (?:until (\d\d:\d\d:\d\d)|(\d+)s)|waiting (\d+) min")
GUARD_TRIP = re.compile(r"pausing (Kolibri|Apriel)")


def procs() -> list[tuple[int, list[str]]]:
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            argv = Path(d, "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in argv if a]
        if argv:
            out.append((int(d[6:]), argv))
    return out


def etime(pid: int) -> float:
    try:
        st = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = float(Path("/proc/uptime").read_text().split()[0])
        return boot - int(st[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return 0.0


def cells_done(rd: Path) -> list[Path]:
    return [p for p in rd.glob("*/*/cells/*/cell.json") if "superseded" not in p.parts]


def planned(rd: Path) -> int | None:
    try:
        return sum(len(v) for v in json.loads((rd / "plan.json").read_text())["cells"].values())
    except (OSError, ValueError, KeyError):
        return None


def segment(out: Path) -> str:
    try:
        text = out.read_text(errors="replace")
    except OSError:
        return ""
    return text[text.rfind("== resumed"):] if "== resumed" in text else text


def run_info(rd: Path, pid: int | None, argv: list[str] | None) -> dict:
    done = cells_done(rd)
    n, total = len(done), planned(rd)
    recs = []
    for p in done:
        try:
            recs.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            pass
    seg = segment(Path(str(rd) + ".out"))
    bad = [ln.strip()[:200] for ln in seg.splitlines() if BAD.search(ln)]
    infra = sum(len(open(f).readlines()) for f in glob.glob(str(rd / "*/*/infra.jsonl")))
    last_call = None
    try:
        last = (rd / "calls.log").read_text().strip().splitlines()[-1]
        last_call = last.split(" ", 1)[0]
    except (OSError, IndexError):
        pass
    info = {"run": rd.name, "pid": pid, "cells": n, "planned": total,
            "first": sum(bool(r.get("pass_at_1")) for r in recs), "final": sum(bool(r.get("pass_final")) for r in recs),
            "infra_errors": infra, "problems": bad[-2:], "last_call": last_call}
    if pid:
        up = etime(pid)
        new = sum(1 for p in done if time.time() - p.stat().st_mtime < up)
        info["up_h"] = round(up / 3600, 2)
        if new and total and up > 600:
            info["eta_h"] = round((total - n) / (new / up) / 3600, 1)
        if last_call:
            try:
                info["idle_min"] = round((time.time() - datetime.fromisoformat(last_call).timestamp()) / 60)
            except ValueError:
                pass
        if argv and "--jobs" in argv:
            info["jobs"] = argv[argv.index("--jobs") + 1]
    return info


def parked(ps) -> list[dict]:
    """Chain and resumer scripts under eval/ that are alive but have no live run: what their log last said."""
    out = []
    seen = set()
    for pid, argv in ps:
        script = next((a for a in argv[1:3] if a.endswith(".sh") and "eval/" in a), None)
        if not script or script in seen or "mem-guard" in script:
            continue
        seen.add(script)
        kids = [a for p, a in ps if a[:1] and "bca.liveeval" in " ".join(a) and parent(p) in family(pid, ps)]
        if kids:
            continue
        log = ROOT / (script[:-3] + ".log")
        cands = [log] + sorted(ROOT.glob(script[:-3].rsplit("/", 1)[0] + "/*" + Path(script).stem.split("-20")[0] + "*.log"))
        line = ""
        for c in cands:
            try:
                lines = [ln for ln in c.read_text(errors="replace").splitlines() if ln.strip()]
            except OSError:
                continue
            if lines:
                line = lines[-1][:200]
                break
        wake = None  # from the script's own `sleep N` child: when it will next do something
        for p, a in ps:
            if os.path.basename(a[0]) == "sleep" and len(a) == 2 and a[1].isdigit() and parent(p) in family(pid, ps):
                left = int(a[1]) - etime(p)
                if left > 90:  # the short polling sleeps of a waiter say nothing
                    wake = datetime.fromtimestamp(time.time() + left).strftime("%a %m-%d %H:%M")
        out.append({"script": script, "pid": pid, "last": line, "sleeping": bool(SLEEP.search(line)), "wakes": wake})
    return out


def parent(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def family(pid: int, ps) -> set[int]:
    fam, grew = {pid}, True
    while grew:
        grew = False
        for p, _ in ps:
            if p not in fam and parent(p) in fam:
                fam.add(p)
                grew = True
    return fam


def memory() -> dict:
    m = {}
    for ln in Path("/proc/meminfo").read_text().splitlines():
        k, v = ln.split(":")
        m[k] = int(v.split()[0]) // 1024
    return {"available_mb": m["MemAvailable"], "free_mb": m["MemFree"], "swap_used_mb": m["SwapTotal"] - m["SwapFree"]}


def servers() -> list[str]:
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}} ({{.Status}})"], capture_output=True, text=True,
                           timeout=20)
        return [ln for ln in r.stdout.splitlines() if "vllm" in ln or "ollama" in ln]
    except (OSError, subprocess.SubprocessError):
        return []


def guards(ps) -> dict:
    live = sorted({a[-1] if a[-1].endswith(".sh") else a[1] for _, a in ps if any("mem-guard" in x for x in a)})
    trips = []
    for f in list(ROOT.glob("eval/**/mem-guard*.log")) + list(RUNS.glob("*mem-guard*.log")) + list(RUNS.glob("kolibri-hex*.log")):
        try:
            trips += [f"{f.name}: {ln.strip()[:160]}" for ln in f.read_text(errors="replace").splitlines()
                      if GUARD_TRIP.search(ln)]
        except OSError:
            pass
    return {"live": live, "trips": trips[-3:]}


def collect() -> dict:
    ps = procs()
    runs = []
    for pid, argv in ps:
        if "bca.liveeval" in argv and "run" in argv and "--run-dir" in argv:
            runs.append(run_info(ROOT / argv[argv.index("--run-dir") + 1], pid, argv))
    subject = {}
    for _, argv in ps:
        exe = os.path.basename(argv[0])
        if exe in ("claude", "agy", "muse", "kimi", "grok", "codex", "copilot", "vibe") and len(argv) > 2 and \
                any(a in ("-p", "exec", "--output-format", "--single", "--prompt-file") or a.startswith("--single=")
                    for a in argv):
            subject[exe] = subject.get(exe, 0) + 1
    return {"time": datetime.now().isoformat(timespec="seconds"), "runs": sorted(runs, key=lambda r: r["run"]),
            "parked": parked(ps), "memory": memory(), "servers": servers(), "guards": guards(ps),
            "subject_processes": subject}


def render(s: dict) -> str:
    out = [f"{s['time']}  memory: {s['memory']['available_mb']:,} MB available, "
           f"{s['memory']['swap_used_mb']:,} MB swap"]
    out.append(f"\nRuns making calls ({len(s['runs'])}):")
    for r in s["runs"]:
        tot = f"/{r['planned']}" if r["planned"] else ""
        eta = f", ETA {r['eta_h']} h" if r.get("eta_h") is not None else ""
        idle = f", last call {r['idle_min']} min ago" if r.get("idle_min") is not None else ""
        out.append(f"  {r['run']}: {r['cells']}{tot} cells ({r['first']} first, {r['final']} within four), "
                   f"jobs {r.get('jobs', '?')}, infra {r['infra_errors']}{eta}{idle}")
        out += [f"    ! {p}" for p in r["problems"]]
    if not s["runs"]:
        out.append("  none")
    out.append(f"\nParked scripts ({len(s['parked'])}):")
    out += [f"  {p['script']}: " + (f"wakes {p['wakes']}; " if p.get("wakes") else "") + (p["last"] or "no log line")
            for p in s["parked"]] or ["  none"]
    out.append("\nLocal servers: " + (", ".join(s["servers"]) or "none"))
    out.append("Memory guards: " + (", ".join(s["guards"]["live"]) or "none live")
               + "".join(f"\n  trip: {t}" for t in s["guards"]["trips"]))
    out.append("Subject CLI processes: " + (", ".join(f"{k} x{v}" for k, v in s["subject_processes"].items()) or "none"))
    return "\n".join(out)


if __name__ == "__main__":
    os.chdir(ROOT)
    st = collect()
    print(json.dumps(st, indent=1) if "--json" in sys.argv else render(st))
