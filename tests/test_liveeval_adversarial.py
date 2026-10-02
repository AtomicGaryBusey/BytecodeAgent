"""Adversarial battery (bca.liveeval.adversarial): pitfall-bait tasks and prompt variants, run
through the real pipelines with a scripted FakeSubject (no model calls)."""

import json

import pytest

from bca.difftest import INT64_MAX, INT64_MIN, compile_c_reference
from bca.emu import CALLBACK_BASE, callback_address, callback_model
from bca.ir import lower
from bca.liveeval import FakeSubject, attempt, build_prompt, check
from bca.liveeval.adversarial import (
    ALL_VARIANTS_DEFAULT,
    BAITS,
    BY_NAME,
    MISLEADING_HINTS,
    RECOMMENDED_VARIANTS,
    TASKS,
    VARIANT_INFO,
    VARIANTS,
    feasible_cells,
    misleading_hint_for,
)
from bca.liveeval.prompts import ABI, ABI_EXTRA, rules
from bca.liveeval.tasks import Task
from tests.liveeval_solutions import to_structured
from tests.liveeval_solutions_adversarial import BAITED, CONTROLS, correct

PATHS = ("stream", "structured", "ir")
ISA_NAMES = ("x86_64", "aarch64")
CELLS = feasible_cells(PATHS, ISA_NAMES)
U64 = 1 << 64


def chunks(text, n=9):
    return [text[i : i + n] for i in range(0, len(text), n)]


def run(task, path, isa, text):
    if path == "structured" and not text.lstrip().startswith("{"):
        text = json.dumps(to_structured(isa, text))
    subj = FakeSubject([chunks(text)])
    return attempt(subj, path, isa, BY_NAME[task], build_prompt(path, isa, BY_NAME[task]))


# -- task data -------------------------------------------------------------------------------------


def test_task_set_shape():
    assert len(TASKS) == 10 and len({t.name for t in TASKS}) == 10
    assert set(BAITS) == set(BY_NAME)
    for t in TASKS:
        assert isinstance(t, Task) and t.round >= 2 and t.inputs and not t.na
    assert {t.name for t in TASKS if not t.mem} == {"madd32", "shift_sat"}  # scalar; the rest use MemCase
    assert len(CELLS) == 60  # every cell is feasible on every path
    assert {t.name for t in TASKS if t.calls == "callback"} == {"map_into", "stats_f"}


def _s(v):
    v %= U64
    return v - U64 if v >> 63 else v


