"""Battery "long" (bca.liveeval.tasks_long) with a scripted FakeSubject: no model calls.

Checks the task definitions (references against independent Python models, input coverage),
that every feasible cell is solvable through its real pipeline with a hand-written answer, that
those answers are long, that canned wrong answers land in the intended failure categories
(including a stream early abort ~100 lines into an answer), and that the prompts read well.
"""

import base64
import ctypes
import json

import pytest

from bca.difftest import Ptr, compile_c_reference
from bca.ir import lower
from bca.isa import ISAS
from bca.liveeval import FakeSubject, attempt, build_prompt, check, run_cell
from bca.liveeval.pipelines import _insns
from bca.liveeval.prompts import ABI_EXTRA
from bca.liveeval.tasks2 import ALL_BY_NAME, feasible_cells
from bca.liveeval.tasks_long import (
    BY_NAME,
    TASKS,
    lcs_model,
    mat_pow_model,
    packbits_decode_model,
    packbits_encode_model,
    vm_model,
)
from bca.stream import StreamAssembler
from tests.liveeval_solutions_long import (
    A64,
    QBE,
    WRONG,
    X86,
    XXH64_A64_ROL,
    XXH64_X86_IMM64,
    correct,
    to_structured,
)

PATHS = ("stream", "structured", "ir")
ISA_NAMES = ("x86_64", "aarch64")
CELLS = feasible_cells(TASKS, PATHS, ISA_NAMES)
M64 = (1 << 64) - 1


def chunks(text, n=9):
    return [text[i : i + n] for i in range(0, len(text), n)]


def stream_insn_count(isa, src):
    sa = StreamAssembler(ISAS[isa])
    sa.feed(src)
    prog = sa.finish()
    assert prog.ok, prog.errors
    return len(prog.instructions)


# -- battery shape ----------------------------------------------------------------------------------


def test_battery_shape():
    names = [t.name for t in TASKS]
    assert len(names) == len(set(names)) == 9
    assert not set(names) & set(ALL_BY_NAME)  # no clash with rounds 1-2
    assert all(t.mem and t.round == 3 and not t.na for t in TASKS)
    assert len(CELLS) == 54  # every (task, path, ISA) cell is feasible
    assert BY_NAME["qsort_pairs"].requires == ("recursion",) and BY_NAME["qsort_pairs"].calls == "self"
    assert all(not t.calls for t in TASKS if t.name != "qsort_pairs")
    for t in TASKS:
        assert 30 <= len(t.inputs) <= 90, t.name
        for c in t.inputs:
            assert c.label.startswith(t.name + "(") and c.readable is not None  # read guard everywhere
            for lo, hi in c.writable:
                assert 0 <= lo <= hi <= len(c.mem)


# -- references against independent Python models ----------------------------------------------------


def run_ref(task, case):
    """Host C reference on a copy of the case memory: (return value, memory after)."""
    ref = compile_c_reference(task.c_ref, task.reference_name, task.nargs)
    buf = ctypes.create_string_buffer(case.mem, max(len(case.mem), 1))
    base = ctypes.addressof(buf)
    ret = ref(*[base + a.offset if isinstance(a, Ptr) else a for a in case.args])
    return ret, buf.raw[: len(case.mem)]


def xxh64_py(data: bytes, seed: int) -> int:
    p1, p2, p3, p4, p5 = (0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9, 0x85EBCA77C2B2AE63,
                          0x27D4EB2F165667C5)

    def rotl(x, r):
        return ((x << r) | (x >> (64 - r))) & M64

    def rnd(acc, x):
        return rotl((acc + x * p2) & M64, 31) * p1 & M64

    seed &= M64
    n, q = len(data), 0
    rd = lambda i, k: int.from_bytes(data[i : i + k], "little")  # noqa: E731
    if n >= 32:
        v = [(seed + p1 + p2) & M64, (seed + p2) & M64, seed, (seed - p1) & M64]
        while n - q >= 32:
            v = [rnd(v[k], rd(q + 8 * k, 8)) for k in range(4)]
            q += 32
        h = (rotl(v[0], 1) + rotl(v[1], 7) + rotl(v[2], 12) + rotl(v[3], 18)) & M64
        for x in v:
            h = ((h ^ rnd(0, x)) * p1 + p4) & M64
    else:
        h = (seed + p5) & M64
    h = (h + n) & M64
    while n - q >= 8:
        h = (rotl(h ^ rnd(0, rd(q, 8)), 27) * p1 + p4) & M64
        q += 8
    if n - q >= 4:
        h = (rotl(h ^ (rd(q, 4) * p1 & M64), 23) * p2 + p3) & M64
        q += 4
    while q < n:
        h = rotl(h ^ (data[q] * p5 & M64), 11) * p1 & M64
        q += 1
    h ^= h >> 33
    h = h * p2 & M64
    h ^= h >> 29
    h = h * p3 & M64
    return h ^ (h >> 32)


