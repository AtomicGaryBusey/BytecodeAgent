import os
import stat

import pytest

from bca.difftest import (
    BUFFER_OFFSET,
    INT64_MAX,
    INT64_MIN,
    buffer_differential,
    cache_dir,
    compile_c_reference,
    differential,
    div_safe,
    edge_values,
    int_inputs,
)
from bca.emu import (
    DATA_BASE,
    ABIViolation,
    EmulationError,
    OutOfBoundsWrite,
    run_function,
)
from bca.isa import AARCH64, X86_64

ADD_REF = "int64_t add3(int64_t a, int64_t b, int64_t c) { return a + b * c; }"
ADD2_REF = "int64_t add2(int64_t a, int64_t b) { return a + b; }"


def assemble(isa, text):
    encoding, _ = isa.assembler().asm(text, 0x100000)
    return bytes(encoding)


def test_x86_function_call_and_trace():
    code = assemble(X86_64, "mov rax, rsi; imul rax, rdx; add rax, rdi; ret")
    res = run_function(X86_64, code, [5, 6, 7], trace=True)
    assert res.ret == 47
    assert res.steps[0].changed["rax"] == 6
    assert len(res.steps) == 4
    assert res.insn_count == 4


def test_aarch64_function_call():
    code = assemble(AARCH64, "madd x0, x1, x2, x0; ret")
    assert run_function(AARCH64, code, [5, 6, 7]).ret == 47


def test_differential_catches_bug():
    ref = compile_c_reference(ADD_REF, "add3", 3)
    good = assemble(AARCH64, "madd x0, x1, x2, x0; ret")
    bad = assemble(AARCH64, "add x0, x0, x1; ret")
    assert differential(AARCH64, good, ref, int_inputs(3, 50)).ok
    assert not differential(AARCH64, bad, ref, int_inputs(3, 50)).ok


def test_shared_assembler_survives_keystone_resolver_bug():
    import gc

    for isa, text, expect in (
        (X86_64, "mov edi, 1000", "bfe8030000"),
        (AARCH64, "mov x0, #1000", "007d80d2"),
    ):
        ks = isa.new_assembler()
        ks.sym_resolver = lambda name, value: False
        del ks
        gc.collect()
        assert bytes(isa.assembler().asm(text)[0]).hex() == expect


# -- uninitialised state ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "isa,text",
    [
        (X86_64, "add rax, rdi; add rax, rsi; ret"),  # accumulates into uninitialised rax
        (X86_64, "lea rax, [rdi+rsi]; add rax, rdx; ret"),  # rdx is not an argument here
        (X86_64, "mov rax, rdi; adc rax, rsi; ret"),  # uninitialised carry flag
        (AARCH64, "add x0, x0, x1; add x0, x0, x9; ret"),
        (AARCH64, "adds x0, x0, x1; ldr x2, [sp, #-8]; add x0, x0, x2; ret"),  # stale stack
    ],
)
def test_uninitialised_reads_fail(isa, text):
    ref = compile_c_reference(ADD2_REF, "add2", 2)
    assert not differential(isa, assemble(isa, text), ref, int_inputs(2, 20)).ok


def test_register_garbage_is_seeded_and_varies():
    code = assemble(X86_64, "mov rax, rbx; ret")
    a = run_function(X86_64, code, [], seed=1).ret
    assert a == run_function(X86_64, code, [], seed=1).ret
    assert a != run_function(X86_64, code, [], seed=2).ret
    assert a not in (0, 2**64 - 1)
    # Tracing keeps the all-zero initial state (matches bca.stream.LiveRunner) unless asked.
    assert run_function(X86_64, code, [], trace=True).ret == 0
    assert run_function(X86_64, code, [], trace=True, garbage=True, seed=1).ret == a
    assert run_function(X86_64, code, [], garbage=False).ret == 0


# -- calling convention -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "isa,text,register",
    [
        (X86_64, "mov rbx, 7; lea rax, [rdi+rsi]; ret", "rbx"),
        (X86_64, "xor ebp, ebp; lea rax, [rdi+rsi]; ret", "rbp"),
        (X86_64, "mov r15, rdi; lea rax, [rdi+rsi]; ret", "r15"),
        (X86_64, "lea rax, [rdi+rsi]; mov rcx, [rsp]; sub rsp, 64; jmp rcx", "rsp"),
        (X86_64, "lea rax, [rdi+rsi]; pop rcx; sub rsp, 8; jmp rcx", "rsp"),
        (X86_64, "lea rax, [rdi+rsi]; std; ret", "eflags"),
        (AARCH64, "mov x19, #7; add x0, x0, x1; ret", "x19"),
        (AARCH64, "mov x29, sp; add x0, x0, x1; ret", "x29"),
        (AARCH64, "fmov d9, x0; add x0, x0, x1; ret", "d9"),
        (AARCH64, "sub sp, sp, #64; add x0, x0, x1; ret", "sp"),
    ],
)
def test_abi_violations_fail(isa, text, register):
    code = assemble(isa, text)
    with pytest.raises(ABIViolation) as info:
        run_function(isa, code, [3, 4])
    assert info.value.register == register
    assert register in str(info.value)
    ref = compile_c_reference(ADD2_REF, "add2", 2)
    report = differential(isa, code, ref, int_inputs(2, 5))
    assert not report.ok and register in report.mismatches[0].error
    # Deliberately non-ABI snippets can opt out.
    assert run_function(isa, code, [3, 4], abi=False).ret == 7


