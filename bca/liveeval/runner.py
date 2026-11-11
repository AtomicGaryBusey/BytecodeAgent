"""Run the task x path x ISA matrix against a subject, with repair rounds, persistence and
resume.

Layout of a run directory:
    calls.log                         one line per subject call (the quota ledger)
    run.lock                          held (flock) by the live run using this directory
    plan.json                         the cells every invocation planned (for coverage)
    results.jsonl                     one record per finished cell
    attempts.jsonl                    one record per attempt (a model answer that was scored)
    infra.jsonl                       one record per infrastructure failure (retried, not an attempt)
    cells/<task>.<path>.<isa>[.s<k>]/   (.s<k>: sample k of a sampled cell; app cells always are)
        attemptN.prompt.txt  attemptN.response.txt  attemptN.raw.jsonl.gz  attemptN.verdict.json
        attemptN.infraK.response.txt / .raw.jsonl.gz   (a failed call of attempt N, kept for debugging)
        (the raw stream is written plain during the call and gzipped when it returns: since 2026-11-11;
        older runs were compressed in place by eval/compress_raw.py)
        final.disasm.txt  cell.json   (cell.json marks the cell complete: resume skips it)

A cell without cell.json resumes from its last scored attempt (attemptN.verdict.json): attempt
N+1 is a repair of attempt N, so pass@1 stays the first draw.

Infrastructure failures (the subject process failed, auth, rate limit / overload, an unknown
model: every `subject_error`) are not attempts and never reach the repair prompt: the attempt is
retried after a back-off, and the run aborts (cells left incomplete) after
`InfraGuard.max_consecutive` consecutive failures or at once on a fatal one (auth, unknown model).
A `subject_timeout` (the model was too slow) is an attempt like any other.

With a subject and/or variant dimension (`run_plan`), each (subject, variant) pair is a
directory of the layout above, `<run>/<subject>/<variant>/`, with a `group.json` naming it;
`calls.log`, `run.lock` and `plan.json` stay at the top, so the call cap is global across
subjects and variants. Records then carry "subject", "variant" and "battery" fields.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from bca.liveeval.pipelines import Attempt, attempt, code_base, disassemble
from bca.liveeval.prompts import build_prompt
from bca.liveeval.subject import Subject
from bca.liveeval.tasks import Task
from bca.liveeval.token_budget import CURRENT_BATTERY

Variant = Callable[[str, str, str, Task], str]

MAX_ATTEMPTS = 4  # attempt 1 + up to 3 repair rounds


class Budget:
    """Hard cap on subject calls, persisted in <run>/calls.log across restarts and shared by every
    process using the run dir: take() holds an exclusive flock on <run>/calls.lock and re-reads
    calls.log before it appends."""

    def __init__(self, run_dir: Path, max_calls: int):
        self.log = Path(run_dir) / "calls.log"
        self.lockfile = Path(run_dir) / "calls.lock"
        self.max_calls = max_calls
        self._lock = threading.Lock()
        self.used = self._count()

    def _count(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def take(self, label: str) -> bool:
        with self._lock:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lockfile, "a") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                try:
                    self.used = self._count()  # another process may have taken calls since
                    if self.used >= self.max_calls:
                        return False
                    with self.log.open("a") as fh:
                        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {label}\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    self.used += 1
                    return True
                finally:
                    fcntl.flock(lk, fcntl.LOCK_UN)


class RunDirBusy(RuntimeError):
    pass


class RunLock:
    """Exclusive, non-blocking flock on <run>/run.lock for the duration of a live run."""

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "run.lock"
        self._fh = None

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            holder = fh.read().strip() or "unknown"
            fh.close()
            raise RunDirBusy(f"another live run holds {self.path} ({holder}); refusing to run two processes "
                             "on one run dir (wait for it or use another --run-dir)") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid {os.getpid()} since {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        fh.flush()
        self._fh = fh
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            self._fh.seek(0)
            self._fh.truncate()
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


# -- infrastructure failures ----------------------------------------------------------------

# Not worth retrying: the next call fails the same way (credentials, an unknown model id).
_FATAL_RE = re.compile(
    r"(?:status|error|code|http)[\"':= ]{0,4}40[134]\b|\b40[134] (?:unauthorized|forbidden)|authenticat|unauthori[sz]ed|forbidden|invalid[ _-]?api[ _-]?key|x-api-key|not logged in|"
    r"/login|oauth token|not_found_error|permission_error|invalid model|model[^\n]{0,40}not (?:found|exist|available|"
    r"supported)|unknown model|no such model|credits? balance|"
    r"local server unreachable|not an audited version|"  # vendor_local: the Ollama server is down or changed
    r"vllm server unreachable|vllm model [^\n]{0,80} not served|"  # vendor_vllm: likewise for the vLLM server
    r"^foundry: |^fireworks: |^apple: |spend cap reached|fm is paused to protect this mac|"  # the Mac's governor says: do not retry  # vendor_foundry: Azure setup/auth errors; the per-deployment spend cap
    # a subscription's usage window is spent: retrying in minutes cannot help; resume the run later
    r"usage limit|hit your (?:weekly |daily )?limit|exceeded your current (?:token )?quota|exceeded_current_quota|"
    r"quota (?:exhausted|exceeded)|insufficient balance|login_required|token_unauthorized|"
    r"exhausted your quota|individual quota reached|upgrade your subscription to increase your limits|"
    # ^ Antigravity plan quota (seen live: "Individual quota reached. ... Resets in 166h"); capacity and
    #   per-minute 429s stay transient
    r"not signed in|no authentication methods available|please sign in|"
    # Kimi Code (verified with dummy credentials; quota wording from its source)
    r"authorization grant is invalid|invalid_grant|no credential configured|provider\.auth_error|"
    r"recharge your account|please recharge|in arrears|check your account balance|"
    # Codex (protocol/src/error.rs, login/src/auth/manager.rs)
    r"out of credits|spend cap|upgrade to plus|sign(?:ing)? in again|"
    # Grok Build: an effort the model does not offer / served at another effort. (Its "hit the rate limit
    # for your plan ... try again later" is grok's generic fallback for a bare 429: transient.)
    r"unknown effort level|effort mismatch|model rerouted|"
    # GitHub Copilot (vendor_copilot: "copilot <errorType>[/<errorCode>] ..."): account and plan errors; its
    # other rate limits (rate_limited, user_model_rate_limited, ...) stay transient
    r"copilot (?:authentication|authorization|quota)\b|user_weekly_rate_limited|billing_not_configured|"
    # the subject could use a tool (bca.liveeval.vendors): never score such an answer, stop the run
    r"^isolation:",
    re.I,
)


def infra_kind(error: str) -> str:
    """"fatal" (auth, unknown model: abort now) or "transient" (overload, rate limit, a crashed
    process, anything else: back off and retry)."""
    return "fatal" if _FATAL_RE.search(error or "") else "transient"


def is_infra_error(a: Attempt) -> bool:
    """A failed call that says nothing about the model's ability: every subject_error except the
    subject's own budget refusal (which is a budget stop)."""
    return a.category == "subject_error" and not _is_budget_refusal(a)


