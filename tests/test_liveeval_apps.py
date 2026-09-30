"""The app battery in the live eval (bca.liveeval.apps, prompts_apps and the runner / registry / plan /
summarize integration): fake subjects answer with builder B's canned programs (tests/fixtures/apps), so the
whole run -- prompt, both paths, both ISAs, tests with whole and chunked stdin, repair, samples, resume,
summaries -- is exercised with no model call."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bca.apps import APP_TASKS
from bca.liveeval import registry
from bca.liveeval.__main__ import main as cli_main
from bca.liveeval.apps import (
    APP_NAMES,
    BY_NAME,
    READ_CHUNK,
    AppEvalTask,
    app_tests,
    escape,
    failure_message,
    first_difference,
)
from bca.liveeval.pipelines import attempt
from bca.liveeval.prompts import PATHS, build_prompt
from bca.liveeval.prompts_apps import build_app_prompt
from bca.liveeval.runner import cell_id, plan_cells, run_cell
from bca.liveeval.subject import FakeSubject, Subject
from bca.liveeval.summarize import compare, load, summarize

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "apps"
ISAS = ("x86_64", "aarch64")
NAMES = tuple(t.name for t in APP_TASKS)


def fixture(name: str, isa: str, broken: int = 0) -> str:
    return (FIX / (f"{name}.{isa}.broken{broken}.s" if broken else f"{name}.{isa}.s")).read_text()


def chunked(text: str, n: int = 53) -> list[str]:
    return [text[i : i + n] for i in range(0, len(text), n)]


_TASK_RE = re.compile(r"^Write the complete program `(\w+)`: a static Linux executable for (x86-64|AArch64)")


class ProgramSubject(Subject):
    """Answers from the prompt alone (the prompt is identical for asm and stream, so the subject
    cannot tell the paths apart): `answers[(task, isa)]` is a list of answers, one per attempt
    (a first attempt gets answers[0], every repair answers[1] or the last); an answer is streamed
    in 53-character chunks."""

    name = "programs"

    def __init__(self, answers: dict | None = None, model: str = "fake-model"):
        self.answers = answers or {}
        self.model = model
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt, on_text=None, *, raw_path="", timeout=None):
        self.prompts.append(prompt)
        self.calls += 1
        m = _TASK_RE.match(prompt)
        assert m, prompt[:200]
        task, isa = m.group(1), "x86_64" if m.group(2) == "x86-64" else "aarch64"
        seq = self.answers.get((task, isa)) or [fixture(task, isa)]
        text = seq[min(1, len(seq) - 1)] if "\n<previous>\n" in prompt else seq[0]
        return FakeSubject([chunked(text)], model=self.model).complete(prompt, on_text, raw_path=raw_path,
                                                                      timeout=timeout)


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


# -- registry, cells, samples -------------------------------------------------------------------------


def test_apps_battery_registry():
    sel = registry.select("apps")
    assert [t.name for t, _b in sel] == list(NAMES) and {b for _t, b in sel} == {"apps"}
    assert all(isinstance(t, AppEvalTask) for t, _b in sel)
    assert "apps" not in {b for _t, b in registry.select("all")}  # `all` = the function batteries
    assert registry.select("wc,sum_to")[0][1] == "apps"
    assert registry.default_timeout("apps") == 900.0 and registry.default_timeout("round1") == 180.0
    fn = {t.name for b in ("round1", "round2", "long", "adversarial") for t in registry.load_battery(b)}
    assert not fn & APP_NAMES  # summaries tell app records by task name
    assert registry.battery_of("json_lint") == "apps"


def test_cells_are_sampled_and_unique():
    cells = plan_cells(registry.select("apps"), None, list(ISAS))
    ids = [cell_id(t, p, i) for t, _b, p, i in cells]
    assert len(ids) == len(set(ids)) == 8 * 2 * 2 * 3
    assert {p for _t, _b, p, _i in cells} == {"asm", "stream"}
    assert "wc.asm.x86_64.s2" in ids and "json_lint.stream.aarch64.s3" in ids
    # sample-major: every cell's sample 1 before any sample 2
    ks = [t.sample for t, _b, _p, _i in cells]
    assert ks == sorted(ks) and ks[0] == 1
    assert len(plan_cells(registry.select("apps"), None, ["x86_64"], samples=1)) == 16
    assert all(cell_id(t, p, i).endswith(".s1") for t, _b, p, i in plan_cells(registry.select("wc"), None, ["x86_64"], 1))
    # a path the apps do not have is skipped; one they have is kept
    assert {p for _t, _b, p, _i in plan_cells(registry.select("apps"), ["stream", "ir"], ["x86_64"])} == {"stream"}


def test_function_cells_unchanged_and_samples_optional():
    sel = registry.select("round1")
    cells = plan_cells(sel, list(PATHS), list(ISAS))
    assert [cell_id(t, p, i) for t, _b, p, i in cells] == [
        f"{t.name}.{p}.{i}" for t, _b in sel for p in PATHS for i in ISAS if p not in t.na_paths]
    assert plan_cells(sel, None, list(ISAS)) == cells  # default paths = the three function paths
    assert all(t.sample == 0 for t, *_ in cells)
    two = [cell_id(t, p, i) for t, _b, p, i in plan_cells(sel[:1], ["stream"], ["x86_64"], samples=2)]
    assert two == [f"{sel[0][0].name}.stream.x86_64.s1", f"{sel[0][0].name}.stream.x86_64.s2"]
    assert plan_cells(sel, ["asm"], list(ISAS)) == []  # functions have no asm path


# -- the prompt ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("isa", ISAS)
def test_prompt_same_for_both_paths_and_complete(isa):
    t = BY_NAME["wc"]
    p = build_prompt("asm", isa, t)
    assert p == build_prompt("stream", isa, t) == build_app_prompt("stream", isa, t)
    assert t.spec.rstrip() in p and "_start" in p and "1 MiB" in p and "50,000,000 instructions" in p
    nums = {"x86_64": ("read = 0", "write = 1", "exit = 60", "exit_group = 231", "`syscall`", "rax"),
            "aarch64": ("read = 63", "write = 64", "exit = 93", "exit_group = 94", "`svc #0`", "x8")}[isa]
    for s in nums:
        assert s in p, s
    assert ".intel_syntax noprefix" in p if isa == "x86_64" else "ldr x0, =label" in p and "NOT" in p
    assert "16-byte aligned" in p and "argc = 0" in p and "zero" in p
    for d in (".section .rodata", ".bss", ".equ", "1f", ".asciz", ".quad"):
        assert d in p, d
    rep = build_prompt("asm", isa, t, ("CODE", "FAILURE"))
    assert rep.startswith(p.rstrip()) and "<previous>\nCODE\n</previous>" in rep and "<failure>\nFAILURE\n</failure>" in rep
    with pytest.raises(ValueError):
        build_prompt("ir", isa, t)


def test_prompt_opening_and_closing_lines():
    p = build_prompt("asm", "x86_64", BY_NAME["wc"])
    assert p.startswith("Write the complete program `wc`: a static Linux executable for x86-64 (AMD64)")
    assert "Output ONLY the assembly source: no Markdown fences, no prose before or after it." in p


PROMISED = {
    "x86_64": r'''
        .section .rodata
msg:    .ascii "hi\n"
msg_len = . - msg
        .equ    NL, 10
        .set    TWO, 2
tbl:    .byte 1, 2, 3, 4
        .short 5
        .2byte 6
        .word 7
        .long 8
        .int 9
        .4byte 10
        .quad msg
        .8byte 11
        .asciz "z"
        .string "y"
        .align 8
        .balign 4
        .p2align 3
        .data
val:    .quad 42
        .bss
        .p2align 4
buf:    .zero 64
more:   .space 16
        .text
        .globl _start
_start:
        mov     al, byte ptr [rip + tbl + 3]
        mov     esi, offset msg
        xor     ecx, ecx
        movzx   eax, byte ptr [tbl + rcx]
        mov     rdx, qword ptr [rip + val]
        mov     byte ptr [rip + buf], NL
        jmp     1f
1:      mov     eax, 1
        mov     edi, 1
        lea     rsi, [rip + msg]
        mov     edx, msg_len
        syscall
        mov     eax, 60
        mov     edi, TWO
        syscall
''',
    "aarch64": r'''
        .section .rodata
msg:    .ascii "hi\n"
msg_len = . - msg
        .equ    NL, 10
        .set    TWO, 2
tbl:    .byte 1, 2, 3, 4
        .short 5
        .2byte 6
        .word 7
        .long 8
        .int 9
        .4byte 10
        .quad msg
        .8byte 11
        .asciz "z"
        .string "y"
        .align 3
        .balign 4
        .p2align 3
        .data
val:    .quad 42
        .bss
        .p2align 4
buf:    .zero 64
more:   .space 16
        .text
        .globl _start
_start:
        adrp    x3, val
        ldr     x4, [x3, :lo12:val]
        adr     x5, tbl
        adrp    x6, buf
        add     x6, x6, :lo12:buf
        mov     w7, NL
        strb    w7, [x6]
        b       1f
1:      mov     x8, 64
        mov     x0, 1
        adrp    x1, msg
        add     x1, x1, :lo12:msg
        mov     x2, msg_len
        svc     #0
        mov     x8, 93
        mov     x0, TWO
        svc     #0
''',
}


@pytest.mark.parametrize("path", ["asm", "stream"])
@pytest.mark.parametrize("isa", ISAS)
def test_every_construct_the_prompt_promises_works(isa, path):
    """The prompt's list of sections, directives, constants, local labels and data references is
    accepted on both paths (and runs), so no path fails a model for following the prompt."""
    import bca.liveeval.apps as apps

    task = AppEvalTask(BY_NAME["wc"].app, 1)
    subj = FakeSubject([chunked(PROMISED[isa], 17)])
    a = apps.APP_PIPELINES[path](subj, isa, task, "p", "", None)
    assert a.category is None, a.failure
    from bca.process import run_program

    r = run_program(a.image, b"")
    assert (r.stdout, r.status, r.fault) == (b"hi\n", 2, None)


# -- end to end with the canned programs ---------------------------------------------------------------------


@pytest.mark.parametrize("isa", ISAS)
def test_canned_programs_pass_on_both_paths(tmp_path, isa):
    # one worker: every concurrent Unicorn engine adds ~40 MB to this process's peak RSS, which a
    # forked child inherits (tests/test_stream.py::test_directive_smuggling_is_refused checks it)
    subj = ProgramSubject()
    rc = cli_main(["run", "--run-dir", str(tmp_path), "--tasks", "apps", "--isas", isa, "--samples", "1",
                   "--max-calls", "40", "--jobs", "1"], subject_factory=lambda m: subj)
    assert rc == 0
    res = _lines(tmp_path / "results.jsonl")
    assert len(res) == 16 and subj.calls == 16
    bad = [(r["cell"], r["categories"]) for r in res if not r["pass_at_1"]]
    assert not bad, bad
    r = next(r for r in res if r["cell"] == f"wc.asm.{isa}.s1")
    assert r["sample"] == 1 and r["code_size"] > 0 and r["timeout_s"] == 900.0
    att = _lines(tmp_path / "attempts.jsonl")
    assert {a["cases"] for a in att if a["task"] == "wc"} == {2 * len(app_tests("wc"))}
    disasm = (tmp_path / "cells" / f"wc.asm.{isa}.s1" / "final.disasm.txt").read_text()
    assert disasm.startswith("0x400000:")


@pytest.mark.parametrize("path", ["asm", "stream"])
@pytest.mark.parametrize("isa", ISAS)
def test_broken_program_is_repaired(tmp_path, isa, path):
    """wc broken2 keeps only the last read's byte count: wrong_output; the repair prompt quotes the
    program and the failing test; the correct program then passes on attempt 2."""
    subj = ProgramSubject({("wc", isa): [fixture("wc", isa, 2), fixture("wc", isa)]})
    rec = run_cell(subj, AppEvalTask(BY_NAME["wc"].app, 1), path, isa, tmp_path)
    assert rec["categories"] == ["wrong_output", None] and rec["pass_final"] and not rec["pass_at_1"]
    rep = subj.prompts[1]
    assert rep.startswith(subj.prompts[0].rstrip())
    assert "<previous>\n" + fixture("wc", isa, 2).strip() in rep
    fail = rep.split("<failure>\n", 1)[1].split("\n</failure>", 1)[0]
    assert re.match(r"Test `[^`]+` failed \(stdin delivered (whole|in reads of at most 7 bytes each)", fail)
    assert "stdin (" in fail and "exit status: expected 0, got 0" in fail
    m = re.search(r"stdout differs from byte (\d+) on .*\n  expected from byte \1: \"(.*)\"\n  actual from byte \1:   \"(.*)\"",
                  fail)
    assert m, fail
    # the windows start AT the first differing byte, not at the start of the output
    label = re.match(r"Test `([^`]+)`", fail).group(1)
    t = next(t for t in app_tests("wc") if t.label == label)
    i = int(m.group(1))
    assert i > 0 and t.stdout[:i] and escape(t.stdout[i:]).strip('"').startswith(m.group(2)[:5])


def test_short_reads_are_tested(tmp_path):
    """A program that reads correctly only when every read is complete fails the chunked delivery."""
    src = fixture("wc", "x86_64")
    # treat a short read as end of input: `cmp rax, 4096 / jl done` after the counting loop
    once = src.replace("        test    rax, rax\n        jle     .Ldone\n",
                       "        test    rax, rax\n        jle     .Ldone\n        cmp     byte ptr [rip+seen], 0\n"
                       "        jne     .Ldone\n        cmp     rax, 4096\n        jae     1f\n"
                       "        mov     byte ptr [rip+seen], 1\n1:\n", 1) + "seen:   .zero 1\n"
    assert once != src
    subj = ProgramSubject({("wc", "x86_64"): [once]})
    rec = run_cell(subj, AppEvalTask(BY_NAME["wc"].app, 1), "asm", "x86_64", tmp_path, max_attempts=1)
    assert rec["categories"] == ["wrong_output"]
    fail = json.loads((tmp_path / "cells" / "wc.asm.x86_64.s1" / "attempt1.verdict.json").read_text())["failure"]
    assert f"reads of at most {READ_CHUNK} bytes" in fail


# -- categories -------------------------------------------------------------------------------------------------

_EXIT = {"x86_64": "        mov eax, 60\n        xor edi, edi\n        syscall\n",
         "aarch64": "        mov x8, 93\n        mov x0, 0\n        svc #0\n"}


def _one(text: str, isa: str, path: str, name: str = "wc"):
    subj = FakeSubject([chunked(text)])
    t = AppEvalTask(BY_NAME[name].app, 1)
    return attempt(subj, path, isa, t, build_prompt(path, isa, t))


@pytest.mark.parametrize("path", ["asm", "stream"])
@pytest.mark.parametrize("isa", ISAS)
def test_categories(isa, path):
    start = "        .text\n        .globl _start\n_start:\n"
    cases = {
        "link": start + ("        call helper\n" if isa == "x86_64" else "        bl helper\n") + _EXIT[isa],
        "bad_syscall": start + ("        mov eax, 57\n        syscall\n" if isa == "x86_64"
                                else "        mov x8, 220\n        svc #0\n"),
        "no_exit": start + ("1:      jmp 1b\n" if isa == "x86_64" else "1:      b 1b\n"),
        "crash": start + ("        ud2\n" if isa == "x86_64" else "        brk #0\n"),
        "oob": start + ("        mov rax, qword ptr [rbx]\n" if isa == "x86_64" else "        ldr x0, [x1]\n"),
        "wrong_output": start + _EXIT[isa],
        "stack_overflow": start + ("1:      push rax\n        jmp 1b\n" if isa == "x86_64"
                                   else "1:      str x0, [sp, #-16]!\n        b 1b\n"),
        "write_to_text": start + ("        lea rax, [rip + _start]\n        mov byte ptr [rax], 0\n" if isa == "x86_64"
                                  else "        adr x0, _start\n        strb wzr, [x0]\n"),
    }
    for want, text in cases.items():
        a = _one(text, isa, path)
        assert a.category == want, (want, a.category, a.failure)
        if want not in ("link", "wrong_output"):
            assert f"fault ({want})" in a.failure and "at pc 0x4" in a.failure
            assert "[the faulting instruction is " in a.failure
            if path == "stream":
                assert "line " in a.failure.split("[the faulting instruction is ", 1)[1]
    # no _start: a link error; wrong status: stdout right, status wrong
    assert _one("        .text\nmain:\n" + _EXIT[isa], isa, path).category == "link"
    old, new = {"x86_64": ("        xor     edi, edi\n        syscall\n", "        mov     edi, 3\n        syscall\n"),
                "aarch64": ("        mov     x0, #0\n        mov     x8, #93", "        mov     x0, #3\n        mov     x8, #93")}[isa]
    body = fixture("wc", isa)
    assert body.count(old) == 1
    a = _one(body.replace(old, new), isa, path)
    assert a.category == "wrong_status" and "exit status: expected 0, got 3" in a.failure
    assert "stdout (" in a.failure and "was as expected" in a.failure


@pytest.mark.parametrize("isa", ISAS)
def test_syntax_format_and_stream_cut(isa):
    bad = "        .text\n        .globl _start\n_start:\n        frobnicate x0\n" + _EXIT[isa]
    assert _one(bad, isa, "asm").category == "syntax"
    a = _one(bad, isa, "stream")
    assert a.category == "syntax" and a.code_text.endswith("frobnicate x0\n") and "rejected line 4" in a.failure
    prose = "Here is the complete program you asked for:\n" + fixture("wc", isa)
    assert _one(prose, isa, "asm").category == "format"
    assert _one(prose, isa, "stream").category == "format"
    fenced = "```asm\n" + fixture("wc", isa) + "```\n"
    assert _one(fenced, isa, "asm").ok and _one(fenced, isa, "stream").ok


def test_literal_pool_refused_on_the_stream_path():
    src = fixture("wc", "aarch64").replace("_start:\n", "_start:\n        ldr x9, =inbuf\n", 1)
    a = _one(src, "aarch64", "stream")
    assert a.category == "syntax" and "ldr x9, =inbuf" in a.failure and a.stream["abort"]["line"]
    assert a.code_text.rstrip().endswith("ldr x9, =inbuf")


def test_bss_limit_is_the_tasks():
    src = "        .text\n        .globl _start\n_start:\n" + _EXIT["x86_64"] + "        .bss\nbig: .zero 2097152\n"
    for path in ("asm", "stream"):
        a = _one(src, "x86_64", path)
        assert a.category == "link" and "limit" in a.failure, (path, a.failure)


# -- the repair message ---------------------------------------------------------------------------------------


def test_failure_message_format():
    from bca.apps import AppTest
    from bca.process import ProcFault, ProcResult

    t = AppTest("long", b"a\x00\n" * 100, b"x" * 300 + b"END\n", 3)
    task = BY_NAME["wc"]
    res = ProcResult(stdout=b"x" * 300 + b"BAD\n", stderr=b"", status=3, insns=10, fault=None, syscalls=3)
    m = failure_message(task, t, 7, 30, 48, res, "wrong_output")
    lines = m.splitlines()
    assert lines[0] == "Test `long` failed (stdin delivered in reads of at most 7 bytes each; test run 30 of 48; " \
                       "the runs before it passed)."
    assert lines[1].startswith('stdin (300 bytes): "a\\x00\\na\\x00\\n') and lines[1].endswith("(100 more bytes)")
    assert lines[2] == "exit status: expected 3, got 3"
    assert lines[3].startswith("stdout differs from byte 300 on (line 1, column 301 of the expected output)")
    assert lines[4] == '  expected from byte 300: "END\\n"' and lines[5] == '  actual from byte 300:   "BAD\\n"'
    f = ProcFault("oob", "write to unmapped memory at 0x10", pc=0x400010)
    res = ProcResult(stdout=b"x" * 10, stderr=b"", status=None, insns=10, fault=f, syscalls=1)
    m = failure_message(task, t, None, 1, 48, res, "oob", locate=lambda pc: "line 9: `mov [rax], 1`")
    assert "stdin delivered whole" in m and "The program stopped with a fault (oob): write to unmapped memory at 0x10 " \
           "at pc 0x400010 [the faulting instruction is line 9: `mov [rax], 1`]" in m
    assert "exit status: expected 3, got none (the program did not exit)" in m
    assert "the 10 bytes written before the fault were correct so far" in m
    assert first_difference(b"abc", b"abc") is None and first_difference(b"abc", b"ab") == 2
    assert escape(b'"\\\t\r\xff') == '"\\"\\\\\\t\\r\\xff"'


# -- resume, samples, subjects, summaries ------------------------------------------------------------------------------


def test_resume_with_samples(tmp_path):
    rd = tmp_path / "run"
    argv = ["run", "--run-dir", str(rd), "--tasks", "wc,rev_lines", "--isas", "x86_64", "--paths", "asm",
            "--subjects", "fake", "--jobs", "1"]
    s1 = ProgramSubject({("wc", "x86_64"): [fixture("wc", "x86_64", 1), fixture("wc", "x86_64")]})
    assert cli_main(argv + ["--max-calls", "4"], subject_factory=lambda m: s1) == 2  # the cap stops it
    g = rd / "fake" / "baseline"
    done = [r["cell"] for r in _lines(g / "results.jsonl")]
    assert s1.calls == 4 and len(done) < 6
    plan = json.loads((rd / "plan.json").read_text())["cells"]["fake/baseline"]
    assert plan[:2] == ["wc.asm.x86_64.s1", "rev_lines.asm.x86_64.s1"] and len(plan) == 6
    s2 = ProgramSubject({("wc", "x86_64"): [fixture("wc", "x86_64", 1), fixture("wc", "x86_64")]})
    assert cli_main(argv + ["--max-calls", "20"], subject_factory=lambda m: s2) == 0
    res = {r["cell"]: r for r in _lines(g / "results.jsonl")}
    assert sorted(res) == sorted(plan)
    assert s1.calls + s2.calls == sum(r["attempts"] for r in res.values())  # nothing re-asked
    assert all(res[f"wc.asm.x86_64.s{k}"]["categories"] == ["wrong_output", None] for k in (1, 2, 3))
    assert [res[f"wc.asm.x86_64.s{k}"]["sample"] for k in (1, 2, 3)] == [1, 2, 3]
    s3 = ProgramSubject()
    assert cli_main(argv + ["--max-calls", "20"], subject_factory=lambda m: s3) == 0 and s3.calls == 0
    text = summarize(rd)
    assert "### App battery by subject (baseline variant)" in text
    assert "#### App battery: per cell" in text
    assert "| wc | 0/3 / ✗ / 3/3 / 2.00 |" in text
    assert "| rev_lines | 3/3 / ✓ / 3/3 / 1.00 |" in text


def test_summary_and_compare_with_samples(tmp_path):
    rd = tmp_path / "flat"
    answers = {("wc", "x86_64"): [fixture("wc", "x86_64", 2), fixture("wc", "x86_64")]}
    subj = ProgramSubject(answers)
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "wc", "--isas", "x86_64", "--max-calls", "30",
                     "--jobs", "1"], subject_factory=lambda m: subj) == 0
    cells, atts = load(rd)
    assert len(cells) == 6 and {c["sample"] for c in cells} == {1, 2, 3}
    text = summarize(rd)
    assert text.startswith("### App battery: per cell")
    assert "| wc | 0/3 / ✗ / 3/3 / 2.00 | 0/3 / ✗ / 3/3 / 2.00 |" in text
    assert "| asm | 1 | 3 | 0/3 (0%) | 0/1 (0%) | 3/3 (100%) | 1/1 (100%) | 2.00 | 2.00 |" in text
    assert "pass@3" in text and "### Per cell (pass@1" not in text  # no function tables
    cmp = compare([rd])
    assert "### App battery by model (baseline variant)" in cmp
    assert "| fake-model | 2 | 6 | 0/6 (0%) | 0/2 (0%) | 6/6 (100%) | 2/2 (100%) | 2.00 | 2.00 |" in cmp
    assert "| model | stream | structured | ir | asm | all |" in cmp


def test_mixed_run_keeps_function_tables(tmp_path):
    from tests.test_liveeval_harness import good

    rd = tmp_path / "mixed"

    class Mixed(ProgramSubject):
        def complete(self, prompt, on_text=None, **kw):
            if prompt.startswith("Implement the function"):
                self.prompts.append(prompt)
                self.calls += 1
                return FakeSubject([good("x86_64")]).complete(prompt, on_text, **kw)
            return super().complete(prompt, on_text, **kw)

    subj = Mixed()
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "sum_to,wc", "--isas", "x86_64", "--paths",
                     "stream", "--samples", "1", "--max-calls", "10", "--jobs", "1"],
                    subject_factory=lambda m: subj) == 0
    ids = [r["cell"] for r in _lines(rd / "results.jsonl")]
    assert sorted(ids) == ["sum_to.stream.x86_64", "wc.stream.x86_64.s1"]  # a function cell with 1 sample: no suffix
    text = summarize(rd)
    assert "### Per cell (pass@1" in text and "### App battery: per cell" in text


def test_function_run_summary_has_no_app_tables(tmp_path):
    from tests.test_liveeval_harness import good

    subj = FakeSubject([good("x86_64")])
    rec = run_cell(subj, registry.select("sum_to")[0][0], "stream", "x86_64", tmp_path)
    assert rec["cell"] == "sum_to.stream.x86_64" and "sample" not in rec
    assert "App battery" not in summarize(tmp_path)


def test_cli_dry_run_and_refusals(tmp_path, capsys):
    rd = tmp_path / "dry"
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--subjects", "a,b", "--effort", "high",
                     "--max-calls", "900", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "a/baseline/wc.asm.x86_64.s1" in out and "b/baseline/json_lint.stream.aarch64.s3" in out
    assert "192 cells; at most 768 calls" in out and "apps 900 s" in out
    assert "| a | baseline | apps | 96 | 0 | 384 | 240 | default |" in out
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--variant", "baseline,terse",
                     "--max-calls", "9", "--dry-run"]) == 1
    assert "variants are defined for the function batteries only" in capsys.readouterr().err
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--paths", "ir", "--max-calls", "9",
                     "--dry-run"]) == 1
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--samples", "0", "--max-calls", "9",
                     "--dry-run"]) == 1
    assert cli_main(["run", "--run-dir", str(rd), "--tasks", "apps", "--paths", "bogus", "--max-calls", "9",
                     "--dry-run"]) == 1