@pytest.mark.parametrize(
    "isa,text",
    [
        (X86_64, "push rbx; push rbp; mov rbx, rdi; lea rbp, [rbx+rsi]; mov rax, rbp; "
                 "pop rbp; pop rbx; ret"),
        (X86_64, "sub rsp, 24; mov [rsp], rdi; mov rax, [rsp]; add rax, rsi; add rsp, 24; ret"),
        (AARCH64, "stp x29, x30, [sp, #-32]!; str x19, [sp, #16]; mov x29, sp; mov x19, x1; "
                  "add x0, x0, x19; ldr x19, [sp, #16]; ldp x29, x30, [sp], #32; ret"),
    ],
)  # fmt: skip
def test_abi_conforming_code_passes(isa, text):
    ref = compile_c_reference(ADD2_REF, "add2", 2)
    assert differential(isa, assemble(isa, text), ref, int_inputs(2, 20)).ok


def test_writing_the_callers_frame_fails():
    for isa, text in (
        (X86_64, "mov [rsp], rdi; ret"),  # clobbers the return address slot
        (X86_64, "mov [rsp+16], rdi; mov rax, rdi; ret"),
        (AARCH64, "str x0, [sp, #8]; ret"),
    ):
        with pytest.raises(ABIViolation, match="caller's stack frame"):
            run_function(isa, assemble(isa, text), [1])


# -- inputs -----------------------------------------------------------------------------------


@pytest.mark.parametrize("nargs,n", [(2, 50), (3, 50), (3, 300), (4, 0), (6, 10)])
def test_every_argument_sees_every_edge_value(nargs, n):
    inputs = list(int_inputs(nargs, n))
    edges = set(edge_values())
    for p in range(nargs):
        assert edges <= {args[p] for args in inputs}, p
    # ...and every pair of arguments sees every pair of edge values.
    for p in range(nargs):
        for q in range(p + 1, nargs):
            pairs = {(a[p], a[q]) for a in inputs}
            assert all((x, y) in pairs for x in edges for y in edges)
    assert inputs == list(int_inputs(nargs, n))  # deterministic


def test_random_inputs_mix_small_and_full_range():
    values = [a[0] for a in int_inputs(1, 400, seed=5)]
    assert any(abs(v) <= 1000 for v in values[14:])
    assert any(abs(v) > 2**40 for v in values[14:] if v not in edge_values())
    bounded = list(int_inputs(2, 100, lo=0, hi=10))
    assert all(0 <= v <= 10 for a in bounded[-100:] for v in a)  # random part only


@pytest.mark.parametrize(
    "isa,text,ref,nargs",
    [
        (AARCH64, "sxtw x2, w2; madd x0, x1, x2, x0; ret", ADD_REF, 3),  # truncates arg 3
        (AARCH64, "sxtw x1, w1; add x0, x0, x1; ret", ADD2_REF, 2),
        (X86_64, "movsxd rdx, edx; mov rax, rsi; imul rax, rdx; add rax, rdi; ret", ADD_REF, 3),
    ],
)
def test_argument_truncation_fails(isa, text, ref, nargs):
    name = "add3" if nargs == 3 else "add2"
    fn = compile_c_reference(ref, name, nargs)
    assert not differential(isa, assemble(isa, text), fn, int_inputs(nargs, 50)).ok


