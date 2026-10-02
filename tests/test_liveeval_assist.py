"""The `assisted` variant (bca/liveeval/assist.py): information only, every fact in it checked."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from bca.liveeval import assist
from bca.liveeval.adversarial import ALL_VARIANTS_DEFAULT, VARIANT_INFO, VARIANTS
from bca.liveeval.prompts import PATHS, build_prompt
from bca.liveeval.tasks import TASKS as ROUND1
from bca.structured import assemble

HAVE_QBE = shutil.which("qbe") is not None
TASK = ROUND1[0]


def _qbe(src: str) -> subprocess.CompletedProcess:
    return subprocess.run(["qbe"], input=src, capture_output=True, text=True)


# -- the notes are true ----------------------------------------------------------------------------


@pytest.mark.skipif(not HAVE_QBE, reason="qbe not installed")
@pytest.mark.parametrize("target", ["amd64_sysv", "arm64"])
def test_qbe_sheet_example_compiles(target):
    example = assist.QBE_SHEET.split("Example:\n", 1)[1]
    r = subprocess.run(["qbe", "-t", target], input=example, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not HAVE_QBE, reason="qbe not installed")
def test_every_qbe_opcode_named_is_valid():
    body = []
    for op in assist.QBE_OPS:
        if op in ("call", "phi", "jmp", "jnz", "ret"):
            continue  # exercised by the fixed tail below
        if op in ("add", "sub", "mul", "div", "rem", "udiv", "urem", "and", "or", "xor", "sar", "shr", "shl"):
            body.append(f"\t%r_{op} =l {op} %a, %b")
        elif op == "neg":
            body.append("\t%r_neg =l neg %a")
        elif op == "copy":
            body.append("\t%r_copy =l copy 5")
        elif re.fullmatch(r"c\w+[lw]", op):
            args = "%a, %b" if op.endswith("l") else "%w1, %w2"
            body.append(f"\t%r_{op} =w {op} {args}")
        elif op.startswith("load"):
            body.append(f"\t%r_{op} ={'l' if op == 'loadl' else 'w'} {op} %p")
        elif op.startswith("store"):
            body.append(f"\t{op} {'%a' if op == 'storel' else '%w1'}, %p")
        elif op.startswith("ext"):
            body.append(f"\t%r_{op} =l {op} %w1")
        elif op.startswith("alloc"):
            body.append(f"\t%r_{op} =l {op} 16")
    assert len(body) == len([o for o in assist.QBE_OPS if o not in ("call", "phi", "jmp", "jnz", "ret")])
    src = ("export function l $f(l %a, l %b, l %p) {\n@start\n\t%w1 =w copy 1\n\t%w2 =w copy 2\n"
           + "\n".join(body) + "\n\tjnz %w1, @x, @y\n@x\n\tjmp @y\n@y\n\t%z =l phi @start %a, @x %b\n"
           "\t%c =l call $f(l %a, l %b, l %p)\n\tret %z\n}\n")
    r = _qbe(src)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_structured_examples_encode(isa):
    insns = assist.STRUCTURED_EXAMPLE_INSNS[isa]
    assemble(isa, insns + [{"op": "ret"}])  # raises if any example is not accepted by the encoder


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_structured_negative_forms_are_rejected(isa):
    reg = "rax" if isa == "x86_64" else "x0"
    with pytest.raises(Exception, match="expected a register"):
        assemble(isa, [{"op": "mov", "dst": {"reg": reg}, "src": [{"imm": 1}]}, {"op": "ret"}])


# -- the variant only adds information ---------------------------------------------------------------


def test_registered_opt_in_and_not_in_all():
    assert "assisted" in VARIANTS and VARIANT_INFO["assisted"]["opt_in"]
    assert "assisted" not in ALL_VARIANTS_DEFAULT
    assert ALL_VARIANTS_DEFAULT == ("baseline", "terse", "rushed", "misleading_hint", "distractor")
    assert not VARIANT_INFO["assisted"].get("max_attempts")  # same attempts as baseline


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_first_attempt_is_baseline_plus_notes(path, isa):
    base = build_prompt(path, isa, TASK)
    out = VARIANTS["assisted"](base, path, isa, TASK)
    assert out.startswith(base.rstrip("\n"))
    assert "Notes (to help you avoid common mistakes)" in out
    assert "Notes on the failure" not in out
    if path == "ir":
        assert "QBE cheat sheet" in out
    if path == "structured":
        assert "never {\"reg\"" in out


def _repair(path, isa, code, failure):
    return build_prompt(path, isa, TASK, (code, failure))


def test_repair_hints_match_failures():
    cases = [
        ("stream", "aarch64", "f:\n  mov x2, #65600\n  ret\n",
         "The assembler rejected line 2 (`mov x2, #65600`): aarch64: GNU as: immediate cannot be moved by a single instruction",
         "16 bits at a time"),
        ("ir", "x86_64", "export function l $f(l %a) {\n@start\n\t%x =l add %a, 0x10\n\tret %x\n}",
         "Compilation failed at stage qbe:\nqbe:-:3: unknown keyword x10", "decimal literals only (write 16)"),
        ("ir", "x86_64", "...", "Compilation failed at stage qbe:\nqbe:-:3: unknown keyword addl",
         "the class goes after `=`"),
        ("ir", "x86_64", "...", "Compilation failed at stage qbe:\nqbe:-:3: unknown keyword csgt",
         "`csgtl`"),
        ("ir", "x86_64", "...", "qbe:-:3: invalid class specifier", "every parameter"),
        ("ir", "x86_64", "...", "qbe:-:7: predecessors not matched in phi %x", "assign the same temporary again"),
        ("structured", "x86_64", "{}",
         "instruction 0 (mov) dst: expected a register name like \"rax\" or {\"mem\": ...}, got {'reg': 'rax'}",
         "bare string"),
        ("structured", "aarch64", "{}", "instruction 0 (cmp) src[0]: expected a register here, got immediate 1",
         "register first"),
        ("stream", "x86_64", "f:\n  call f\n  ret",
         "fib_rec(2): expected 1, but the run failed: x86_64: misaligned call to 0x100000", "odd number of 8-byte pushes"),
        ("stream", "aarch64", "f:\n  ret", "stats_f(...): aarch64: callee-saved register x19 clobbered: 0x5 -> 0x6",
         "callee-saved"),
    ]
    for path, isa, code, failure, expect in cases:
        out = VARIANTS["assisted"](_repair(path, isa, code, failure), path, isa, TASK)
        notes = out[out.index("Notes on the failure above"):]
        assert expect in notes, (failure, notes)


def test_no_failure_notes_when_nothing_matches():
    out = VARIANTS["assisted"](_repair("stream", "x86_64", "f:\n  mov rax, rdi\n  ret", "3 of 10 test cases failed. "
                                       "First failures:\n  f(1): expected 2, got 1"), "stream", "x86_64", TASK)
    assert "Notes on the failure above" not in out


def test_failure_notes_reveal_no_test_data():
    failure = "3 of 10 test cases failed. First failures:\n  f(123456789): expected 987654321, got 1"
    notes = assist.failure_notes(_repair("stream", "x86_64", "f:\n  ret", failure), "stream", "x86_64")
    assert "987654321" not in notes and "123456789" not in notes


# -- lints ---------------------------------------------------------------------------------------------


def test_x86_lints():
    code = "f:\n  mov rbx, rdi\n  xor rax, 0\n  lea rcx, [rip + tab + rdx]\n  call f\n  ret\n"
    out = assist.lints(code, "stream", "x86_64")
    assert any("rbx is callee-saved" in n for n in out)
    assert any("xor rax, 0" in n for n in out)
    assert any("rip-relative" in n for n in out)
    assert any("not be 16-byte aligned" in n for n in out)
    good = "f:\n  push rbx\n  mov rbx, rdi\n  call f\n  pop rbx\n  ret\n"
    assert assist.lints(good, "stream", "x86_64") == []


def test_aarch64_lints():
    code = "f:\n  mov x19, x0\n  bl f\n  ret\n"
    out = assist.lints(code, "stream", "aarch64")
    assert any("x19 is callee-saved" in n for n in out)
    assert any("never saves x30" in n for n in out)
    good = ("f:\n  stp x29, x30, [sp, #-32]!\n  str x19, [sp, #16]\n  mov x19, x0\n  bl f\n"
            "  ldr x19, [sp, #16]\n  ldp x29, x30, [sp], #32\n  ret\n")
    assert assist.lints(good, "stream", "aarch64") == []


def test_structured_lint():
    code = '{"instructions": [{"op": "mov", "dst": "rbx", "src": ["rdi"]}, {"op": "ret"}]}'
    assert any("rbx is callee-saved" in n for n in assist.lints(code, "structured", "x86_64"))


def test_hex_branch_lint():
    # b +0x40 from offset 0 in a 2-word program: outside the code
    code = "14000010\nd65f03c0\n"
    out = assist.lints(code, "hex", "aarch64")
    assert any("outside the code" in n for n in out)
    assert assist.lints("14000001\nd65f03c0\n", "hex", "aarch64") == []


def test_lints_never_raise_on_garbage():
    for path in ("stream", "structured", "hex", "ir"):
        for isa in ("x86_64", "aarch64"):
            assert isinstance(assist.lints("%%% not code {", path, isa), list)
