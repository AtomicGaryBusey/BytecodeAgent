"""Regression tests for the live-eval harness review (H1-H8, L3): infrastructure errors, resume
of partially attempted cells, interleaving and coverage, variant pooling, cross-process call cap,
per-battery timeouts, effort / environment of the subject, per-variant dry-run estimates, named
memory windows. FakeSubjects and fake `claude` executables only: no model calls."""

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from bca.difftest import MemCase, Mismatch, Ptr
from bca.liveeval import registry
from bca.liveeval.__main__ import main as cli_main
from bca.liveeval.pipelines import _mem_detail, param_names
from bca.liveeval.plan import Rates
from bca.liveeval.runner import Budget, InfraGuard, RunLock, infra_kind, interleave, run_cell, run_plan
from bca.liveeval.subject import ClaudeSubject, FakeSubject, Response, Subject, child_env
from bca.liveeval.summarize import compare, load, summarize
from bca.liveeval.tasks import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
SUM = BY_NAME["sum_to"]
LSUM = dataclasses.replace(SUM, name="lsum", ref_name="sum_to", signature="int64_t lsum(int64_t n)")
ASUM = dataclasses.replace(SUM, name="asum", ref_name="sum_to", signature="int64_t asum(int64_t n)")
BSUM = dataclasses.replace(SUM, name="bsum", ref_name="sum_to", signature="int64_t bsum(int64_t n)")

X86 = """{n}:
    xor eax, eax
    test rdi, rdi
    jle .Ldone
.Lloop:
    add rax, rdi
    dec rdi
    jnz .Lloop
.Ldone:
    ret
"""
A64 = """{n}:
    mov x1, x0
    mov x0, #0
.Lloop:
    cmp x1, #0
    b.le .Ldone
    add x0, x0, x1
    sub x1, x1, #1
    b .Lloop
.Ldone:
    ret
"""
OVERLOADED = "exit status 1: API Error: 529 Overloaded"


def good(isa, name="sum_to"):
    return (X86 if isa == "x86_64" else A64).format(n=name)


def bad(isa, name="sum_to"):
    return good(isa, name).replace("add rax, rdi", "add rax, 2").replace("add x0, x0, x1", "add x0, x0, #2")


def shout(prompt, path, isa, task):
    return "VARIANT-SHOUT " + prompt


def legacy(prompt, path, isa, task):
    return prompt + "\nHouse style: AT&T syntax.\n"


@pytest.fixture
def fake_batteries(monkeypatch):
    long_mod = types.ModuleType("fake_tasks_long_h")
    long_mod.TASKS = (LSUM,)
    adv = types.ModuleType("fake_adversarial_h")
    adv.TASKS = (ASUM, BSUM)
    adv.VARIANTS = {"baseline": registry.baseline, "shout": shout, "legacy": legacy, "capped": shout}
    adv.VARIANT_INFO = {"baseline": {"conflicts_with_format": False},
                        "shout": {"conflicts_with_format": False},
                        "legacy": {"conflicts_with_format": True},
                        "capped": {"conflicts_with_format": False, "max_attempts": 1}}
    monkeypatch.setitem(sys.modules, "fake_tasks_long_h", long_mod)
    monkeypatch.setitem(sys.modules, "fake_adversarial_h", adv)
    monkeypatch.setitem(registry.BATTERY_MODULES, "long", "fake_tasks_long_h")
    monkeypatch.setitem(registry.BATTERY_MODULES, "adversarial", "fake_adversarial_h")
    monkeypatch.setattr(registry, "VARIANTS_MODULE", "fake_adversarial_h")
    return long_mod, adv


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


NOSLEEP = dict(sleep=lambda s: None)


# -- H1: infrastructure errors are not attempts ----------------------------------------------------------


class Down(Subject):
    """The reviewer's outage.py subject: every call fails like an overloaded API."""

    def __init__(self, error=OVERLOADED):
        self.calls = 0
        self.error = error

    def complete(self, prompt, on_text=None, *, raw_path=""):
        self.calls += 1
        return Response("", ok=False, error=self.error)


