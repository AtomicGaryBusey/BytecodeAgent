"""Round-2 live-eval harness with a scripted FakeSubject (no model calls, no quota), plus the
emulator / difftest / encoder extensions round 2 needs."""

import json
import math
import zlib

import pytest

from bca.difftest import (
    BUFFER_OFFSET,
    MemCase,
    Ptr,
    buffer_differential,
    compile_c_reference,
    differential,
    memory_differential,
)
from bca.emu import (
    CALLBACK_BASE,
    DATA_BASE,
    ABIViolation,
    EmulationError,
    InstructionLimit,
    MisalignedAccess,
    MisalignedCall,
    OutOfBoundsRead,
    OutOfBoundsWrite,
    callback_segments,
    hostile_callback,
    run_function,
)
from bca.ir import lower
from bca.isa import AARCH64, ISAS, X86_64
from bca.liveeval import FakeSubject, attempt, build_prompt, check, run_cell
from bca.liveeval.__main__ import main as cli_main
from bca.liveeval.pipelines import (
    calls_own_entry,
    classify_mismatch,
    requirement_failure,
    uses_simd,
)
from bca.liveeval.prompts import ABI_EXTRA
from bca.liveeval.runner import run_matrix
from bca.liveeval.summarize import summarize
from bca.liveeval.tasks import TASKS as ROUND1
from bca.liveeval.tasks2 import ALL_BY_NAME, ROUND2, feasible_cells, select_tasks
from bca.stream import StreamAssembler
from bca.structured import assemble as structured_assemble
from tests.liveeval_solutions import A64, QBE, WRONG, X86, X86_DOT4, correct, to_structured

PATHS = ("stream", "structured", "ir")
ISA_NAMES = ("x86_64", "aarch64")
CELLS = feasible_cells(ROUND2, PATHS, ISA_NAMES)


def asm(isa, src):
    sa = StreamAssembler(isa)
    sa.feed(src)
    prog = sa.finish()
    assert prog.ok, prog.errors
    return prog.code


def chunks(text, n=9):
    return [text[i : i + n] for i in range(0, len(text), n)]


# -- emulator: stack arguments ------------------------------------------------------------------


def test_stack_args_x86_and_aarch64():
    code = asm(X86_64, "mov rax, qword ptr [rsp + 8]\nsub rax, qword ptr [rsp + 16]\nret\n")
    assert run_function(X86_64, code, [0, 0, 0, 0, 0, 0, 50, 8]).ret == 42
    code = asm(AARCH64, "ldr x9, [sp]\nldr x10, [sp, #8]\nsub x0, x9, x10\nret\n")
    assert run_function(AARCH64, code, [0] * 8 + [50, 8]).ret == 42
    # Register-only calls are unchanged.
    assert run_function(X86_64, asm(X86_64, "lea rax, [rdi + rsi]\nret\n"), [40, 2]).ret == 42


def test_stack_arg_slots_are_the_callees_but_the_rest_of_the_frame_is_not():
    own = asm(X86_64, "mov qword ptr [rsp + 8], 0\nxor eax, eax\nret\n")
    run_function(X86_64, own, list(range(7)))  # writing its own 7th-argument slot is allowed
    with pytest.raises(ABIViolation, match="caller's stack frame"):
        run_function(X86_64, asm(X86_64, "mov qword ptr [rsp + 16], 0\nret\n"), list(range(7)))
    with pytest.raises(ABIViolation, match="caller's stack frame"):
        run_function(X86_64, asm(X86_64, "mov qword ptr [rsp], 0\nret\n"), list(range(7)))
    run_function(AARCH64, asm(AARCH64, "str xzr, [sp]\nret\n"), list(range(9)))
    with pytest.raises(ABIViolation):
        run_function(AARCH64, asm(AARCH64, "str xzr, [sp, #8]\nret\n"), list(range(9)))


def test_sum8_reference_with_stack_args_on_both_isas():
    ref = compile_c_reference(ALL_BY_NAME["sum8"].c_ref, "sum8", 8)
    args = [(1, 2, 3, 4, 5, 6, 7, 8), (-1, 2**62, -(2**63), 5, 0, 1, 2**63 - 1, -9)]
    for isa, src in ((X86_64, X86["sum8"]), (AARCH64, A64["sum8"])):
        assert differential(isa, asm(isa, src), ref, args).ok