def test_references_against_python_models():
    import ctypes

    def mem_ref(task):
        return compile_c_reference(task.c_ref, task.reference_name, task.nargs)

    def call(task, case):
        buf = ctypes.create_string_buffer(case.mem, max(len(case.mem), 1))
        base = ctypes.addressof(buf)
        args = [base + a.offset if hasattr(a, "offset") else a for a in case.args]
        return mem_ref(task)(*args), buf.raw[: len(case.mem)]

    def words(b, off, n):
        return [int.from_bytes(b[off + 8 * i : off + 8 * i + 8], "little") for i in range(n)]

    t = BY_NAME["count_ugt"]
    for case in t.inputs:
        off, n, th = case.args[0].offset, case.args[1], case.args[2] % U64
        assert call(t, case)[0] == sum(v > th for v in words(case.mem, off, n))
    t = BY_NAME["max_byte"]
    for case in t.inputs:
        off, n = case.args[0].offset, case.args[1]
        assert call(t, case)[0] == max(case.mem[off : off + n], default=0)
    madd = compile_c_reference(BY_NAME["madd32"].c_ref, "madd32", 3)
    for a, b, c in BY_NAME["madd32"].inputs:
        r = ((a % 2**32) * (b % 2**32) + c % 2**32) % 2**32
        assert madd(a, b, c) == (r - 2**32 if r >> 31 else r)
    t = BY_NAME["sdivrem"]
    for case in t.inputs:
        a, b = case.args[0], case.args[1]
        if b == 0:
            q, r = 0, a
        elif b == -1:
            q, r = _s(-a), 0
        else:
            q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
            r = a - q * b
        got, mem = call(t, case)
        assert (got, _s(words(mem, 8, 1)[0])) == (q, r), (a, b)
    sh = compile_c_reference(BY_NAME["shift_sat"].c_ref, "shift_sat", 2)
    for x, n in BY_NAME["shift_sat"].inputs:
        left = (x << n) % U64 if n < 64 else 0
        right = x >> min(n, 63)
        assert sh(x, n) == _s(left + right)
    for name, model in (("bmin_empty", lambda xs: min(xs, default=INT64_MAX)),
                        ("count_desc", lambda xs: sum(p > q for p, q in zip(xs, xs[1:])))):
        t = BY_NAME[name]
        for case in t.inputs:
            xs = [_s(v) for v in words(case.mem, case.args[0].offset, case.args[1])]
            assert call(t, case)[0] == model(xs)
    t = BY_NAME["fill_down"]
    for case in t.inputs:
        off, n, start = case.args[0].offset, case.args[1], case.args[2]
        got, mem = call(t, case)
        assert got == n and words(mem, off, n) == [(start - i) % U64 for i in range(n)]
    fix3 = callback_model("fix3")  # map_into's callback: x if x % 3 == 0 else 3x + 1
    t = BY_NAME["map_into"]
    for case in t.inputs:
        d, s, n = case.args[0].offset, case.args[1].offset, case.args[2]
        assert case.args[3] == callback_address("fix3")
        src = [_s(v) for v in words(case.mem, s, n)]
        got, mem = call(t, case)
        assert words(mem, d, n) == [fix3(x) % U64 for x in src] and got == sum(fix3(x) < x for x in src)
    f = callback_model("3x+1")  # stats_f keeps the default callback f(x) = 3x + 1
    t = BY_NAME["stats_f"]
    for case in t.inputs:
        a, n, o = case.args[0].offset, case.args[1], case.args[3].offset
        ys = [f(_s(v)) for v in words(case.mem, a, n)]
        got, mem = call(t, case)
        assert [_s(v) for v in words(mem, o, 3)] == [min(ys), max(ys), _s(sum(ys))]
        assert got == sum(y & 1 for y in ys)


def test_inputs_cover_the_edges_each_bait_needs():
    ugt = BY_NAME["count_ugt"].inputs
    assert any(c.args[1] == 0 for c in ugt) and any(c.args[2] % U64 >= 1 << 63 for c in ugt)
    assert {(INT64_MIN, -1), (7, 0), (0, 0)} <= {c.args[:2] for c in BY_NAME["sdivrem"].inputs}
    assert {n for _, n in BY_NAME["shift_sat"].inputs} >= {63, 64, 65, 128, 200}
    for name in ("bmin_empty", "count_desc", "max_byte", "map_into"):
        assert any(c.args[1 if name != "map_into" else 2] == 0 for c in BY_NAME[name].inputs), name
    assert any(c.args[1] == 0 for c in BY_NAME["fill_down"].inputs)
    assert any(c.args[1] == 0 and c.readable == () for c in BY_NAME["fill_down"].inputs)


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
@pytest.mark.parametrize("isa", ISA_NAMES)
def test_c_reference_lowered_passes_its_own_check(task, isa):
    v = check(task, isa, lower(task.c_ref, "c", isa, task.name))
    assert v.ok, v.message


# -- every feasible cell is solvable through its pipeline ------------------------------------------


@pytest.mark.parametrize("task,path,isa", CELLS, ids=lambda x: getattr(x, "name", x))
def test_canned_correct_solution_passes(task, path, isa):
    a = run(task.name, path, isa, correct(path, isa, task.name))
    assert a.ok and a.category is None, a.failure


