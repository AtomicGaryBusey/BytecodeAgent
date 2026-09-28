"""Subject / variant / battery dimensions of the live eval, with FakeSubjects and tiny fake
batteries and variants (no model calls, no dependency on tasks_long.py / adversarial.py)."""

import dataclasses
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from bca.liveeval import registry
from bca.liveeval.__main__ import main as cli_main
from bca.liveeval.plan import DEFAULT_ATTEMPTS, Rates
from bca.liveeval.runner import Budget, plan_cells, run_cell, run_plan
from bca.liveeval.subject import FakeSubject
from bca.liveeval.summarize import compare, find_groups, load_all, summarize
from bca.liveeval.tasks import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
RUN1 = ROOT / "eval/runs/20260927-130149"
RUN2 = ROOT / "eval/runs/round2-20260927-150117"
SMOKE = ROOT / "eval/runs/round2-smoke-20260927-145143"

SUM = BY_NAME["sum_to"]
LSUM = dataclasses.replace(SUM, name="lsum", ref_name="sum_to", signature="int64_t lsum(int64_t n)")
ASUM = dataclasses.replace(SUM, name="asum", ref_name="sum_to", signature="int64_t asum(int64_t n)")

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


def good(isa, name):
    return (X86 if isa == "x86_64" else A64).format(n=name)


def bad(isa, name):
    return good(isa, name).replace("add rax, rdi", "add rax, 2").replace("add x0, x0, x1", "add x0, x0, #2")


def shout(prompt, path, isa, task):
    return "VARIANT-SHOUT " + prompt.upper()


def tail(prompt, path, isa, task):
    return prompt + f"\n[tail {path} {isa} {task.name}]\n"


@pytest.fixture
def fake_batteries(monkeypatch):
    """Fake `long` and `adversarial` modules in sys.modules under private names."""
    long_mod = types.ModuleType("fake_tasks_long")
    long_mod.TASKS = (LSUM,)
    adv = types.ModuleType("fake_adversarial")
    adv.TASKS = (ASUM,)
    adv.VARIANTS = {"baseline": registry.baseline, "shout": shout, "tail": tail}
    monkeypatch.setitem(sys.modules, "fake_tasks_long", long_mod)
    monkeypatch.setitem(sys.modules, "fake_adversarial", adv)
    monkeypatch.setitem(registry.BATTERY_MODULES, "long", "fake_tasks_long")
    monkeypatch.setitem(registry.BATTERY_MODULES, "adversarial", "fake_adversarial")
    monkeypatch.setattr(registry, "VARIANTS_MODULE", "fake_adversarial")
    return long_mod, adv


@pytest.fixture
def missing_batteries(monkeypatch):
    monkeypatch.setitem(registry.BATTERY_MODULES, "long", "no_such_module_long")
    monkeypatch.setitem(registry.BATTERY_MODULES, "adversarial", "no_such_module_adv")
    monkeypatch.setattr(registry, "VARIANTS_MODULE", "no_such_module_adv")


# -- registry ----------------------------------------------------------------------------------------


def test_select_batteries_and_names(fake_batteries):
    assert [(t.name, b) for t, b in registry.select("long")] == [("lsum", "long")]
    assert [(t.name, b) for t, b in registry.select("adversarial")] == [("asum", "adversarial")]
    assert len(registry.select("round1")) == 8 and len(registry.select("round2")) == 11
    names = [t.name for t, _b in registry.select("all")]
    assert len(names) == 21 and names[-2:] == ["lsum", "asum"]
    assert [(t.name, b) for t, b in registry.select("gcd,asum,fib_rec,gcd")] == [
        ("gcd", "round1"), ("asum", "adversarial"), ("fib_rec", "round2")]
    assert registry.battery_of("lsum") == "long" and registry.battery_of("isqrt") == "round2"
    assert registry.battery_of("nope") == "?"
    with pytest.raises(registry.RegistryError, match="unknown task or battery 'nope'"):
        registry.select("nope")