def test_outage_aborts_run_leaves_cells_incomplete_and_resumes(tmp_path, capsys):
    rd = tmp_path / "outage"
    down = Down()
    argv = ["run", "--run-dir", str(rd), "--max-calls", "100", "--tasks", "sum_to,gcd", "--paths", "stream",
            "--isas", "x86_64", "--model", "opus", "--jobs", "1", "--infra-backoff", "0", "--max-infra-errors", "3"]
    assert cli_main(argv, subject_factory=lambda m: down) == 3
    err = capsys.readouterr().err
    assert "run aborted: 3 consecutive infrastructure errors" in err and "529 Overloaded" in err
    assert down.calls == 3
    g = rd / "opus" / "baseline"
    assert not list(g.glob("cells/*/cell.json"))  # nothing marked complete
    assert not (g / "attempts.jsonl").exists() and not (g / "results.jsonl").exists()  # no attempt was scored
    infra = _lines(g / "infra.jsonl")
    assert len(infra) == 3 and all(r["attempt"] == 1 and r["kind"] == "transient" for r in infra)
    assert (g / "cells/sum_to.stream.x86_64/attempt1.infra1.response.txt").exists()
    # service back: the same command resumes; the first scored draw is attempt 1 (pass@1 intact)
    ok = FakeSubject([good("x86_64")] + [bad("x86_64", "gcd")] * 4, model="m")
    assert cli_main(argv, subject_factory=lambda m: ok) == 0
    rec = json.loads((g / "cells/sum_to.stream.x86_64/cell.json").read_text())
    assert rec["pass_at_1"] and rec["attempts"] == 1 and rec["categories"] == [None]
    assert [a["attempt"] for a in _lines(g / "attempts.jsonl") if a["cell"] == "sum_to.stream.x86_64"] == [1]
    assert "infrastructure errors (retried calls, not attempts) 3" in summarize(rd)


def test_transient_infra_error_is_retried_and_never_fed_to_repair(tmp_path):
    subj = FakeSubject([bad("x86_64"), {"error": OVERLOADED}, {"error": OVERLOADED}, good("x86_64")])
    slept = []
    guard = InfraGuard(max_consecutive=5, base_delay=2, sleep=slept.append)
    rec = run_cell(subj, SUM, "stream", "x86_64", tmp_path, budget=Budget(tmp_path, 10), guard=guard)
    assert rec["attempts"] == 2 and rec["categories"] == ["wrong_result", None] and rec["infra_errors"] == 2
    assert not rec["pass_at_1"] and rec["pass_final"]
    assert slept == [2, 4]  # exponential back-off
    # the three calls of attempt 2 got the same repair prompt, built from the scored attempt 1
    assert subj.prompts[1] == subj.prompts[2] == subj.prompts[3]
    assert "add rax, 2" in subj.prompts[3] and "model call failed" not in subj.prompts[3]
    assert [a["attempt"] for a in _lines(tmp_path / "attempts.jsonl")] == [1, 2]
    assert (tmp_path / "calls.log").read_text().count("\n") == 4  # every call is a call
    assert guard.consecutive == 0 and guard.total == 2


def test_fatal_infra_error_aborts_at_once():
    for err in ("exit status 1: Invalid API key · Please run /login", "result error: error 401",
                'API Error: 404 {"type":"error","error":{"type":"not_found_error","message":"model: claude-nope"}}'):
        assert infra_kind(err) == "fatal", err
    for err in (OVERLOADED, "result error: error_during_execution 429", "exit status -9: "):
        assert infra_kind(err) == "transient", err
    guard = InfraGuard(max_consecutive=5, base_delay=0)
    assert not guard.failed("exit status 1: model 'opsu' not found") and "fatal" in guard.aborted


