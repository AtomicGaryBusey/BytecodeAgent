import gc
import random
import subprocess
import sys
from pathlib import Path

import pytest

from bca.difftest import buffer_differential, compile_c_reference, differential, int_inputs
from bca.emu import CODE_BASE, EmulationError, run_function
import bca.stream as stream_mod
from bca.isa import AARCH64, X86_64
from bca.stream import (
    Instruction,
    LiveRunner,
    StreamAssembler,
    StreamHalted,
    _KEYSTONE,
    assemble_stream,
    trace_program,
)


def keystone_whole(isa, text):
    encoding, _ = isa.assembler().asm(text, CODE_BASE)
    return bytes(encoding)


def random_chunks(text, seed):
    rng = random.Random(seed)
    top = rng.choice([1, 2, 5, 16, 64])
    out, i = [], 0
    while i < len(text):
        n = rng.randint(1, top)
        out.append(text[i : i + n])
        i += n
    return out


def stream(isa, text, seed=None, **kwargs):
    chunks = list(text) if seed is None else random_chunks(text, seed)
    prog = assemble_stream(isa, chunks, **kwargs)
    assert prog.ok, [e.message for e in prog.errors]
    assert not prog.warnings, [(w.text, w.message) for w in prog.warnings]
    return prog


# -- chunking fuzz vs. keystone assembling the whole program ---------------------------

FILLER_X86 = "\n".join(f"    add rax, {0x1000 + i:#x}" for i in range(30))  # 180 bytes

# Backward branches (short and long), far forward branches, forward/backward RIP-relative.
X86_CLEAN = f"""
start:
    mov rax, rdi
    cmp rdi, 0
    jl far_neg
back:
    add rax, 1
    lea rcx, [rip + back]
    lea rdx, [rip + tail]
    sub rdi, 1
    jg back
    call far_neg
{FILLER_X86}
    jmp start
    jmp back
far_neg:
    neg rax
    mov qword ptr [rip + tail + 8], 0x1234
tail:
    ret
"""

X86_NOISY = f"""```asm
start:  # entry; not a statement
    mov rax, rdi // copy; still a comment
    cmp rdi, 0;   jl far_neg
back: add rax, 1
    lea rcx, [rip + back]   # backward rip-relative
    lea rdx, [rip + tail]
    sub rdi, 1 ; jg back
    call far_neg
{FILLER_X86}
    jmp start  # backward, > 127 bytes
    jmp back
far_neg: neg rax
    mov qword ptr [rip + tail + 8], 0x1234
tail:
    ret
```
"""

A64_CLEAN = """
start:
    mov x1, x0
    cbz x0, out
    tbz x0, #40, mid
    adr x2, out
    ldr x3, lit
    bl mid
mid:
    subs x1, x1, #1
    b.ne mid
    cbnz x1, start
    tbnz x1, #5, start
    adr x4, start
    b.hs out
    b out
lit:
    nop
out:
    ret
"""

A64_NOISY = """
start: mov x1, x0   // n; comment with ; inside
    cbz x0, out; tbz x0, #40, mid
    adr x2, out
    ldr x3, lit // literal load
    bl mid
mid: subs x1, x1, #1
    b.ne mid // loop
    cbnz x1, start
    tbnz x1, #5, start
    adr x4, start;b.hs out
    b out
lit:
    nop
out:
    ret"""


@pytest.mark.parametrize(
    "isa,clean,noisy",
    [(X86_64, X86_CLEAN, X86_NOISY), (AARCH64, A64_CLEAN, A64_NOISY)],
    ids=["x86_64", "aarch64"],
)
def test_random_chunking_matches_keystone(isa, clean, noisy):
    expected = keystone_whole(isa, clean)
    assert stream(isa, noisy).code == expected  # one character at a time
    assert stream(isa, noisy, abort_on_error=True).code == expected
    for seed in range(60):
        assert stream(isa, noisy, seed=seed).code == expected, seed


def test_x86_short_forward_branches_chunking_invariant():
    # Keystone relaxes short forward jumps to rel8; the stream keeps rel32, so compare the
    # stream with itself across chunkings and check semantics instead.
    text = "xor eax, eax\ntest rdi, rdi\njz z\nmov eax, 5\nz: ret\n"
    whole = assemble_stream(X86_64, [text]).code
    for seed in range(40):
        assert stream(X86_64, text, seed=seed).code == whole
    assert run_function(X86_64, whole, [0]).ret == 0
    assert run_function(X86_64, whole, [9]).ret == 5


# -- forward-branch programs vs. C references -------------------------------------------

SUM_TO_C = """int64_t sum_to(int64_t n) {
    if (n > 1000) n = 1000;
    int64_t s = 0;
    for (int64_t i = 1; i <= n; i++) s += i;
    return s;
}"""
GCD_C = """int64_t gcd(int64_t a, int64_t b) {
    uint64_t x = a, y = b;
    while (y) { uint64_t t = x % y; x = y; y = t; }
    return x;
}"""
POPCOUNT_C = """int64_t popcount(int64_t v) {
    uint64_t x = v; int64_t c = 0;
    while (x) { c += x & 1; x >>= 1; }
    return c;
}"""
BMAX_C = """int64_t bmax(int64_t *a, int64_t n) {
    int64_t m = -9223372036854775807LL - 1;
    for (int64_t i = 0; i < n; i++) if (a[i] > m) m = a[i];
    return m;
}"""
FIRST_NEG_C = """int64_t first_neg(int64_t *a, int64_t n) {
    for (int64_t i = 0; i < n; i++) { if (a[i] < 0) return i; a[i] ^= 1; }
    return -1;
}"""

PROGRAMS = {
    ("x86_64", "sum_to"): """
    xor eax, eax
    test rdi, rdi
    jle done
    cmp rdi, 1000
    jle loop
    mov edi, 1000
loop:
    add rax, rdi
    dec rdi
    jnz loop
done:
    ret
""",
    ("aarch64", "sum_to"): """
    mov x1, x0
    mov x0, #0
    cmp x1, #0
    b.le done
    mov x2, #1000
    cmp x1, x2
    b.le loop
    mov x1, x2
loop:
    add x0, x0, x1
    subs x1, x1, #1
    b.ne loop
done:
    ret
""",
    ("x86_64", "gcd"): """
    mov rax, rdi
    mov rcx, rsi
top:
    test rcx, rcx
    jz done
    xor edx, edx
    div rcx
    mov rax, rcx
    mov rcx, rdx
    jmp top
done:
    ret
""",
    ("aarch64", "gcd"): """
top:
    cbz x1, done
    udiv x2, x0, x1
    msub x2, x2, x1, x0
    mov x0, x1
    mov x1, x2
    b top
done:
    ret
""",
    ("x86_64", "popcount"): """
    xor eax, eax
top:
    test rdi, rdi
    jz done
    mov rdx, rdi
    and edx, 1
    add rax, rdx
    shr rdi, 1
    jmp top
done: ret
""",
    ("aarch64", "popcount"): """
    mov x1, x0
    mov x0, #0
top:
    cbz x1, done
    and x2, x1, #1
    add x0, x0, x2
    lsr x1, x1, #1
    b top
done:
    ret
""",
    ("x86_64", "bmax"): """
    mov rax, 0x8000000000000000
    test rsi, rsi
    jle done
    xor ecx, ecx
loop:
    mov rdx, qword ptr [rdi + rcx*8]
    cmp rdx, rax
    jle skip
    mov rax, rdx
skip:
    inc rcx
    cmp rcx, rsi
    jl loop
done:
    ret
""",
    ("aarch64", "bmax"): """
    mov x2, #1
    lsl x2, x2, #63
    mov x3, #0
    cmp x1, #0
    b.le done
loop:
    ldr x4, [x0, x3, lsl #3]
    cmp x4, x2
    b.le skip
    mov x2, x4
skip:
    add x3, x3, #1
    cmp x3, x1
    b.lt loop
done:
    mov x0, x2
    ret
""",
    ("x86_64", "first_neg"): """
    xor eax, eax
loop:
    cmp rax, rsi
    jge none
    mov rdx, qword ptr [rdi + rax*8]
    test rdx, rdx
    js found
    xor qword ptr [rdi + rax*8], 1
    inc rax
    jmp loop
none:
    mov rax, -1
found:
    ret
""",
    ("aarch64", "first_neg"): """
    mov x2, #0
loop:
    cmp x2, x1
    b.ge none
    ldr x3, [x0, x2, lsl #3]
    tbnz x3, #63, found
    eor x3, x3, #1
    str x3, [x0, x2, lsl #3]
    add x2, x2, #1
    b loop
none:
    mov x0, #-1
    ret
found:
    mov x0, x2
    ret
""",
}

INT_CASES = [
    ("sum_to", SUM_TO_C, 1, {}),
    ("gcd", GCD_C, 2, {"lo": 0, "hi": 10**6}),
    ("popcount", POPCOUNT_C, 1, {"lo": -(2**63), "hi": 2**63 - 1}),
]


def buffers(seed=0, count=250):
    rng = random.Random(seed)
    edges = [0, 1, -1, 2**63 - 1, -(2**63), 42, -42]
    yield []
    yield [-(2**63)]
    yield [2**63 - 1, -(2**63)]
    yield [-5] * 4
    for _ in range(count):
        yield [
            rng.choice(edges) if rng.random() < 0.3 else rng.randint(-(2**63), 2**63 - 1)
            for _ in range(rng.randint(0, 16))
        ]