def test_missing_or_broken_battery_fails_only_when_used(missing_batteries, monkeypatch):
    assert len(registry.select("round1,round2")) == 19  # core batteries never import the others
    with pytest.raises(registry.RegistryError, match="battery 'long' is unavailable.*No module named"):
        registry.select("long")
    with pytest.raises(registry.RegistryError, match="unavailable"):
        registry.select("all")
    with pytest.raises(registry.RegistryError, match="unavailable: .*no_such_module"):
        registry.select("round1,zzz")
    broken = types.ModuleType("broken_adv")  # no TASKS / VARIANTS attribute
    monkeypatch.setitem(sys.modules, "broken_adv", broken)
    monkeypatch.setitem(registry.BATTERY_MODULES, "adversarial", "broken_adv")
    with pytest.raises(registry.RegistryError, match="AttributeError"):
        registry.select("adversarial")
    assert registry.load_variants(["baseline"])["baseline"]("p", "stream", "x86_64", SUM) == "p"
    with pytest.raises(registry.RegistryError, match="variant 'shout' is unavailable"):
        registry.load_variants(["baseline", "shout"])


def test_load_variants(fake_batteries):
    v = registry.load_variants(["baseline", "shout"])
    assert list(v) == ["baseline", "shout"] and v["shout"]("a", "ir", "x86_64", SUM) == "VARIANT-SHOUT A"
    assert list(registry.load_variants(["all"])) == ["baseline", "shout", "tail"]
    with pytest.raises(registry.RegistryError, match="unknown variant 'nope'; have baseline, shout, tail"):
        registry.load_variants(["nope"])


def test_cli_without_battery_modules_still_works(missing_batteries, capsys):
    assert cli_main(["run", "--run-dir", "/nonexistent/x", "--max-calls", "4", "--dry-run"]) == 0
    assert "48 cells; at most 192 calls" in capsys.readouterr().out
    assert cli_main(["run", "--run-dir", "/nonexistent/x", "--max-calls", "4", "--tasks", "long", "--dry-run"]) == 1
    assert "battery 'long' is unavailable" in capsys.readouterr().err
    assert cli_main(["run", "--run-dir", "/nonexistent/x", "--max-calls", "4", "--variant", "shout",
                     "--dry-run"]) == 1
    assert "variant 'shout' is unavailable" in capsys.readouterr().err
    assert summarize(RUN1) + "\n" == (RUN1 / "summary.md").read_text()


# -- runner: subjects x variants, global budget, resume -----------------------------------------------


def _subjects(script_a, script_b):
    return {"alpha": FakeSubject(script_a, model="model-a"), "beta": FakeSubject(script_b, model="model-b")}


def test_run_plan_layout_records_and_variant_on_repair_prompts(tmp_path, fake_batteries):
    tasks = registry.select("lsum")
    variants = registry.load_variants(["baseline", "shout"])
    # alpha: first cell fails once then passes, then all pass; beta: all pass. Work is interleaved
    # by cell index: baseline x86, shout x86, baseline aarch64, shout aarch64 for each subject.
    a = [bad("x86_64", "lsum"), good("x86_64", "lsum"), good("x86_64", "lsum"),
         good("aarch64", "lsum"), good("aarch64", "lsum")]
    b = [good("x86_64", "lsum")] * 2 + [good("aarch64", "lsum")] * 2
    subj = _subjects(a, b)
    recs = run_plan(subj, variants, tasks, ["stream"], ["x86_64", "aarch64"], tmp_path, jobs=1,
                    budget=Budget(tmp_path, 100), progress=lambda m: None, models={"alpha": "a-id", "beta": "b-id"})
    assert [(r["subject"], r["variant"], r["cell"]) for r in recs] == [
        (s, v, f"lsum.stream.{i}") for s in ("alpha", "beta") for v in ("baseline", "shout")
        for i in ("x86_64", "aarch64")]
    assert all(r["battery"] == "long" and r["pass_final"] for r in recs)
    assert recs[0]["attempts"] == 2 and not recs[0]["pass_at_1"]
    gj = json.loads((tmp_path / "alpha/shout/group.json").read_text())
    assert {k: gj[k] for k in ("subject", "model_arg", "variant", "effort", "cli_version")} == {
        "subject": "alpha", "model_arg": "a-id", "variant": "shout", "effort": "cli-default", "cli_version": "fake"}
    assert len(gj["invocations"]) == 1 and gj["invocations"][0]["max_attempts"] == 4
    assert (tmp_path / "calls.log").read_text().count("\n") == 9
    assert not (tmp_path / "alpha/baseline/calls.log").exists()  # one ledger, at the top
    assert "alpha/baseline/lsum.stream.x86_64 attempt2" in (tmp_path / "calls.log").read_text()
    # the variant sees every prompt: first attempts and the repair round
    p = subj["alpha"].prompts
    assert "Your previous attempt" in p[1] and not p[1].startswith("VARIANT")  # baseline repair
    assert all(x.startswith("VARIANT-SHOUT ") for x in [p[2], p[4], *subj["beta"].prompts[1::2]])
    assert not any(x.startswith("VARIANT") for x in [p[0], p[3], *subj["beta"].prompts[0::2]])
    saved = (tmp_path / "beta/shout/cells/lsum.stream.aarch64/attempt1.prompt.txt").read_text()
    assert saved.startswith("VARIANT-SHOUT IMPLEMENT THE FUNCTION")
    att = [json.loads(x) for x in (tmp_path / "alpha/baseline/attempts.jsonl").read_text().splitlines()]
    assert att[0]["subject"] == "alpha" and att[0]["variant"] == "baseline" and att[0]["battery"] == "long"


