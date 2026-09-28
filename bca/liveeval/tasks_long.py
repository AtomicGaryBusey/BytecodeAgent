"""Battery "long": whole programs of roughly 60-250 instructions.

Rounds 1 and 2 (bca.liveeval.tasks / tasks2) were function-sized kernels, and the subject passed
105/110 cells on the first attempt; the streaming early abort and the repair rounds barely
mattered. Every task here is a single exported entry function whose natural implementation is
long: an interpreter with a dispatch chain of compares, a recursive quicksort of key/value
records, base64 with an arithmetic alphabet, xxHash64 (five 64-bit constants, rotations, a
4-lane main loop and three tails), PackBits run-length encode/decode with capacity and
truncation checks, integer matrix power with a scratch buffer, an LCS dynamic program over one
row, and schoolbook multi-precision multiplication. Long outputs make syntax slips,
register-pressure bugs and ABI mistakes likelier, and give the stream's
early abort room to save work.

Every task is checked with `difftest.memory_differential` (return value + the whole memory
block, a write guard on the output windows and a read guard on the input windows). Where an
output buffer doubles as scratch space, the spec pins its final contents exactly (the checker
compares every byte). Inputs are deterministic and bounded; the C references are host-compiled
with -fwrapv.

No task needs a pipeline capability the harness lacks, so every (path, ISA) cell is feasible
(`na` is empty): no SIMD, no data sections (tables are built with arithmetic), and the only
calls are qsort_pairs's calls to its own entry.
"""

from __future__ import annotations

import random

from bca.difftest import INT64_MAX, INT64_MIN, MemCase, Ptr
from bca.emu import MASK64
from bca.liveeval.tasks import Task

U64 = 1 << 64
ROUND = 3  # battery "long" (>= 2: prompts carry the extended ABI facts)


def _s64(v: int) -> int:
    v &= MASK64
    return v - U64 if v >> 63 else v


def _pack(values, size: int = 8) -> bytes:
    return b"".join((v & ((1 << (8 * size)) - 1)).to_bytes(size, "little") for v in values)


def _show(xs, k: int = 8) -> str:
    xs = list(xs)
    return "[" + ", ".join(map(str, xs[:k])) + (f", ... ({len(xs)} elements)" if len(xs) > k else "") + "]"


def _hex(b: bytes, k: int = 12) -> str:
    return f"bytes.fromhex('{b[:k].hex()}{'...' if len(b) > k else ''}')"


class _Layout:
    """Builds a MemCase block: regions separated by junk, each at a chosen alignment offset."""

    def __init__(self, rng: random.Random, lead: int = 16):
        self.rng = rng
        self.mem = bytearray(rng.randbytes(lead))

    def put(self, data: bytes, align: int = 8, skew: int = 0) -> int:
        pad = (-len(self.mem)) % align + skew
        self.mem += self.rng.randbytes(pad)
        off = len(self.mem)
        self.mem += data
        self.mem += self.rng.randbytes(8 + self.rng.randrange(3) * 8)
        return off

    def bytes(self) -> bytes:
        return bytes(self.mem)


# == 1. vm_run: a stack-machine interpreter ===================================================

HALT, PUSH, ADD, SUB, MUL, DUP, SWAP, DROP, OVER, LT, JZ, JMP = range(12)
_ARG_OPS = (PUSH, JZ, JMP)
VM_MAX_STEPS = 5000


class VMInvalid(Exception):
    pass


def vm_model(code: list[int], cap: int, max_steps: int = VM_MAX_STEPS,
             trace: set | None = None) -> tuple[int, list[int], int]:
    """Python model of vm_run: (result, final stack, steps). Raises VMInvalid for programs
    outside the guarantees (underflow, overflow, bad opcode/target, too many steps). Executed
    opcodes (and "end" when execution runs off the end) are added to `trace`."""
    st: list[int] = []
    pc = steps = 0
    n = len(code)
    while pc != n:
        if not 0 <= pc < n:
            raise VMInvalid(f"pc {pc}")
        steps += 1
        if steps > max_steps:
            raise VMInvalid("too many steps")
        op = code[pc]
        if trace is not None:
            trace.add(op)
        if op in _ARG_OPS and pc + 1 >= n:
            raise VMInvalid("missing operand")
        need = {ADD: 2, SUB: 2, MUL: 2, DUP: 1, SWAP: 2, DROP: 1, OVER: 2, LT: 2, JZ: 1}.get(op, 0)
        if len(st) < need:
            raise VMInvalid("underflow")
        if op == HALT:
            break
        if op == PUSH:
            st.append(_s64(code[pc + 1]))
            pc += 2
        elif op in (ADD, SUB, MUL, LT):
            b, a = st.pop(), st.pop()
            st.append(_s64(a + b) if op == ADD else _s64(a - b) if op == SUB else _s64(a * b) if op == MUL
                      else int(a < b))
            pc += 1
        elif op == DUP:
            st.append(st[-1])
            pc += 1
        elif op == SWAP:
            st[-1], st[-2] = st[-2], st[-1]
            pc += 1
        elif op == DROP:
            st.pop()
            pc += 1
        elif op == OVER:
            st.append(st[-2])
            pc += 1
        elif op == JZ:
            t = code[pc + 1]
            if not 0 <= t <= n:
                raise VMInvalid("target")
            pc = t if st.pop() == 0 else pc + 2
        elif op == JMP:
            t = code[pc + 1]
            if not 0 <= t <= n:
                raise VMInvalid("target")
            pc = t
        else:
            raise VMInvalid(f"opcode {op}")
        if len(st) > cap:
            raise VMInvalid("overflow")
    else:
        if trace is not None:
            trace.add("end")
    return (st[-1] if st else 0), st, steps


def _vm_loop_sum(n: int, op: int = ADD, init: int = 0) -> list[int]:
    # [acc, i]: while i != 0: acc = acc op i; i -= 1.  Result: acc.
    return [PUSH, init, PUSH, n,
            DUP, JZ, 22,                       # 4: loop head
            SWAP, OVER, op, SWAP,              # 7: acc op= i
            PUSH, 1, SUB, JMP, 4,              # 11
            HALT, HALT, HALT, HALT, HALT, HALT,  # 16..21: never reached
            DROP, HALT]                        # 22


def _vm_pow(x: int, k: int) -> list[int]:
    # [acc, k]: while k != 0: acc *= x; k -= 1.
    return [PUSH, 1, PUSH, k,
            DUP, JZ, 19,                       # 4
            SWAP, PUSH, x, MUL, SWAP,          # 7
            PUSH, 1, SUB, JMP, 4, HALT, HALT,  # 12
            DROP]                              # 19: then runs off the end


def _vm_count_lt(limit: int, step: int) -> list[int]:
    # i = 0; while i < limit: i += step.  Result: i.
    return [PUSH, 0,
            DUP, PUSH, limit, LT, JZ, 14,      # 2
            PUSH, step, ADD, JMP, 2, HALT,     # 8
            HALT]                              # 14