# -- each bait's "fell for it" answer lands in the intended category --------------------------------


@pytest.mark.parametrize("task,path,isa,text,category,fragment,what", BAITED,
                         ids=[f"{b[0]}-{b[1]}-{b[2]}-{b[4]}" for b in BAITED])
def test_baited_solution_is_caught(task, path, isa, text, category, fragment, what):
    a = run(task, path, isa, text)
    assert not a.ok and a.category == category, (what, a.category, a.failure)
    assert fragment in a.failure, a.failure
    assert category in BAITS[task].caught_as


def test_every_bait_has_a_caught_example_per_category():
    seen = {(b[0], b[4]) for b in BAITED}
    for name, bait in BAITS.items():
        for cat in bait.caught_as:
            assert (name, cat) in seen, (name, cat)
    assert {b[1] for b in BAITED} == set(PATHS) and {b[2] for b in BAITED} == set(ISA_NAMES)


@pytest.mark.parametrize("task,path,isa,text,what", CONTROLS, ids=[c[4] for c in CONTROLS])
def test_suspicious_but_correct_controls_pass(task, path, isa, text, what):
    a = run(task, path, isa, text)
    assert a.ok, (what, a.failure)


# -- prompts and variants ---------------------------------------------------------------------------


def test_prompts_carry_the_round2_abi_facts_and_callback_wording():
    for task in TASKS:
        for isa in ISA_NAMES:
            for path in PATHS:
                p = build_prompt(path, isa, task)
                assert ABI_EXTRA[isa] in p and task.spec in p
                if task.calls == "callback" and path == "stream":
                    assert "no calls to other functions" not in p


def test_variant_registry():
    assert set(VARIANTS) == set(VARIANT_INFO) >= {"baseline", "terse", "rushed", "misleading_hint", "distractor",
                                                  "legacy_style"}
    assert set(RECOMMENDED_VARIANTS) <= set(VARIANTS) and RECOMMENDED_VARIANTS[0] == "baseline"
    assert [k for k, v in VARIANT_INFO.items() if v["misleading"]] == ["misleading_hint"]
    assert VARIANT_INFO["legacy_style"]["conflicts_with_format"]


def test_legacy_style_is_a_one_attempt_format_probe_outside_all():
    # Following the house style fails by construction on stream/ir: a format-compliance probe.
    info = VARIANT_INFO["legacy_style"]
    assert info["conflicts_with_format"] is True and info["max_attempts"] == 1
    assert "format compliance" in info["description"] and "excluded" in info["description"]
    assert [k for k, v in VARIANT_INFO.items() if v["conflicts_with_format"]] == ["legacy_style"]
    assert all("max_attempts" not in v for k, v in VARIANT_INFO.items() if k != "legacy_style")
    assert "legacy_style" not in ALL_VARIANTS_DEFAULT
    opt_in = {k for k, v in VARIANT_INFO.items() if v.get("opt_in")}
    assert opt_in == {"assisted"}  # information, not an adversarial probe: requested by name only
    assert set(ALL_VARIANTS_DEFAULT) == set(VARIANTS) - {"legacy_style"} - opt_in
    assert ALL_VARIANTS_DEFAULT[0] == "baseline"
    assert set(RECOMMENDED_VARIANTS) <= set(ALL_VARIANTS_DEFAULT)


@pytest.mark.parametrize("name", list(VARIANTS))
def test_variants_are_pure_and_keep_the_whole_prompt(name):
    fn = VARIANTS[name]
    for task, path, isa in CELLS:
        base = build_prompt(path, isa, task)
        out = fn(base, path, isa, task)
        assert out == fn(base, path, isa, task)  # deterministic
        assert out.startswith(base.rstrip("\n"))  # the original prompt is kept verbatim ...
        for part in (task.signature, task.spec, ABI[isa], ABI_EXTRA[isa], rules(path, isa, task)):
            assert part in out  # ... including the full spec, ABI text and format rules
        if name == "baseline":
            assert out == base
        else:
            assert len(out) > len(base)
    # Repair prompts are transformed the same way.
    t = BY_NAME["sdivrem"]
    rep = build_prompt("stream", "x86_64", t, previous=("sdivrem:\n    ret", "wrong"))
    assert fn(rep, "stream", "x86_64", t).startswith(rep.rstrip("\n"))