@pytest.mark.parametrize("isa", [X86_64, AARCH64], ids=lambda i: i.name)
@pytest.mark.parametrize("name,source,nargs,bounds", INT_CASES, ids=[c[0] for c in INT_CASES])
def test_forward_branch_programs_differential(isa, name, source, nargs, bounds):
    prog = stream(isa, PROGRAMS[(isa.name, name)], seed=7)
    assert any(i.data for i in prog.instructions)
    assert any(e.kind == "patch" for e in prog.events), "expected a forward fixup"
    ref = compile_c_reference(source, name, nargs)
    inputs = list(int_inputs(nargs, 200, seed=3, **bounds))
    assert len(inputs) >= 200
    report = differential(isa, prog.code, ref, inputs)
    assert report.ok, report.mismatches[:3]


@pytest.mark.parametrize("isa", [X86_64, AARCH64], ids=lambda i: i.name)
@pytest.mark.parametrize(
    "name,source", [("bmax", BMAX_C), ("first_neg", FIRST_NEG_C)], ids=["bmax", "first_neg"]
)
def test_forward_branch_buffer_programs_differential(isa, name, source):
    prog = stream(isa, PROGRAMS[(isa.name, name)], seed=11)
    report = buffer_differential(isa, prog.code, source, name, buffers())
    assert report.cases >= 200
    assert report.ok, report.mismatches[:3]


# -- x86 branch sizes ---------------------------------------------------------------------


@pytest.mark.parametrize("fill", [3, 60])  # 18 bytes (rel8 range) and 360 bytes
def test_x86_forward_jump_near_and_far(fill):
    body = "\n".join("add rax, 0x100" for _ in range(fill))
    text = f"mov eax, 1\ntest rdi, rdi\njnz skip\n{body}\nskip2:\nskip: ret\n"
    prog = stream(X86_64, text, seed=fill)
    jnz = prog.instructions[2]
    assert jnz.data[:2] == b"\x0f\x85" and len(jnz.data) == 6  # rel32 at both distances
    distance = int.from_bytes(jnz.data[2:], "little", signed=True)
    assert jnz.address + 6 + distance == prog.labels["skip"]
    assert (distance > 127) == (fill == 60)
    assert run_function(X86_64, prog.code, [1]).ret == 1
    assert run_function(X86_64, prog.code, [0]).ret == 1 + 0x100 * fill
    if fill == 60:  # far enough that keystone's relaxation also picks rel32
        assert prog.code == keystone_whole(X86_64, text)


@pytest.mark.parametrize("fill", [3, 60])
def test_x86_backward_jump_matches_keystone_size(fill):
    body = "\n".join("add rax, 0x100" for _ in range(fill))
    text = f"mov eax, 0\ntop:\n{body}\ndec rdi\njnz top\nret\n"
    prog = stream(X86_64, text)
    assert prog.code == keystone_whole(X86_64, text)
    assert len(prog.instructions[-2].data) == (6 if fill == 60 else 2)
    assert run_function(X86_64, prog.code, [3]).ret == 3 * 0x100 * fill


def test_x86_rel8_only_forward_branch():
    ok = stream(X86_64, "mov ecx, 3\nxor eax, eax\nl: inc eax\nloop l\njrcxz out\nud2\nout: ret\n")
    assert run_function(X86_64, ok.code).ret == 3
    far = "\n".join("add rax, 0x100" for _ in range(40))
    prog = assemble_stream(X86_64, [f"jrcxz out\n{far}\nout: ret\n"])
    assert not prog.ok and "out" in prog.errors[0].message


# -- errors, early abort, retract -------------------------------------------------------


def test_invalid_instruction_halts_before_later_lines():
    sa = StreamAssembler(X86_64)
    events = sa.feed("mov rax, rdi\nfrobnicate rax\nadd rax, 1\nret\n")
    assert [e.kind for e in events] == ["insn", "error"]
    err = events[-1]
    assert "frobnicate rax" in err.message and "line 2" in err.message
    assert "mnemonic" in err.message.lower()
    assert sa.halted is err and sa.pending_text == "add rax, 1\nret\n"
    with pytest.raises(StreamHalted):
        sa.feed("nop\n")
    assert sa.retract() == CODE_BASE + 3
    sa.feed("add rax, 2\n")
    sa.feed("ret\n")
    prog = sa.finish()
    assert prog.ok
    assert prog.code == keystone_whole(X86_64, "mov rax, rdi\nadd rax, 2\nret")
    assert run_function(X86_64, prog.code, [40]).ret == 42


@pytest.mark.parametrize("isa", [X86_64, AARCH64], ids=lambda i: i.name)
def test_error_fires_on_the_completing_chunk(isa):
    good = "mov rax, rdi\n" if isa is X86_64 else "mov x1, x0\n"
    text = good + "bogus r1, r2\n" + good + "ret\n"
    sa = StreamAssembler(isa)
    consumed = 0
    for ch in text:
        events = sa.feed(ch)
        consumed += 1
        if any(e.kind == "error" for e in events):
            break
    assert consumed == len(good) + len("bogus r1, r2\n")
    assert len(sa.finish().instructions) == 1


def test_retract_committed_statements_and_live_runner_restarts():
    sa = StreamAssembler(X86_64)
    runner = LiveRunner(sa, [10])
    sa.feed("mov rax, rdi\nadd rax, 5\n")
    assert [s.text for s in runner.advance()] == ["mov rax, rdi", "add rax, 5"]
    events = sa.feed("imul rax, rax, rax, rax\n")
    assert events[-1].kind == "error"
    sa.retract(statements=1)  # also un-commit "add rax, 5"
    sa.feed("add rax, 7\nret\n")
    runner.advance()
    assert runner.done and runner.ret == 17
    assert [s.text for s in runner.steps] == ["mov rax, rdi", "add rax, 7", "ret"]


def test_retract_undoes_labels_and_fixups():
    sa = StreamAssembler(AARCH64)
    sa.feed("cbz x0, out\nout: mov x0, #1\n")
    sa.retract(statements=1)  # label `out` and the patch are gone again
    assert sa.instruction_at(CODE_BASE).pending == "out"
    sa.feed("mov x0, #2\nout: ret\n")
    prog = sa.finish()
    assert prog.ok
    assert run_function(AARCH64, prog.code, [0]).ret == 0
    assert run_function(AARCH64, prog.code, [3]).ret == 2


def test_continue_after_error_when_not_aborting():
    prog = assemble_stream(X86_64, ["mov rax, rdi\nnope\nadd rax, 1\nret\n"], abort_on_error=False)
    assert not prog.ok and len(prog.errors) == 1
    assert run_function(X86_64, prog.code, [1]).ret == 2


def test_undefined_label_is_error_at_finish_and_can_be_supplied():
    sa = StreamAssembler(X86_64)
    events = sa.feed("test rdi, rdi\njz nowhere\nmov eax, 1\n")
    assert all(e.kind != "error" for e in events)
    prog = sa.finish()
    assert not prog.ok
    assert "undefined label 'nowhere'" in prog.errors[0].message
    sa.feed("ret\nnowhere: xor eax, eax\nret\n")
    prog = sa.finish()
    assert prog.ok
    assert run_function(X86_64, prog.code, [0]).ret == 0


@pytest.mark.parametrize(
    "isa,text,needle",
    [
        (X86_64, "mov rax, rbxx\n", "non-PC-relative"),  # register typo -> immediate error
        (X86_64, "a: nop\na: ret\n", "duplicate label"),
        (X86_64, ".p2align 4\n", "unsupported directive"),
        (AARCH64, "b.eq .+0x90000000\n", "out of range"),  # keystone would abort the process
        (AARCH64, "b.eq 0x90000000\n", "ambiguous"),  # GNU as: offset; keystone: address
    ],
)
def test_immediate_errors(isa, text, needle):
    events = StreamAssembler(isa).feed(text)
    assert events[-1].kind == "error" and needle in events[-1].message


@pytest.mark.parametrize("backward", [False, True])
def test_aarch64_branch_range_checked_without_crashing(backward):
    nops = "nop\n" * 8200  # 32800 bytes > tbz range of 32 KiB
    text = f"top: {nops}tbz x0, #0, top\n" if backward else f"tbz x0, #0, far\n{nops}far: ret\n"
    events = StreamAssembler(AARCH64).feed(text)
    assert events[-1].kind == "error" and "out of range" in events[-1].message
    ok = stream(AARCH64, "cbz x0, far\n" + "nop\n" * 8200 + "far: ret\n")
    assert len(ok.code) == 4 * 8202


def test_aarch64_backward_tbnz_high_bit():
    # Keystone itself rejects `tbnz x1, #63, <backward label>`; field patching handles it.
    prog = stream(AARCH64, "mov x0, #0\ntop: add x0, x0, #1\nlsr x1, x1, #1\n"
                           "tbnz x1, #63, top\ncbnz x1, top\nret\n")  # fmt: skip
    tbnz = next(AARCH64.disassembler().disasm(prog.instructions[3].data, 0x10000C))
    assert tbnz.op_str == "x1, #0x3f, #0x100004"
    assert run_function(AARCH64, prog.code, [0, 1 << 63]).ret == 64