def test_domain_bounds_every_value_for_loop_kernels():
    inputs = list(int_inputs(2, 200, domain=(-5, 1000)))
    assert all(-5 <= v <= 1000 for a in inputs for v in a)
    for p in range(2):
        assert {-5, -4, 0, 1, 999, 1000, 255} <= {a[p] for a in inputs}
    per_arg = list(int_inputs(2, 50, domain=[(0, 10), (INT64_MIN, INT64_MAX)]))
    assert all(0 <= a[0] <= 10 for a in per_arg)
    assert INT64_MIN in {a[1] for a in per_arg}
    # A loop-count kernel over the bounded domain terminates quickly.
    ref = compile_c_reference(
        "int64_t sum(int64_t n) { int64_t s = 0; for (int64_t i = 1; i <= n; i++) s += i; return s; }",
        "sum", 1,
    )  # fmt: skip
    code = assemble(AARCH64, "mov x1, #0\nl: cmp x0, #0\nb.le d\nadd x1, x1, x0\n"
                             "sub x0, x0, #1\nb l\nd: mov x0, x1\nret")  # fmt: skip
    assert differential(AARCH64, code, ref, int_inputs(1, 100, domain=(-1000, 1000))).ok


# -- reference semantics ----------------------------------------------------------------------


def test_reference_wraps_on_signed_overflow():
    ref = compile_c_reference("int64_t inc_gt(int64_t a) { return a + 1 > a; }", "inc_gt", 1)
    assert ref(INT64_MAX) == 0
    code = assemble(X86_64, "lea rcx, [rdi+1]; xor eax, eax; cmp rcx, rdi; setg al; ret")
    assert differential(X86_64, code, ref, int_inputs(1, 50)).ok
    add = compile_c_reference(ADD2_REF, "add2", 2)
    assert add(INT64_MAX, 1) == INT64_MIN
    assert differential(X86_64, assemble(X86_64, "lea rax, [rdi+rsi]; ret"), add,
                        [(INT64_MAX, 1), (INT64_MIN, -1)]).ok  # fmt: skip


def test_division_precondition_excludes_undefined_inputs():
    ref = compile_c_reference("int64_t q(int64_t a, int64_t b) { return a / b; }", "q", 2)
    code = assemble(X86_64, "mov rax, rdi; cqo; idiv rsi; ret")
    report = differential(X86_64, code, ref, int_inputs(2, 100))
    assert not report.ok  # b == 0 traps on x86 (the host reference said 0)
    report = differential(X86_64, code, ref, int_inputs(2, 100), where=div_safe(0, 1))
    assert report.ok and report.skipped > 0
    inputs = list(int_inputs(2, 100, where=div_safe(0, 1)))
    assert all(b != 0 and (a, b) != (INT64_MIN, -1) for a, b in inputs)
    a64 = assemble(AARCH64, "sdiv x0, x0, x1; ret")
    assert differential(AARCH64, a64, ref, inputs).ok