# -- emulator: guards, categories, alignment -----------------------------------------------------


def test_read_guard_and_window_lists():
    load = asm(X86_64, "mov rax, qword ptr [rdi]\nret\n")
    a = DATA_BASE + 0x1000
    data = (7).to_bytes(8, "little")
    assert run_function(X86_64, load, [a], data=data, data_offset=0x1000, read_guard=(0x1000, 0x1008)).ret == 7
    with pytest.raises(OutOfBoundsRead) as ei:
        run_function(X86_64, load, [a + 8], data=data, data_offset=0x1000, read_guard=(0x1000, 0x1008))
    assert ei.value.category == "oob_read" and ei.value.address == a + 8 and ei.value.pc == 0x100000
    # Two windows: both readable, the gap is not.
    run_function(X86_64, load, [a + 0x20], data_offset=0x1000, read_guard=[(0x1000, 0x1008), (0x1020, 0x1028)])
    with pytest.raises(OutOfBoundsRead):
        run_function(X86_64, load, [a + 0x10], read_guard=[(0x1000, 0x1008), (0x1020, 0x1028)])
    store = asm(AARCH64, "str xzr, [x0]\nret\n")
    run_function(AARCH64, store, [a + 0x20], guard=[(0x1000, 0x1008), (0x1020, 0x1028)])
    with pytest.raises(OutOfBoundsWrite) as ei:
        run_function(AARCH64, store, [a + 0x10], guard=[(0x1000, 0x1008), (0x1020, 0x1028)])
    assert ei.value.category == "oob_write" and ei.value.pc == 0x100000


def test_error_categories():
    assert EmulationError("x").category == "crash" and EmulationError("x").pc is None
    with pytest.raises(InstructionLimit) as ei:
        run_function(AARCH64, asm(AARCH64, "l: b l\n"), [], max_insns=1000)
    assert ei.value.category == "hang" and ei.value.pc == 0x100000
    assert "did not return within 1000" in str(ei.value)  # message kept for text consumers
    with pytest.raises(EmulationError) as ei:
        run_function(X86_64, asm(X86_64, "mov rax, qword ptr [rdi]\nret\n"), [0x10])
    assert ei.value.category == "crash" and ei.value.pc == 0x100000
    with pytest.raises(ABIViolation) as ei:
        run_function(X86_64, asm(X86_64, "xor ebx, ebx\nret\n"), [])
    assert ei.value.category == "abi"
    assert issubclass(MisalignedCall, ABIViolation) and MisalignedCall.category == "misaligned_call"


@pytest.mark.parametrize("isa", [X86_64, AARCH64])
def test_hostile_callback_semantics_and_clobbers(isa):
    cb = hostile_callback(isa)
    for x in (0, 5, -1, 2**63 - 1):
        assert run_function(isa, cb, [x]).ret == (3 * x + 1) & (2**64 - 1)  # ABI-clean itself
    # A caller that keeps a value in a caller-saved register across the call loses it.
    if isa is X86_64:
        src = "push rbx\nmov rcx, 7\ncall rdi\nmov rax, rcx\npop rbx\nret\n"
    else:
        src = ("stp x29, x30, [sp, #-16]!\nmov x9, x0\nmov x10, #7\nmov x0, #1\nblr x9\n"
               "mov x0, x10\nldp x29, x30, [sp], #16\nret\n")
    got = run_function(isa, asm(isa, src), [CALLBACK_BASE], segments=callback_segments(isa)).ret
    assert got != 7