def test_keystone_radix_bug_does_not_leak():
    # Freeing a keystone handle that had a symbol resolver makes later handles read
    # decimals as hex; the stream keeps its resolver handles alive to avoid that.
    for _ in range(3):
        StreamAssembler(X86_64).feed("jmp later\n")
        StreamAssembler(AARCH64).feed("b later\n")
    gc.collect()
    assert keystone_whole(X86_64, "mov edi, 1000") == bytes.fromhex("bfe8030000")
    assert keystone_whole(AARCH64, "mov x0, #1000") == bytes.fromhex("007d80d2")


def test_semicolon_comment_mode():
    text = "mov rax, rdi ; copy the arg; really\nadd rax, 1 ; bump\nret\n"
    prog = stream(X86_64, text, semicolon_comments=True)
    assert prog.code == keystone_whole(X86_64, "mov rax, rdi\nadd rax, 1\nret")


def test_crosscheck_reports_mismatch():
    sa = StreamAssembler(X86_64)
    sub = keystone_whole(X86_64, "sub rax, rbx")
    warnings = sa._crosscheck(Instruction(CODE_BASE, "add rax, rbx", sub, 1))
    assert warnings and "sub" in warnings[0].message
    warnings = sa._crosscheck(Instruction(CODE_BASE, "add rax, rbx", sub[:2], 1))
    assert warnings and "decoded" in warnings[0].message
    # Condition-code aliases are not mismatches.
    assert not sa._crosscheck(Instruction(CODE_BASE, "jz 0x100000", b"\x74\xfe", 1))


# -- tracing ----------------------------------------------------------------------------


def test_trace_register_deltas():
    prog = stream(X86_64, "mov rax, rsi; imul rax, rdx; add rax, rdi; ret\n")
    ret, steps = trace_program(prog, [5, 6, 7])
    assert ret == 47
    assert [s.text for s in steps] == ["mov rax, rsi", "imul rax, rdx", "add rax, rdi", "ret"]
    assert steps[0].changed == {"rax": 6}
    assert steps[1].changed["rax"] == 42
    assert steps[2].changed["rax"] == 47
    assert "rsp" in steps[3].changed


@pytest.mark.parametrize("isa", [X86_64, AARCH64], ids=lambda i: i.name)
def test_live_runner_executes_prefix_as_lines_arrive(isa):
    text = PROGRAMS[(isa.name, "sum_to")]
    lines = text.strip("\n").split("\n")
    sa = StreamAssembler(isa)
    runner = LiveRunner(sa, [4])
    progress = []
    for line in lines:
        sa.feed(line + "\n")
        runner.advance()
        progress.append(len(runner.steps))
    # Straight-line prefix runs as it arrives, then waits at the forward branch.
    first_branch = next(i for i, ln in enumerate(lines) if "done" in ln and ":" not in ln)
    assert progress[:first_branch] == list(range(1, first_branch + 1))
    assert progress[first_branch] == first_branch
    assert runner.done and runner.ret == 10
    prog = sa.finish()
    ret, steps = trace_program(prog, [4])
    assert ret == 10
    assert [(s.pc, s.changed) for s in steps] == [(s.pc, s.changed) for s in runner.steps]


# -- keystone is untrusted: regressions for reviewed silent-wrong-code and crash inputs -----


def a64_target(prog, index):
    """Decoded PC-relative target (last `#imm` operand) of the index-th instruction."""
    insn = prog.instructions[index]
    decoded = next(AARCH64.disassembler().disasm(insn.data, insn.address))
    return int(decoded.op_str.rsplit("#", 1)[-1], 16)


@pytest.mark.parametrize(
    "text,index,target",
    [
        ("b lbl+8\nnop\nnop\nlbl: nop\nnop\nret\n", 0, CODE_BASE + 0x14),
        ("tbz x0, #40, fwd-4\nnop\nfwd: ret\n", 0, CODE_BASE + 4),
        ("b.ne fwd-4\nnop\nfwd: ret\n", 0, CODE_BASE + 4),
        ("cbz x0, fwd+4\nfwd: nop\nret\n", 0, CODE_BASE + 8),
        ("ldr x0, fwd+8\nfwd: nop\nnop\nnop\nret\n", 0, CODE_BASE + 0xC),
        ("adr x0, lbl+3\nlbl: ret\n", 0, CODE_BASE + 7),
        ("nop\nlbl: nop\nb lbl+8\nnop\nret\n", 2, CODE_BASE + 0xC),  # backward
        ("nop\nlbl: nop\ncbnz x1, lbl-4\nret\n", 2, CODE_BASE),
        ("nop\nb .+8\nnop\nret\n", 1, CODE_BASE + 0xC),
        ("beq .+8\nnop\nret\n", 0, CODE_BASE + 8),
    ],
)
def test_aarch64_label_addends_are_kept(text, index, target):
    prog = stream(AARCH64, text, seed=len(text))
    assert a64_target(prog, index) == target


def test_aarch64_addends_execute():
    # ldr literal with an addend loads the second word pair; adrp + :lo12: to a forward label.
    prog = stream(AARCH64, "ldr x0, fwd+8\nret\nfwd: nop\nnop\nmov x0, #5\nmov x1, #1\n")
    want = int.from_bytes(prog.code[16:24], "little")
    assert run_function(AARCH64, prog.code, [], abi=False).ret == want
    prog = stream(AARCH64, "adrp x0, tbl\nadd x0, x0, #:lo12:tbl\nldr x0, [x0]\nret\n"
                           "nop\ntbl: mov x0, #5\nmov x1, #7\n")  # fmt: skip
    want = int.from_bytes(prog.code[20:28], "little")
    assert run_function(AARCH64, prog.code, [], abi=False).ret == want
    # Backward :lo12: is substituted directly.
    prog = stream(AARCH64, "tbl: nop\nadrp x0, tbl\nadd x0, x0, :lo12:tbl\nldr w0, [x0]\nret\n")
    assert run_function(AARCH64, prog.code, [], abi=False).ret == 0xD503201F


CRASH_INPUTS = [  # each of these used to abort the whole Python process inside keystone
    ("aarch64", "add x0, x0, #:lo12:table\ntable: ret"),
    ("aarch64", "ldr x1, [x0, :lo12:table]\nret\ntable: ret"),
    ("aarch64", "b.ne .+0x200000"),
    ("aarch64", "b .+0x10000000"),
    ("aarch64", "adr x0, .+0x200000"),
    ("aarch64", "b.eq 0x90000000"),
    ("x86_64", "movzx rcx, ah"),  # LLVM ERROR: Cannot encode high byte register ...
    ("x86_64", "sub ah, byte ptr [r13-1]"),
]


def test_no_input_can_kill_the_host_process():
    # Run in a child so a regression shows up as a failed test, not a dead test run.
    script = f"""
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from bca.isa import ISAS
from bca.stream import StreamAssembler, _KEYSTONE
for isa, text in {CRASH_INPUTS!r}:
    sa = StreamAssembler(ISAS[isa])
    sa.feed(text + "\\n")
    prog = sa.finish()
    print("RESULT", isa, repr(text), prog.ok, [e.message for e in prog.errors])
# Straight into keystone, bypassing every pre-check: the worker dies, not us.
for isa, text in [("aarch64", "b.ne .+0x200000"), ("aarch64", "b .+0x10000000"),
                  ("x86_64", "movzx rcx, ah")]:
    ok, msg, _ = _KEYSTONE.call(isa, text, 0x100000)
    assert not ok and "LLVM ERROR" in msg, msg
    assert _KEYSTONE.call(isa, "nop", 0x100000)[0]
print("restarts", _KEYSTONE.restarts)
print("survived")
"""
    res = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
    )
    assert res.returncode == 0, res.stderr[-2000:]
    assert res.stdout.rstrip().endswith("survived")
    results = [ln for ln in res.stdout.splitlines() if ln.startswith("RESULT")]
    assert len(results) == len(CRASH_INPUTS)
    for (isa, text), ln in zip(CRASH_INPUTS, results, strict=True):
        ok = ":lo12:" in text  # with the label present these now assemble correctly
        assert ln.startswith(f"RESULT {isa} {text!r} {ok}"), ln


def test_keystone_crash_is_an_error_event_and_the_stream_goes_on():
    restarts = _KEYSTONE.restarts
    sa = StreamAssembler(X86_64)
    events = sa.feed("mov rax, rdi\nmovzx rcx, ah\nadd rax, 1\n")
    assert [e.kind for e in events] == ["insn", "error"]
    assert "keystone crashed" in events[-1].message and "LLVM ERROR" in events[-1].message
    assert sa.pending_text == "add rax, 1\n"
    assert _KEYSTONE.restarts == restarts + 1
    sa.retract()
    sa.feed("add rax, 2\nret\n")
    prog = sa.finish()
    assert prog.ok and run_function(X86_64, prog.code, [40]).ret == 42
    ok, msg, _ = _KEYSTONE.call("aarch64", "b .+0x10000000", CODE_BASE)
    assert not ok and "LLVM ERROR" in msg and _KEYSTONE.restarts == restarts + 2


