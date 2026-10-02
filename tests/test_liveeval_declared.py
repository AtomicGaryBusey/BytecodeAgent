"""The `declared` prompt variants (bca/liveeval/declared.py): a plain statement of purpose before the
unchanged prompt, the same words for every model, and nothing about the reader's rules."""

import re

import pytest

from bca.liveeval import declared as dec
from bca.liveeval.__main__ import main as cli_main
from bca.liveeval.adversarial import ALL_VARIANTS_DEFAULT, VARIANT_INFO, VARIANTS
from bca.liveeval.apps import TASKS as APP_TASKS
from bca.liveeval.hexpath import parse, parse_program
from bca.liveeval.prompts import build_prompt
from bca.liveeval.registry import select
from bca.liveeval.runner import check_variants, plan_cells

NAMES = ("declared", "declared_annotated")
# Words about the model's rules, permissions or policies: the paragraph describes the task only.
FORBIDDEN = re.compile(r"safe|polic|authori[sz]|guideline|classif|refus|ignore your|permi|allowed to|harm|"
                       r"malic|legitimate|benign|jailbreak|restrict|filter|violat", re.I)


def _all_cells():
    cells = plan_cells(select("round1,round2,long,adversarial"), None, ["x86_64", "aarch64"], None)
    cells += plan_cells(select("round1"), ["hex"], ["x86_64", "aarch64"], None)
    cells += plan_cells(select("apps"), ["asm", "stream", "hex"], ["x86_64", "aarch64"], 1)
    return cells


def test_registered_opt_in_apps_ok_and_not_in_all():
    for n in NAMES:
        assert n in VARIANTS and VARIANT_INFO[n]["opt_in"] and VARIANT_INFO[n]["apps_ok"]
        assert n not in ALL_VARIANTS_DEFAULT and not VARIANT_INFO[n].get("max_attempts")
    assert [k for k, v in VARIANT_INFO.items() if v.get("apps_ok")] == list(NAMES)


def test_every_cell_gets_a_clean_paragraph_and_the_unchanged_prompt():
    cells = _all_cells()
    check_variants({n: VARIANTS[n] for n in NAMES}, cells)  # every first attempt and a repair
    seen = set()
    for t, _b, p, i in cells:
        base = build_prompt(p, i, t)
        for n in NAMES:
            out = VARIANTS[n](base, p, i, t)
            ctx = dec.context(p, i, t)
            assert out.startswith(ctx + "\n\n" + base.rstrip("\n"))
            assert not FORBIDDEN.search(ctx), (t.name, p, ctx)
            assert f"`{t.name}`" in ctx or f"{t.name}(" in ctx
            seen.add(ctx)
        if p == "hex":
            assert dec.ANNOTATE[i] in VARIANTS["declared_annotated"](base, p, i, t)
        else:
            assert VARIANTS["declared_annotated"](base, p, i, t) == VARIANTS["declared"](base, p, i, t)
    for a in dec.ANNOTATE.values():
        assert not FORBIDDEN.search(a)
    assert len(seen) > 50


def test_the_paragraph_states_what_is_true_for_the_path():
    app = next(t for t in APP_TASKS if t.name == "wc")
    hexp = dec.context("hex", "x86_64", app)
    assert "raw machine-code bytes" in hexp and "counts the lines, words and bytes" in hexp
    assert "reading standard input, writing standard output and standard error, and exiting" in hexp
    assert "raw machine-code" not in dec.context("asm", "x86_64", app)
    fn = select("sum_to")[0][0]
    f = dec.context("stream", "aarch64", fn)
    assert "one small function" in f and "not a program" in f and "standard input" not in f
    assert "QBE" in dec.context("ir", "aarch64", fn)


def test_every_app_has_a_purpose():
    assert {t.name for t in APP_TASKS} == set(dec.APP_PURPOSE)


def test_repair_prompts_carry_the_paragraph_too():
    app = next(t for t in APP_TASKS if t.name == "rpn")
    rep = build_prompt("hex", "aarch64", app, previous=(".text\nd503201f", "wrong output"))
    out = VARIANTS["declared"](rep, "hex", "aarch64", app)
    assert out.startswith(dec.context("hex", "aarch64", app)) and "<previous>" in out


def test_the_same_words_for_every_subject():
    # the variant sees only (prompt, path, isa, task): nothing about the subject can reach it
    import inspect

    for n in NAMES:
        assert list(inspect.signature(VARIANTS[n]).parameters) == ["prompt", "path", "isa", "task"]


@pytest.mark.parametrize("isa,line", [("x86_64", "48 31 c0  // xor rax, rax"),
                                      ("aarch64", "d2800000  // mov x0, #0")])
def test_the_annotation_example_is_accepted_by_the_unchanged_hex_format(isa, line):
    assert line in dec.ANNOTATE[isa]
    parse(isa, line)  # functions
    parse_program(isa, ".text\n" + line + "\n")  # whole programs


def test_the_cli_accepts_only_flagged_variants_on_apps(tmp_path, capsys):
    rd = tmp_path / "dry"
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--paths", "hex", "--samples", "1",
                     "--subjects", "a", "--variant", "declared,declared_annotated", "--max-calls", "200",
                     "--dry-run"]) == 0
    assert "a/declared_annotated/wc.hex.x86_64.s1" in capsys.readouterr().out
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--variant", "declared,terse",
                     "--max-calls", "9", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "variants are defined for the function batteries only (terse)" in err