def _vm_nested(n: int, m: int) -> list[int]:
    # acc = 0; for i = n..1: for j = m..1: acc += 1 (kept as [acc, i, j]) using only
    # 2-deep operations is awkward, so count with a single stack cell per level:
    # [acc, i]: while i: acc = acc + m (computed by an inner loop that adds 1 m times); i -= 1
    return [PUSH, 0, PUSH, n,
            DUP, JZ, 35,                       # 4: outer head
            SWAP, PUSH, m,                     # 7: [i, acc, j]
            DUP, JZ, 26,                       # 10: inner head
            SWAP, PUSH, 1, ADD, SWAP,          # 13: acc += 1
            PUSH, 1, SUB, JMP, 10, HALT, HALT,  # 18: j -= 1
            HALT,                              # 25
            DROP, SWAP, PUSH, 1, SUB, JMP, 4,  # 26: drop j, [acc, i-1]
            HALT, HALT,                        # 33
            DROP, HALT]                        # 35


def _vm_random(rng: random.Random, cap: int, length: int) -> list[int]:
    """Random well-formed-looking program with forward jumps only (validated by the model)."""
    code: list[int] = []
    depth = 0
    while len(code) < length:
        choices = [PUSH, PUSH]
        if depth >= 1:
            choices += [DUP, DROP, JZ]
        if depth >= 2:
            choices += [ADD, SUB, MUL, SWAP, OVER, LT, ADD, MUL]
        if rng.random() < 0.05:
            choices.append(JMP)
        op = rng.choice(choices)
        if op in (PUSH, DUP, OVER) and depth >= cap:
            op = DROP if depth else PUSH
        if op == PUSH:
            v = rng.choice([rng.randint(-20, 20), rng.randint(INT64_MIN, INT64_MAX), 0, 1, -1,
                            INT64_MAX, INT64_MIN])
            code += [PUSH, v]
            depth += 1
        elif op in (JZ, JMP):
            code += [op, 0]  # target patched below
            depth -= op == JZ
        else:
            code.append(op)
            depth += {DUP: 1, OVER: 1, DROP: -1}.get(op, 0) + (-1 if op in (ADD, SUB, MUL, LT) else 0)
    if rng.random() < 0.7:
        code.append(HALT)
    # Patch jump targets to forward positions that start an instruction (or the end).
    starts, i = [], 0
    while i < len(code):
        starts.append(i)
        i += 2 if code[i] in _ARG_OPS else 1
    starts.append(len(code))
    for s in starts[:-1]:
        if code[s] in (JZ, JMP):
            code[s + 1] = rng.choice([t for t in starts if t > s + 1] or [len(code)])
    return code


def _vm_cases() -> tuple:
    rng = random.Random(301)
    progs: list[tuple[list[int], int, str]] = [
        ([], 1, "empty program"), ([], 8, "empty program"), ([HALT], 4, "HALT only"),
        ([PUSH, 5], 1, "PUSH 5 (runs off the end)"), ([PUSH, 5, HALT], 3, "PUSH 5; HALT"),
        ([PUSH, INT64_MIN, PUSH, -1, MUL], 2, "INT64_MIN * -1 wraps"),
        ([PUSH, INT64_MAX, PUSH, 1, ADD], 2, "INT64_MAX + 1 wraps"),
        ([PUSH, 3, PUSH, 10, SUB], 2, "3 - 10"), ([PUSH, 10, PUSH, 3, SUB], 2, "10 - 3"),
        ([PUSH, -5, PUSH, 3, LT], 2, "-5 < 3 (signed)"), ([PUSH, 3, PUSH, -5, LT], 2, "3 < -5"),
        ([PUSH, 1, PUSH, 2, SWAP], 2, "SWAP"), ([PUSH, 1, PUSH, 2, OVER], 3, "OVER"),
        ([PUSH, 1, PUSH, 2, DROP], 2, "DROP"), ([PUSH, 7, DROP], 1, "empty at the end"),
        ([PUSH, 0, JZ, 6, PUSH, 99, PUSH, 1], 2, "JZ taken"),
        ([PUSH, 4, JZ, 6, PUSH, 99, PUSH, 1], 2, "JZ not taken"),
        ([JMP, 4, 77, 88, PUSH, 6, HALT, 123, 456], 1, "JMP over garbage"),
        ([PUSH, 2, HALT, 999, -7], 1, "garbage after HALT"),
        ([PUSH, 1, JMP, 4], 1, "JMP to the end"),
    ]
    for n in (0, 1, 2, 10, 50):
        progs.append((_vm_loop_sum(n), 3 + (n % 3), f"sum 1..{n}"))
    for n in (0, 1, 5, 20, 25):
        progs.append((_vm_loop_sum(n, MUL, 1), 3, f"{n}!"))
    for x, k in ((3, 0), (3, 4), (-2, 63), (7, 40), (2, 64)):
        progs.append((_vm_pow(x, k), 3, f"{x}^{k}"))
    for lim, step in ((0, 1), (10, 3), (100, 7), (-5, 1), (500, 13)):
        progs.append((_vm_count_lt(lim, step), 3, f"count to {lim} by {step}"))
    for n, m in ((0, 3), (3, 0), (4, 5), (12, 9)):
        progs.append((_vm_nested(n, m), 4, f"nested {n}x{m}"))
    made = 0
    while made < 40:
        cap = rng.choice([4, 8, 16, 32, 64])
        code = _vm_random(rng, cap, rng.randint(1, 60 if made < 30 else 200))
        try:
            vm_model(code, cap)
        except VMInvalid:
            continue
        progs.append((code, cap, "random straight-line/forward-jump program"))
        made += 1
    cases = []
    for i, (code, cap, what) in enumerate(progs):
        try:
            vm_model(code, cap)  # every program meets the guarantees in the spec
        except VMInvalid as exc:
            raise AssertionError(f"{what}: {exc}") from exc
        lay = _Layout(rng)
        co = lay.put(_pack(code))
        so = lay.put(rng.randbytes(8 * cap), align=16 if i % 2 else 8)
        cases.append(MemCase(lay.bytes(), (Ptr(co), len(code), Ptr(so), cap), ((so, so + 8 * cap),),
                             ((co, co + 8 * len(code)), (so, so + 8 * cap)),
                             f"vm_run(code={_show(code, 16)}, len={len(code)}, stack, cap={cap}) [{what}]"))
    return tuple(cases)


