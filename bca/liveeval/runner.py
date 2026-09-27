"""Run the task x path x ISA matrix against a subject, with repair rounds, persistence and
resume.

Layout of a run directory:
    calls.log                         one line per subject call (the quota ledger)
    results.jsonl                     one record per finished cell
    attempts.jsonl                    one record per attempt
    cells/<task>.<path>.<isa>/
        attemptN.prompt.txt  attemptN.response.txt  attemptN.raw.jsonl  attemptN.verdict.json
        final.disasm.txt  cell.json   (cell.json marks the cell complete: resume skips it)
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bca.liveeval.pipelines import Attempt, attempt, disassemble
from bca.liveeval.prompts import build_prompt
from bca.liveeval.subject import Subject
from bca.liveeval.tasks import Task

MAX_ATTEMPTS = 4  # attempt 1 + up to 3 repair rounds


class Budget:
    """Hard cap on subject calls, persisted in <run>/calls.log across restarts."""

    def __init__(self, run_dir: Path, max_calls: int):
        self.log = run_dir / "calls.log"
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self.used = len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def take(self, label: str) -> bool:
        with self._lock:
            if self.used >= self.max_calls:
                return False
            self.used += 1
            with self.log.open("a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {label}\n")
            return True


def cell_id(task: Task, path: str, isa: str) -> str:
    return f"{task.name}.{path}.{isa}"


def _summary(a: Attempt, n: int) -> dict:
    r = a.response
    v = a.verdict
    return {
        "attempt": n, "ok": a.ok, "category": a.category, "wall_s": round(a.wall_s, 3),
        "check_s": round(a.check_s, 3), "code_size": len(a.code) if a.code is not None else None,
        "cases": v.cases if v else None, "failed_cases": v.failed if v else None,
        "model": r.model if r else None, "first_text_s": r.first_text_s if r else None,
        "killed": r.killed if r else None, "response_chars": len(r.text) if r else None,
        "usage": r.usage if r else None, "cost_usd": r.cost_usd if r else None,
        "subject_error": r.error if r and r.error else None, "stream": a.stream or None,
        "failure": a.failure,
    }  # fmt: skip


def run_cell(subject: Subject, task: Task, path: str, isa: str, run_dir: Path, *,
             budget: Budget | None = None, max_attempts: int = MAX_ATTEMPTS,
             lock: threading.Lock | None = None) -> dict:
    lock = lock or threading.Lock()
    cid = cell_id(task, path, isa)
    cdir = run_dir / "cells" / cid
    done = cdir / "cell.json"
    if done.exists():
        return json.loads(done.read_text())
    cdir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    attempts: list[dict] = []
    previous = None
    final: Attempt | None = None
    budget_hit = False
    for n in range(1, max_attempts + 1):
        if budget is not None and not budget.take(f"{cid} attempt{n}"):
            budget_hit = True
            break
        prompt = build_prompt(path, isa, task, previous)
        (cdir / f"attempt{n}.prompt.txt").write_text(prompt)
        raw = str(cdir / f"attempt{n}.raw.jsonl")
        a = attempt(subject, path, isa, task, prompt, raw_path=raw)
        (cdir / f"attempt{n}.response.txt").write_text(a.response.text if a.response else "")
        s = _summary(a, n)
        (cdir / f"attempt{n}.verdict.json").write_text(json.dumps(
            {**s, "code": a.code.hex() if a.code is not None else None,
             "disasm": disassemble(isa, a.code)}, indent=2))
        rec = {"cell": cid, "task": task.name, "path": path, "isa": isa, **s}
        with lock:
            with (run_dir / "attempts.jsonl").open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        attempts.append(s)
        final = a
        if a.ok:
            break
        if a.category == "subject_error" and a.response and "budget" in a.response.error:
            budget_hit = True
            break
        # Repair input: what the model wrote (stream: up to the abort) and the exact failure.
        previous = (a.code_text, a.failure)
    record = {
        "cell": cid, "task": task.name, "path": path, "isa": isa,
        "pass_at_1": bool(attempts and attempts[0]["ok"]),
        "pass_final": bool(final and final.ok),
        "attempts": len(attempts),
        "categories": [s["category"] for s in attempts],
        "wall_s": round(time.perf_counter() - t0, 3),
        "code_size": len(final.code) if final and final.ok and final.code is not None else None,
        "model": next((s["model"] for s in attempts if s.get("model")), None),
        "incomplete": budget_hit,
    }  # fmt: skip
    (cdir / "final.disasm.txt").write_text(disassemble(isa, final.code) if final else "")
    if budget_hit:
        return record  # not marked complete: a later run with more budget resumes it
    done.write_text(json.dumps(record, indent=2))
    with lock:
        with (run_dir / "results.jsonl").open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    return record


def run_matrix(subject: Subject, tasks, paths, isas, run_dir: Path, *, jobs: int = 4,
               budget: Budget | None = None, max_attempts: int = MAX_ATTEMPTS,
               progress=print) -> list[dict]:
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    cells = [(t, p, i) for t in tasks for p in paths for i in isas]

    def one(cell):
        t, p, i = cell
        rec = run_cell(subject, t, p, i, run_dir, budget=budget, max_attempts=max_attempts, lock=lock)
        progress(f"{rec['cell']:<44} pass@1={int(rec['pass_at_1'])} final={int(rec['pass_final'])} "
                 f"attempts={rec['attempts']} {rec['categories']}"
                 + (" INCOMPLETE (budget)" if rec.get("incomplete") else ""))
        return rec

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        return list(ex.map(one, cells))