def test_bad_model_alias_run_aborts_after_one_call(tmp_path, capsys):
    down = Down("result error: error 404 not_found_error model: opsu")
    argv = ["run", "--run-dir", str(tmp_path), "--max-calls", "50", "--tasks", "sum_to", "--paths", "stream,ir",
            "--model", "opsu", "--infra-backoff", "0", "--jobs", "1"]
    assert cli_main(argv, subject_factory=lambda m: down) == 3
    assert down.calls == 1 and "fatal infrastructure error" in capsys.readouterr().err


def test_subject_timeout_still_counts_as_attempt(tmp_path):
    subj = FakeSubject([{"timeout": True, "text": "sum_to:\n"}, good("x86_64")])
    rec = run_cell(subj, SUM, "stream", "x86_64", tmp_path, guard=InfraGuard(base_delay=0))
    assert rec["categories"] == ["subject_timeout", None] and rec["attempts"] == 2 and "infra_errors" not in rec


# -- H2: resume continues a partially attempted cell ----------------------------------------------------


def test_resume_continues_from_next_attempt(tmp_path):
    """resume.py: cap 1 stops after a failed attempt 1; the next run makes attempt 2 as a repair."""
    rd = tmp_path / "resume"
    argv = ["run", "--run-dir", str(rd), "--tasks", "sum_to", "--paths", "stream", "--isas", "x86_64",
            "--model", "opus", "--jobs", "1"]
    s1 = FakeSubject([bad("x86_64"), good("x86_64")], model="m")
    assert cli_main(argv + ["--max-calls", "1"], subject_factory=lambda m: s1) == 2
    s2 = FakeSubject([good("x86_64")], model="m")
    assert cli_main(argv + ["--max-calls", "5"], subject_factory=lambda m: s2) == 0
    assert s2.calls == 1 and "Your previous attempt" in s2.prompts[0] and "add rax, 2" in s2.prompts[0]
    g = rd / "opus" / "baseline"
    rec = json.loads((g / "cells/sum_to.stream.x86_64/cell.json").read_text())
    assert (rec["pass_at_1"], rec["pass_final"], rec["attempts"], rec["resumed_at_attempt"]) == (False, True, 2, 2)
    assert rec["categories"] == ["wrong_result", None]
    assert [(a["attempt"], a["ok"]) for a in _lines(g / "attempts.jsonl")] == [(1, False), (2, True)]
    assert (rd / "calls.log").read_text().count("\n") == 2
    cells, atts = load(g)
    assert len(cells) == 1 and len(atts) == 2


def test_summaries_ignore_legacy_orphan_attempts(tmp_path):
    """A pre-fix resume restarted the cell at attempt 1: attempts.jsonl has 1, 1, 2 for a 2-attempt cell."""
    cell = {"cell": "sum_to.stream.x86_64", "task": "sum_to", "path": "stream", "isa": "x86_64",
            "pass_at_1": False, "pass_final": True, "attempts": 2, "categories": ["wrong_result", None],
            "wall_s": 1, "code_size": 10, "model": "m", "incomplete": False}
    att = lambda n, ok: {"cell": cell["cell"], "task": "sum_to", "path": "stream", "isa": "x86_64", "attempt": n,  # noqa: E731
                         "ok": ok, "category": None if ok else "wrong_result", "wall_s": 1, "check_s": 0,
                         "first_text_s": 0.1, "model": "m", "stream": None}
    (tmp_path / "results.jsonl").write_text(json.dumps(cell) + "\n")
    (tmp_path / "attempts.jsonl").write_text("\n".join(json.dumps(a) for a in (
        att(1, False), att(1, False), att(2, True), att(3, False),
        {**att(1, False), "cell": "gcd.stream.x86_64"})) + "\n")  # an unfinished cell's attempt
    cells, atts = load(tmp_path)
    assert sorted(a["attempt"] for a in atts) == [1, 2]
    assert "attempts (calls) 2" in summarize(tmp_path)