VM_RUN = Task(
    "vm_run", "int64_t vm_run(const int64_t *code, int64_t len, int64_t *stack, int64_t cap)",
    "Interpret a program for a small stack machine and return the value on top of its stack. "
    "code points to len 64-bit words (0 <= len <= 250); stack points to cap 64-bit slots "
    "(1 <= cap <= 64) that hold the machine's stack (stack[0] is the bottom entry). The "
    "machine starts with pc = 0 and an empty stack. Each instruction is the word code[pc]; "
    "PUSH, JZ and JMP take the following word code[pc+1] as their operand. Opcodes (a is the "
    "entry below the top b; \"pop\" removes the top entry): "
    "0 HALT: stop. "
    "1 PUSH v: push v; pc += 2. "
    "2 ADD: pop b, pop a, push a + b. "
    "3 SUB: pop b, pop a, push a - b. "
    "4 MUL: pop b, pop a, push a * b (ADD/SUB/MUL wrap modulo 2^64). "
    "5 DUP: push a copy of the top. "
    "6 SWAP: exchange the top two entries. "
    "7 DROP: pop and discard the top. "
    "8 OVER: push a copy of the entry below the top. "
    "9 LT: pop b, pop a, push 1 if a < b (signed) else 0. "
    "10 JZ t: pop v; if v == 0 then pc = t else pc += 2. "
    "11 JMP t: pc = t. "
    "Every other instruction does pc += 1. Execution also stops when pc == len (running off "
    "the end). Guarantees: every instruction that is executed has a valid opcode and its "
    "operand word (if any) exists; jump targets are in [0, len]; the stack never underflows "
    "and never holds more than cap entries; at most 5000 instructions are executed. Words "
    "that are never executed (e.g. after a HALT) may hold anything. "
    "Return the top entry when execution stops, or 0 if the stack is empty. On return the "
    "stack buffer must hold exactly: stack[0..d-1] = the d final entries, bottom first, and "
    "stack[d..cap-1] = 0 (the whole buffer is checked; its initial contents are garbage). "
    "Read only code[0..len-1] and stack[0..cap-1]; write only stack[0..cap-1].",
    """int64_t vm_run(const int64_t *code, int64_t len, int64_t *stack, int64_t cap) {
        int64_t pc = 0, sp = 0;
        while (pc != len) {
            int64_t op = code[pc];
            if (op == 0) break;
            if (op == 1) { stack[sp++] = code[pc + 1]; pc += 2; continue; }
            if (op == 10) { int64_t v = stack[--sp]; pc = v == 0 ? code[pc + 1] : pc + 2; continue; }
            if (op == 11) { pc = code[pc + 1]; continue; }
            int64_t b = sp > 0 ? stack[sp - 1] : 0, a = sp > 1 ? stack[sp - 2] : 0;
            switch (op) {
            case 2: stack[sp - 2] = (int64_t)((uint64_t)a + (uint64_t)b); sp--; break;
            case 3: stack[sp - 2] = (int64_t)((uint64_t)a - (uint64_t)b); sp--; break;
            case 4: stack[sp - 2] = (int64_t)((uint64_t)a * (uint64_t)b); sp--; break;
            case 5: stack[sp] = b; sp++; break;
            case 6: stack[sp - 1] = a; stack[sp - 2] = b; break;
            case 7: sp--; break;
            case 8: stack[sp] = a; sp++; break;
            case 9: stack[sp - 2] = a < b; sp--; break;
            }
            pc++;
        }
        int64_t r = sp > 0 ? stack[sp - 1] : 0;
        for (int64_t i = sp; i < cap; i++) stack[i] = 0;
        return r;
    }""",
    4, inputs=_vm_cases(), mem=True, round=ROUND, max_insns=3_000_000,
)  # fmt: skip


# == 2. qsort_pairs (recursive quicksort of key/value records) =================================


def _qs_cases() -> tuple:
    rng = random.Random(302)

    def recs(keys, vals=None):
        return [(k, v) for k, v in zip(keys, vals if vals is not None else [rng.randint(-9, 9) for _ in keys])]

    arrays: list[tuple[list[tuple[int, int]], str]] = [
        ([], "empty"), ([(5, 1)], "one"), ([(2, 0), (1, 0)], "two"), ([(1, 0), (2, 0)], "two sorted"),
        ([(3, 2), (3, 1)], "equal keys, values decide"), ([(3, 1), (3, 1)], "identical records"),
        (recs([INT64_MAX, INT64_MIN, 0, -1, 1]), "extreme keys"),
        (recs([7] * 6, [INT64_MAX, INT64_MIN, 0, -1, 1, INT64_MIN]), "equal keys, extreme values"),
        (recs(list(range(150)), [0] * 150), "sorted, n = 150"),
        (recs(list(range(150, 0, -1)), [0] * 150), "reverse sorted, n = 150"),
        (recs([4] * 150, [4] * 150), "all identical, n = 150"),
        (recs([4] * 150, list(range(150, 0, -1))), "equal keys, reverse values, n = 150"),
        (recs([(i * 37) % 101 - 50 for i in range(101)]), "permutation"),
    ]
    for _ in range(25):
        n = rng.choice([rng.randint(0, 12), rng.randint(0, 150)])
        mode = rng.random()
        if mode < 0.4:
            keys = [rng.randint(-5, 5) for _ in range(n)]
            vals = [rng.randint(-3, 3) for _ in range(n)]
        elif mode < 0.7:
            keys = [rng.randint(INT64_MIN, INT64_MAX) for _ in range(n)]
            vals = [rng.randint(INT64_MIN, INT64_MAX) for _ in range(n)]
        else:
            keys = sorted(rng.randint(-1000, 1000) for _ in range(n))
            vals = [rng.choice([rng.randint(-2, 2), rng.randint(INT64_MIN, INT64_MAX)]) for _ in range(n)]
        arrays.append((recs(keys, vals), "random"))
    cases = []
    for arr, what in arrays:
        lay = _Layout(rng)
        o = lay.put(_pack([x for r in arr for x in r]), align=16, skew=rng.choice([0, 8]))
        win = ((o, o + 16 * len(arr)),)
        cases.append(MemCase(lay.bytes(), (Ptr(o), len(arr)), win, win,
                             f"qsort_pairs(a={_show(arr, 5)}, n={len(arr)}) [{what}; records as (key, value)]"))
    return tuple(cases)


QSORT_PAIRS = Task(
    "qsort_pairs", "int64_t qsort_pairs(int64_t *a, int64_t n)",
    "a points to n records of two signed 64-bit integers each: record i is (key, value) = "
    "(a[2*i], a[2*i+1]), 0 <= n <= 150. Sort the records in place into ascending order by key, "
    "and by value among equal keys (signed comparisons; records with equal key and value are "
    "identical, so the result is unique), and return 0. The input may be sorted, reverse "
    "sorted, all equal, or contain INT64_MIN/INT64_MAX keys and values. The implementation "
    "MUST be a recursive quicksort: partition the records around a pivot record, then sort the "
    "parts with real call instructions to qsort_pairs's own entry point, passing a pointer to "
    "the first record of the part and its number of records (you may loop on one part and "
    "recurse on the other; the machine code is checked for a call to its own entry). Every "
    "recursive call is a normal ABI call and is checked as one: the stack must be 16-byte "
    "aligned at each call, and any callee-saved register you use must be saved and restored. "
    "Read and write only a[0..2n-1] (both are checked).",
    """static int less(const int64_t *x, const int64_t *y) {
        return x[0] < y[0] || (x[0] == y[0] && x[1] < y[1]);
    }
    static void swp(int64_t *x, int64_t *y) {
        int64_t k = x[0], v = x[1]; x[0] = y[0]; x[1] = y[1]; y[0] = k; y[1] = v;
    }
    static void qs(int64_t *a, int64_t n) {
        while (n > 1) {
            int64_t *p = a + 2 * (n - 1), i = 0;
            for (int64_t j = 0; j < n - 1; j++)
                if (less(a + 2 * j, p)) { swp(a + 2 * i, a + 2 * j); i++; }
            swp(a + 2 * i, p);
            qs(a, i);
            a += 2 * (i + 1); n -= i + 1;
        }
    }
    int64_t qsort_pairs(int64_t *a, int64_t n) { qs(a, n); return 0; }""",
    2, inputs=_qs_cases(), mem=True, requires=("recursion",), calls="self", round=ROUND,
    max_insns=5_000_000,
    ir_hint="Call the function itself with `%r =l call $qsort_pairs(l %p, l %m)`.",
)  # fmt: skip