def test_misaligned_call_trap_names_the_call_site():
    src = "call rdi\nret\n"  # rsp is 8 mod 16 at the call
    with pytest.raises(MisalignedCall) as ei:
        run_function(X86_64, asm(X86_64, src), [CALLBACK_BASE], segments=callback_segments(X86_64),
                     aligned_entries=[CALLBACK_BASE])
    e = ei.value
    assert e.category == "misaligned_call" and e.target == CALLBACK_BASE and e.return_address == 0x100002
    assert 0x100000 <= e.pc < 0x100002 and "must be 16-byte aligned at the call" in str(e)
    ok = "push rbx\ncall rdi\npop rbx\nret\n"
    assert run_function(X86_64, asm(X86_64, ok), [CALLBACK_BASE], segments=callback_segments(X86_64),
                        aligned_entries=[CALLBACK_BASE]).ret == 3 * CALLBACK_BASE + 1
    bad = "sub sp, sp, #8\nstr x30, [sp]\nmov x9, x0\nblr x9\nldr x30, [sp]\nadd sp, sp, #8\nret\n"
    with pytest.raises(MisalignedCall) as ei:
        run_function(AARCH64, asm(AARCH64, bad), [CALLBACK_BASE], segments=callback_segments(AARCH64),
                     aligned_entries=[CALLBACK_BASE], abi=False)
    assert ei.value.pc == 0x10000c  # the blr


def test_strict_sse_alignment():
    code = asm(X86_64, "movdqa xmm0, xmmword ptr [rdi]\nmovq rax, xmm0\nret\n")
    data = bytes(range(32))
    for off, bad in ((0, False), (4, True), (8, True)):
        args = [DATA_BASE + 0x1000 + off]
        run_function(X86_64, code, args, data=data, data_offset=0x1000)  # default: not checked
        if bad:
            with pytest.raises(MisalignedAccess) as ei:
                run_function(X86_64, code, args, data=data, data_offset=0x1000, strict_sse_alignment=True)
            assert ei.value.category == "misaligned_access" and "movdqa" in str(ei.value)
        else:
            run_function(X86_64, code, args, data=data, data_offset=0x1000, strict_sse_alignment=True)
    unaligned_ok = asm(X86_64, "movdqu xmm0, xmmword ptr [rdi]\npmovsxdq xmm1, qword ptr [rdi]\nmovq rax, xmm0\nret\n")
    run_function(X86_64, unaligned_ok, [DATA_BASE + 0x1004], strict_sse_alignment=True)


def test_pooled_machine_restores_writable_segments():
    seg = [(0x600000, (5).to_bytes(8, "little"), "rw")]
    code = asm(X86_64, "mov rax, qword ptr [rdi]\nmov qword ptr [rdi], 99\nret\n")
    for _ in range(3):
        assert run_function(X86_64, code, [0x600000], segments=seg).ret == 5


# -- difftest --------------------------------------------------------------------------------------


def test_mismatch_kinds_and_data():
    ref = compile_c_reference("int64_t f(int64_t x) { return x; }", "f", 1)
    rep = differential(X86_64, asm(X86_64, "xor ebx, ebx\nmov rax, rdi\nret\n"), ref, [(1,)])
    assert rep.mismatches[0].kind == "abi"
    rep = differential(X86_64, asm(X86_64, "lea rax, [rdi + 1]\nret\n"), ref, [(1,)])
    assert rep.mismatches[0].kind == "wrong_result" and rep.mismatches[0].error is None
    rep = differential(X86_64, asm(X86_64, "l: jmp l\n"), ref, [(1,)], max_insns=100)
    assert rep.mismatches[0].kind == "hang" and rep.mismatches[0].pc == 0x100000
    rev = ROUND1[5]
    rep = buffer_differential(AARCH64, asm(AARCH64, "mov x0, #0\nret\n"), rev.c_ref, rev.name, [[1, 2]])
    m = rep.mismatches[0]
    assert m.kind == "wrong_result" and m.error == "buffer differs"
    assert m.actual_data == (1).to_bytes(8, "little") + (2).to_bytes(8, "little") != m.expected_data
    over = asm(X86_64, "mov rax, qword ptr [rdi + rsi*8]\nret\n")  # reads a[n]
    rep = buffer_differential(X86_64, over, "int64_t g(int64_t *a, int64_t n) { return 0; }", "g", [[1]],
                              read_guard=True)
    assert rep.mismatches[0].kind == "oob_read"