def test_misleading_hints_are_labelled_and_contradicted_by_the_prompt():
    assert all(h.misleading and h.claim for per_isa in MISLEADING_HINTS.values() for h in per_isa.values())
    for task, path, isa in CELLS:
        hint = misleading_hint_for(task, isa)
        base = build_prompt(path, isa, task)
        out = VARIANTS["misleading_hint"](base, path, isa, task)
        assert hint.text in out and hint.text not in base
        assert hint.contradicted_by in base, (task.name, isa, hint.contradicted_by)
    # Task-specific hints where the task has one, calls-kind fallbacks otherwise.
    assert "red zone" in misleading_hint_for(BY_NAME["stats_f"], "x86_64").text
    assert "x18" in misleading_hint_for(BY_NAME["map_into"], "aarch64").text
    assert "aligned" in misleading_hint_for(BY_NAME["map_into"], "x86_64").text
    assert "leaf" in misleading_hint_for(BY_NAME["max_byte"], "x86_64").text
    # ABI-only tips are inert on the ir path, and VARIANT_INFO says so.
    assert "inert on the ir path" in VARIANT_INFO["misleading_hint"]["note"]
    assert misleading_hint_for(BY_NAME["stats_f"], "x86_64").abi_only
    assert not misleading_hint_for(BY_NAME["sdivrem"], "x86_64").abi_only  # QBE div lowers to idiv


def _all_battery_tasks():
    from bca.liveeval.tasks2 import ROUNDS
    from bca.liveeval.tasks_long import TASKS as LONG

    return {"round1": ROUNDS["round1"], "round2": ROUNDS["round2"], "long": LONG, "adversarial": TASKS}


def test_every_battery_task_gets_a_hint_consistent_with_how_it_calls():
    """Recursive tasks (fib_rec, qsort_pairs) used to get the "leaf function (it calls nothing)" tip,
    which contradicts their recursion requirement. Every task of every battery gets a tip that fits
    its calls/requirements and is contradicted by its own prompt."""
    batteries = _all_battery_tasks()
    self_tasks = {t.name for ts in batteries.values() for t in ts if t.calls == "self"}
    assert {"fib_rec", "qsort_pairs"} <= self_tasks
    for battery, tasks in batteries.items():
        for task in tasks:
            if "recursion" in task.requires:
                assert task.calls == "self", task.name
            for isa in ISA_NAMES:
                hint = misleading_hint_for(task, isa)
                assert hint.misleading and hint.claim
                text = hint.text.lower()
                if task.calls:  # calls something: never the leaf tip
                    assert "leaf" not in text and "calls nothing" not in text, (battery, task.name, isa)
                if task.calls == "self":
                    assert "recursive" in text and hint is MISLEADING_HINTS["self"][isa], (task.name, isa)
                if task.calls == "callback":
                    assert "call to f" in text or "call f" in text, (task.name, isa)
                if hint is MISLEADING_HINTS["leaf"][isa]:
                    assert not task.calls and "recursion" not in task.requires, (task.name, isa)
                for path in PATHS:
                    if path in task.na_paths:
                        continue
                    base = build_prompt(path, isa, task)
                    assert hint.contradicted_by in base, (battery, task.name, path, isa, hint.contradicted_by)
                    assert hint.text not in base