def _limbs(mem, off, k):
    return sum(int.from_bytes(mem[off + 8 * i : off + 8 * i + 8], "little") << (64 * i) for i in range(k))


def _words(mem, off, k):
    return [int.from_bytes(mem[off + 8 * i : off + 8 * i + 8], "little", signed=True) for i in range(k)]


def test_xxh64_known_vectors():
    assert xxh64_py(b"", 0) == 0xEF46DB3751D8E999
    assert xxh64_py(b"abc", 0) == 0x44BC2CF5AD770999
    assert xxh64_py(b"a", 0) == 0xD24EC4F1A98C6E5B


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
def test_reference_matches_python_model(task):
    for case in task.inputs:
        ret, after = run_ref(task, case)
        ret &= M64
        a = case.args
        # nothing outside the writable windows changes
        for i in range(len(case.mem)):
            if not any(lo <= i < hi for lo, hi in case.writable):
                assert after[i] == case.mem[i], (case.label, i)
        if task.name == "vm_run":
            code = _words(case.mem, a[0].offset, a[1])
            res, stack, _ = vm_model(code, a[3])
            assert ret == res & M64, case.label
            assert _words(after, a[2].offset, a[3]) == stack + [0] * (a[3] - len(stack)), case.label
        elif task.name == "qsort_pairs":
            w = _words(case.mem, a[0].offset, 2 * a[1])
            recs = sorted(zip(w[::2], w[1::2]))
            assert ret == 0 and _words(after, a[0].offset, 2 * a[1]) == [x for r in recs for x in r], case.label
        elif task.name == "base64_encode":
            src = case.mem[a[0].offset : a[0].offset + a[1]]
            enc = base64.b64encode(src)
            assert ret == len(enc) and after[a[2].offset : a[2].offset + len(enc)] == enc, case.label
        elif task.name == "xxh64":
            assert ret == xxh64_py(case.mem[a[0].offset : a[0].offset + a[1]], a[2]), case.label
        elif task.name in ("packbits_encode", "packbits_decode"):
            model = packbits_encode_model if task.name == "packbits_encode" else packbits_decode_model
            r, out = model(case.mem[a[0].offset : a[0].offset + a[1]], a[3])
            assert ret == r & M64 and after[a[2].offset : a[2].offset + len(out)] == out, case.label
            if task.name == "packbits_encode" and r >= 0:  # round trip
                assert packbits_decode_model(out, 1 << 20) == (a[1], case.mem[a[0].offset : a[0].offset + a[1]])
        elif task.name == "mat_pow":
            n = a[1]
            want = mat_pow_model(_words(case.mem, a[0].offset, n * n), n, a[2])
            assert ret == 0 and _words(after, a[3].offset, n * n) == want, case.label
            assert after[a[4].offset : a[4].offset + 16 * n * n] == bytes(16 * n * n), case.label
        elif task.name == "lcs_len":
            x = case.mem[a[0].offset : a[0].offset + a[1]]
            y = case.mem[a[2].offset : a[2].offset + a[3]]
            res, row = lcs_model(x, y)
            assert ret == res and _words(after, a[4].offset, a[3] + 1) == row, case.label
        elif task.name == "mp_mul":
            na, nb = a[1], a[3]
            prod = _limbs(case.mem, a[0].offset, na) * _limbs(case.mem, a[2].offset, nb)
            assert _limbs(after, a[4].offset, na + nb) == prod, case.label
            assert ret == (prod.bit_length() + 63) // 64, case.label
        else:  # pragma: no cover
            raise AssertionError(task.name)