# -- H3: interleaving, coverage, common cells ------------------------------------------------------------


class Nonsense(Subject):
    """cap.py's subject: every answer is a format failure."""

    def __init__(self):
        self.calls = 0

    def complete(self, prompt, on_text=None, *, raw_path=""):
        self.calls += 1
        return Response("nonsense line", model="m")


def test_interleave_order():
    assert interleave(["a", "b"], [1, 2]) == [("a", 1), ("b", 1), ("a", 2), ("b", 2)]


def test_cap_spreads_over_subjects_and_variants(tmp_path, fake_batteries):
    subjects = {"a": Nonsense(), "b": Nonsense()}
    rd = tmp_path / "cap"
    argv = ["run", "--run-dir", str(rd), "--max-calls", "4", "--tasks", "adversarial", "--paths", "stream",
            "--isas", "x86_64,aarch64", "--subjects", "a,b", "--variant", "baseline,shout", "--jobs", "1",
            "--max-attempts", "1", "--effort", "high"]
    assert cli_main(argv, subject_factory=lambda m: subjects[m]) == 2
    assert subjects["a"].calls == 2 and subjects["b"].calls == 2  # before: a took all 4
    for s in "ab":
        for v in ("baseline", "shout"):
            assert len(_lines(rd / s / v / "results.jsonl")) == 1, (s, v)
    plan = json.loads((rd / "plan.json").read_text())["cells"]
    assert sorted(plan) == ["a/baseline", "a/shout", "b/baseline", "b/shout"] and len(plan["a/shout"]) == 4


def test_summary_restricts_cross_subject_tables_to_common_cells(tmp_path):
    rd = tmp_path / "r"
    subj = {"a": FakeSubject([good("x86_64"), good("x86_64", "asum")], model="ma"),
            "b": FakeSubject([good("x86_64"), good("x86_64", "asum")], model="mb")}
    run_plan(subj, {"baseline": registry.baseline}, [(SUM, "round1"), (ASUM, "round1")], ["stream"], ["x86_64"], rd,
             jobs=1, budget=Budget(rd, 3), progress=lambda m: None)
    s = summarize(rd)
    # b completed 1 of its 2 planned cells: the table compares a and b on that one cell only
    assert "| b | mb | baseline | 2 | 1 |" in s
    assert "Restricted to the 1 cells completed by every subject; 1 of 2 cells excluded" in s
    assert "| a | stream | x86_64 | 1 | 1/1 (100%) |" in s


# -- H4: no pooling of variants; format probes labelled ---------------------------------------------------


def test_cross_tables_are_baseline_only_and_probe_is_labelled(tmp_path, fake_batteries):
    """mix.py: baseline answers well, the legacy (conflicts_with_format) variant answers in AT&T."""
    rd = tmp_path / "mix"

    class F(FakeSubject):
        def complete(self, prompt, on_text=None, *, raw_path="", timeout=None):
            self.script = [bad("x86_64", "asum") if "House style" in prompt
                           else good("x86_64", "asum" if "asum(" in prompt else "bsum")]
            return super().complete(prompt, on_text, raw_path=raw_path, timeout=timeout)

    assert cli_main(["run", "--run-dir", str(rd), "--max-calls", "20", "--tasks", "adversarial", "--paths", "stream",
                     "--isas", "x86_64", "--model", "opus", "--variant", "baseline,legacy", "--max-attempts", "1"],
                    subject_factory=lambda m: F([], model="claude-opus-5-5")) == 0
    s = summarize(rd)
    i = s.index("### By subject × battery (baseline variant)")
    assert "| opus | adversarial | 2 | 2/2 (100%) |" in s[i:i + 400]  # not 2/4: legacy is not pooled in
    assert "legacy (format-compliance probe)" in s and "## opus/legacy (format-compliance probe)" in s
    c = compare([rd])
    assert "| claude-opus-5-5 | 2/2 (100%) | 2/2 (100%) |" in c[c.index("### pass@1 by model × battery"):]
    assert "legacy (format-compliance probe)" in c[c.index("### pass@1 by model × variant"):]