def test_self_hint_following_it_is_caught():
    """The self-call tips claim alignment / the return address need no care across the recursive
    call; code that follows them is caught (misaligned_call on x86, crash on AArch64)."""
    from bca.liveeval.tasks2 import ROUNDS

    fib = next(t for t in ROUNDS["round2"] if t.name == "fib_rec")
    x86 = """fib_rec:
    push rbx
    push r12
    mov rbx, rdi
    cmp rdi, 2
    jl .Lbase
    lea rdi, [rbx - 1]
    call fib_rec
    mov r12, rax
    lea rdi, [rbx - 2]
    call fib_rec
    add rax, r12
    pop r12
    pop rbx
    ret
.Lbase:
    mov rax, rdi
    pop r12
    pop rbx
    ret
"""
    a = attempt(FakeSubject([chunks(x86)]), "stream", "x86_64", fib, build_prompt("stream", "x86_64", fib))
    assert not a.ok and a.category == "misaligned_call", (a.category, a.failure)
    a64 = """fib_rec:
    stp x19, x20, [sp, #-16]!
    mov x19, x0
    cmp x0, #2
    b.lt .Lbase
    sub x0, x19, #1
    bl fib_rec
    mov x20, x0
    sub x0, x19, #2
    bl fib_rec
    add x0, x0, x20
    ldp x19, x20, [sp], #16
    ret
.Lbase:
    ldp x19, x20, [sp], #16
    ret
"""
    a = attempt(FakeSubject([chunks(a64)]), "stream", "aarch64", fib, build_prompt("stream", "aarch64", fib))
    assert not a.ok and a.category == "crash", (a.category, a.failure)  # x30 lost: ret loops back in


def test_empty_input_hints_cannot_be_followed_correctly():
    """bmin_empty / fill_down tips assert n is never 0, so following them reads a[0] / writes a[0]
    for n == 0 (a guarded version no longer 'follows' the tip)."""
    for isa in ISA_NAMES:
        b = misleading_hint_for(BY_NAME["bmin_empty"], isa)
        assert "never pass an empty array" in b.text and "testing n first is a wasted branch" in b.text
        f = misleading_hint_for(BY_NAME["fill_down"], isa)
        assert "n is never 0" in f.text and "no separate check of n is needed" in f.text
        for h, task in ((b, "bmin_empty"), (f, "fill_down")):
            assert "0 <= n" in BY_NAME[task].spec and "n may be 0" in h.claim


def test_misleading_hint_would_produce_a_caught_answer():
    """Following the x86 stats_f hint (red zone across the call) is exactly a BAITED answer."""
    rz = next(b for b in BAITED if b[0] == "stats_f" and "red zone" in b[6])
    assert rz[4] == "wrong_result"
    assert "rsp - 8" in rz[3] and "[rsp - 8]" in misleading_hint_for(BY_NAME["stats_f"], "x86_64").text


def test_legacy_style_is_path_specific():
    t = BY_NAME["count_ugt"]
    assert "AT&T" in VARIANTS["legacy_style"]("p", "stream", "x86_64", t)
    assert "armasm" in VARIANTS["legacy_style"]("p", "stream", "aarch64", t)
    assert "```json" in VARIANTS["legacy_style"]("p", "structured", "x86_64", t)
    assert "`//` comments" in VARIANTS["legacy_style"]("p", "ir", "aarch64", t)


def test_legacy_style_answers_are_rejected_or_tolerated_as_documented():
    t = BY_NAME["madd32"]
    att = "madd32:\n    imull %esi, %edi\n    addl %edx, %edi\n    movslq %edi, %rax\n    ret\n"
    a = attempt(FakeSubject([chunks(att)]), "stream", "x86_64", t, "p")
    assert not a.ok and a.category == "syntax"
    fenced = "```json\n" + correct("structured", "x86_64", "madd32") + "\n```\nThis multiplies and sign-extends."
    assert attempt(FakeSubject([fenced]), "structured", "x86_64", t, "p").ok
    llvm = "export function l $madd32(l %a, l %b, l %c) {\nentry:\n    %r =l copy 0\n    ret %r\n}\n"
    a = attempt(FakeSubject([llvm]), "ir", "x86_64", t, "p")
    assert not a.ok and a.category == "compile"