def test_inputs_cover_the_interesting_cases():
    # vm_run: every opcode is executed, loops run, programs stop by HALT and by running off the end.
    seen, steps = set(), []
    for case in BY_NAME["vm_run"].inputs:
        code = _words(case.mem, case.args[0].offset, case.args[1])
        steps.append(vm_model(code, case.args[3], trace=seen)[2])
    assert seen == set(range(12)) | {"end"} and max(steps) > 500
    vm_cases = BY_NAME["vm_run"].inputs
    assert {c.args[3] for c in vm_cases} >= {1, 64} and min(c.args[1] for c in vm_cases) == 0
    # packbits: every outcome, including truncated input (-2) and a full output buffer (-1).
    outcomes = {name: set() for name in ("packbits_encode", "packbits_decode")}
    for name, model in (("packbits_encode", packbits_encode_model), ("packbits_decode", packbits_decode_model)):
        for c in BY_NAME[name].inputs:
            r = model(c.mem[c.args[0].offset : c.args[0].offset + c.args[1]], c.args[3])[0]
            outcomes[name].add(min(r, 0) if r < 0 else "ok")
    assert outcomes == {"packbits_encode": {"ok", -1}, "packbits_decode": {"ok", -1, -2}}
    # xxh64: every tail length with and without the 32-byte stripe loop, unaligned data.
    lens = {c.args[1] for c in BY_NAME["xxh64"].inputs}
    assert set(range(41)) <= lens and max(lens) == 300
    assert {c.args[0].offset % 8 for c in BY_NAME["xxh64"].inputs} == set(range(8))
    # mp_mul: products with every limb count, including zero and full carries.
    assert {(c.args[1], c.args[3]) for c in BY_NAME["mp_mul"].inputs} >= {(0, 0), (8, 8), (1, 8), (8, 1)}


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
@pytest.mark.parametrize("isa", ISA_NAMES)
def test_c_reference_lowered_passes_its_own_check(task, isa):
    """Checker sanity: the C reference, compiled for the target, passes every case."""
    code = lower(task.c_ref, "c", isa, task.name)
    v = check(task, isa, code)
    assert v.ok, v.message


# -- every feasible cell is solvable through its pipeline -------------------------------------------


@pytest.mark.parametrize("task,path,isa", CELLS, ids=lambda x: getattr(x, "name", x))
def test_canned_correct_solution_passes(task, path, isa):
    text = correct(path, isa, task.name)
    a = attempt(FakeSubject([chunks(text)]), path, isa, task, build_prompt(path, isa, task))
    assert a.ok and a.category is None, a.failure