# == 3. base64_encode ============================================================================


def _b64_cases() -> tuple:
    rng = random.Random(303)
    datas = [b"", b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar", b"\x00", b"\xff", b"\xff\xff",
             b"\xff\xff\xff", b"\x00\x00\x00\x00", bytes([0xFB, 0xEF, 0xBE]), bytes([0x3E, 0x3F]),
             bytes(range(256))[:200], b"Many hands make light work.",
             b"The quick brown fox jumps over the lazy dog"]
    datas += [rng.randbytes(rng.randint(0, 200)) for _ in range(25)]
    cases = []
    for i, d in enumerate(datas):
        lay = _Layout(rng)
        so = lay.put(d, align=1, skew=i % 5)
        olen = 4 * ((len(d) + 2) // 3)
        oo = lay.put(rng.randbytes(olen), align=1, skew=(i * 3) % 7)
        cases.append(MemCase(lay.bytes(), (Ptr(so), len(d), Ptr(oo)), ((oo, oo + olen),), ((so, so + len(d)),),
                             f"base64_encode(src={_hex(d)}, n={len(d)}, out)"
                             + (f" [src = {d.decode()!r}]" if d.isascii() and d.isalpha() else "")))
    return tuple(cases)


BASE64_ENCODE = Task(
    "base64_encode", "int64_t base64_encode(const uint8_t *src, int64_t n, char *out)",
    "Encode the n bytes at src (0 <= n <= 200) as standard Base64 (RFC 4648, with '=' "
    "padding) into out and return the number of characters written, 4 * ceil(n / 3). Each "
    "group of 3 input bytes b0 b1 b2 forms the 24-bit value (b0 << 16) | (b1 << 8) | b2, "
    "emitted as four 6-bit indices from the most significant; index v maps to 'A'+v for "
    "v < 26, 'a'+(v-26) for v < 52, '0'+(v-52) for v < 62, '+' for 62 and '/' for 63. A final "
    "group of 1 byte gives 2 characters then \"==\"; of 2 bytes, 3 characters then \"=\". "
    "Example: \"foobar\" -> \"Zm9vYmFy\", \"fo\" -> \"Zm8=\". Do not write a terminating NUL. "
    "There is no data section: compute the alphabet mapping with arithmetic/compares. Read "
    "only src[0..n-1] (reads are checked: never load past src[n-1], even for a partial final "
    "group) and write only out[0..4*ceil(n/3)-1].",
    """int64_t base64_encode(const uint8_t *src, int64_t n, char *out) {
        int64_t o = 0;
        for (int64_t i = 0; i < n; i += 3) {
            int64_t k = n - i < 3 ? n - i : 3;
            uint32_t v = (uint32_t)src[i] << 16;
            if (k > 1) v |= (uint32_t)src[i + 1] << 8;
            if (k > 2) v |= src[i + 2];
            for (int j = 0; j < 4; j++) {
                uint32_t x = (v >> (18 - 6 * j)) & 63;
                char c = x < 26 ? 'A' + x : x < 52 ? 'a' + (x - 26) : x < 62 ? '0' + (x - 52) : x == 62 ? '+' : '/';
                out[o + j] = j <= k ? c : '=';
            }
            o += 4;
        }
        return o;
    }""",
    3, inputs=_b64_cases(), mem=True, round=ROUND, mem_elem=1, mem_signed=False,
    ir_hint="Load a byte zero-extended with `%b =l loadub %p`; store one with `storeb %c, %p`.",
)  # fmt: skip


# == 4. xxh64 ======================================================================================

XXH_P1, XXH_P2, XXH_P3 = 0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9
XXH_P4, XXH_P5 = 0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5


def _xxh_cases() -> tuple:
    rng = random.Random(304)
    items: list[tuple[bytes, int]] = [(b"", 0), (b"", 1), (b"a", 0), (b"abc", 0), (b"abc", 0x9E3779B1),
                                      (b"\xff" * 32, 0), (b"\x00" * 31, -1), (b"\x00" * 33, 2**63),
                                      (bytes(range(64)), 5), (b"The quick brown fox jumps over the lazy dog", 0)]
    for n in list(range(0, 41)) + [47, 48, 63, 64, 65, 95, 96, 97, 100, 127, 128, 199, 256, 300]:
        seed = rng.choice([0, 1, rng.randint(0, U64 - 1), U64 - 1])
        items.append((rng.randbytes(n), seed))
    cases = []
    for i, (d, seed) in enumerate(items):
        lay = _Layout(rng)
        o = lay.put(d, align=8, skew=i % 8)  # only byte aligned
        cases.append(MemCase(lay.bytes(), (Ptr(o), len(d), _s64(seed)), (), ((o, o + len(d)),),
                             f"xxh64(p={_hex(d)}, n={len(d)}, seed={seed & MASK64:#x}) [p % 8 = {i % 8}]"))
    return tuple(cases)


XXH64 = Task(
    "xxh64", "uint64_t xxh64(const uint8_t *p, int64_t n, uint64_t seed)",
    "Return the 64-bit xxHash (XXH64) of the n bytes at p (0 <= n <= 300) with the given "
    "seed. All arithmetic is on unsigned 64-bit values modulo 2^64; rotl(x, r) rotates left "
    "by r bits; read64(q) / read32(q) are little-endian unsigned loads of 8 / 4 bytes (p is "
    "only byte-aligned). Constants: P1 = 0x9E3779B185EBCA87, P2 = 0xC2B2AE3D27D4EB4F, "
    "P3 = 0x165667B19E3779F9, P4 = 0x85EBCA77C2B2AE63, P5 = 0x27D4EB2F165667C5. "
    "round(acc, x) = rotl(acc + x * P2, 31) * P1; merge(h, v) = (h ^ round(0, v)) * P1 + P4. "
    "(1) If n >= 32: v1 = seed + P1 + P2, v2 = seed + P2, v3 = seed, v4 = seed - P1; for "
    "each whole 32-byte stripe at q: v1 = round(v1, read64(q)), v2 = round(v2, read64(q+8)), "
    "v3 = round(v3, read64(q+16)), v4 = round(v4, read64(q+24)); then h = rotl(v1, 1) + "
    "rotl(v2, 7) + rotl(v3, 12) + rotl(v4, 18); h = merge(h, v1); h = merge(h, v2); "
    "h = merge(h, v3); h = merge(h, v4). Otherwise h = seed + P5. "
    "(2) h += n. (3) For the remaining bytes after the stripes (fewer than 32), in order: "
    "while at least 8 remain: h ^= round(0, read64(q)); h = rotl(h, 27) * P1 + P4; q += 8. "
    "If at least 4 remain: h ^= read32(q) * P1; h = rotl(h, 23) * P2 + P3; q += 4. For each "
    "remaining byte c: h ^= c * P5; h = rotl(h, 11) * P1. "
    "(4) h ^= h >> 33; h *= P2; h ^= h >> 29; h *= P3; h ^= h >> 32 (logical shifts); "
    "return h. Check values: xxh64(\"\", 0, 0) = 0xEF46DB3751D8E999, xxh64(\"abc\", 3, 0) = "
    "0x44BC2CF5AD770999. Do not read outside p[0..n-1] (reads are checked) and do not write "
    "to memory.",
    """static uint64_t rotl(uint64_t x, int r) { return (x << r) | (x >> (64 - r)); }
    static uint64_t rd64(const uint8_t *p) { uint64_t v = 0; for (int i = 7; i >= 0; i--) v = v << 8 | p[i]; return v; }
    static uint64_t rd32(const uint8_t *p) { uint64_t v = 0; for (int i = 3; i >= 0; i--) v = v << 8 | p[i]; return v; }
    static const uint64_t P1 = 0x9E3779B185EBCA87ull, P2 = 0xC2B2AE3D27D4EB4Full, P3 = 0x165667B19E3779F9ull,
        P4 = 0x85EBCA77C2B2AE63ull, P5 = 0x27D4EB2F165667C5ull;
    static uint64_t rnd(uint64_t acc, uint64_t x) { return rotl(acc + x * P2, 31) * P1; }
    static uint64_t mrg(uint64_t h, uint64_t v) { return (h ^ rnd(0, v)) * P1 + P4; }
    int64_t xxh64(const uint8_t *p, int64_t n, int64_t sseed) {
        uint64_t seed = (uint64_t)sseed, h;
        const uint8_t *q = p, *end = p + n;
        if (n >= 32) {
            uint64_t v1 = seed + P1 + P2, v2 = seed + P2, v3 = seed, v4 = seed - P1;
            while (end - q >= 32) {
                v1 = rnd(v1, rd64(q)); v2 = rnd(v2, rd64(q + 8));
                v3 = rnd(v3, rd64(q + 16)); v4 = rnd(v4, rd64(q + 24));
                q += 32;
            }
            h = rotl(v1, 1) + rotl(v2, 7) + rotl(v3, 12) + rotl(v4, 18);
            h = mrg(h, v1); h = mrg(h, v2); h = mrg(h, v3); h = mrg(h, v4);
        } else h = seed + P5;
        h += (uint64_t)n;
        while (end - q >= 8) { h ^= rnd(0, rd64(q)); h = rotl(h, 27) * P1 + P4; q += 8; }
        if (end - q >= 4) { h ^= rd32(q) * P1; h = rotl(h, 23) * P2 + P3; q += 4; }
        while (q < end) { h ^= *q++ * P5; h = rotl(h, 11) * P1; }
        h ^= h >> 33; h *= P2; h ^= h >> 29; h *= P3; h ^= h >> 32;
        return (int64_t)h;
    }""",
    3, inputs=_xxh_cases(), mem=True, round=ROUND, mem_elem=1, mem_signed=False,
    ir_hint="QBE has no rotate instruction: rotl(x, r) = (x shl r) or (x shr (64 - r)) (`shr` is "
            "logical); `loadl`/`loadw` work on unaligned addresses; zero-extend a 32-bit load with "
            "`%v =l loaduw %q`; a 64-bit constant is written as a decimal integer (e.g. "
            "-7046029288634856825 for 0x9E3779B185EBCA87).",
)  # fmt: skip


# == 5. packbits_encode / 6. packbits_decode =========================================================


def _run_at(src: bytes, i: int) -> int:
    r = 1
    while i + r < len(src) and src[i + r] == src[i] and r < 128:
        r += 1
    return r


def packbits_encode_model(src: bytes, cap: int) -> tuple[int, bytes]:
    out = bytearray()
    i = 0
    while i < len(src):
        r = _run_at(src, i)
        if r >= 3:
            if cap - len(out) < 2:
                return -1, bytes(out)
            out += bytes([(1 - r) & 0xFF, src[i]])
            i += r
            continue
        j = i
        while j < len(src) and j - i < 128 and _run_at(src, j) < 3:
            j += 1
        if cap - len(out) < j - i + 1:
            return -1, bytes(out)
        out += bytes([j - i - 1]) + src[i:j]
        i = j
    return len(out), bytes(out)


def packbits_decode_model(src: bytes, cap: int) -> tuple[int, bytes]:
    out = bytearray()
    k = 0
    while k < len(src):
        h = src[k] - 256 if src[k] > 127 else src[k]
        if h == -128:
            k += 1
            continue
        if h >= 0:
            if k + 1 + h + 1 > len(src):
                return -2, bytes(out)
            data = src[k + 1 : k + 2 + h]
            k += h + 2
        else:
            if k + 1 >= len(src):
                return -2, bytes(out)
            data = src[k + 1 : k + 2] * (1 - h)
            k += 2
        for b in data:
            if len(out) == cap:
                return -1, bytes(out)
            out.append(b)
    return len(out), bytes(out)


def _runs(rng: random.Random, total: int) -> bytes:
    out = bytearray()
    while len(out) < total:
        out += bytes([rng.choice([0, 255, rng.randrange(256)])]) * rng.choice(
            [1, 1, 1, 2, 2, 3, rng.randint(1, 20), rng.randint(100, 300)])
    return bytes(out[:total])


def _pb_enc_cases() -> tuple:
    rng = random.Random(305)
    items: list[tuple[bytes, int | None]] = [
        (b"", None), (b"", 0), (b"a", None), (b"a", 0), (b"a", 1), (b"aa", None), (b"aaa", None),
        (b"aaa", 1), (b"abc", None), (b"abc", 3), (b"aabbcc", None), (b"aaabcc", None), (b"abcccd", None),
        (b"abcccd", 3), (b"abcccd", 5), (b"\x00" * 128, None), (b"\x00" * 129, None), (b"\x00" * 130, None),
        (b"\x07" * 600, None), (bytes(range(256)), None), (bytes(range(128)) + b"xxx", None),
        (bytes(range(129)), None), (bytes(range(129)), 129), (b"\xAA\xBB" * 100, None),
        (bytes.fromhex("aaaaaa80000000aa"), None),
    ]
    for _ in range(25):
        d = _runs(rng, rng.randint(0, 600))
        need = packbits_encode_model(d, 1 << 30)[0]
        items.append((d, rng.choice([None, None, rng.randint(0, need + 1), max(need - 1, 0), need])))
    cases = []
    for i, (d, cap) in enumerate(items):
        need = packbits_encode_model(d, 1 << 30)[0]
        cap = need if cap is None else cap
        lay = _Layout(rng)
        so = lay.put(d, align=1, skew=i % 3)
        oo = lay.put(rng.randbytes(cap), align=1, skew=i % 5)
        cases.append(MemCase(lay.bytes(), (Ptr(so), len(d), Ptr(oo), cap), ((oo, oo + cap),), ((so, so + len(d)),),
                             f"packbits_encode(src={_hex(d)}, n={len(d)}, out, cap={cap}) "
                             f"[the full encoding is {need} bytes]"))
    return tuple(cases)


PACKBITS_ENCODE = Task(
    "packbits_encode", "int64_t packbits_encode(const uint8_t *src, int64_t n, uint8_t *out, int64_t cap)",
    "Compress the n bytes at src (0 <= n <= 600) with PackBits run-length encoding into out, "
    "which has room for cap bytes (0 <= cap <= 1200), and return the number of bytes written. "
    "The output is a sequence of packets: a repeat packet is the header byte 257 - r (i.e. "
    "-(r-1) as a signed byte) followed by one byte b, meaning r copies of b (3 <= r <= 128); "
    "a literal packet is the header byte L - 1 followed by L bytes copied verbatim "
    "(1 <= L <= 128). Encode greedily, left to right, exactly as follows. Let run(j) be the "
    "number of consecutive bytes equal to src[j] starting at j, capped at 128. At position i: "
    "if run(i) >= 3, emit a repeat packet with r = run(i) and b = src[i], and advance i by r. "
    "Otherwise emit a literal packet of the bytes src[i..j-1], where j starts at i and "
    "advances while j < n, j - i < 128 and run(j) < 3; then set i = j. (So a pair of equal "
    "bytes stays inside a literal; a literal stops just before a run of 3 or more.) Example: "
    "\"abcccd\" -> 01 'a' 'b' FE 'c' 00 'd' (7 bytes). If a whole packet does not fit in the "
    "remaining room, write none of it and return -1 immediately (earlier packets stay "
    "written). Read only src[0..n-1] (never look past src[n-1] when measuring a run) and "
    "write only out[0..cap-1] (both are checked).",
    """static int64_t run(const uint8_t *s, int64_t n, int64_t j) {
        int64_t r = 1;
        while (j + r < n && s[j + r] == s[j] && r < 128) r++;
        return r;
    }
    int64_t packbits_encode(const uint8_t *src, int64_t n, uint8_t *out, int64_t cap) {
        int64_t i = 0, o = 0;
        while (i < n) {
            int64_t r = run(src, n, i);
            if (r >= 3) {
                if (cap - o < 2) return -1;
                out[o] = (uint8_t)(1 - r); out[o + 1] = src[i]; o += 2; i += r;
                continue;
            }
            int64_t j = i;
            while (j < n && j - i < 128 && run(src, n, j) < 3) j++;
            if (cap - o < j - i + 1) return -1;
            out[o++] = (uint8_t)(j - i - 1);
            while (i < j) out[o++] = src[i++];
        }
        return o;
    }""",
    4, inputs=_pb_enc_cases(), mem=True, round=ROUND, mem_elem=1, mem_signed=False,
    ir_hint="Load a byte zero-extended with `%b =w loadub %p`; store one with `storeb %c, %p`.",
)  # fmt: skip


def _pb_dec_cases() -> tuple:
    rng = random.Random(306)
    items: list[tuple[bytes, int | None]] = [
        (b"", None), (b"", 0), (b"\x00a", None), (b"\x00a", 0), (b"\xfea", None), (b"\xfea", 2),
        (b"\x80", None), (b"\x80\x80\x00z", None), (bytes.fromhex("0161 62fe 6300 64"), None),
        (bytes.fromhex("0161 62fe 6300 64"), 4), (bytes.fromhex("0161 62fe 6300 64"), 2),
        (b"\x81\xff", None), (b"\x81\xff" * 5, 300), (b"\x7f" + bytes(range(128)), None),
        (b"\x7f" + bytes(range(127)), None), (b"\x02ab", None), (b"\xfd", None), (b"\x00a\xfd", None),
        (b"\x05abc", 1), (b"\x7f" + bytes(128) + b"\x81\x07", 200),
    ]
    for _ in range(26):
        d = packbits_encode_model(_runs(rng, rng.randint(0, 500)), 1 << 30)[1]
        r = rng.random()
        if r < 0.2 and d:
            d = d[: rng.randrange(len(d))]  # truncated input
        elif r < 0.35:
            d = bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))  # arbitrary bytes
        elif r < 0.45:
            d = b"\x80" + d
        need = packbits_decode_model(d, 1 << 30)[1]
        items.append((d, rng.choice([None, None, rng.randint(0, len(need) + 2), max(len(need) - 1, 0)])))
    cases = []
    for i, (d, cap) in enumerate(items):
        full = packbits_decode_model(d, 1 << 30)
        cap = len(full[1]) if cap is None else cap
        lay = _Layout(rng)
        so = lay.put(d, align=1, skew=i % 3)
        oo = lay.put(rng.randbytes(cap), align=1, skew=i % 7)
        tail = "malformed input" if full[0] == -2 else f"decoded length {full[0]}"
        cases.append(MemCase(lay.bytes(), (Ptr(so), len(d), Ptr(oo), cap), ((oo, oo + cap),), ((so, so + len(d)),),
                             f"packbits_decode(src={_hex(d)}, n={len(d)}, out, cap={cap}) [{tail}]"))
    return tuple(cases)