def test_variant_applies_to_repair_prompt(tmp_path):
    subj = FakeSubject([bad("x86_64", "sum_to"), good("x86_64", "sum_to")])
    rec = run_cell(subj, SUM, "stream", "x86_64", tmp_path, variant=tail, meta={"variant": "tail"})
    assert rec["categories"] == ["wrong_result", None] and rec["variant"] == "tail"
    assert all(p.endswith("[tail stream x86_64 sum_to]\n") for p in subj.prompts)
    assert "Your previous attempt" in subj.prompts[1] and "add rax, 2" in subj.prompts[1]


def test_broken_variant_fails_before_any_call(tmp_path):
    def boom(prompt, path, isa, task):
        if "previous attempt" in prompt:
            raise KeyError("repair")
        return prompt

    subj = FakeSubject([])
    with pytest.raises(ValueError, match="variant 'boom' failed on sum_to.stream.x86_64: KeyError"):
        run_plan({"s": subj}, {"boom": boom}, [SUM], ["stream"], ["x86_64"], tmp_path,
                 budget=Budget(tmp_path, 5), progress=lambda m: None)
    assert subj.calls == 0 and not (tmp_path / "calls.log").exists()


def test_global_budget_across_subjects_and_resume(tmp_path):
    tasks = [(SUM, "round1")]
    variants = {"baseline": registry.baseline}
    g = lambda i: good(i, "sum_to")  # noqa: E731
    subj = _subjects([g("x86_64"), g("aarch64")], [g("x86_64"), g("aarch64")])
    recs = run_plan(subj, variants, tasks, ["stream"], ["x86_64", "aarch64"], tmp_path, jobs=1,
                    budget=Budget(tmp_path, 3), progress=lambda m: None)
    assert (tmp_path / "calls.log").read_text().count("\n") == 3
    assert subj["alpha"].calls + subj["beta"].calls == 3  # the cap is global, not per subject
    assert sum(bool(r["incomplete"]) for r in recs) == 1
    # resume with a larger cap: only the missing cell runs
    subj2 = _subjects([], [g("x86_64"), g("aarch64")])
    subj2["beta"].script = [g("aarch64")]
    recs2 = run_plan(subj2, variants, tasks, ["stream"], ["x86_64", "aarch64"], tmp_path, jobs=1,
                     budget=Budget(tmp_path, 10), progress=lambda m: None)
    assert subj2["alpha"].calls == 0 and subj2["beta"].calls == 1
    assert all(r["pass_final"] and not r["incomplete"] for r in recs2)
    assert (tmp_path / "calls.log").read_text().count("\n") == 4
    cells, atts = load_all(tmp_path)
    assert len(cells) == 4 and len(atts) == 4


# -- CLI: multi-subject + variants, dry-run numbers, summaries, compare ---------------------------------


def _factory(scripts):
    def make(model_arg):
        return FakeSubject(scripts[model_arg], model=f"reported-{model_arg}")
    return make