@pytest.mark.parametrize(
    "isa,text",
    [
        (AARCH64, "b lbl+0x10000000\nlbl: ret"),
        (AARCH64, "lbl: b lbl+0x10000000"),
        (AARCH64, "b .+0x10000000"),
        (AARCH64, "cbz x0, .+0x200000"),
        (AARCH64, "b (.+0x20000000)"),
        (AARCH64, "bl . + 1<<28"),
        (AARCH64, "ldr q0, .+0x100000"),
        (AARCH64, "beq .+0x9000000"),
        (AARCH64, "lbl: adrp x0, lbl+0x100000000"),
        (AARCH64, "tbz x0, #3, .+0x8000"),
        (AARCH64, "ldr x0, lbl+2\nlbl: ret"),  # misaligned literal
        (X86_64, "loop 0x100100"),
        (X86_64, "jmp 0x100000+0x100000000"),
        (X86_64, "mov rax, [rip+0x100000000]"),
        (X86_64, "lea rax, [rip+fwd+0x80000000]\nfwd: ret"),
        (X86_64, "mov rax, [rdi+0x80000000]"),
        (X86_64, "top: jz top+0x100000000"),
    ],
)
def test_out_of_range_targets_and_displacements_are_errors(isa, text):
    prog = assemble_stream(isa, [text + "\n"])
    assert not prog.ok
    msg = prog.errors[0].message
    assert "out of range" in msg or "does not fit" in msg or "not 4-byte aligned" in msg, msg


def test_aarch64_literal_pools_rejected():
    for text in ("mov x0, #5\nldr x1, =0x91000400d503201f\nret\n", "ldr x0, =5\nret\n"):
        prog = assemble_stream(AARCH64, [text])
        assert not prog.ok and "literal pool" in prog.errors[0].message
        assert all(len(i.data) == 4 for i in prog.instructions)


@pytest.mark.parametrize(
    "text", ["mov rax, rip", "jmp rip", "call rip", "push rip", "add rax, rip", "mov eax, eip"]
)
def test_x86_rip_is_not_a_general_register(text):
    events = StreamAssembler(X86_64).feed(text + "\n")
    assert events[-1].kind == "error" and "base register" in events[-1].message


@pytest.mark.parametrize(
    "isa,text",
    [
        (X86_64, "push 0x80000000"),
        (X86_64, "push 0xffffffff"),
        (X86_64, "push 65535"),  # keystone: 6a ff, i.e. push -1
        (X86_64, "mov al, 256"),
        (X86_64, "mov byte ptr [rdi], 300"),
        (X86_64, "add al, 300"),
        (X86_64, "cmp al, -200"),
        (X86_64, "mov ax, 70000"),
        (X86_64, "mov eax, 0x1ffffffff"),
        (X86_64, "add eax, -0x80000001"),
        (X86_64, "enter 0x10000, 0"),
        (X86_64, "ret 0x10000"),
        (AARCH64, "mov w0, #0x100000000"),
        (AARCH64, "mov w0, #-0x80000001"),
        (AARCH64, "mov x0, #-0x8000000000000001"),
    ],
)
def test_truncated_immediates_are_errors(isa, text):
    events = StreamAssembler(isa).feed(text + "\n")
    msg = events[-1].message
    assert events[-1].kind == "error" and ("does not fit" in msg or "overflow" in msg), msg


@pytest.mark.parametrize(
    "isa,text,ret",
    [
        (X86_64, "mov eax, 0xffffffff\nret", 0xFFFFFFFF),
        (X86_64, "mov rax, -1\nret", 2**64 - 1),
        (X86_64, "xor eax, eax\nmov al, 255\nret", 255),
        (X86_64, "xor eax, eax\nmov al, -128\nret", 0x80),
        (X86_64, "mov rax, 0x8000000000000000\nret", 2**63),
        (X86_64, "push -1\npop rax\nret", 2**64 - 1),
        (X86_64, "push 0x7fffffff\npop rax\nret", 0x7FFFFFFF),
        (X86_64, "mov rax, 5\nadd rax, -1\nret", 4),
        (AARCH64, "mov w0, #0xffffffff\nret", 0xFFFFFFFF),
        (AARCH64, "mov w0, #-1\nret", 0xFFFFFFFF),
        (AARCH64, "mov x0, #-1\nret", 2**64 - 1),
        (AARCH64, "mov x0, #0x5555555555555555\nret", 0x5555555555555555),
        (AARCH64, "mov x0, #1<<40\nret", 1 << 40),
    ],
)
def test_edge_immediates_still_accepted(isa, text, ret):
    prog = stream(isa, text + "\n")
    assert run_function(isa, prog.code, []).ret == ret


def test_numeric_literals_mean_the_same_with_and_without_labels():
    cs = AARCH64.disassembler()
    for text in ("tbz x0, #010, l\nl: ret\n", "tbz x0, #010, .+4\nret\n",
                 "l: nop\ntbz x0, #010, l\nret\n"):  # fmt: skip
        prog = stream(AARCH64, text)
        insn = next(i for i in prog.instructions if i.text.startswith("tbz"))
        assert ", #8, " in next(cs.disasm(insn.data, insn.address)).op_str  # octal 010
    assert stream(X86_64, "mov eax, 010\nret\n").code == keystone_whole(X86_64, "mov eax, 8\nret")
    for isa, text in ((X86_64, "mov eax, 09\n"), (AARCH64, "tbz x0, #09, .+4\n")):
        events = StreamAssembler(isa).feed(text)
        assert events[-1].kind == "error" and "octal" in events[-1].message


@pytest.mark.parametrize(
    "isa,text",
    [
        (AARCH64, "b b1\nb1: ret"),
        (AARCH64, "b1: ret"),
        (AARCH64, "cbz x0, x1\nx1: ret"),
        (AARCH64, "w0: ret"),
        (AARCH64, "eq: ret"),
        (X86_64, "rax: ret"),
        (X86_64, "nop\nbl: ret"),
    ],
)
def test_register_named_labels_get_a_clear_error(isa, text):
    prog = assemble_stream(isa, [text + "\n"])
    assert not prog.ok and "register name" in prog.errors[0].message, prog.errors[0].message


def test_x86_label_plus_constant():
    assert stream(X86_64, "top: jmp top+0\n").code == bytes.fromhex("ebfe")
    prog = stream(X86_64, "jmp top+0\ntop: ret\n")
    assert run_function(X86_64, prog.code, []).ret is not None
    text = "xor eax, eax\ntest rdi, rdi\njz fwd+2\nmov eax, 10\nfwd: inc eax\nadd eax, 2\nret\n"
    prog = stream(X86_64, text, seed=5)
    assert run_function(X86_64, prog.code, [0]).ret == 2  # skipped the 2-byte inc
    assert run_function(X86_64, prog.code, [1]).ret == 13


def test_gas_crosscheck_rejects_wrong_register_and_rolls_back():
    # keystone silently encodes `cmp sil, ch` as `cmp sil, bpl`; GNU as refuses it.
    sa = StreamAssembler(X86_64)
    events = sa.feed("mov rax, rdi\ncmp sil, ch\nadd rax, 1\nret\n")
    assert [e.kind for e in events] == ["insn", "error"]
    assert "GNU as" in events[-1].message and "line 2" in events[-1].message
    assert sa.halted is events[-1] and sa.pending_text == "add rax, 1\nret\n"
    assert sa.code() == keystone_whole(X86_64, "mov rax, rdi")
    sa.retract(keep_tail=True)
    prog = sa.finish()
    assert prog.ok and run_function(X86_64, prog.code, [41]).ret == 42
    prog = assemble_stream(X86_64, ["mov rax, rdi\ncmp sil, ch\nadd rax, 1\nret\n"],
                           abort_on_error=False)  # fmt: skip
    assert len(prog.errors) == 1 and run_function(X86_64, prog.code, [1]).ret == 2


def test_crosscheck_failure_on_a_patch_rolls_back_the_label(monkeypatch):
    real = stream_mod._gas_check
    calls = []

    def once_wrong(tool, isa, checks):
        bad = [i for i, c in enumerate(checks) if c.text.startswith("cbz")]
        if calls or not bad:
            return real(tool, isa, checks)
        calls.append(checks)
        return real(tool, isa, checks[: bad[0]]) + [(None, "injected disagreement")]

    monkeypatch.setattr(stream_mod, "_gas_check", once_wrong)
    sa = StreamAssembler(AARCH64)
    events = sa.feed("cbz x0, out\nnop\nout: mov x0, #1\nret\n")
    assert [e.kind for e in events] == ["insn", "insn", "error"]
    assert "injected disagreement" in events[-1].message and "line 3" in events[-1].message
    assert sa.pending_text == "ret\n" and "out" not in sa.finish().labels
    assert sa.instruction_at(CODE_BASE).pending == "out"
    sa.retract()
    sa.feed("out: mov x0, #1\nret\n")
    prog = sa.finish()
    assert prog.ok
    assert run_function(AARCH64, prog.code, [0]).ret == 1


@pytest.mark.parametrize(
    "text",
    ["mov rax, [0x80000000]\nret", "mov rax, [0x7fffffff]\nret", "xchg ah, cl\nret",
     "xchg cx, r8w\nret"],  # fmt: skip
)
def test_encoding_differences_that_are_not_bugs(text):
    stream(X86_64, text + "\n")