PACKBITS_DECODE = Task(
    "packbits_decode", "int64_t packbits_decode(const uint8_t *src, int64_t n, uint8_t *out, int64_t cap)",
    "Decompress PackBits data: src holds n bytes (0 <= n <= 600) of packets, decoded left to "
    "right into out, which has room for cap bytes (0 <= cap <= 1200). Each packet starts with "
    "a header byte h read as a signed byte: 0 <= h <= 127: a literal packet, the next h + 1 "
    "bytes are copied to the output; -127 <= h <= -1: a repeat packet, the next byte is "
    "written 1 - h times; h = -128: no operation (the packet is just that byte). Return the "
    "number of bytes written. Errors: if a packet needs more source bytes than remain in "
    "src[0..n-1] (checked when its header is read, before any of its output is written), "
    "return -2 (the output of earlier packets stays written). If the output would exceed "
    "cap bytes, write exactly the first cap bytes of the output (out[0..cap-1] completely "
    "filled) and return -1. Example: 01 'a' 'b' FE 'c' 00 'd' decodes to \"abcccd\" (returns "
    "6); with cap = 4 it writes \"abcc\" and returns -1. Read only src[0..n-1] and write only "
    "out[0..cap-1] (both are checked: never write out[cap], even when cap is 0).",
    """int64_t packbits_decode(const uint8_t *src, int64_t n, uint8_t *out, int64_t cap) {
        int64_t k = 0, o = 0;
        while (k < n) {
            int64_t h = (int8_t)src[k];
            if (h == -128) { k++; continue; }
            if (h >= 0) {
                if (k + h + 2 > n) return -2;
                for (int64_t t = 0; t <= h; t++) { if (o == cap) return -1; out[o++] = src[k + 1 + t]; }
                k += h + 2;
            } else {
                if (k + 2 > n) return -2;
                uint8_t v = src[k + 1];
                for (int64_t t = 0; t < 1 - h; t++) { if (o == cap) return -1; out[o++] = v; }
                k += 2;
            }
        }
        return o;
    }""",
    4, inputs=_pb_dec_cases(), mem=True, round=ROUND, mem_elem=1, mem_signed=False,
    ir_hint="Load a byte zero-extended with `%b =w loadub %p` or sign-extended with `%b =w loadsb %p`; "
            "store one with `storeb %c, %p`.",
)  # fmt: skip