def test_canned_solutions_are_long():
    """Static instruction counts of the hand-written answers (terse, expert-written: a model's
    answers are usually longer)."""
    counts = {}
    for t in TASKS:
        x86 = stream_insn_count("x86_64", X86[t.name])
        a64 = stream_insn_count("aarch64", A64[t.name])
        qbe = [len(_insns(isa, lower(QBE[t.name], "qbe", isa, t.name))) for isa in ISA_NAMES]
        counts[t.name] = (x86, a64, *qbe)
        assert min(x86, a64) >= 30 and min(qbe) >= 45, (t.name, counts[t.name])
    assert counts["xxh64"][0] >= 120 and counts["vm_run"][0] >= 80 and counts["packbits_encode"][0] >= 65
    stream = sorted(c for v in counts.values() for c in v[:2])
    assert stream[len(stream) // 2] >= 50  # median stream answer


# -- every safety-net category is triggered by a canned wrong answer ----------------------------------


@pytest.mark.parametrize("task,path,isa,text,category,fragment", WRONG,
                         ids=[f"{w[0]}-{w[1]}-{w[2]}-{w[4]}" for w in WRONG])
def test_canned_wrong_solution_is_caught(task, path, isa, text, category, fragment):
    if path == "structured":
        text = json.dumps(to_structured(isa, text))
    subj = FakeSubject([chunks(text)])
    a = attempt(subj, path, isa, BY_NAME[task], "p")
    assert not a.ok and a.category == category, (a.category, a.failure)
    assert fragment in a.failure, a.failure
    if category in ("syntax", "crosscheck"):  # streaming early abort: stopped mid-output
        assert subj.stopped_at[0] is not None and subj.stopped_at[0] < len(chunks(text)) - 1
        assert a.response.killed and "Generation was stopped" in a.failure


def test_wrong_answers_cover_the_categories():
    assert {w[4] for w in WRONG} >= {"syntax", "oob", "oob_read", "abi", "misaligned_call", "requirement",
                                     "hang", "wrong_result"}
    assert {w[1] for w in WRONG} == set(PATHS)


@pytest.mark.parametrize("isa,text,line", [("x86_64", XXH64_X86_IMM64, 99), ("aarch64", XXH64_A64_ROL, 103)])
def test_early_abort_deep_in_a_long_answer(isa, text, line):
    """The mistake sits ~100 lines into a ~120-line answer: the stream stops there, the
    instructions before it are already encoded, and the rest of the output is never generated."""
    subj = FakeSubject([chunks(text)])
    a = attempt(subj, "stream", isa, BY_NAME["xxh64"], "p")
    ab = a.stream["abort"]
    assert a.category == "syntax" and ab["line"] == line and ab["insns_committed"] >= 90
    assert f"line {line}" in a.failure and "instructions before it had been accepted" in a.failure
    saved = 1 - ab["chars_received"] / len(text)
    assert 0.1 < saved < 0.4  # the tail that was never generated


def test_repair_round_on_a_long_answer(tmp_path):
    subj = FakeSubject([chunks(XXH64_X86_IMM64), chunks(X86["xxh64"])])
    rec = run_cell(subj, BY_NAME["xxh64"], "stream", "x86_64", tmp_path)
    assert rec["categories"] == ["syntax", None] and rec["pass_final"]
    assert "line 99" in subj.prompts[1] and "operand type mismatch" in subj.prompts[1]


# -- known harness gap: keystone rejects r8b-r15b (REQUESTS.md) --------------------------------------


def test_stream_accepts_r8b_to_r15b():
    src = X86["packbits_decode"].replace("movzx eax, byte ptr [rdi + r11]\n    mov byte ptr [rdx + r9], al",
                                         "movzx r10d, byte ptr [rdi + r11]\n    mov byte ptr [rdx + r9], r10b")
    assert "r10b" in src
    a = attempt(FakeSubject([src]), "stream", "x86_64", BY_NAME["packbits_decode"], "p")
    assert a.ok, a.failure


def test_structured_accepts_r8b_to_r15b():
    """The same answer passes on the structured path, so the stream rejection is a harness gap."""
    src = X86["packbits_decode"].replace("movzx eax, byte ptr [rdi + r11]\n    mov byte ptr [rdx + r9], al",
                                         "movzx r10d, byte ptr [rdi + r11]\n    mov byte ptr [rdx + r9], r10b")
    a = attempt(FakeSubject([json.dumps(to_structured("x86_64", src))]), "structured", "x86_64",
                BY_NAME["packbits_decode"], "p")
    assert a.ok, a.failure


# -- prompts ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("isa", ISA_NAMES)
def test_long_prompts(path, isa):
    for task in TASKS:
        p = build_prompt(path, isa, task)
        assert task.signature in p and task.spec in p and ABI_EXTRA[isa] in p
        assert "{name}" not in p and "{" + "name" not in p
        if path == "ir" and task.ir_hint:
            assert task.ir_hint in p
        if task.calls == "self":
            assert "no calls to other functions" not in p and "No data definitions, no calls" not in p
            if path == "stream":
                assert ("call qsort_pairs" if isa == "x86_64" else "bl qsort_pairs") in p
            if path == "ir":
                assert "$qsort_pairs" in p
        else:
            assert "no calls" in p.lower() or path == "structured"
        assert len(p) < (20_000 if path == "structured" else 6_000), (task.name, len(p))