def test_memory_differential_pointers_windows_and_stack_args():
    src = """int64_t f(int64_t *dst, const int64_t *src, int64_t a, int64_t b, int64_t c, int64_t d,
                     int64_t e) { dst[0] = src[0] + e; return a + b + c + d; }"""
    ref = compile_c_reference(src, "f", 7)
    mem = (5).to_bytes(8, "little") + bytes(8)
    case = MemCase(mem, (Ptr(8), Ptr(0), 1, 2, 3, 4, 100), ((8, 16),), ((0, 8),), "f(...)")
    good = asm(X86_64, "mov rax, qword ptr [rsi]\nadd rax, qword ptr [rsp + 8]\nmov qword ptr [rdi], rax\n"
                       "lea rax, [rdx + rcx]\nadd rax, r8\nadd rax, r9\nret\n")
    assert memory_differential(X86_64, good, ref, [case]).ok
    case2 = MemCase(mem, case.args, ((8, 16),), ((8, 16),))  # src no longer readable
    assert memory_differential(X86_64, good, ref, [case2]).mismatches[0].kind == "oob_read"
    wrong = asm(X86_64, "mov qword ptr [rdi], 1\nxor eax, eax\nret\n")
    m = memory_differential(X86_64, wrong, ref, [case]).mismatches[0]
    assert m.kind == "wrong_result" and m.error == "memory differs" and m.case is case
    assert m.expected_data[8:] == (105).to_bytes(8, "little") and m.actual_data[8:] == (1).to_bytes(8, "little")
    assert BUFFER_OFFSET == 0x1000


# -- encoders ----------------------------------------------------------------------------------------


def test_structured_x86_indirect_call_and_jump():
    prog = structured_assemble("x86_64", [{"op": "call", "target": "r12"}, {"op": "jmp", "target": "rax"},
                                          {"op": "ret"}])
    assert prog.code == bytes.fromhex("41ffd4ffe0c3")
    with pytest.raises(Exception, match="only jmp/call can be indirect"):
        structured_assemble("x86_64", [{"op": "jne", "target": "rax"}])
    with pytest.raises(Exception, match="64-bit register"):
        structured_assemble("x86_64", [{"op": "call", "target": "eax"}])


@pytest.mark.parametrize("text,want", [
    ("lea rax, [rdi + rdi*2 + 1]", 31), ("lea rax, [rdi + rdi]", 20), ("lea rax, [rdi*2 + rdi]", 30),
    ("lea rax, [rdi*2]", 20), ("lea rax, [rdi + rdi*8 - 3]", 87),
])
def test_stream_same_register_base_and_index(text, want):
    # These used to be rejected ("invalid address expression") or cross-check mismatches.
    code = asm(X86_64, text + "\nret\n")
    assert run_function(X86_64, code, [10]).ret == want


# -- tasks and references -------------------------------------------------------------------------


def test_task_sets():
    assert [t.name for t in select_tasks("round1")] == [t.name for t in ROUND1]
    assert len(select_tasks("round2")) == 11 and select_tasks("fib_rec,gcd")[1].name == "gcd"
    assert len(select_tasks("all")) == 19
    with pytest.raises(ValueError, match="unknown task"):
        select_tasks("nope")
    assert len(CELLS) == 62  # 10 tasks x 6 cells + dot_i32_simd on the stream path only
    assert ALL_BY_NAME["dot_i32_simd"].na_paths.keys() == {"structured", "ir"}


def _mem_ref(task):
    return compile_c_reference(task.c_ref, task.reference_name, task.nargs)