# == 7. mat_pow ======================================================================================


def mat_pow_model(a: list[int], n: int, k: int) -> list[int]:
    r = [int(i == j) for i in range(n) for j in range(n)]
    for _ in range(k):
        r = [_s64(sum(r[i * n + t] * a[t * n + j] for t in range(n))) for i in range(n) for j in range(n)]
    return r


def _matpow_cases() -> tuple:
    rng = random.Random(307)
    specs: list[tuple[int, int, str]] = [(1, 0, "small"), (1, 1, "small"), (1, 5, "small"), (1, 64, "wide"),
                                         (2, 0, "small"), (2, 1, "small"), (2, 10, "fib"), (2, 64, "wide"),
                                         (3, 2, "small"), (3, 7, "wide"), (4, 3, "small"), (5, 13, "small"),
                                         (6, 0, "small"), (6, 1, "wide"), (6, 9, "small"), (6, 64, "wide"),
                                         (6, 63, "small")]
    specs += [(rng.randint(1, 6), rng.randint(0, 64), rng.choice(["small", "wide"])) for _ in range(18)]
    cases = []
    for n, k, kind in specs:
        if kind == "fib":
            a = [1, 1, 1, 0]
        elif kind == "small":
            a = [rng.randint(-3, 3) for _ in range(n * n)]
        else:
            a = [rng.choice([rng.randint(INT64_MIN, INT64_MAX), rng.randint(-9, 9)]) for _ in range(n * n)]
        lay = _Layout(rng)
        ao = lay.put(_pack(a), align=8, skew=rng.choice([0, 8]))
        oo = lay.put(rng.randbytes(8 * n * n))
        to = lay.put(rng.randbytes(16 * n * n), skew=rng.choice([0, 8]))
        cases.append(MemCase(lay.bytes(), (Ptr(ao), n, k, Ptr(oo), Ptr(to)),
                             ((oo, oo + 8 * n * n), (to, to + 16 * n * n)),
                             ((ao, ao + 8 * n * n), (oo, oo + 8 * n * n), (to, to + 16 * n * n)),
                             f"mat_pow(a={_show(a, 9)}, n={n}, k={k}, out, tmp)"))
    return tuple(cases)