def _is_budget_refusal(a: Attempt) -> bool:
    return a.category == "subject_error" and a.response is not None and a.response.error == "call budget exhausted"


class InfraGuard:
    """Shared by every worker of a run: counts consecutive infrastructure failures, backs off
    exponentially (base_delay * 2**(k-1), at most max_delay) and aborts the run after
    `max_consecutive` of them, or at once on a fatal one. Any scored attempt resets the count."""

    def __init__(self, max_consecutive: int = 5, base_delay: float = 10.0, max_delay: float = 300.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.max_consecutive = max(1, max_consecutive)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep
        self.consecutive = 0
        self.total = 0
        self.aborted: str | None = None
        self._lock = threading.Lock()

    def ok(self) -> None:
        with self._lock:
            self.consecutive = 0

    def failed(self, error: str) -> bool:
        """Record one failure; sleep the back-off; True if the call should be retried."""
        with self._lock:
            self.consecutive += 1
            self.total += 1
            if self.aborted is None:
                if infra_kind(error) == "fatal":
                    self.aborted = f"fatal infrastructure error (not retried): {error.strip()[:300]}"
                elif self.consecutive >= self.max_consecutive:
                    self.aborted = (f"{self.consecutive} consecutive infrastructure errors "
                                    f"(last: {error.strip()[:300]})")
            if self.aborted:
                return False
            delay = min(self.max_delay, self.base_delay * 2 ** (self.consecutive - 1))
        if delay > 0:
            self.sleep(delay)
        return self.aborted is None


def cell_id(task: Task, path: str, isa: str) -> str:
    """`<task>.<path>.<isa>`, plus `.s<k>` for sample k of a sampled cell (e.g. wc.asm.x86_64.s2)."""
    k = getattr(task, "sample", 0)
    return f"{task.name}.{path}.{isa}" + (f".s{k}" if k else "")


def _ids(task: Task, path: str, isa: str) -> dict:
    """The identifying fields of a record: cell, task, path, isa (and sample when sampled)."""
    k = getattr(task, "sample", 0)
    return {"cell": cell_id(task, path, isa), "task": task.name, "path": path, "isa": isa,
            **({"sample": k} if k else {})}


def _summary(a: Attempt, n: int) -> dict:
    r = a.response
    v = a.verdict
    return {
        "attempt": n, "ok": a.ok, "category": a.category, "wall_s": round(a.wall_s, 3),
        "check_s": round(a.check_s, 3), "code_size": len(a.code) if a.code is not None else None,
        "cases": v.cases if v else None, "failed_cases": v.failed if v else None,
        "model": r.model if r else None, "model_source": r.model_source if r else None,
        "refusal": (r.refusal or None) if r else None, "notes": (getattr(r, "notes", None) or None) if r else None,
        "first_text_s": r.first_text_s if r else None,
        "killed": r.killed if r else None, "response_chars": len(r.text) if r else None,
        "usage": r.usage if r else None, "cost_usd": r.cost_usd if r else None,
        "subject_error": r.error if r and r.error else None, "stream": a.stream or None,
        "failure": a.failure,
    }  # fmt: skip


def _stored_attempts(cdir: Path) -> list[dict]:
    """The scored attempts of an unfinished cell (attempt1..k.verdict.json, contiguous), each with
    its "code" (hex or None) and "response_text"."""
    out = []
    n = 1
    while (cdir / f"attempt{n}.verdict.json").exists():
        try:
            v = json.loads((cdir / f"attempt{n}.verdict.json").read_text())
        except ValueError:
            break  # torn write: redo this attempt
        resp = cdir / f"attempt{n}.response.txt"
        v["response_text"] = resp.read_text() if resp.exists() else ""
        out.append(v)
        n += 1
    return out


def compress_raw(path: str | Path) -> Path | None:
    """Gzip a finished raw stream in place (`x.raw.jsonl` -> `x.raw.jsonl.gz`) and return the new path. A raw
    stream is an audit record nothing scores from; per-token streams (Fireworks: one event per token) run to tens
    of MB uncompressed and compress about 70-fold. Deterministic output (mtime 0), so a re-run is byte-identical."""
    src = Path(path)
    if not src.exists():
        return None
    dst = src.with_name(src.name + ".gz")
    tmp = dst.with_name(dst.name + ".tmp")
    with open(src, "rb") as fin, open(tmp, "wb") as raw_out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, compresslevel=9, mtime=0) as out:
            while chunk := fin.read(1 << 20):
                out.write(chunk)
    tmp.replace(dst)
    src.unlink()
    return dst