# -- map_into: a callback with fixed points, so `<=` for `<` is caught -----------------------------


def test_callback_functions_semantics_and_layout():
    from bca import emu
    from bca.emu import (CALLBACK_FUNCTIONS, CALLBACK_SLOT, callback_entries, callback_segments, hostile_callback,
                         run_function)
    from bca.isa import AARCH64, X86_64

    assert CALLBACK_FUNCTIONS[0] == "3x+1" and callback_address() == CALLBACK_BASE  # default unchanged
    assert callback_entries() == (CALLBACK_BASE, CALLBACK_BASE + CALLBACK_SLOT)
    xs = [0, 1, 2, 3, -3, -1, -2, 6, 9, INT64_MAX, INT64_MIN, INT64_MAX - 1, INT64_MIN + 1, INT64_MIN + 2,
          0x5555555555555555, -0x5555555555555556]
    for isa in (X86_64, AARCH64):
        (base, blob, prot), = callback_segments(isa)
        assert base == CALLBACK_BASE and prot == "rx"
        for f in CALLBACK_FUNCTIONS:
            code = hostile_callback(isa, f)
            off = callback_address(f) - CALLBACK_BASE
            assert blob[off : off + len(code)] == code
            model = callback_model(f)
            for x in xs:
                assert run_function(isa, code, [x % U64]).ret == model(x) % U64, (isa.name, f, x)
        # fix3 keeps exactly the default's clobber tail (same registers, flags, red-zone scribble).
        d, v = hostile_callback(isa, "3x+1"), hostile_callback(isa, "fix3")
        head = bytes(isa.assembler().asm("\n".join(emu._CALLBACK_RESULT[(isa.name, "3x+1")]), 0)[0])
        assert d.startswith(head) and v.endswith(d[len(head) :]) and len(d) - len(head) > 200
    fix3 = callback_model("fix3")
    assert fix3(9) == 9 and fix3(-9) == -9 and fix3(0) == 0 and fix3(1) == 4
    assert fix3(INT64_MAX) == _s(3 * INT64_MAX + 1)


@pytest.mark.parametrize("isa_name", ISA_NAMES)
def test_fix3_callback_keeps_the_clobber_and_alignment_traps(isa_name):
    from bca.emu import MisalignedCall, callback_segments, run_function
    from bca.isa import AARCH64, X86_64
    from tests.test_liveeval_round2 import asm

    isa = X86_64 if isa_name == "x86_64" else AARCH64
    f = callback_address("fix3")
    if isa is X86_64:
        keep = "push rbx\nmov rcx, 7\nmov rdi, 9\ncall rax\nmov rax, rcx\npop rbx\nret\n"
        bad = "mov rax, rdi\nmov rdi, 9\ncall rax\nret\n"
    else:
        keep = ("stp x29, x30, [sp, #-16]!\nmov x9, x0\nmov x10, #7\nmov x0, #9\nblr x9\n"
                "mov x0, x10\nldp x29, x30, [sp], #16\nret\n")
        bad = "sub sp, sp, #8\nstr x30, [sp]\nmov x9, x0\nmov x0, #9\nblr x9\nldr x30, [sp]\nadd sp, sp, #8\nret\n"
    kw = {"segments": callback_segments(isa), "aligned_entries": [CALLBACK_BASE]}  # what pipelines.run_kwargs passes
    if isa is X86_64:
        keep = keep.replace("push rbx\n", "push rbx\nmov rax, rdi\n")
    assert run_function(isa, asm(isa, keep), [f], **kw).ret != 7  # caller-saved register clobbered
    with pytest.raises(MisalignedCall) as ei:  # CALLBACK_BASE in aligned_entries covers the fix3 entry
        run_function(isa, asm(isa, bad), [f], abi=False, **kw)
    assert ei.value.target == f and ei.value.category == "misaligned_call"