def test_references_against_python_models():
    import ctypes

    crc = ALL_BY_NAME["crc32"]
    ref = _mem_ref(crc)
    for case in crc.inputs:
        off, n = case.args[0].offset, case.args[1]
        buf = ctypes.create_string_buffer(case.mem, len(case.mem))
        assert ref(ctypes.addressof(buf) + off, n) == zlib.crc32(case.mem[off : off + n])
    isq = ALL_BY_NAME["isqrt"]
    ref = compile_c_reference(isq.c_ref, "isqrt", 1)
    for (v,) in isq.inputs:
        assert ref(v) & (2**64 - 1) == math.isqrt(v & (2**64 - 1))
    mh = compile_c_reference(ALL_BY_NAME["mulhi_u64"].c_ref, "mulhi_u64", 2)
    for a, b in ALL_BY_NAME["mulhi_u64"].inputs:
        assert mh(a, b) & (2**64 - 1) == ((a % 2**64) * (b % 2**64)) >> 64
    fib = compile_c_reference(ALL_BY_NAME["fib_rec"].c_ref, "fib_rec", 1)
    assert [fib(n) for n in range(8)] == [0, 1, 1, 2, 3, 5, 8, 13] and fib(20) == 6765

    def steps(k):
        s = 0
        while k != 1:
            k, s = (3 * k + 1 if k & 1 else k >> 1), s + 1
        return s

    col = compile_c_reference(ALL_BY_NAME["collatz_max"].c_ref, "collatz_max", 1)
    for n in (1, 6, 27, 97, 3000):
        assert col(n) == max(steps(k) for k in range(1, n + 1))


@pytest.mark.parametrize("task", ROUND2, ids=lambda t: t.name)
@pytest.mark.parametrize("isa", ISA_NAMES)
def test_c_reference_lowered_passes_its_own_check(task, isa):
    """Checker sanity: the C reference, compiled for the target, passes every case."""
    code = lower(task.c_ref, "c", isa, task.name)
    v = check(task, isa, code)
    assert v.ok, v.message


# -- every feasible cell is solvable through its pipeline ------------------------------------------


@pytest.mark.parametrize("task,path,isa", CELLS, ids=lambda x: getattr(x, "name", x))
def test_canned_correct_solution_passes(task, path, isa):
    text = correct(path, isa, task.name)
    a = attempt(FakeSubject([chunks(text)]), path, isa, task, build_prompt(path, isa, task))
    assert a.ok and a.category is None, a.failure


def test_alternative_4wide_sse_dot_product_passes():
    a = attempt(FakeSubject([X86_DOT4]), "stream", "x86_64", ALL_BY_NAME["dot_i32_simd"], "p")
    assert a.ok, a.failure


# -- every safety-net category is triggered by a canned wrong answer ------------------------------


@pytest.mark.parametrize("task,path,isa,text,category,fragment", WRONG,
                         ids=[f"{w[0]}-{w[1]}-{w[2]}-{w[4]}" for w in WRONG])
def test_canned_wrong_solution_is_caught(task, path, isa, text, category, fragment):
    if path == "structured":
        text = json.dumps(to_structured(isa, text))
    subj = FakeSubject([chunks(text)])
    a = attempt(subj, path, isa, ALL_BY_NAME[task], "p")
    assert not a.ok and a.category == category, (a.category, a.failure)
    assert fragment in a.failure, a.failure
    if category in ("syntax", "crosscheck"):  # streaming early abort: stopped mid-output
        assert subj.stopped_at[0] is not None and subj.stopped_at[0] < len(chunks(text)) - 1
        assert a.response.killed and "Generation was stopped" in a.failure


def test_all_safety_net_categories_are_covered():
    assert {w[4] for w in WRONG} >= {"misaligned_call", "abi", "oob_read", "oob", "requirement", "hang",
                                     "crosscheck", "syntax", "crash", "wrong_result"}


def test_fault_source_line_in_feedback_for_each_path():
    t = ALL_BY_NAME["binary_search"]
    bad_asm = X86["binary_search"].replace("lea rcx, [rsi - 1]", "mov rcx, rsi")
    a = attempt(FakeSubject([bad_asm]), "stream", "x86_64", t, "p")
    assert "[the faulting instruction is line 11: `mov r9, qword ptr [rdi + r8*8]`]" in a.failure
    a = attempt(FakeSubject([json.dumps(to_structured("x86_64", bad_asm))]), "structured", "x86_64", t, "p")
    assert a.category == "oob_read" and '[the faulting instruction is instruction 10: {"op":"mov","dst":"r9"' in a.failure
    bad_qbe = QBE["binary_search"].replace("%hi =l sub %n, 1", "%hi =l copy %n")
    a = attempt(FakeSubject([bad_qbe]), "ir", "aarch64", t, "p")
    assert a.category == "oob_read" and "(disassembly of the compiled code)" in a.failure and "`ldr" in a.failure


