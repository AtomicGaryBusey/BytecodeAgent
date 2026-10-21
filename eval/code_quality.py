"""Quality of passing code (TODO.md item 10): re-run every passing baseline solution on its task's own tests with
instruction counting on, and record executed instructions and code size per cell. No model calls: the recorded
final answer of each passing cell is rebuilt exactly as the harness built it.

- Function batteries: the final attempt's machine code (attemptN.verdict.json "code") re-runs through the same
  differential check the harness uses (bca.liveeval.pipelines.run_differential), with every emulator run
  counting instructions. Executed instructions = the sum over the task's test cases.
- App battery: the final attempt's source (attemptN.response.txt) is re-assembled and linked on its path (asm:
  GNU as + the harness linker; stream: the stream assembler in program mode), then every test runs with its
  stdin delivered whole. Executed instructions = the sum over the tests.

Every rebuilt solution must pass again; one that does not is recorded with ok=false and left out of the
analysis. Writes eval/code_quality.jsonl (one line per passing cell), which eval/analytics.py reads.
Run from the repo root: .venv/bin/python eval/code_quality.py [--jobs N]"""
import argparse
import json
import multiprocessing as mp
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root, for spawned workers
OUT = "eval/code_quality.jsonl"


def _jobs():
    import analytics as A

    by_fn, by_apps = A.load()
    jobs = []
    for data, kind in ((by_fn, "fn"), (by_apps, "apps")):
        for m, cells in data.items():
            for key, r in cells.items():
                if not r["pass_final"]:
                    continue
                n = r["attempts"]
                cdir = os.path.join(r["_dir"], "cells", r["cell"])
                jobs.append({"kind": kind, "model": m, "battery": r["_bat"], "cell": r["cell"],
                             "attempts": n, "verdict": os.path.join(cdir, f"attempt{n}.verdict.json"),
                             "response": os.path.join(cdir, f"attempt{n}.response.txt")})
    return jobs


def _measure_fn(job):
    import bca.difftest as dt
    from bca.liveeval.pipelines import run_differential
    from bca.liveeval.registry import load_battery

    task_name, path, isa = job["cell"].split(".")
    task = next(t for t in load_battery(job["battery"]) if t.name == task_name)
    v = json.load(open(job["verdict"]))
    if not v.get("code"):
        return {**job, "ok": False, "why": "no code recorded"}
    code = bytes.fromhex(v["code"])
    total = [0]
    orig = dt.run_function

    def counting(*a, **kw):
        kw["count_insns"] = True
        res = orig(*a, **kw)
        total[0] += res.insn_count
        return res

    dt.run_function = counting
    try:
        rep = run_differential(task, isa, code)
    finally:
        dt.run_function = orig
    return {**job, "ok": bool(rep.ok), "path": path, "isa": isa, "task": task_name, "cases": rep.cases,
            "insns": total[0], "code_size": len(code)}


def _measure_app(job):
    from bca.image import assemble_program, link_program
    from bca.isa import ISAS
    from bca.liveeval.apps import BY_NAME, app_tests, text_bytes
    from bca.liveeval.pipelines import strip_fences
    from bca.process import run_program
    from bca.stream import StreamAssembler

    task_name, path, isa, _s = job["cell"].split(".")
    task = BY_NAME[task_name]
    text = open(job["response"]).read()
    if path == "asm":
        image = link_program(assemble_program(strip_fences(text), isa), isa, bss_size_limit=task.app.bss_size)
    else:
        sa = StreamAssembler(ISAS[isa], program=True)
        sa.feed(text)
        obj = sa.finish()
        if obj.errors:
            return {**job, "ok": False, "why": obj.errors[0].message}
        image = obj.link(bss_size_limit=task.app.bss_size)
    total, ok = 0, True
    for t in app_tests(task_name):
        res = run_program(image, t.stdin, max_insns=task.app.max_insns)
        total += res.insns
        ok = ok and res.fault is None and res.stdout == t.stdout and res.status == t.status
    return {**job, "ok": ok, "path": path, "isa": isa, "task": task_name, "cases": len(app_tests(task_name)),
            "insns": total, "code_size": len(text_bytes(image))}


def measure(job):
    try:
        out = _measure_fn(job) if job["kind"] == "fn" else _measure_app(job)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        out = {**job, "ok": False, "why": f"{type(exc).__name__}: {exc}"[:300]}
    for k in ("verdict", "response"):
        out.pop(k, None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()
    jobs = _jobs()
    print(f"{len(jobs)} passing cells to re-run", file=sys.stderr)
    done = 0
    with mp.get_context("spawn").Pool(args.jobs) as pool, open(OUT + ".tmp", "w") as fh:
        for rec in pool.imap_unordered(measure, jobs, chunksize=4):
            fh.write(json.dumps(rec) + "\n")
            done += 1
            if done % 250 == 0:
                print(f"{done}/{len(jobs)}", file=sys.stderr)
    os.replace(OUT + ".tmp", OUT)
    bad = [json.loads(l) for l in open(OUT) if not json.loads(l)["ok"]]
    print(f"done: {done} cells, {len(bad)} did not pass again", file=sys.stderr)


if __name__ == "__main__":
    main()