def _keep_infra_artifacts(cdir: Path, n: int) -> int:
    """Rename attempt N's response/raw files to attemptN.infraK.*; returns K."""
    k = 1
    while (cdir / f"attempt{n}.infra{k}.response.txt").exists():
        k += 1
    for suffix in ("response.txt", "raw.jsonl", "raw.jsonl.gz"):
        src = cdir / f"attempt{n}.{suffix}"
        if src.exists():
            src.replace(cdir / f"attempt{n}.infra{k}.{suffix}")
    return k


def _append(path: Path, rec: dict, lock: threading.Lock) -> None:
    with lock:
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")


def run_cell(subject: Subject, task: Task, path: str, isa: str, run_dir: Path, *,
             budget: Budget | None = None, max_attempts: int = MAX_ATTEMPTS,
             lock: threading.Lock | None = None, variant: Variant | None = None,
             meta: dict | None = None, label: str = "", timeout: float | None = None,
             guard: InfraGuard | None = None) -> dict:
    """One cell. `variant` rewrites every prompt (first attempt and repairs); `meta` (subject,
    variant, battery, effort, ...) is added to every record; `label` prefixes the calls.log
    entries; `timeout` is the per-call subject timeout (recorded as "timeout_s"). An
    infrastructure failure is retried while `guard` allows it (without a guard: the cell stops,
    incomplete); it is logged to infra.jsonl, never to attempts.jsonl."""
    lock = lock or threading.Lock()
    meta = dict(meta or {})
    if timeout is not None:
        meta.setdefault("timeout_s", timeout)
    cid = cell_id(task, path, isa)
    cdir = run_dir / "cells" / cid
    done = cdir / "cell.json"
    if done.exists():
        return json.loads(done.read_text())
    cdir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    stored = _stored_attempts(cdir)[:max_attempts]
    attempts: list[dict] = [{k: v for k, v in s.items() if k not in ("code", "disasm", "response_text")}
                            for s in stored]
    final_ok = bool(stored and stored[-1].get("ok"))
    final_code = bytes.fromhex(stored[-1]["code"]) if stored and stored[-1].get("code") else None
    # Repair input: what the model wrote (stream: up to the abort) and the exact failure.
    previous = (stored[-1]["response_text"], stored[-1].get("failure") or "") if stored and not final_ok else None
    prior_wall = sum((s.get("wall_s") or 0) + (s.get("check_s") or 0) for s in stored)
    stop: str | None = None  # "budget" | "infra"
    infra_errors = 0
    n = len(stored) + 1
    CURRENT_BATTERY.set(meta.get("battery"))  # token-budget subjects size their calls by it (token_budget)
    while n <= max_attempts and not final_ok:
        prompt = build_prompt(path, isa, task, previous)
        if variant is not None:
            prompt = variant(prompt, path, isa, task)
        if guard is not None and guard.aborted:
            stop = "infra"
            break
        if budget is not None and not budget.take(f"{label}{cid} attempt{n}"):
            stop = "budget"
            break
        (cdir / f"attempt{n}.prompt.txt").write_text(prompt)
        raw = str(cdir / f"attempt{n}.raw.jsonl")
        a = attempt(subject, path, isa, task, prompt, raw_path=raw, timeout=timeout)
        compress_raw(raw)
        (cdir / f"attempt{n}.response.txt").write_text(a.response.text if a.response else "")
        if _is_budget_refusal(a):
            stop = "budget"
            break
        if is_infra_error(a):
            infra_errors += 1
            k = _keep_infra_artifacts(cdir, n)
            err = a.response.error if a.response else a.failure
            _append(run_dir / "infra.jsonl", {
                **_ids(task, path, isa), **meta, "attempt": n, "try": k,
                "error": err, "kind": infra_kind(err), "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "wall_s": round(a.wall_s, 3)}, lock)
            if guard is not None and guard.failed(err):
                continue  # same attempt number, same prompt
            stop = "infra"
            break
        if guard is not None:
            guard.ok()
        s = _summary(a, n)
        rec = {**_ids(task, path, isa), **meta, **s}
        # attempts.jsonl first, then the verdict file resume reads: a crash in between re-runs the
        # attempt, and summaries keep the last record per (cell, attempt).
        _append(run_dir / "attempts.jsonl", rec, lock)
        (cdir / f"attempt{n}.verdict.json").write_text(json.dumps(
            {**s, "code": a.code.hex() if a.code is not None else None,
             "disasm": disassemble(isa, a.code, code_base(task))}, indent=2))
        attempts.append(s)
        final_ok, final_code = a.ok, a.code
        previous = (a.code_text, a.failure)
        n += 1
    record = {
        **_ids(task, path, isa), **meta,
        "pass_at_1": bool(attempts and attempts[0]["ok"]),
        "pass_final": bool(attempts and final_ok),
        "attempts": len(attempts),
        "categories": [s["category"] for s in attempts],
        "wall_s": round(time.perf_counter() - t0 + prior_wall, 3),
        "code_size": len(final_code) if final_ok and final_code is not None else None,
        "model": next((s["model"] for s in attempts if s.get("model")), None),
        "incomplete": stop is not None,
    }  # fmt: skip
    if stored:
        record["resumed_at_attempt"] = len(stored) + 1
    if infra_errors:
        record["infra_errors"] = infra_errors
    if stop:
        record["stopped"] = stop
    (cdir / "final.disasm.txt").write_text(disassemble(isa, final_code, code_base(task)) if attempts else "")
    if stop:
        return record  # not marked complete: a later run resumes it from the next attempt
    done.write_text(json.dumps(record, indent=2))
    _append(run_dir / "results.jsonl", record, lock)
    return record


def _progress_line(prefix: str, rec: dict) -> str:
    tail = ""
    if rec.get("incomplete"):
        tail = " INCOMPLETE (" + ("infrastructure errors" if rec.get("stopped") == "infra" else "budget") + ")"
    return (f"{prefix}{rec['cell']:<44} pass@1={int(rec['pass_at_1'])} final={int(rec['pass_final'])} "
            f"attempts={rec['attempts']} {rec['categories']}" + tail)


def write_plan(run_dir: Path, groups: dict[str, list[str]]) -> None:
    """Merge the planned cells of this invocation into <run>/plan.json ({"cells": {group: [cell
    ids]}}; group "." is the flat layout, else "<subject>/<variant>")."""
    p = run_dir / "plan.json"
    plan = {"cells": {}}
    if p.exists():
        try:
            plan = json.loads(p.read_text())
        except ValueError:
            pass
    cells = plan.setdefault("cells", {})
    for g, ids in groups.items():
        cells[g] = list(dict.fromkeys([*cells.get(g, []), *ids]))
    p.write_text(json.dumps(plan, indent=1))


def run_matrix(subject: Subject, tasks, paths, isas, run_dir: Path, *, jobs: int = 4,
               budget: Budget | None = None, max_attempts: int = MAX_ATTEMPTS,
               progress=print, timeout_for: Callable[[str], float | None] | None = None,
               guard: InfraGuard | None = None, meta: dict | None = None,
               samples: int | None = None) -> list[dict]:
    """The flat (round-1) layout. `tasks` is [task] or [(task, battery)]; `timeout_for(battery)`
    gives the per-call timeout; `samples` as in plan_cells."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    # Cells a pipeline cannot express (Task.na, e.g. SIMD on QBE) are skipped, not failed.
    cells = plan_cells(tasks, paths, isas, samples)
    write_plan(run_dir, {".": [cell_id(t, p, i) for t, _b, p, i in cells]})

    def one(cell):
        t, b, p, i = cell
        rec = run_cell(subject, t, p, i, run_dir, budget=budget, max_attempts=max_attempts, lock=lock,
                       timeout=timeout_for(b) if timeout_for else None, guard=guard, meta=meta)
        progress(_progress_line("", rec))
        return rec

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        return list(ex.map(one, cells))


# -- plans: subjects x variants x cells, one global call cap ---------------------------------


def group_dir(run_dir: Path, subject: str, variant: str) -> Path:
    return run_dir / subject / variant


APP_SAMPLES = 3  # default samples per app cell (design decision 2)


def task_paths(task) -> tuple[str, ...]:
    """The paths a task runs on by default: asm + stream for an app, the three function paths else."""
    from bca.liveeval.prompts import PATHS

    if getattr(task, "is_app", False):
        from bca.liveeval.apps import APP_PATHS

        return APP_PATHS
    return PATHS


def task_samples(task, samples: int | None) -> int:
    """Samples per cell: `samples` if given, else APP_SAMPLES for an app and 1 for a function."""
    if samples is not None:
        return samples
    return APP_SAMPLES if getattr(task, "is_app", False) else 1


def plan_cells(tasks, paths, isas, samples: int | None = None) -> list[tuple[Task, str, str, str]]:
    """(task, battery, path, isa) for the feasible cells; `tasks` is [(task, battery)] or [task].
    `paths` None means each task's own paths (task_paths); a path a task does not have is skipped.
    A sampled cell (an app, or any task when `samples` > 1) is planned once per sample, as a copy
    of the task with `sample` = 1..N, sample-major: sample 1 of every cell, then sample 2, ... (a
    call cap then leaves whole samples, not whole tasks, unrun). Unsampled cells keep their
    historical order and ids."""
    from bca.liveeval.apps import APP_EXTRA_PATHS
    from bca.liveeval.prompts import EXTRA_PATHS

    pairs = [t if isinstance(t, tuple) else (t, "") for t in tasks]
    base = []
    for t, b in pairs:
        own = task_paths(t)
        # opt-in paths (hex): only when asked for explicitly
        allowed = (*own, *APP_EXTRA_PATHS) if getattr(t, "is_app", False) else (*own, *EXTRA_PATHS)
        for p in (own if paths is None else paths):
            if p not in allowed or p in t.na_paths:
                continue
            for i in isas:
                base.append((t, b, p, i))
    n_max = max((task_samples(t, samples) for t, _b, _p, _i in base), default=1)
    out = []
    for k in range(1, n_max + 1):
        for t, b, p, i in base:
            n = task_samples(t, samples)
            sampled = getattr(t, "is_app", False) or n > 1
            if not sampled:
                if k == 1:
                    out.append((t, b, p, i))
            elif k <= n:
                out.append((replace(t, sample=k), b, p, i))
    return out


def check_variants(variants: dict[str, Variant], cells) -> None:
    """Apply every variant to every first-attempt prompt and to a repair prompt before any call is
    made, so a broken variant fails the run up front instead of mid-run."""
    for name, fn in variants.items():
        for t, _b, p, i in cells:
            for prev in (None, ("<code>", "<failure>")):
                try:
                    out = fn(build_prompt(p, i, t, prev), p, i, t)
                except Exception as e:
                    raise ValueError(f"variant {name!r} failed on {cell_id(t, p, i)}: {type(e).__name__}: {e}") from e
                if not isinstance(out, str) or not out.strip():
                    raise ValueError(f"variant {name!r} returned an empty or non-string prompt on {cell_id(t, p, i)}")


def interleave(groups: list, cells: list) -> list[tuple]:
    """(group, cell) work items, round-robin by cell index: cell 0 of every group, then cell 1 of
    every group, ... so a call cap below the worst case spreads over all subjects and variants
    instead of starving the last ones."""
    return [(g, c) for c in cells for g in groups]


def _write_group_meta(gdir: Path, info: dict) -> None:
    """group.json: subject, model_arg, variant, plus the effort / CLI version / timeouts of the first
    invocation at the top level and every invocation under "invocations"."""
    p = gdir / "group.json"
    meta: dict = {}
    if p.exists():
        try:
            meta = json.loads(p.read_text())
        except ValueError:
            meta = {}
    for k, v in info.items():
        if k != "invocation":
            meta.setdefault(k, v)
    meta.setdefault("invocations", []).append(info["invocation"])
    p.write_text(json.dumps(meta, indent=2))


def run_plan(subjects: dict[str, Subject], variants: dict[str, Variant], tasks, paths, isas, run_dir: Path, *,
             jobs: int = 4, budget: Budget | None = None, max_attempts: int = MAX_ATTEMPTS, progress=print,
             models: dict[str, str] | None = None, timeout_for: Callable[[str], float | None] | None = None,
             guard: InfraGuard | None = None, samples: int | None = None) -> list[dict]:
    """Every (subject, variant, cell) in `<run>/<subject>/<variant>/`; one pool of at most `jobs`
    workers and one Budget (calls.log at the top of `run_dir`) for all of them. Work is interleaved
    across (subject, variant) groups by cell index; the returned records are subject-major.
    `samples` as in plan_cells."""
    from bca.liveeval.registry import variant_max_attempts

    run_dir.mkdir(parents=True, exist_ok=True)
    cells = plan_cells(tasks, paths, isas, samples)
    check_variants(variants, cells)
    lock = threading.Lock()
    groups = []
    timeouts = {b: timeout_for(b) for b in dict.fromkeys(b for _t, b, _p, _i in cells)} if timeout_for else {}
    for sname, subj in subjects.items():
        desc = subj.describe() if hasattr(subj, "describe") else {}
        for vname, vfn in variants.items():
            gdir = group_dir(run_dir, sname, vname)
            gdir.mkdir(parents=True, exist_ok=True)
            vmax = variant_max_attempts(vname, max_attempts)
            _write_group_meta(gdir, {
                "subject": sname, "model_arg": (models or {}).get(sname, sname), "variant": vname, **desc,
                "invocation": {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), **desc, "max_attempts": vmax,
                               "timeouts_s": timeouts}})
            groups.append((sname, subj, vname, vfn, gdir, desc, vmax))
    write_plan(run_dir, {f"{g[0]}/{g[2]}": [cell_id(t, p, i) for t, _b, p, i in cells] for g in groups})
    work = interleave(groups, cells)
    # One guard per subject: a subject whose plan quota is spent (or whose CLI keeps failing) stops its
    # own cells only; the other subjects of the run go on. `guard` then reports the union.
    guards = {sname: InfraGuard(guard.max_consecutive, guard.base_delay, guard.max_delay, guard.sleep)
              for sname in subjects} if guard is not None else {}

    def one(w):
        (sname, subj, vname, vfn, gdir, desc, vmax), (t, b, p, i) = w
        rec = run_cell(subj, t, p, i, gdir, budget=budget, max_attempts=vmax, lock=lock, variant=vfn,
                       meta={"subject": sname, "variant": vname, "battery": b, **desc},
                       label=f"{sname}/{vname}/", timeout=timeout_for(b) if timeout_for else None,
                       guard=guards.get(sname))
        progress(_progress_line(f"{sname}/{vname}/", rec))
        return rec

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        recs = list(ex.map(one, work))
    if guard is not None:
        guard.total += sum(g.total for g in guards.values())
        stopped = [f"{s}: {g.aborted}" for s, g in guards.items() if g.aborted]
        if stopped:
            guard.aborted = "; ".join(stopped)
    # subject-major, as planned
    gi = {(g[0], g[2]): n for n, g in enumerate(groups)}
    ci = {cell_id(t, p, i): n for n, (t, _b, p, i) in enumerate(cells)}
    return sorted(recs, key=lambda r: (gi[(r["subject"], r["variant"])], ci[r["cell"]]))