MAT_POW = Task(
    "mat_pow", "int64_t mat_pow(const int64_t *a, int64_t n, int64_t k, int64_t *out, int64_t *tmp)",
    "a points to an n x n matrix of signed 64-bit integers in row-major order (element (i, j) "
    "at a[i*n + j]), 1 <= n <= 6. Compute the matrix power a^k (0 <= k <= 64; a^0 is the "
    "identity matrix) into out (n*n elements, row-major) and return 0. Matrix products use "
    "wrapping 64-bit arithmetic: (x*y)(i, j) = sum over t of x(i, t) * y(t, j), modulo 2^64. "
    "tmp points to 2*n*n elements of scratch space that you may use freely (e.g. for a copy "
    "of the base and a product), but on return every one of those 2*n*n elements must be 0 "
    "(the whole of out and tmp is checked; both start out as garbage). Any correct method "
    "is fine (k repeated multiplications, or square-and-multiply). a, out and tmp do not "
    "overlap. Do not write to a; read only a, out and tmp; write only out and tmp.",
    """int64_t mat_pow(const int64_t *a, int64_t n, int64_t k, int64_t *out, int64_t *tmp) {
        for (int64_t i = 0; i < n * n; i++) out[i] = (i / n == i % n);
        for (int64_t e = 0; e < k; e++) {
            for (int64_t i = 0; i < n; i++)
                for (int64_t j = 0; j < n; j++) {
                    uint64_t s = 0;
                    for (int64_t t = 0; t < n; t++) s += (uint64_t)out[i * n + t] * (uint64_t)a[t * n + j];
                    tmp[i * n + j] = (int64_t)s;
                }
            for (int64_t i = 0; i < n * n; i++) out[i] = tmp[i];
        }
        for (int64_t i = 0; i < 2 * n * n; i++) tmp[i] = 0;
        return 0;
    }""",
    5, inputs=_matpow_cases(), mem=True, round=ROUND, max_insns=3_000_000,
)  # fmt: skip


# == 8. lcs_len ======================================================================================


def lcs_model(x: bytes, y: bytes) -> tuple[int, list[int]]:
    row = [0] * (len(y) + 1)
    for c in x:
        diag = 0
        for j in range(1, len(y) + 1):
            up = row[j]
            row[j] = diag + 1 if c == y[j - 1] else max(row[j], row[j - 1])
            diag = up
    return row[-1], row


def _lcs_cases() -> tuple:
    rng = random.Random(308)
    items: list[tuple[bytes, bytes]] = [
        (b"", b""), (b"", b"abc"), (b"abc", b""), (b"a", b"a"), (b"a", b"b"), (b"ABCBDAB", b"BDCABA"),
        (b"AGGTAB", b"GXTXAYB"), (b"abcdef", b"abcdef"), (b"abcdef", b"fedcba"), (b"\x00\xff" * 10, b"\xff\x00" * 7),
        (b"a" * 120, b"a" * 120), (b"a" * 120, b"b" * 120), (bytes(range(120)), bytes(range(119, -1, -1))),
    ]
    for _ in range(27):
        alpha = rng.choice([2, 4, 26, 256])
        m, n = rng.choice([(rng.randint(0, 10), rng.randint(0, 10)), (rng.randint(0, 120), rng.randint(0, 120))])
        items.append((bytes(rng.randrange(alpha) for _ in range(m)), bytes(rng.randrange(alpha) for _ in range(n))))
    cases = []
    for i, (x, y) in enumerate(items):
        lay = _Layout(rng)
        xo = lay.put(x, align=1, skew=i % 4)
        yo = lay.put(y, align=1, skew=i % 3)
        ro = lay.put(rng.randbytes(8 * (len(y) + 1)), skew=rng.choice([0, 8]))
        rw = (ro, ro + 8 * (len(y) + 1))
        cases.append(MemCase(lay.bytes(), (Ptr(xo), len(x), Ptr(yo), len(y), Ptr(ro)), (rw,),
                             ((xo, xo + len(x)), (yo, yo + len(y)), rw),
                             f"lcs_len(x={_hex(x)}, m={len(x)}, y={_hex(y)}, n={len(y)}, row)"))
    return tuple(cases)