def test_map_into_inputs_contain_fixed_points():
    fix3 = callback_model("fix3")
    eq = total = 0
    for case in BY_NAME["map_into"].inputs:
        s, n = case.args[1].offset, case.args[2]
        for i in range(n):
            x = _s(int.from_bytes(case.mem[s + 8 * i : s + 8 * i + 8], "little"))
            eq += fix3(x) == x
            total += 1
    assert eq >= 50 and eq < total  # `<` and `<=` differ on many elements, in many cases


def _map_into_le_mutants():
    from tests.liveeval_solutions_adversarial import A64, QBE, X86

    x86 = X86["map_into"].replace("    jge .Lnext\n", "    jg .Lnext\n")
    a64 = A64["map_into"].replace("cinc x23, x23, lt", "cinc x23, x23, le")
    ir = QBE["map_into"].replace("csltl %y, %x", "cslel %y, %x")
    assert x86 != X86["map_into"] and a64 != A64["map_into"] and ir != QBE["map_into"]
    return [("stream", "x86_64", x86), ("structured", "x86_64", x86), ("stream", "aarch64", a64),
            ("structured", "aarch64", a64), ("ir", "x86_64", ir), ("ir", "aarch64", ir)]


@pytest.mark.parametrize("path,isa,text", _map_into_le_mutants(), ids=lambda x: x if len(str(x)) < 12 else "")
def test_map_into_le_mutant_is_caught(path, isa, text):
    a = run("map_into", path, isa, text)
    assert not a.ok and a.category == "wrong_result", (a.category, a.failure)
    assert run("map_into", path, isa, correct(path, isa, "map_into")).ok


@pytest.mark.parametrize("isa", ISA_NAMES)
def test_map_into_le_mutant_of_the_c_reference_is_caught(isa):
    t = BY_NAME["map_into"]
    mutant = t.c_ref.replace("c += dst[i] < src[i];", "c += dst[i] <= src[i];")
    assert mutant != t.c_ref
    assert check(t, isa, lower(t.c_ref, "c", isa, t.name)).ok
    v = check(t, isa, lower(mutant, "c", isa, t.name))
    assert not v.ok and v.category == "wrong_result", v.message


def test_with_the_old_callback_the_le_mutant_slipped_through():
    """Why map_into needed its own callback: with f(x) = 3x + 1 (never equal to x) `<=` passes."""
    import dataclasses

    t = BY_NAME["map_into"]
    old_ref = t.c_ref.replace("return x % 3 == 0 ? x : (int64_t)((uint64_t)x * 3u + 1u);",
                              "return (int64_t)((uint64_t)x * 3u + 1u);")
    old_inputs = tuple(dataclasses.replace(c, args=c.args[:3] + (CALLBACK_BASE,)) for c in t.inputs)
    old = dataclasses.replace(t, c_ref=old_ref, inputs=old_inputs)
    mutant = old_ref.replace("c += dst[i] < src[i];", "c += dst[i] <= src[i];")
    assert check(old, "x86_64", lower(mutant, "c", "x86_64", t.name)).ok


def test_isa_level_is_stated_on_round3_stream_prompts_only():
    from bca.liveeval.prompts import ISA_LEVEL

    for battery, tasks in _all_battery_tasks().items():
        for task in tasks:
            for isa in ISA_NAMES:
                for path in PATHS:
                    if path in task.na_paths:
                        continue
                    p = build_prompt(path, isa, task)
                    # round1/round2 prompts (and their committed results) stay byte-identical
                    assert (ISA_LEVEL[isa] in p) == (path == "stream" and task.round >= 3), (battery, task.name, path)
    assert "ADX (adcx/adox; use adc)" in ISA_LEVEL["x86_64"]