def test_keystone_generic_errors_carry_gas_diagnostics():
    events = StreamAssembler(AARCH64).feed("mov x0, #0x12345678\n")
    assert events[-1].kind == "error" and "GNU as:" in events[-1].message
    assert not stream(AARCH64, "beq l\nl: ret\n").warnings  # b<cc> alias is not a mismatch


@pytest.mark.parametrize("text", ["mov al, 256 shl 0", "mov al, 'ab'", "mov eax, 1 and 3"])
def test_x86_operand_syntax_that_cannot_be_verified_is_refused(text):
    events = StreamAssembler(X86_64).feed(text + "\n")
    assert events[-1].kind == "error" and "not supported" in events[-1].message


def test_x86_labels_named_like_masm_operators_still_work():
    prog = stream(X86_64, "mov eax, 1\njmp low\nmov eax, 2\nlow: ret\n")
    assert run_function(X86_64, prog.code, []).ret == 1


# -- second adversarial review (attack2) regressions -------------------------------------

REPO = str(Path(__file__).resolve().parents[1])
needs_gas = pytest.mark.skipif(
    not (stream_mod._gas_tool("x86_64") and stream_mod._gas_tool("aarch64")),
    reason="GNU as for both ISAs is required",
)


def run_child(script: str, timeout: float) -> str:
    """Run `script` in a fresh interpreter; the test runner survives crashes and hangs."""
    res = subprocess.run([sys.executable, "-c", f"import sys\nsys.path.insert(0, {REPO!r})\n"
                          + script], capture_output=True, text=True, timeout=timeout)  # fmt: skip
    assert res.returncode == 0, res.stderr[-3000:]
    return res.stdout


def errors_of(isa, text, **kwargs):
    return [e.message for e in assemble_stream(isa, [text], **kwargs).errors]


# 1. Whitespace: a TAB between mnemonic and operands used to disable the immediate checks.
@pytest.mark.parametrize(
    "isa,text",
    [(X86_64, "mov\teax,0x12345678\nadd\teax,0x100000000\nret\n"),
     (X86_64, "add \t eax,\t0x100000000\n"), (X86_64, "mov\x0bal, 300\n"),
     (AARCH64, "mov\tw0, #0x100000005\nret\n"), (AARCH64, "mov\t\tw0,\t#0x100000005\n")],
)  # fmt: skip
def test_tab_separated_operands_are_still_checked(isa, text):
    errs = errors_of(isa, text)
    assert errs and "does not fit" in errs[0], errs


def test_tab_separated_statements_assemble_and_run():
    prog = stream(X86_64, "xor\teax, eax\ntest\trdi,\trdi\njz\tz\nmov\teax,\t5\nz:\tret\n")
    assert run_function(X86_64, prog.code, [0]).ret == 0
    assert run_function(X86_64, prog.code, [1]).ret == 5
    prog = stream(AARCH64, "cbz\tx0,\tout\nmov\tx0, #5\nout:\tret\n")
    assert run_function(AARCH64, prog.code, [1]).ret == 5


# 2. x86 bare labels are memory operands in GNU as (and llvm-mc) Intel syntax.
@pytest.mark.parametrize(
    "stmt,expect",
    [
        ("mov rax, qword ptr lbl", "load"),
        ("mov rax, lbl", "load"),
        ("xor eax, eax\nadd rax, qword ptr lbl", "load"),
        ("push qword ptr lbl\npop rax", "load"),
        ("xor eax, eax\nmov al, byte ptr lbl", "load8"),
        ("mov rax, [lbl]", "load"),
        ("xor ebx, ebx\nmov rax, lbl[rbx]", "load"),
        ("mov rax, 1[lbl]", "load+1"),
        ("mov rax, offset lbl", "address"),
        ("lea rax, lbl", "address"),
        ("mov eax, after - lbl", "size"),
    ],
)
def test_x86_label_operands_follow_gnu_as(stmt, expect):
    text = "jmp start\nlbl: ret\n" + "nop\n" * 7 + f"after: ud2\nstart: {stmt}\nret\n"
    prog = stream(X86_64, text, seed=3)
    lbl = prog.labels["lbl"]
    at = lbl - CODE_BASE
    want = {
        "load": int.from_bytes(prog.code[at : at + 8], "little"),
        "load+1": int.from_bytes(prog.code[at + 1 : at + 9], "little"),
        "load8": prog.code[at],
        "address": lbl,
        "size": prog.labels["after"] - lbl,
    }[expect]
    res = run_function(X86_64, prog.code, [], abi=False)
    assert res.ret == want


# 3. x86 operand forms whose value keystone (and llvm-mc) wrap or drop.
@pytest.mark.parametrize(
    "text",
    ["mov al, byte ptr 300", "mov ax, word ptr 70000", "mov byte ptr [rbx], byte ptr 300",
     "mov eax, 0x100000000[rbx]", "mov eax, [rbx][0x100000000]",
     "mov eax, [rbx][rcx*4][0x80000000]", "mov eax, [rbx + 0x80000000]"],
)  # fmt: skip
def test_x86_wrapped_operands_are_errors(text):
    errs = errors_of(X86_64, text + "\nret\n")
    assert errs and ("does not fit" in errs[0] or "GNU as" in errs[0]), errs


@pytest.mark.parametrize(
    "text,same_as",
    [("mov eax, 8[rbx]", "mov eax, [rbx + 8]"),
     ("mov eax, [rbx][rcx*4][8]", "mov eax, [rbx + rcx*4 + 8]"),
     ("mov eax, 8[rbx+4]", "mov eax, [rbx + 12]"),
     ("mov eax, [4*rcx + rbx]", "mov eax, [rbx + rcx*4]")],
)  # fmt: skip
def test_x86_displacement_forms_accepted(text, same_as):
    assert stream(X86_64, text + "\n").code == keystone_whole(X86_64, same_as)


# 4. AArch64 adrp / :lo12: values: 64-bit two's complement, logical >>, as GNU as + ld.
@pytest.mark.parametrize(
    "stmt,expect",
    [
        ("adrp x0, La + (-1 >> 63)", "adrp x0, #0x100000"),
        ("add x0, x0, #:lo12:La + (0xffffffffffffffff / 6)", "add x0, x0, #0"),
        ("adrp x0, La + (0x8000000000000000 / -2) / 0x4000000000000000 * 0x1000",
         "adrp x0, #0x101000"),
        ("add x0, x0, #:lo12:La + ((3 << 63) >> 62)", "!overflow"),  # GNU as wraps silently
        ("add x0, x0, #:lo12:La + (1 << 64)", "!shift count"),  # GNU as only warns
        ("adrp x0, La + 5 / 0", "!division by zero"),
    ],
)  # fmt: skip
def test_aarch64_adrp_lo12_use_gnu_as_arithmetic(stmt, expect):
    prog = assemble_stream(AARCH64, [f"La: nop\n{stmt}\nret\n"])
    if expect.startswith("!"):
        assert not prog.ok and expect[1:] in prog.errors[0].message, prog.errors
        return
    assert prog.ok, prog.errors[0].message
    d = next(AARCH64.disassembler().disasm(prog.code[4:8], CODE_BASE + 4))
    assert f"{d.mnemonic} {d.op_str}" == expect


GAS_VALUES = [  # measured with GNU as 2.42/2.43 (`.quad expr`, identical on both ISAs)
    ("-1 >> 63", 1), ("-8 >> 1", 0x7FFFFFFFFFFFFFFC), ("0xffffffffffffffff / 6", 0),
    ("-7 / 2", -3), ("-7 % 2", -1), ("7 % -2", 1), ("1<<63", 1 << 63), ("~0", -1),
    ("256 - 2>>0*96", 64), ("1 + 2 << 3", 17), ("1 | 2 + 4", 7), ("6 & 3 + 1", 3),
    ("2 * 3 << 1", 12), ("8 / 2 * 2", 8), ("- -3", 3), ("1 ^ 3 | 4", 6), ("3 & 1 | 4", 5),
    ("0x8000000000000000 / -1", -(1 << 63)), ("0x8000000000000000 % -1", 0),
    ("-(0x8000000000000000)", -(1 << 63)), ("010 + 0b11", 11), ("-(-(12)>>7)", -0x1FFFFFFFFFFFFFF),
]  # fmt: skip


@pytest.mark.parametrize("expr,value", GAS_VALUES)
def test_evaluator_matches_gnu_as(expr, value):
    for x86 in (True, False):
        assert stream_mod._eval_const(expr, x86) & stream_mod.MASK64 == value & stream_mod.MASK64


@pytest.mark.parametrize(
    "expr",
    ["1 << 64", "1 >> 64", "1 << -1", "5 / 0", "5 % 0", "0x10000000000000000",
     "0x7fffffffffffffff * 3", "-0x8000000000000001", "0xffffffffffffffff + 1", "09"],
)  # fmt: skip
def test_evaluator_refuses_what_gnu_as_only_warns_about_or_wraps(expr):
    with pytest.raises(stream_mod._ExprError):
        stream_mod._eval_const(expr, True)