def test_classification_uses_kind_not_text():
    from bca.difftest import Mismatch

    m = Mismatch((1,), 1, None, "some text mentioning out-of-bounds write", kind="hang")
    assert classify_mismatch(m) == "hang"
    assert classify_mismatch(Mismatch((1,), 1, None, "x: out-of-bounds read at 0x1")) == "oob_read"
    assert classify_mismatch(Mismatch((1,), 1, 2)) == "wrong_result"
    assert classify_mismatch(Mismatch((1,), 1, None, "boom", kind="misaligned_access")) == "crash"


def test_requirement_helpers_on_compiled_code():
    fib_qbe = lower(QBE["fib_rec"], "qbe", "x86_64", "fib_rec")  # starts with endbr64
    assert calls_own_entry("x86_64", fib_qbe)
    assert calls_own_entry("aarch64", lower(QBE["fib_rec"], "qbe", "aarch64", "fib_rec"))
    assert not calls_own_entry("x86_64", asm(X86_64, "call l\nret\nl: ret\n"))
    assert uses_simd("x86_64", asm(X86_64, X86["dot_i32_simd"]))
    assert not uses_simd("x86_64", asm(X86_64, "pxor xmm0, xmm0\nmovq rax, xmm0\nret\n"))
    assert uses_simd("aarch64", asm(AARCH64, A64["dot_i32_simd"]))
    assert requirement_failure(ALL_BY_NAME["isqrt"], "x86_64", b"\xc3") is None


# -- prompts -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("isa", ISA_NAMES)
def test_round2_prompts(path, isa):
    for task in ROUND2:
        if path in task.na_paths:
            with pytest.raises(ValueError, match="not applicable"):
                build_prompt(path, isa, task)
            continue
        p = build_prompt(path, isa, task)
        assert task.signature in p and task.spec in p and ABI_EXTRA[isa] in p
        if task.calls and path == "stream":
            assert "no calls to other functions" not in p
        if task.calls == "callback" and path == "ir":
            assert "call %f(l %x)" in p and "no calls" not in p
    for task in ROUND1:  # round-1 prompts are unchanged
        assert ABI_EXTRA[isa] not in build_prompt(path, isa, task)


# -- runner / summary / CLI ----------------------------------------------------------------------------


def test_repair_round_carries_the_call_site(tmp_path):
    t = ALL_BY_NAME["map_sum"]
    wrong = next(w[3] for w in WRONG if w[0] == "map_sum" and w[4] == "misaligned_call" and w[2] == "x86_64")
    subj = FakeSubject([chunks(wrong), chunks(X86["map_sum"])])
    rec = run_cell(subj, t, "stream", "x86_64", tmp_path)
    assert rec["categories"] == ["misaligned_call", None] and rec["pass_final"]
    assert "misaligned call to 0x600000" in subj.prompts[1] and "`call r13`" in subj.prompts[1]


def test_matrix_skips_na_cells_and_summary_marks_them(tmp_path):
    t = ALL_BY_NAME["dot_i32_simd"]
    subj = FakeSubject([X86["dot_i32_simd"], A64["dot_i32_simd"]])
    recs = run_matrix(subj, [t], PATHS, ISA_NAMES, tmp_path, jobs=1, progress=lambda m: None)
    assert [r["cell"] for r in recs] == ["dot_i32_simd.stream.x86_64", "dot_i32_simd.stream.aarch64"]
    assert all(r["pass_at_1"] for r in recs) and subj.calls == 2
    out = summarize(tmp_path)
    assert "| dot_i32_simd | 1 / ✓ / 1 | 1 / ✓ / 1 | N/A | N/A | N/A | N/A |" in out


def test_cli_dry_run(capsys):
    assert cli_main(["run", "--run-dir", "/nonexistent/never-created", "--tasks", "round2", "--max-calls", "248",
                     "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "62 cells; at most 248 calls" in out and "dot_i32_simd.ir" not in out


def test_isas_registry():
    assert set(ISAS) == set(ISA_NAMES)