def test_reference_cache_is_private_and_atomic(tmp_path, monkeypatch):
    shared = tmp_path / "cache"
    shared.mkdir(mode=0o777)
    os.chmod(shared, 0o777)
    monkeypatch.setenv("BCA_CACHE_DIR", str(shared))
    assert cache_dir() == shared
    assert stat.S_IMODE(os.stat(shared).st_mode) == 0o700
    fn = compile_c_reference("int64_t seven(void) { return 7; }", "seven", 0)
    assert fn() == 7
    libs = list(shared.iterdir())
    assert [p.suffix for p in libs] == [".so"] and not libs[0].name.startswith(".")
    link = tmp_path / "link"
    link.symlink_to(shared)
    monkeypatch.setenv("BCA_CACHE_DIR", str(link))
    with pytest.raises(RuntimeError, match="not a directory"):
        cache_dir()
    monkeypatch.delenv("BCA_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_dir() == tmp_path / "xdg" / "bca"
    assert stat.S_IMODE(os.stat(tmp_path / "xdg" / "bca").st_mode) == 0o700


# -- buffers ----------------------------------------------------------------------------------

NEG_REF = "int64_t neg(int64_t *b, int64_t n) { for (int64_t i = 0; i < n; i++) b[i] = -b[i]; return n; }"
NEG_LOOP = """mov x2, #0
loop: cmp x2, x1
b.ge done
ldr x3, [x0, x2, lsl #3]
neg x3, x3
str x3, [x0, x2, lsl #3]
add x2, x2, #1
b loop
done: {tail}
mov x0, x1
ret"""
BUFS = [[1, 2, 3], [-5], [], [7] * 10, [INT64_MIN, INT64_MAX]]


def test_buffer_correct_code_passes():
    code = assemble(AARCH64, NEG_LOOP.format(tail="nop"))
    assert buffer_differential(AARCH64, code, NEG_REF, "neg", BUFS).ok


@pytest.mark.parametrize(
    "tail",
    [
        "str x2, [x0, x2, lsl #3]",  # buf[n] = n
        "str xzr, [x0, #-8]",  # buf[-1] = 0
        "stur x2, [x0, #-4]",  # straddles the start of the buffer
        "mov x9, #0x400000\nstr x1, [x9]",  # a stray absolute write elsewhere in the region
    ],
)
def test_buffer_out_of_bounds_write_fails(tail):
    code = assemble(AARCH64, NEG_LOOP.format(tail=tail))
    report = buffer_differential(AARCH64, code, NEG_REF, "neg", BUFS)
    assert not report.ok
    assert "out-of-bounds write" in report.mismatches[0].error


def test_buffer_reads_past_the_end_see_junk():
    # Returns buf[0] even for n == 0; the reference returns 0 then.
    ref = "int64_t first(int64_t *b, int64_t n) { return n ? b[0] : 0; }"
    code = assemble(AARCH64, "ldr x0, [x0]; ret")
    report = buffer_differential(AARCH64, code, ref, "first", [[5], [], [0]])
    assert [m.args for m in report.mismatches] == [()]


def test_guarded_run_function_reports_address():
    code = assemble(X86_64, "mov [rdi+8], rsi; ret")
    with pytest.raises(OutOfBoundsWrite) as info:
        run_function(X86_64, code, [DATA_BASE + BUFFER_OFFSET, 1], data=bytes(8),
                     data_offset=BUFFER_OFFSET, guard=(BUFFER_OFFSET, BUFFER_OFFSET + 8))  # fmt: skip
    assert info.value.address == DATA_BASE + BUFFER_OFFSET + 8
    # Scalar differential forbids data writes altogether.
    ref = compile_c_reference(ADD2_REF, "add2", 2)
    code = assemble(X86_64, "mov qword ptr [0x400100], rdi; lea rax, [rdi+rsi]; ret")
    assert not differential(X86_64, code, ref, [(1, 2)]).ok


# -- machine reuse and options ----------------------------------------------------------------


def test_pooled_machine_resets_between_runs():
    long_code = assemble(X86_64, "mov rax, 1; mov rbx, rbx; " + "nop; " * 40 + "ret")
    assert run_function(X86_64, long_code, []).ret == 1
    # Shorter code at the same address: stale bytes/translations must not survive.
    assert run_function(X86_64, assemble(X86_64, "mov rax, 2; ret"), []).ret == 2
    falls_off = assemble(X86_64, "mov rax, 3")
    with pytest.raises(EmulationError):
        run_function(X86_64, falls_off, [])
    # A fault must not poison the next run; nor may memory written by a previous run leak.
    run_function(X86_64, assemble(X86_64, "mov [rdi], rsi; ret"), [DATA_BASE, 99], data=bytes(8))
    res = run_function(X86_64, assemble(X86_64, "mov rax, [rdi]; ret"), [DATA_BASE], data=bytes(8))
    assert res.ret == 0
    spin = assemble(AARCH64, "l: b l")
    with pytest.raises(EmulationError, match="did not return within 1000"):
        run_function(AARCH64, spin, [], max_insns=1000)
    assert run_function(AARCH64, assemble(AARCH64, "mov x0, #5; ret"), []).ret == 5


def test_insn_count_and_extra_segments():
    code = assemble(AARCH64, "add x0, x0, x1; ret")
    assert run_function(AARCH64, code, [1, 2], count_insns=True).insn_count == 2
    # An extra writable segment, e.g. a .data section for position-dependent code.
    seg = 0x2000000
    code = bytes(AARCH64.assembler().asm(
        "movz x1, #0x200, lsl #16; ldr x2, [x1]; add x2, x2, x0; str x2, [x1]; mov x0, x2; ret",
        0x100000,
    )[0])  # fmt: skip
    res = run_function(AARCH64, code, [5], segments=[(seg, (37).to_bytes(8, "little"), "rw")])
    assert res.ret == 42
    # Alternative load base.
    base = 0x3000000
    code = bytes(X86_64.assembler().asm("lea rax, [rip]; ret", base)[0])
    assert run_function(X86_64, code, [], code_base=base).ret == base + 7
    with pytest.raises(ValueError, match="overlaps"):
        run_function(X86_64, code, [], code_base=DATA_BASE)


# -- structured session -----------------------------------------------------------------------


def test_session_emit_copies_the_callers_instruction():
    from bca.structured import Session

    s = Session("aarch64")
    insn = {"op": "mov", "dst": "x0", "src": [{"imm": 7}]}
    assert s.emit(insn)["ok"]
    insn["src"][0]["imm"] = 99  # caller reuses its dict afterwards
    insn["dst"] = "x5"
    assert s.emit({"op": "ret"})["ok"]
    assert run_function(AARCH64, s.finish().code).ret == 7
    program = [{"op": "mov", "dst": "x0", "src": [{"imm": 3}]}, {"op": "ret"}]
    assert s.emit_program(program)["ok"]
    program[0]["src"][0]["imm"] = 4
    assert run_function(AARCH64, s.finish().code).ret == 3