# -- H5: the call cap across processes; one live run per run dir -----------------------------------------


def test_budget_is_safe_across_processes(tmp_path):
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from bca.liveeval.runner import Budget
        b = Budget(Path({str(tmp_path)!r}), 25)  # opened before the others take anything
        n = sum(b.take(f"p{{sys.argv[1]}} {{i}}") for i in range(40))
        print(n)
    """)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(k)], stdout=subprocess.PIPE, text=True)
             for k in range(4)]
    taken = [int(p.communicate(timeout=60)[0]) for p in procs]
    assert sum(taken) == 25
    assert (tmp_path / "calls.log").read_text().count("\n") == 25


def test_second_live_run_on_same_dir_is_refused(tmp_path, capsys):
    argv = ["run", "--run-dir", str(tmp_path), "--max-calls", "5", "--tasks", "sum_to", "--paths", "stream",
            "--isas", "x86_64"]
    subj = FakeSubject([good("x86_64")])
    with RunLock(tmp_path):
        assert cli_main(argv, subject_factory=lambda m: subj) == 1
        assert "another live run holds" in capsys.readouterr().err and subj.calls == 0
        assert cli_main(argv + ["--dry-run"]) == 0  # a dry run makes no calls: allowed
    assert cli_main(argv, subject_factory=lambda m: subj) == 0 and subj.calls == 1


# -- H6: per-battery timeout -----------------------------------------------------------------------------


def test_per_battery_timeout_recorded(tmp_path, fake_batteries, capsys):
    subj = FakeSubject([good("x86_64", "lsum"), good("x86_64")], model="m")
    argv = ["run", "--run-dir", str(tmp_path / "a"), "--max-calls", "5", "--tasks", "long,sum_to", "--paths",
            "stream", "--isas", "x86_64", "--jobs", "1"]
    assert cli_main(argv, subject_factory=lambda m: subj) == 0
    assert subj.timeouts == [400.0, 180.0]
    recs = {r["task"]: r for r in _lines(tmp_path / "a/results.jsonl")}
    assert recs["lsum"]["timeout_s"] == 400.0 and recs["sum_to"]["timeout_s"] == 180.0
    subj = FakeSubject([good("x86_64", "lsum"), good("x86_64")], model="m")
    argv[2] = str(tmp_path / "b")
    assert cli_main(argv + ["--timeout", "50"], subject_factory=lambda m: subj) == 0
    assert subj.timeouts == [50.0, 50.0]
    capsys.readouterr()
    assert cli_main(argv + ["--dry-run"]) == 0
    assert "per-call timeout: long 400 s, round1 180 s; effort: cli-default" in capsys.readouterr().out


# -- H7: effort, CLI version, child environment ----------------------------------------------------------


def test_effort_argv_and_validation():
    argv = ClaudeSubject(binary="claude", effort="high").argv("hi")
    assert argv[argv.index("--effort") + 1] == "high" and argv[-2:] == ["--", "hi"]
    assert "--effort" not in ClaudeSubject(binary="claude").argv("hi")
    with pytest.raises(ValueError, match="unknown effort"):
        ClaudeSubject(effort="extreme")


def test_child_env_is_an_allowlist():
    parent = {"PATH": "/bin", "HOME": "/h", "USER": "u", "LANG": "C.UTF-8", "LC_ALL": "C", "TERM": "xterm",
              "TMPDIR": "/t", "XDG_CONFIG_HOME": "/h/.c", "XDG_RUNTIME_DIR": "/run/u", "CLAUDE_CONFIG_DIR": "/h/.cl",
              "ANTHROPIC_API_KEY": "k", "HTTPS_PROXY": "p", "NODE_EXTRA_CA_CERTS": "/ca",
              "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_EFFORT": "max",
              "CLAUDE_PID": "1", "ANTHROPIC_MODEL": "x", "ANTHROPIC_DEFAULT_OPUS_MODEL": "y", "SECRET_TOKEN": "z",
              "PYTHONPATH": "/p"}
    env = child_env(parent)
    assert set(env) == {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "XDG_CONFIG_HOME",
                        "XDG_RUNTIME_DIR", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "HTTPS_PROXY",
                        "NODE_EXTRA_CA_CERTS"}


_ENV_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
if "--version" in sys.argv:
    print("9.8.7 (Claude Code)")
    sys.exit(0)
def out(o):
    print(json.dumps(o), flush=True)
text = json.dumps({"env": sorted(os.environ), "argv": sys.argv[1:-2]})
out({"type": "system", "subtype": "init", "model": "fake"})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
out({"type": "result", "subtype": "success", "is_error": False, "usage": {}, "result": text})
'''


def test_live_subject_child_env_effort_and_version(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(_ENV_CLAUDE)
    exe.chmod(0o755)
    for k, v in {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_EFFORT": "max", "LC_ALL": "C"}.items():
        monkeypatch.setenv(k, v)
    subj = ClaudeSubject(binary=str(exe), timeout=30, effort="low")
    r = subj.complete("hi", timeout=20)
    got = json.loads(r.text)
    assert r.ok and "CLAUDECODE" not in got["env"] and "CLAUDE_EFFORT" not in got["env"]
    assert not any(k.startswith("CLAUDE_CODE_") for k in got["env"]) and {"PATH", "HOME", "LC_ALL"} <= set(got["env"])
    assert got["argv"][got["argv"].index("--effort") + 1] == "low"
    assert subj.describe() == {"effort": "low", "cli_version": "9.8.7"}


def test_effort_recorded_and_multi_subject_warning(tmp_path, monkeypatch, capsys):
    made = {}

    class Capture(FakeSubject):
        def __init__(self, *, timeout, model, effort):
            super().__init__([good("x86_64")], model=model)
            self.effort, self.cli_version = effort, "9.8.7"
            made[model] = self

    import bca.liveeval.vendors as v

    monkeypatch.setattr(v, "ClaudeSubject", Capture)  # make_subject builds Claude subjects
    argv = ["run", "--run-dir", str(tmp_path), "--max-calls", "5", "--tasks", "sum_to", "--paths", "stream",
            "--isas", "x86_64", "--subjects", "haiku,opus"]
    assert cli_main(argv + ["--effort", "xhigh"]) == 0
    assert "without --effort" not in capsys.readouterr().err
    assert made["opus"].effort == "xhigh"
    gj = json.loads((tmp_path / "opus/baseline/group.json").read_text())
    assert gj["effort"] == "xhigh" and gj["cli_version"] == "9.8.7"
    rec = json.loads((tmp_path / "opus/baseline/cells/sum_to.stream.x86_64/cell.json").read_text())
    assert rec["effort"] == "xhigh" and rec["cli_version"] == "9.8.7"
    assert cli_main(["run", "--run-dir", str(tmp_path / "x"), "--max-calls", "1", "--subjects", "haiku,opus",
                     "--dry-run"]) == 0
    assert "2 subjects without --effort" in capsys.readouterr().err


# -- H8: per-variant dry-run estimates ---------------------------------------------------------------------


def test_rates_per_variant():
    recs = [{"subject_model": "m", "battery": "b", "path": "ir", "attempts": a, "variant": v}
            for v, xs in (("baseline", (1, 1, 1)), ("shout", (3, 3, 3))) for a in xs]
    r = Rates(recs)
    assert r.rate("m", "b", "ir") == (1, "m/b/ir n=3")
    assert r.rate("m", "b", "ir", "shout") == (3, "shout: m/b/ir n=3")
    assert r.rate("m", "b", "ir", "terse") == (1, "m/b/ir (baseline rate) n=3")


def test_dry_run_format_probe_and_variant_attempt_cap(tmp_path, fake_batteries, capsys):
    argv = ["run", "--run-dir", str(tmp_path / "new"), "--tasks", "adversarial", "--paths", "stream",
            "--isas", "x86_64", "--model", "opus", "--variant", "baseline,legacy,capped", "--max-calls", "100",
            "--dry-run"]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert "6 cells; at most 18 calls (attempts per variant: baseline 4, legacy 4, capped 1)" in out
    assert "| opus | legacy | adversarial | 2 | 0 | 8 | 8 | format probe: every attempt |" in out
    assert "| opus | capped | adversarial | 2 | 0 | 2 | 2 | default |" in out
    assert "| opus | baseline | adversarial | 2 | 0 | 8 | 3 | default |" in out


def test_runner_honours_variant_max_attempts(tmp_path, fake_batteries):
    subj = FakeSubject([bad("x86_64", "asum")] * 4)
    recs = run_plan({"s": subj}, registry.load_variants(["capped"]), [(ASUM, "adversarial")], ["stream"],
                    ["x86_64"], tmp_path, budget=Budget(tmp_path, 10), progress=lambda m: None)
    assert recs[0]["attempts"] == 1 and subj.calls == 1 and not recs[0]["incomplete"]


# -- L3: named memory windows in repair feedback ---------------------------------------------------------


def _mem_task(sig):
    return dataclasses.replace(SUM, name="f", signature=sig, mem=True, nargs=3, mem_elem=8)


def test_memory_window_named_after_pointer_argument():
    task = _mem_task("int64_t f(const int64_t *a, int64_t n, int64_t *out)")
    exp = bytes(16) + (5).to_bytes(8, "little") + (6).to_bytes(8, "little")
    act = bytes(16) + (5).to_bytes(8, "little") + (7).to_bytes(8, "little")
    case = MemCase(exp, (Ptr(0), 2, Ptr(16)), ((16, 32),), None, "f(...)")
    m = Mismatch((0,), 0, 0, None, "wrong_result", expected_data=exp, actual_data=act, case=case)
    text = _mem_detail(task, m)
    assert "out[0..2) (memory [16, 32) of the block) after the call: expected [5, 6], got [5, 7]" in text
    named = dataclasses.replace(case, writable_names=("result",))
    assert "result (memory [16, 32) of the block)" in _mem_detail(task, dataclasses.replace(m, case=named))
    # a window that does not start at a pointer argument is shown relative to the one it lies in
    part = dataclasses.replace(case, writable=((24, 32),))
    assert "out[1..2) (memory [24, 32)" in _mem_detail(task, dataclasses.replace(m, case=part))
    # no pointer information: the old wording
    nop = dataclasses.replace(case, args=(0, 2, 16))
    assert "; memory [16, 32) of the block after the call" in _mem_detail(task, dataclasses.replace(m, case=nop))


def test_param_names():
    assert param_names("int64_t mp_mul(const uint64_t *a, int64_t na, const uint64_t *b, int64_t nb, "
                       "uint64_t *r)") == ["a", "na", "b", "nb", "r"]
    assert param_names("int64_t map_sum(const int64_t *a, int64_t n, int64_t (*f)(int64_t))") == ["a", "n", "f"]
    assert param_names("int64_t f(void)") == [] and param_names("int64_t g(int64_t, uint8_t *)") == ["", ""]


def test_memcase_backward_compatible():
    c = MemCase(b"\0" * 8, (Ptr(0),), ((0, 8),), ((0, 8),), "x")
    assert c.writable_names == () and c.label == "x"


def test_env_of_this_process_would_be_filtered():
    """The live subject never passes this session's CLAUDECODE / CLAUDE_CODE_* on."""
    env = child_env(dict(os.environ, CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="x"))
    assert "CLAUDECODE" not in env and not any(k.startswith("CLAUDE_CODE_") for k in env)