LCS_LEN = Task(
    "lcs_len", "int64_t lcs_len(const uint8_t *x, int64_t m, const uint8_t *y, int64_t n, int64_t *row)",
    "Return the length of the longest common subsequence of the byte strings x[0..m-1] and "
    "y[0..n-1] (0 <= m, n <= 120; bytes are compared as unsigned values, any value 0..255). "
    "row points to n+1 signed 64-bit integers of working memory (initially garbage) for the "
    "O(n)-memory dynamic program. On return, row[j] must equal the LCS length of all of x "
    "and the first j bytes of y, for every j in 0..n (the whole row is checked; so row[0] = 0 "
    "and row[n] is the result). The standard single-row update does this: set row[0..n] = 0; "
    "for each i in 0..m-1: diag = 0; for j in 1..n: up = row[j]; row[j] = (x[i] == y[j-1]) ? "
    "diag + 1 : max(row[j], row[j-1]); diag = up. Example: x = \"ABCBDAB\", y = \"BDCABA\" -> 4. "
    "Read only x[0..m-1], y[0..n-1] and row[0..n]; write only row[0..n] (all checked).",
    """int64_t lcs_len(const uint8_t *x, int64_t m, const uint8_t *y, int64_t n, int64_t *row) {
        for (int64_t j = 0; j <= n; j++) row[j] = 0;
        for (int64_t i = 0; i < m; i++) {
            int64_t diag = 0;
            for (int64_t j = 1; j <= n; j++) {
                int64_t up = row[j];
                row[j] = x[i] == y[j - 1] ? diag + 1 : (row[j] > row[j - 1] ? row[j] : row[j - 1]);
                diag = up;
            }
        }
        return row[n];
    }""",
    5, inputs=_lcs_cases(), mem=True, round=ROUND, max_insns=3_000_000,
    ir_hint="Load a byte zero-extended with `%b =w loadub %p`.",
)  # fmt: skip


# == 9. mp_mul ========================================================================================


def _mpmul_cases() -> tuple:
    rng = random.Random(309)
    top = U64 - 1

    def rnd_limbs(k: int) -> list[int]:
        return [rng.choice([0, 1, top, rng.randint(0, top), rng.randint(0, top), 1 << 63, 0xFFFFFFFF])
                for _ in range(k)]

    items: list[tuple[list[int], list[int]]] = [
        ([], []), ([], [5]), ([5], []), ([0], [0]), ([0, 0], [0, 0, 0]), ([1], [1]), ([2], [3]),
        ([top], [top]), ([top] * 8, [top] * 8), ([top] * 8, [1]), ([1], [top] * 8), ([0, 1], [0, 1]),
        ([top, top], [2]), ([1 << 63], [2]), ([0xFFFFFFFF, 0xFFFFFFFF], [0xFFFFFFFF]), ([7, 0, 0], [0, 9, 0, 0]),
        ([0] * 8, [top] * 8),
    ]
    for _ in range(28):
        items.append((rnd_limbs(rng.randint(0, 8)), rnd_limbs(rng.randint(0, 8))))
    cases = []
    for a, b in items:
        lay = _Layout(rng)
        ao = lay.put(_pack(a), skew=rng.choice([0, 8]))
        bo = lay.put(_pack(b))
        ro = lay.put(rng.randbytes(8 * (len(a) + len(b))), skew=rng.choice([0, 8]))
        rw = (ro, ro + 8 * (len(a) + len(b)))
        cases.append(MemCase(lay.bytes(), (Ptr(ao), len(a), Ptr(bo), len(b), Ptr(ro)), (rw,),
                             ((ao, ao + 8 * len(a)), (bo, bo + 8 * len(b)), rw),
                             f"mp_mul(a={_show([hex(v) for v in a], 4)}, na={len(a)}, "
                             f"b={_show([hex(v) for v in b], 4)}, nb={len(b)}, r)"))
    return tuple(cases)


MP_MUL = Task(
    "mp_mul",
    "int64_t mp_mul(const uint64_t *a, int64_t na, const uint64_t *b, int64_t nb, uint64_t *r)",
    "Multi-precision multiplication. a holds na and b holds nb 64-bit limbs (0 <= na, nb <= 8), "
    "each an unsigned number in little-endian limb order: A = a[0] + a[1]*2^64 + ... . Write "
    "the full product A*B to r[0..na+nb-1] (little-endian limbs, every one of the na+nb limbs "
    "written, including high zero limbs; r starts out as garbage and does not overlap a or b) "
    "and return the number of significant limbs: the smallest k such that r[k..na+nb-1] are "
    "all 0 (so 0 when the product is 0, e.g. when na or nb is 0). Limbs are full unsigned "
    "64-bit values (up to 2^64 - 1): the schoolbook method needs the full 128-bit product of "
    "two limbs and carries that propagate. Example: a = {2^64-1}, b = {2^64-1} gives r = "
    "{1, 2^64-2} and returns 2. Read only a[0..na-1], b[0..nb-1] and r[0..na+nb-1]; write "
    "only r[0..na+nb-1] (all checked).",
    """int64_t mp_mul(const uint64_t *a, int64_t na, const uint64_t *b, int64_t nb, uint64_t *r) {
        for (int64_t i = 0; i < na + nb; i++) r[i] = 0;
        for (int64_t i = 0; i < na; i++) {
            unsigned __int128 c = 0;
            for (int64_t j = 0; j < nb; j++) {
                c += (unsigned __int128)a[i] * b[j] + r[i + j];
                r[i + j] = (uint64_t)c;
                c >>= 64;
            }
            r[i + nb] = (uint64_t)c;
        }
        int64_t k = na + nb;
        while (k > 0 && r[k - 1] == 0) k--;
        return k;
    }""",
    5, inputs=_mpmul_cases(), mem=True, round=ROUND,
    ir_hint="QBE has no high-multiply instruction: build the 128-bit product of two limbs from "
            "32-bit halves (`and`, `shr` (logical), `mul`, `add`) with explicit carries; an "
            "unsigned carry out of `%s =l add %x, %y` is `%c =w cultl %s, %x`.",
    mem_signed=False,
)  # fmt: skip


TASKS: tuple[Task, ...] = (
    VM_RUN, QSORT_PAIRS, BASE64_ENCODE, XXH64, PACKBITS_ENCODE, PACKBITS_DECODE, MAT_POW, LCS_LEN, MP_MUL,
)
BY_NAME = {t.name: t for t in TASKS}

__all__ = ["BY_NAME", "TASKS", "lcs_model", "mat_pow_model", "packbits_decode_model", "packbits_encode_model",
           "vm_model"]