@needs_gas
def test_evaluator_agrees_with_gnu_as_quad(tmp_path):
    exprs = [e for e, _ in GAS_VALUES]
    for isa in (X86_64, AARCH64):
        src = "".join(f".quad {e}\n" for e in exprs)
        tool = stream_mod._gas_tool(isa.name)
        obj = tmp_path / f"{isa.name}.o"
        res = subprocess.run([tool, "-o", str(obj), "--"], input=src.encode(), capture_output=True)
        assert res.returncode == 0 and not res.stderr.strip(), res.stderr
        text = stream_mod._elf_text(obj.read_bytes())[0]
        for i, e in enumerate(exprs):
            got = int.from_bytes(text[8 * i : 8 * i + 8], "little")
            assert got == stream_mod._eval_const(e, isa is X86_64) & stream_mod.MASK64, e


# 5. x86 expressions follow GNU as precedence (not llvm-mc's Intel parser).
@pytest.mark.parametrize(
    "text,imm", [("sub rsp, 256 - 2>>0*96", 64), ("mov eax, 1 + 2 << 3", 17),
                 ("mov eax, 1 | 2 + 4", 7), ("mov eax, -1 >> 63", 1)],
)  # fmt: skip
def test_x86_expressions_use_gnu_as_precedence(text, imm):
    import iced_x86

    prog = stream(X86_64, text + "\n")
    ins = iced_x86.Decoder(64, prog.code, ip=CODE_BASE).decode()
    assert ins.immediate(1) == imm


# 6. A transient failure of the cross-check tool is a retryable error, never cached.
@needs_gas
def test_transient_gas_failure_is_retryable_and_not_cached(tmp_path, monkeypatch):
    real = stream_mod._gas_tool("x86_64")
    flag = tmp_path / "died"
    flaky = tmp_path / "flaky-as"
    flaky.write_text(f"#!/bin/sh\nif [ ! -e {flag} ]; then touch {flag}; cat >/dev/null; "
                     f"kill -9 $$; fi\nexec {real} \"$@\"\n")  # fmt: skip
    flaky.chmod(0o755)
    monkeypatch.setitem(stream_mod._GAS_PATH, "x86_64", str(flaky))
    text = "mov ecx, 0x7a11\nret\n"  # not seen before: must reach GNU as
    stream_mod._GAS_CACHE.pop(("x86_64", "mov ecx, 0x7a11"), None)
    sa = StreamAssembler(X86_64)
    events = sa.feed(text)  # must not raise
    assert [e.kind for e in events] == ["error"]
    assert "could not run" in events[0].message and "signal 9" in events[0].message
    assert sa.halted is events[0] and sa.pending_text == "ret\n" and sa.code() == b""
    sa.retract()
    sa.feed(text)
    assert sa.finish().ok
    assert StreamAssembler(X86_64).feed(text)[0].kind == "insn"  # nothing was poisoned


@needs_gas
def test_gas_failure_without_a_line_is_a_tool_failure(tmp_path, monkeypatch):
    broken = tmp_path / "broken-as"
    broken.write_text("#!/bin/sh\ncat >/dev/null\necho 'as: internal error' >&2\nexit 3\n")
    broken.chmod(0o755)
    monkeypatch.setitem(stream_mod._GAS_PATH, "aarch64", str(broken))
    sa = StreamAssembler(AARCH64)
    events = sa.feed("add x9, x9, #0x7a1\n")
    assert events[-1].kind == "error" and "exit status 3" in events[-1].message
    assert not stream_mod._GAS_CACHE.get(("aarch64", "add x9, x9, #0x7a1"))


# 7. Nothing escapes feed(): deep expressions and internal errors become error events.
@pytest.mark.parametrize(
    "isa,text",
    [(X86_64, "mov eax, " + "(" * 1000 + "1" + ")" * 1000),
     (X86_64, "mov eax, " + "-" * 1200 + "1"),
     (AARCH64, "b " + "(" * 1000 + "." + ")" * 1000),
     (AARCH64, "add x0, x0, #:lo12:" + "~" * 1200 + "0"),
     (X86_64, "jmp " + "+" * 1200 + "0x100000"), (AARCH64, "mov x0, #" + "-" * 3000 + "1")],
)  # fmt: skip
def test_deep_expressions_are_error_events_with_state_intact(isa, text):
    sa = StreamAssembler(isa)
    nop = keystone_whole(isa, "nop")
    events = sa.feed("nop\n" + text + "\nnop\n")
    assert [e.kind for e in events] == ["insn", "error"]
    assert sa.halted is events[-1] and sa.pending_text == "nop\n" and sa.code() == nop
    sa.retract(keep_tail=True)
    sa.feed("")
    assert sa.finish().code == nop * 2


def test_unexpected_exception_is_an_error_event(monkeypatch):
    real = StreamAssembler._encode

    def boom(self, text, addr):
        if "rcx" in text:
            raise RecursionError("maximum recursion depth exceeded (injected)")
        return real(self, text, addr)

    monkeypatch.setattr(StreamAssembler, "_encode", boom)
    sa = StreamAssembler(X86_64)
    events = sa.feed("x: mov rax, rdi\ny: mov rcx, 1\nadd rax, 1\n")
    assert [e.kind for e in events] == ["label", "insn", "error"]
    assert "internal error" in events[-1].message and "RecursionError" in events[-1].message
    assert "y" not in sa.finish().labels and sa.pending_text == "add rax, 1\n"
    sa.retract()
    sa.feed("y: ret\n")
    prog = sa.finish()
    assert prog.ok and prog.labels == {"x": CODE_BASE, "y": CODE_BASE + 3}


# 8. keystone hangs: known shapes never reach it; a timeout is paid at most once.
def test_keystone_hang_shapes_are_refused_quickly():
    out = run_child("""
import time
from bca.isa import X86_64, AARCH64
from bca.stream import StreamAssembler
for isa in (X86_64, AARCH64):
    for text in ["a:: nop", "a::", "nop ::", "a: :", ":: nop", ":", "a::b", "mov x0, a::b",
                 "x: y: :"]:
        t = time.time()
        ev = StreamAssembler(isa).feed(text + "\\n")
        assert ev and ev[-1].kind == "error", (isa.name, text, ev)
        assert time.time() - t < 2, (isa.name, text, time.time() - t)
print("fine")
""", timeout=120)
    assert out.strip().endswith("fine")


def test_keystone_timeout_is_paid_once_per_statement():
    out = run_child("""
import time
from bca.stream import _KEYSTONE
_KEYSTONE.timeout = 1.0
t = time.time(); ok, msg, _ = _KEYSTONE.call("x86_64", "nop ::", 0x100000); first = time.time() - t
assert not ok and msg.startswith("keystone hung"), msg
t = time.time()
ok2, msg2, _ = _KEYSTONE.call("x86_64", "nop ::", 0x100000)
second = time.time() - t
assert not ok2 and msg2 == msg and second < 0.1 and 0.9 < first < 5, (first, second)
assert _KEYSTONE.call("x86_64", "nop", 0x100000)[0]  # restarted and healthy
print("fine")
""", timeout=120)
    assert out.strip().endswith("fine")


# 9. Directives smuggled behind label syntax the stream does not recognise.
def test_directive_smuggling_is_refused(tmp_path):
    inc = tmp_path / "inc.s"
    inc.write_text("mov x0, #42\n")
    blob = tmp_path / "four.bin"
    blob.write_bytes(b"\x40\x05\x80\xd2")
    out = run_child(f"""
import time, resource
from bca.isa import X86_64, AARCH64
from bca.stream import StreamAssembler, _KEYSTONE
for isa in (X86_64, AARCH64):
    for d in ['@x: .fill 100000000, 1, 0x90', '"x": .fill 100000000, 1, 0x90', '@b: .byte 0x90',
              '@b: .incbin "{blob}"', '@b: .include "{inc}"', 'x: .fill 16, 1, 0x90',
              '"x": nop', 'a.b@c: nop']:
        t = time.time()
        sa = StreamAssembler(isa, abort_on_error=False)
        ev = sa.feed("nop\\n" + d + "\\nnop\\n")
        prog = sa.finish()
        assert [e.kind for e in ev] == ["insn", "error", "insn"], (d, ev)
        assert len(prog.code) == (2 if isa is X86_64 else 8), (d, prog.code.hex())
        assert len(ev[1].message) < 1000 and time.time() - t < 5, d
# The worker itself refuses to ship huge encodings back to the host.
ok, msg, _ = _KEYSTONE.call("x86_64", ".fill 100000, 1, 0x90", 0x100000)
assert not ok and "too long" in msg, msg
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
assert rss < 300_000, rss  # KiB
print("fine")
""", timeout=180)
    assert out.strip().endswith("fine")


@pytest.mark.parametrize("isa", [X86_64, AARCH64], ids=lambda i: i.name)
def test_labels_named_like_mnemonics_do_not_warn(isa):
    prog = stream(isa, "nop: nop\nret: ret\n")
    assert prog.labels == {"nop": CODE_BASE, "ret": CODE_BASE + len(keystone_whole(isa, "nop"))}


def test_messages_are_bounded():
    text = "mov rax, " + "+".join(["1"] * 2000)
    ev = StreamAssembler(X86_64).feed(text + "\n")
    assert ev[-1].kind == "error" and len(ev[-1].message) < 1000