def test_cli_multi_subject_variants_and_summary(tmp_path, fake_batteries, capsys):
    run = tmp_path / "run"
    # interleaved by cell: baseline x86, tail x86, baseline aarch64, tail aarch64
    goods = lambda n: [good("x86_64", n)] * 2 + [good("aarch64", n)] * 2  # noqa: E731
    scripts = {"haiku": [bad("x86_64", "asum"), *goods("asum")], "opus": goods("asum")}
    argv = ["run", "--run-dir", str(run), "--tasks", "adversarial", "--paths", "stream",
            "--subjects", "haiku,opus", "--variant", "baseline,tail", "--max-calls", "50", "--jobs", "1"]
    assert cli_main(argv, subject_factory=_factory(scripts)) == 0
    out = capsys.readouterr().out
    assert "calls used in this run dir: 9/50" in out and "haiku/tail/asum.stream.x86_64" in out
    assert sorted(str(g.relative_to(run)) for g, _s, _v in find_groups(run)) == [
        "haiku/baseline", "haiku/tail", "opus/baseline", "opus/tail"]
    s = summarize(run)
    assert "| haiku | reported-haiku | baseline | 2 | 2 | 1/2 (50%) | 2/2 (100%) | 1.50 | 3 | 0 | cli-default |" in s
    assert "| opus | reported-opus | tail | 2 | 2 | 2/2 (100%) | 2/2 (100%) | 1.00 | 2 |" in s
    # cross-subject tables by path/ISA/battery use the baseline variant only (no pooling of variants)
    assert "### By subject × path × ISA (baseline variant)" in s
    assert "| haiku | stream | x86_64 | 1 | 0/1 (0%) | 1/1 (100%) | 2.00 |" in s
    assert "| haiku | adversarial | 2 | 1/2 (50%) | 2/2 (100%) | 1.50 |" in s
    assert "| opus | tail | 2 |" in s and "### By variant × battery" in s
    assert "| wrong_result | 1 | 0 | 1 |" in s  # failure categories per subject
    assert "## haiku/tail" in s and "#### Per cell" in s and "| asum | 1 / ✓ / 1 |" in s
    # re-running is a no-op (resume), the dry run then shows everything done
    assert cli_main(argv, subject_factory=_factory({"haiku": [], "opus": []})) == 0
    capsys.readouterr()
    assert cli_main(argv + ["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "| haiku | tail | adversarial | 2 | 2 | 0 | 0 |" in out
    assert "calls already used in this run dir: 9; --max-calls 50 leaves 41; worst case 0, expected 0" in out


def test_cli_model_and_subjects_are_exclusive(capsys):
    assert cli_main(["run", "--run-dir", "/nonexistent/x", "--max-calls", "1", "--model", "opus",
                     "--subjects", "haiku", "--dry-run"]) == 1
    assert "not both" in capsys.readouterr().err
    assert cli_main(["run", "--run-dir", "/nonexistent/x", "--max-calls", "1", "--paths", "streem",
                     "--dry-run"]) == 1


def test_cli_single_model_uses_subject_dir(tmp_path, capsys):
    run = tmp_path / "r"
    seen = []

    def make(model_arg):
        seen.append(model_arg)
        return FakeSubject([good("x86_64", "sum_to")], model="claude-haiku-4-5-20251001")

    assert cli_main(["run", "--run-dir", str(run), "--tasks", "sum_to", "--paths", "stream", "--isas", "x86_64",
                     "--model", "haiku", "--max-calls", "1"], subject_factory=make) == 0
    assert seen == ["haiku"] and (run / "haiku/baseline/cells/sum_to.stream.x86_64/cell.json").exists()


def test_dry_run_numbers(tmp_path, fake_batteries, capsys):
    argv = ["run", "--run-dir", str(tmp_path / "new"), "--tasks", "round1,long", "--subjects", "haiku,opus",
            "--variant", "baseline,shout,tail", "--max-calls", "300", "--dry-run"]
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    # (8 + 1 tasks) x 6 cells x 2 subjects x 3 variants
    assert "324 cells; at most 1296 calls (4 attempts each); --max-calls 300" in out
    assert "haiku/shout/lsum.ir.aarch64" in out
    assert f"| haiku | tail | long | 6 | 0 | 24 | {int(6 * DEFAULT_ATTEMPTS)} | default |" in out
    assert "WARNING: the cap can stop the plan early" in out
    # observed rates: round 1 took 52 calls for 48 cells with claude-opus-5-5
    assert cli_main(argv + ["--estimate-from", str(RUN1)]) == 0
    out = capsys.readouterr().out
    assert "| opus | baseline | round1 | 48 | 0 | 192 | 52 |" in out
    assert "claude-opus-5-5/round1/stream n=16" in out
    assert "| haiku | baseline | round1 | 48 | 0 | 192 | 52 | round1/ir (any model) n=16" in out
    assert "| opus | shout | long | 6 | 0 | 24 | 9 | default |" in out  # rates never cross batteries


def test_rates_fallback():
    r = Rates([{"subject_model": "m", "battery": "b", "path": "ir", "attempts": a} for a in (1, 2, 3)])
    assert r.rate("m", "b", "ir") == (2, "m/b/ir n=3")
    assert r.rate("x", "b", "stream") == (2, "b (any model) n=3")
    assert Rates().rate("m", "b", "ir") == (DEFAULT_ATTEMPTS, "default")


# -- backward compatibility with the committed run directories -------------------------------------------


@pytest.mark.parametrize("run", [RUN1, RUN2, SMOKE])
def test_committed_summaries_unchanged(run):
    assert summarize(run) + "\n" == (run / "summary.md").read_text()  # as printed by the CLI


def test_cli_legacy_flat_run_unchanged(tmp_path, capsys):
    subj = FakeSubject([good("x86_64", "sum_to")])
    assert cli_main(["run", "--run-dir", str(tmp_path), "--tasks", "sum_to", "--paths", "stream", "--isas",
                     "x86_64", "--max-calls", "1"], subject_factory=lambda m: subj) == 0
    assert (tmp_path / "cells/sum_to.stream.x86_64/cell.json").exists() and (tmp_path / "results.jsonl").exists()
    assert "### Per cell" in summarize(tmp_path) and "## Run" not in summarize(tmp_path)


def test_compare_committed_and_new(tmp_path, capsys):
    run = tmp_path / "new"
    subj = {"haiku": FakeSubject([bad("x86_64", "sum_to"), good("x86_64", "sum_to")],
                                 model="claude-haiku-4-5-20251001")}
    run_plan(subj, {"baseline": registry.baseline}, [(SUM, "round1")], ["stream"], ["x86_64"], run,
             budget=Budget(run, 5), progress=lambda m: None)
    assert cli_main(["compare", str(RUN1), str(RUN2), str(run)]) == 0
    out = capsys.readouterr().out
    # coverage differs: the cross-model tables use only the one cell both models completed
    assert "| claude-opus-5-5 | 110 | 110 | 1 | 105/110 (95%) |" in out  # coverage row keeps the full rate
    assert "Restricted to the 1 cells completed by every model; 109 of 110 cells excluded" in out
    assert "| claude-opus-5-5 | 1/1 (100%) | 1/1 (100%) |" in out
    assert "| claude-haiku-4-5-20251001 | 0/1 (0%) | 0/1 (0%) |" in out
    assert "| mean attempts by model × battery" not in out and "### mean attempts by model × battery" in out
    assert f"| {run} | haiku | claude-haiku-4-5-20251001 | round1 | baseline | 1 | 1 | 2 |" in out
    assert "| wrong_result | 1 | 1 |" in out
    # one model: nothing to restrict
    one = compare([RUN1, RUN2])
    assert "| claude-opus-5-5 | 45/48 (94%) | 60/62 (97%) | 105/110 (95%) |" in one and "Restricted" not in one
    assert compare([RUN1]).count("claude-opus-5-5") >= 3


# -- the real battery modules, once they exist ------------------------------------------------------------


def _real(mod):
    try:
        return importlib.import_module(mod)
    except ModuleNotFoundError as e:
        if e.name == mod:
            pytest.skip(f"{mod} not written yet")
        raise


def test_real_long_battery_loads():
    _real("bca.liveeval.tasks_long")
    tasks = registry.load_battery("long")
    assert tasks and len({t.name for t in tasks}) == len(tasks)
    core = {t.name for b in ("round1", "round2") for t in registry.load_battery(b)}
    assert not core & {t.name for t in tasks}
    assert plan_cells([(t, "long") for t in tasks], ["stream", "structured", "ir"], ["x86_64", "aarch64"])


def test_real_adversarial_battery_and_variants_load():
    _real("bca.liveeval.adversarial")
    tasks = registry.load_battery("adversarial")
    assert tasks and len({t.name for t in tasks}) == len(tasks)
    names = {t.name for b in ("round1", "round2") for t in registry.load_battery(b)}
    assert not names & {t.name for t in tasks}
    variants = registry.load_variants(["all"])
    assert "baseline" in variants
    from bca.liveeval.runner import check_variants

    cells = plan_cells([(t, "adversarial") for t in tasks] + [(SUM, "round1")], ["stream", "structured", "ir"],
                       ["x86_64", "aarch64"])
    check_variants(variants, cells)  # every variant rewrites every first and repair prompt
    p = "Implement the function."
    assert variants["baseline"](p, "stream", "x86_64", SUM) == p