# 10. Buffering is linear and bounded.
def test_splitter_is_linear_and_bounded():
    import time

    sa = StreamAssembler(X86_64)
    t = time.time()
    sa.feed("// c\n" * 200_000)
    assert time.time() - t < 3
    sa = StreamAssembler(X86_64, abort_on_error=False)
    line, t = "a" * 400_000, time.time()
    for i in range(0, len(line), 8):
        sa.feed(line[i : i + 8])
    sa.feed("\nnop\n")
    assert time.time() - t < 5
    prog = sa.finish()
    assert len(prog.errors) == 1 and "longer than" in prog.errors[0].message
    assert prog.code == b"\x90"
    ev = StreamAssembler(X86_64).feed("x" * (stream_mod.MAX_BUFFER + 1))
    assert ev[-1].kind == "error" and "input limit" in ev[-1].message


def test_over_long_line_halts_and_resumes_after_it():
    sa = StreamAssembler(X86_64)
    events = sa.feed("nop\n" + "x" * (stream_mod.MAX_LINE + 1))
    assert events[-1].kind == "error" and "line 2" in events[-1].message
    sa.retract(keep_tail=True)  # keep going: the rest of the long line is dropped too
    sa.feed("yyy\nret\n")
    assert sa.finish().code == b"\x90\xc3"
    sa = StreamAssembler(X86_64)
    sa.feed("nop\n" + "x" * (stream_mod.MAX_LINE + 1))
    sa.retract()  # start afresh: text fed from now on is new
    sa.feed("ret\n")
    assert sa.finish().code == b"\x90\xc3"


# Minor findings.
def test_x86_dot_and_dollar_are_the_statement_address():
    prog = stream(X86_64, "jmp $+4\nud2\nnop\nlea rax, [rip + .]\njmp .+2\nnop\nret\n")
    assert run_function(X86_64, prog.code, []).ret == CODE_BASE + 5


def test_c_block_comments():
    text = "mov eax, /* one */ 1 /* multi\nline ; still comment */\nadd eax, 2 /* x */ ; ret\n"
    prog = stream(X86_64, text, seed=4)
    assert prog.code == keystone_whole(X86_64, "mov eax, 1\nadd eax, 2\nret")
    assert stream(AARCH64, "mov x0, #3 /* c */\nret /*\n*/\n").code == keystone_whole(
        AARCH64, "mov x0, #3\nret"
    )


def test_gnu_as_rejected_negative_shift_mov():
    errs = errors_of(AARCH64, "mov w0, #-(-(12)>>7)\n")
    assert errs and "does not fit" in errs[0]


def test_warnings_of_retracted_statements_are_dropped(monkeypatch):
    def fake(self, insn):
        return [stream_mod.Event("warning", insn.address, insn.text, insn.data, "injected")]

    monkeypatch.setattr(StreamAssembler, "_crosscheck", fake)
    sa = StreamAssembler(X86_64)
    sa.feed("nop\nadd rax, 1\n")
    assert len(sa.finish().warnings) == 2
    sa.retract(statements=1)
    assert [w.text for w in sa.finish().warnings] == ["nop"]


# The GNU as oracle itself.
@needs_gas
def test_gnu_as_catches_a_wrong_keystone_encoding(monkeypatch):
    real = stream_mod._KEYSTONE.call

    def lying(isa, text, addr, placeholder=None):
        if text == "sub rdx, 0x77":  # answer with `add rdx, 0x77`: every in-process check passes
            return real(isa, "add rdx, 0x77", addr, placeholder)
        return real(isa, text, addr, placeholder)

    monkeypatch.setattr(stream_mod._KEYSTONE, "call", lying)
    events = StreamAssembler(X86_64).feed("nop\nsub rdx, 0x77\n")
    assert [e.kind for e in events] == ["insn", "error"]
    assert "GNU as encodes" in events[-1].message


@needs_gas
def test_gnu_as_check_handles_far_windows_and_longer_gas_encodings():
    # A patch 20 KiB before its label (two address windows) in one feed ...
    prog = assemble_stream(X86_64, ["jmp dest\n" + "nop\n" * 20000 + "dest: mov eax, 7\nret\n"])
    assert prog.ok and run_function(X86_64, prog.code, []).ret == 7
    # ... and GNU as picking a longer encoding than keystone (disp32+SIB vs rip-relative).
    prog = assemble_stream(X86_64, ["lea rax, [0x10]\nlea rcx, [0x20]\nret\n"])
    assert prog.ok and run_function(X86_64, prog.code, []).ret == 0x10


@needs_gas
def test_gnu_as_verifies_adrp_across_pages():
    base = CODE_BASE + 0xFF0  # the label lands on the next 4 KiB page
    text = "adrp x0, tbl\nadd x0, x0, #:lo12:tbl\nret\nnop\ntbl: nop\n"
    prog = assemble_stream(AARCH64, [text], base=base)
    assert prog.ok, prog.errors
    d = next(AARCH64.disassembler().disasm(prog.code[:4], base))
    assert d.op_str == f"x0, #{(base + 16) & ~0xFFF:#x}"


def gnu_link_a64(src: str, base: int, tmp_path) -> bytes | None:
    """Assemble with GNU as and link at `base` with GNU ld: fully independent bytes."""
    import shutil

    gas = stream_mod._gas_tool("aarch64")
    ld, objcopy = shutil.which("aarch64-linux-gnu-ld"), shutil.which("aarch64-linux-gnu-objcopy")
    if not (gas and ld and objcopy):
        pytest.skip("GNU as/ld/objcopy for aarch64 required")
    s, o, e, b = (tmp_path / n for n in ("a.s", "a.o", "a.elf", "a.bin"))
    s.write_text(".text\n.globl _start\n_start:\n" + src + "\n")
    for cmd in ([gas, "-o", o, s], [ld, "--no-relax", f"-Ttext={base:#x}", "-o", e, o],
                [objcopy, "-O", "binary", "-j", ".text", e, b]):  # fmt: skip
        res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
        if res.returncode or "arning" in res.stderr:
            return None
    return b.read_bytes()


@pytest.mark.parametrize(
    "stmt",
    ["adrp x2, La + -(2147483647)", "adrp x2, Ld + ~(4095 * 07777<<0X2)", "adrp x0, La - 0x100001",
     "adrp x0, Ld + 0x7ffff000", "add x0, x0, #:lo12:La + (-1 >> 52)",
     "ldr x0, [x0, #:lo12:Ld + 8]",
     "b Ld + (0xffffffffffffffff / 6)", "cbz x3, La + 1<<3", "tbz x0, #0x1f, Ld - (8 >> 1)",
     "adr x0, . + (Lb - La) + 7", "ldr w0, Ld + 010"],
)  # fmt: skip
@pytest.mark.parametrize("base", [CODE_BASE, CODE_BASE + 0xFF8])
def test_aarch64_pc_relative_values_match_gnu_as_and_ld(stmt, base, tmp_path):
    src = f"La: nop\nLb: nop\n{stmt}\nret\nLd: nop\n"
    want = gnu_link_a64(src, base, tmp_path)
    prog = assemble_stream(AARCH64, [src], base=base)
    assert prog.ok == (want is not None), prog.errors
    if want is not None:
        assert prog.code == want


@needs_gas
def test_failed_gas_run_reaps_every_window(tmp_path, monkeypatch):
    broken = tmp_path / "broken-as"
    broken.write_text("#!/bin/sh\ncat >/dev/null\nexit 7\n")
    broken.chmod(0o755)
    monkeypatch.setitem(stream_mod._GAS_PATH, "x86_64", str(broken))
    sa = StreamAssembler(X86_64, crosscheck=False)
    sa.feed("jmp dest\n" + "nop\n" * 6000)  # a pending branch far from the next checks
    sa._gas = str(broken)
    events = sa.feed("dest: mov edx, 0x5151\n")  # patch + new insn: two address windows
    assert events[-1].kind == "error" and "exit status 7" in events[-1].message
    out = subprocess.run(["ps", "-o", "stat=", "--ppid", str(__import__("os").getpid())],
                         capture_output=True, text=True).stdout  # fmt: skip
    assert not [s for s in out.split() if s.startswith("Z")], out


@pytest.mark.parametrize(
    "isa,text", [(AARCH64, 'mov x0, "a'), (X86_64, "nop \\"), (AARCH64, "add x0, x0, #'a'")]
)
def test_quotes_and_backslashes_are_refused(isa, text):
    errs = errors_of(isa, text + "\n")
    assert errs and "not supported" in errs[0]


# -- x86 ISA level: what the emulator executes, what the stream refuses ---------------------
# Probed against Unicorn: every "supported" program below runs and returns the value real
# hardware would; every "unsupported" one either raises UC_ERR_INSN_INVALID in Unicorn (ADX,
# AVX2, FMA, F16C, SHA, AVX-512, TBM, XOP, 3DNow!) or, for VEX-128 AVX, silently computes the
# wrong value. The stream refuses the latter group; prompts.ISA_LEVEL states the split.

_X, _Y = 0x123456789ABCDEF1, 0x0FEDCBA987654321
_M64 = (1 << 64) - 1


def _pdep(v, m):
    r = k = 0
    for i in range(64):
        if m >> i & 1:
            r |= (v >> k & 1) << i
            k += 1
    return r


def _pext(v, m):
    r = k = 0
    for i in range(64):
        if m >> i & 1:
            r |= (v >> i & 1) << k
            k += 1
    return r


def _crc32c(v):
    crc = 0
    for byte in v.to_bytes(8, "little"):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc


def _clmul(a, b):
    r = 0
    for i in range(64):
        if b >> i & 1:
            r ^= a << i
    return r


def _lanes32(f):
    lo = f(_X & 0xFFFFFFFF, _Y & 0xFFFFFFFF) & 0xFFFFFFFF
    hi = f(_X >> 32, _Y >> 32) & 0xFFFFFFFF
    return lo | hi << 32


# (extension, program with x in rdi and y in rsi, expected rax)
X86_SUPPORTED = [
    ("BMI2 mulx", "mov rdx, rdi\nmulx rcx, rax, rsi\nmov rax, rcx", (_X * _Y) >> 64),
    ("BMI2 rorx", "rorx rax, rdi, 13", ((_X >> 13) | (_X << 51)) & _M64),
    ("BMI2 shlx", "mov ecx, 70\nshlx rax, rdi, rcx", (_X << 6) & _M64),
    ("BMI2 sarx", "mov ecx, 5\nsarx rax, rdi, rcx", _X >> 5),
    ("BMI2 shrx", "mov ecx, 5\nshrx rax, rdi, rcx", _X >> 5),
    ("BMI2 bzhi", "mov ecx, 12\nbzhi rax, rdi, rcx", _X & 0xFFF),
    ("BMI2 pdep", "pdep rax, rdi, rsi", _pdep(_X, _Y)),
    ("BMI2 pext", "pext rax, rdi, rsi", _pext(_X, _Y)),
    ("BMI1 andn", "andn rax, rdi, rsi", ~_X & _Y & _M64),
    ("BMI1 bextr", "mov ecx, 0x0804\nbextr rax, rdi, rcx", (_X >> 4) & 0xFF),
    ("BMI1 blsr", "blsr rax, rdi", _X & (_X - 1)),
    ("BMI1 blsi", "blsi rax, rdi", _X & -_X),
    ("BMI1 tzcnt", "shl rdi, 8\ntzcnt rax, rdi", 8),
    ("BMI1 tzcnt of 0", "xor edi, edi\ntzcnt rax, rdi", 64),
    ("LZCNT", "mov edi, 0xff\nlzcnt rax, rdi", 56),
    ("LZCNT of 0", "xor edi, edi\nlzcnt rax, rdi", 64),
    ("POPCNT", "popcnt rax, rdi", bin(_X).count("1")),
    ("MOVBE", "mov qword ptr [rsp - 8], rdi\nmovbe rax, qword ptr [rsp - 8]",
     int.from_bytes(_X.to_bytes(8, "little"), "big")),
    ("SSE4.2 crc32", "xor eax, eax\ncrc32 rax, rdi", _crc32c(_X)),
    ("SSSE3 pshufb", "movq xmm0, rdi\npxor xmm1, xmm1\npshufb xmm0, xmm1\nmovq rax, xmm0",
     (_X & 0xFF) * 0x0101010101010101),
    ("SSE4.1 pmulld", "movq xmm0, rdi\nmovq xmm1, rsi\npmulld xmm0, xmm1\nmovq rax, xmm0",
     _lanes32(lambda a, b: a * b)),
    ("SSE4.1 ptest", "movq xmm0, rdi\npxor xmm1, xmm1\nptest xmm0, xmm1\nsetz al\nmovzx eax, al", 1),
    ("SSE4.1 pextrq", "movq xmm0, rdi\npinsrq xmm0, rsi, 1\npextrq rax, xmm0, 1", _Y),
    ("SSE4.2 pcmpgtq", "movq xmm0, rdi\nmovq xmm1, rsi\npcmpgtq xmm0, xmm1\nmovq rax, xmm0", _M64),
    ("SSE2 paddd", "movq xmm0, rdi\nmovq xmm1, rsi\npaddd xmm0, xmm1\nmovq rax, xmm0",
     _lanes32(lambda a, b: a + b)),
    ("PCLMULQDQ", "movq xmm0, rdi\nmovq xmm1, rsi\npclmulqdq xmm0, xmm1, 0\nmovq rax, xmm0",
     _clmul(_X, _Y) & _M64),
    ("AES-NI aesenc", "movq xmm0, rdi\nmovq xmm1, rsi\naesenc xmm0, xmm1\nmovq rax, xmm0", None),
]

X86_UNSUPPORTED = [
    ("adcx rax, rdi", "ADX instructions are not supported by the emulator; use adc"),
    ("adox rax, rdi", "ADX instructions are not supported by the emulator; use adc"),
    ("vpaddd xmm0, xmm1, xmm2", "VEX-encoded vector"),  # VEX-128 AVX: Unicorn miscomputes it
    ("vpxor xmm0, xmm1, xmm2", "VEX-encoded vector"),
    ("vpaddd ymm0, ymm1, ymm2", "VEX-encoded vector"),  # AVX2
    ("vmovdqu ymm0, ymm1", "VEX-encoded vector"),
    ("vfmadd231pd xmm0, xmm1, xmm2", "VEX-encoded vector"),  # FMA
    ("vcvtph2ps xmm0, xmm1", "VEX-encoded vector"),  # F16C
    ("vzeroupper", "VEX-encoded vector"),
    ("vpaddd zmm0, zmm1, zmm2", "AVX-512 (EVEX)"),
    ("sha256rnds2 xmm0, xmm1, xmm0", "SHA instructions"),
    ("sha1rnds4 xmm0, xmm1, 0", "SHA instructions"),
    ("blcfill rax, rdi", "TBM instructions"),
    ("vprotd xmm0, xmm1, 3", "XOP instructions"),
    ("pfadd mm0, mm1", "3DNow! instructions"),
]


@pytest.mark.parametrize("name,body,want", X86_SUPPORTED, ids=[s[0] for s in X86_SUPPORTED])
def test_x86_isa_level_supported_instructions_assemble_and_execute(name, body, want):
    prog = stream(X86_64, "f:\n" + body + "\nret\n")
    got = run_function(X86_64, prog.code, [_X, _Y], abi=False).ret
    if want is not None:
        assert got == want, (name, hex(got), hex(want))


@pytest.mark.parametrize("text,message", X86_UNSUPPORTED, ids=[u[0] for u in X86_UNSUPPORTED])
def test_x86_isa_level_unsupported_instructions_are_refused(text, message):
    # The emulator cannot run them (or runs them wrongly): check that keystone encodes them, then
    # that the stream refuses the statement with the reason.
    enc = keystone_whole(X86_64, text)
    prog = assemble_stream(X86_64, ["f:\n", text + "\n", "ret\n"])
    assert not prog.ok and message in prog.errors[0].message, [e.message for e in prog.errors]
    assert "not supported by the emulator" in prog.errors[0].message
    if text in _UNICORN_INVALID:  # and the emulator really cannot run them
        with pytest.raises(EmulationError, match="UC_ERR_INSN_INVALID"):
            run_function(X86_64, enc + b"\xc3", [], abi=False)


_UNICORN_INVALID = {"adcx rax, rdi", "adox rax, rdi", "vpaddd ymm0, ymm1, ymm2", "vmovdqu ymm0, ymm1",
                    "vfmadd231pd xmm0, xmm1, xmm2", "vcvtph2ps xmm0, xmm1", "sha256rnds2 xmm0, xmm1, xmm0",
                    "sha1rnds4 xmm0, xmm1, 0", "vpaddd zmm0, zmm1, zmm2", "pfadd mm0, mm1"}


def test_vex128_avx_is_silently_wrong_in_the_emulator():
    """Why VEX-128 AVX is refused rather than left to crash: Unicorn runs it with a wrong result."""
    enc = keystone_whole(X86_64, "movq xmm1, rdi\nmovq xmm2, rsi\nvpaddd xmm0, xmm1, xmm2\nmovq rax, xmm0\nret")
    got = run_function(X86_64, enc, [_X, _Y], abi=False).ret
    assert got != _lanes32(lambda a, b: a + b)


def test_x86_isa_level_matches_the_prompt():
    from bca.liveeval.prompts import ISA_LEVEL

    text = ISA_LEVEL["x86_64"]
    for name, _, _ in X86_SUPPORTED:
        ext, _, mnemonic = name.partition(" ")
        ext = {"SSE4.1": "SSE4.1", "SSE4.2": "SSE4.2", "SSE2": "SSE", "AES-NI": "AES-NI"}.get(ext, ext)
        assert ext.split(".")[0] in text, name
        if ext.startswith("BMI") and mnemonic and " of " not in mnemonic:
            assert mnemonic in text, name
    for ext in ("AVX, AVX2, AVX-512", "FMA", "F16C", "ADX (adcx/adox; use adc)", "SHA"):
        assert ext in text.split("Not supported")[1], ext


def test_x86_bmi_vex_forms_are_not_mistaken_for_avx():
    stream(X86_64, "f:\nandn rax, rdi, rsi\nmulx rcx, rax, rdi\nrorx rax, rdi, 3\nblsmsk rax, rdi\nret\n")


def test_structured_x86_cannot_express_adx_or_avx():
    from bca.structured import x64

    ops = {op for fam in x64.FAMILIES for op in fam.ops}
    assert "adc" in ops
    assert not ops & {"adcx", "adox", "mulx", "vpaddd", "vmovdqu", "vzeroupper"}
    assert not any(op.startswith("v") for op in ops)
