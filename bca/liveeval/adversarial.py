"""Battery "adversarial": pitfall-bait tasks and prompt variants for the live-model evaluation.

Rounds 1-2 were passed almost entirely on the first attempt and the safety net (ABI checks,
misaligned-call trap, out-of-bounds guards, hang limit, GNU-as cross-check) never fired on
live output. This battery provokes the classic low-level mistakes on purpose, to measure
whether a model falls for them and whether the pipeline catches them.

Part A, `TASKS`: honest tasks (the specification is correct and complete, and the ABI text in
the prompt is correct) where the natural shortcut is wrong. `BAITS[name]` records, for each
task, the pitfall, the tempting mistake and the check that should catch it.

Part B, `VARIANTS`: prompt transformations `variant(prompt, path, isa, task) -> prompt`. Each
one is a deterministic pure function that keeps the whole original prompt (task, specification,
ABI text and output rules) and only adds text after it. `VARIANT_INFO` describes each one;
the misleading hints are listed in `MISLEADING_HINTS` and labelled `misleading=True`, with the
sentence of the original prompt that contradicts them (`contradicted_by`), so reports can flag
them. Every task gets a hint that fits how it calls: its own, else the fallback for its `calls`
kind ("callback", "self" for recursive tasks, "leaf"). Hints marked `abi_only` are about
register/stack rules and are inert on the ir path (QBE owns the ABI there).

`VARIANT_INFO[name]` keys: "description", "misleading", "conflicts_with_format" (the variant asks
for a format the pipelines reject: a format-compliance probe, not a coding measurement) and,
optionally, "max_attempts" (a cap on attempts per cell). `legacy_style` is such a probe (one
attempt), so `ALL_VARIANTS_DEFAULT` (what `--variant all` means) leaves it out; request it by name.

Everything runs inside the sandboxed emulator harness: the "hostile" callback is one of the
harness's own callees (bca.emu.CALLBACK_FUNCTIONS) that clobber caller-saved state: stats_f
uses f(x) = 3x + 1, map_into uses "fix3" (x when x % 3 == 0, else 3x + 1) so that f(x) == x
happens and a `<=` for `<` is caught. The guards only watch the emulated data region.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from bca.difftest import INT64_MAX, INT64_MIN, MemCase, Ptr, int_inputs
from bca.emu import CALLBACK_BASE, MASK64, callback_address
from bca.liveeval.tasks import Task

U64 = 1 << 64
ROUND = 3  # >= 2: the prompts carry ABI_EXTRA (call clobbers, alignment, "below rsp may be overwritten")


def _s64(v: int) -> int:
    v &= MASK64
    return v - U64 if v >> 63 else v


def _pack(values, size: int = 8) -> bytes:
    return b"".join((v & ((1 << (8 * size)) - 1)).to_bytes(size, "little") for v in values)


def _show(xs, k: int = 8) -> str:
    xs = list(xs)
    return "[" + ", ".join(map(str, xs[:k])) + (f", ... ({len(xs)} elements)" if len(xs) > k else "") + "]"


_CALLBACK_TEXT = (
    "f is opaque (you do not know what it computes); it may clobber every caller-saved "
    "register, the flags and the stack below the stack pointer, and it checks that the stack is "
    "16-byte aligned at each call."
)
_CALLBACK_IR_HINT = "Call through the pointer with `%r =l call %f(l %x)` (an indirect call through a temporary)."


# -- 1. count_ugt: unsigned comparison over the full range -----------------------------------

_U_EDGES = [0, 1, 2, (1 << 63) - 1, 1 << 63, (1 << 63) + 1, U64 - 2, U64 - 1, 1 << 32, 0xFFFFFFFF]


def _count_ugt_cases() -> tuple:
    rng = random.Random(301)
    arrays = [[], [], [0], [U64 - 1], [1 << 63], [(1 << 63) - 1, 1 << 63], _U_EDGES, list(reversed(_U_EDGES))]
    for _ in range(30):
        n = rng.randint(1, 40)
        arrays.append([rng.choice([rng.randint(0, U64 - 1), rng.choice(_U_EDGES),
                                   (1 << 63) + rng.randint(-50, 50)]) for _ in range(n)])
    cases = []
    for i, arr in enumerate(arrays):
        ts = [0, (1 << 63) - 1, 1 << 63, U64 - 1, rng.randint(0, U64 - 1)]
        if arr:
            ts.append(arr[rng.randrange(len(arr))])
        t = ts[i % len(ts)]
        lead = 8 * (1 + i % 2)
        mem = rng.randbytes(lead) + _pack(arr) + rng.randbytes(16)
        cases.append(MemCase(mem, (Ptr(lead), len(arr), _s64(t)), (), ((lead, lead + 8 * len(arr)),),
                             f"count_ugt(a={_show(arr)}, n={len(arr)}, t={t}) [values shown unsigned]"))
    return tuple(cases)


COUNT_UGT = Task(
    "count_ugt", "int64_t count_ugt(const uint64_t *a, int64_t n, uint64_t t)",
    "a points to n unsigned 64-bit integers (0 <= n <= 40). Return how many elements are strictly "
    "greater than t, comparing as unsigned 64-bit values; the elements and t range over the whole "
    "unsigned range 0 .. 2^64 - 1. Never read outside a[0..n-1] (reads are checked; when n = 0, a "
    "must not be read at all) and do not write to memory.",
    """int64_t count_ugt(const uint64_t *a, int64_t n, uint64_t t) {
        int64_t c = 0;
        for (int64_t i = 0; i < n; i++) c += a[i] > t;
        return c;
    }""",
    3, inputs=_count_ugt_cases(), mem=True, round=ROUND, mem_signed=False,
)  # fmt: skip


# -- 2. max_byte: 8-bit operations do not zero-extend; bytes are unsigned ------------------------


def _max_byte_cases() -> tuple:
    rng = random.Random(302)
    datas = [b"", b"\x00", b"\x7f", b"\x80", b"\xff", b"\x7f\x80", b"\x80\x7f", b"\x01\x02\x03",
             bytes(range(200)), bytes(range(127, -1, -1)), b"\x00" * 17, b"\x90" * 5 + b"\x10"]
    datas += [rng.randbytes(rng.randint(1, 200)) for _ in range(14)]
    datas += [bytes(rng.randint(0, 0x7F) for _ in range(rng.randint(1, 30))) + bytes([rng.randint(0x80, 0xFF)])
              for _ in range(6)]
    cases = []
    for i, d in enumerate(datas):
        off = 8 + i % 5
        mem = rng.randbytes(off) + d + rng.randbytes(16)
        shown = d[:12].hex() + ("..." if len(d) > 12 else "")
        cases.append(MemCase(mem, (Ptr(off), len(d)), (), ((off, off + len(d)),),
                             f"max_byte(p=bytes.fromhex('{shown}'), n={len(d)})"))
    return tuple(cases)


MAX_BYTE = Task(
    "max_byte", "int64_t max_byte(const uint8_t *p, int64_t n)",
    "p points to n bytes (0 <= n <= 200), each an unsigned value 0..255. Return the largest byte "
    "value as an int64_t in 0..255 (all upper 56 bits of the 64-bit result must be 0), or 0 when "
    "n = 0 (then p must not be read). Never read outside p[0..n-1] (reads are checked) and do not "
    "write to memory.",
    """int64_t max_byte(const uint8_t *p, int64_t n) {
        int64_t m = 0;
        for (int64_t i = 0; i < n; i++) if (p[i] > m) m = p[i];
        return m;
    }""",
    2, inputs=_max_byte_cases(), mem=True, round=ROUND, mem_elem=1, mem_signed=False,
    ir_hint="Load a byte zero-extended with `%b =l loadub %p`.",
)  # fmt: skip


# -- 3. madd32: 32-bit results must be SIGN-extended (x86 32-bit ops and AArch64 w writes zero-extend)

_W_EDGES = [0, 1, -1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x100000000, -(2**31), 2**31 - 1,
            0x123456789ABCDEF0, -0x0FEDCBA987654321, INT64_MAX, INT64_MIN, 65536, 46341]

MADD32 = Task(
    "madd32", "int64_t madd32(int64_t a, int64_t b, int64_t c)",
    "Let a32, b32 and c32 be the low 32 bits of a, b and c (the upper 32 bits of the arguments are "
    "arbitrary and must be ignored). Compute a32 * b32 + c32 with wrapping 32-bit arithmetic "
    "(modulo 2^32), interpret the 32-bit result as a signed int32_t, and return it sign-extended "
    "to int64_t. Example: a result of 0xFFFFFFFF returns -1 (not 4294967295); a result of "
    "0x7FFFFFFF returns 2147483647.",
    """int64_t madd32(int64_t a, int64_t b, int64_t c) {
        uint32_t r = (uint32_t)a * (uint32_t)b + (uint32_t)c;
        return (int64_t)(int32_t)r;
    }""",
    3, inputs=tuple(dict.fromkeys(int_inputs(3, 60, seed=303, edges=_W_EDGES))), round=ROUND,
    ir_hint="A `w` instruction may take an `l` operand (it uses the low 32 bits); `extsw` sign-extends a "
            "`w` to `l`, `extuw` zero-extends.",
)  # fmt: skip


# -- 4. sdivrem: C truncating division; the two trapping cases are defined -----------------------


def _sdivrem_cases() -> tuple:
    rng = random.Random(304)
    pairs = [(7, 2), (-7, 2), (7, -2), (-7, -2), (6, 3), (-6, 3), (1, 7), (-1, 7), (0, 5), (0, -5),
             (7, 0), (-7, 0), (0, 0), (INT64_MIN, 0), (INT64_MAX, 0), (INT64_MIN, -1), (INT64_MAX, -1),
             (-1, -1), (5, -1), (INT64_MIN, 1), (INT64_MIN, 2), (INT64_MIN, -2), (INT64_MIN, 3),
             (INT64_MIN, INT64_MIN), (INT64_MAX, INT64_MIN), (-5, INT64_MIN), (INT64_MAX, INT64_MAX),
             (INT64_MIN + 1, -1), (INT64_MAX, 2), (-(2**62), 3), (100, -7), (-100, 7), (-100, -7)]
    for _ in range(40):
        a = rng.choice([rng.randint(-1000, 1000), rng.randint(INT64_MIN, INT64_MAX)])
        b = rng.choice([rng.randint(-20, 20), rng.randint(INT64_MIN, INT64_MAX), 0, -1])
        pairs.append((a, b))
    cases = []
    for a, b in dict.fromkeys(pairs):
        cases.append(MemCase(rng.randbytes(24), (a, b, Ptr(8)), ((8, 16),), ((8, 16),),
                             f"sdivrem(a={a}, b={b}, rem)"))
    return tuple(cases)


SDIVREM = Task(
    "sdivrem", "int64_t sdivrem(int64_t a, int64_t b, int64_t *rem)",
    "Signed 64-bit division with C semantics: the quotient q = a / b is truncated toward zero and "
    "the remainder r = a - q*b has the sign of a (or is 0); e.g. a = -7, b = 2 gives q = -3, "
    "r = -1, and a = 7, b = -2 gives q = -3, r = 1. Store r to *rem and return q. a and b may be any "
    "int64_t values, and two special cases are defined (they must return normally, not trap): if "
    "b == 0, then q = 0 and r = a; if a == INT64_MIN and b == -1, then q = INT64_MIN and r = 0. "
    "Always store *rem. Write only the 8 bytes at rem; the only memory you may read is *rem.",
    """int64_t sdivrem(int64_t a, int64_t b, int64_t *rem) {
        int64_t q, r;
        if (b == 0) { q = 0; r = a; }
        else if (b == -1) { q = (int64_t)(0 - (uint64_t)a); r = 0; }
        else { q = a / b; r = a % b; }
        *rem = r;
        return q;
    }""",
    3, inputs=_sdivrem_cases(), mem=True, round=ROUND,
    ir_hint="QBE `div`/`rem` are signed and truncate toward zero; store with `storel %v, %rem`.",
)  # fmt: skip


# -- 5. shift_sat: counts >= 64 are defined by the spec (hardware masks the count) ----------------


def _shift_inputs() -> tuple:
    rng = random.Random(305)
    xs = [0, 1, -1, 2, -2, 0x5A5A5A5A5A5A5A5A, INT64_MAX, INT64_MIN, 1 << 40, -(1 << 40), 12345]
    ns = [0, 1, 2, 31, 32, 33, 62, 63, 64, 65, 66, 96, 127, 128, 129, 191, 192, 200]
    out = [(x, n) for x in xs for n in ns if (xs.index(x) + ns.index(n)) % 2 == 0 or n in (63, 64, 65, 128)]
    out += [(rng.randint(INT64_MIN, INT64_MAX), rng.randint(0, 200)) for _ in range(40)]
    return tuple(dict.fromkeys(out))


SHIFT_SAT = Task(
    "shift_sat", "int64_t shift_sat(int64_t x, int64_t n)",
    "0 <= n <= 200. Return (x << n) + (x >> n) with wrapping 64-bit addition, where x << n is a "
    "logical left shift and x >> n an arithmetic (sign-propagating) right shift, each defined for "
    "every n as n repeated single-bit shifts. So for n >= 64: x << n is 0, and x >> n is 0 when "
    "x >= 0 and -1 when x < 0.",
    """int64_t shift_sat(int64_t x, int64_t n) {
        uint64_t l = n >= 64 ? 0 : (uint64_t)x << n;
        int64_t r = n >= 64 ? (x < 0 ? -1 : 0) : x >> n;
        return (int64_t)(l + (uint64_t)r);
    }""",
    2, inputs=_shift_inputs(), round=ROUND,
)  # fmt: skip


# -- 6. bmin_empty: n = 0 must read nothing -----------------------------------------------------


def _array_cases(name: str, seed: int, arrays, fmt=_show) -> tuple:
    rng = random.Random(seed)
    cases = []
    for i, arr in enumerate(arrays):
        lead = 8 * (1 + i % 3)
        mem = rng.randbytes(lead) + _pack(arr) + rng.randbytes(16)
        cases.append(MemCase(mem, (Ptr(lead), len(arr)), (), ((lead, lead + 8 * len(arr)),),
                             f"{name}(a={fmt(arr)}, n={len(arr)})"))
    return tuple(cases)


def _signed_arrays(seed: int, extra) -> list:
    rng = random.Random(seed)
    arrays = [[], [], [5], [-5], [INT64_MAX], [INT64_MIN], [INT64_MAX, INT64_MIN], [INT64_MIN, INT64_MAX],
              [3, 2, 1], [1, 2, 3], [0, -1, 0, -1], [2] * 9, list(range(20, -21, -3))] + list(extra)
    for _ in range(25):
        n = rng.randint(1, 60)
        arrays.append([rng.choice([rng.randint(-50, 50), rng.randint(INT64_MIN, INT64_MAX)]) for _ in range(n)])
    return arrays


BMIN_EMPTY = Task(
    "bmin_empty", "int64_t bmin_empty(const int64_t *a, int64_t n)",
    "a points to n signed 64-bit integers, 0 <= n <= 60. Return the smallest element (signed "
    "comparison), or INT64_MAX when n == 0. Never read outside a[0..n-1] (reads are checked; when "
    "n == 0, a must not be read at all, since it may point just past the end of readable memory) "
    "and do not write to memory.",
    """int64_t bmin_empty(const int64_t *a, int64_t n) {
        int64_t m = INT64_MAX;
        for (int64_t i = 0; i < n; i++) if (a[i] < m) m = a[i];
        return m;
    }""",
    2, inputs=_array_cases("bmin_empty", 306, _signed_arrays(316, [[INT64_MAX] * 3])), mem=True, round=ROUND,
)  # fmt: skip


# -- 7. count_desc: adjacent pairs, the last pair ends at a[n-1] ---------------------------------

COUNT_DESC = Task(
    "count_desc", "int64_t count_desc(const int64_t *a, int64_t n)",
    "a points to n signed 64-bit integers, 0 <= n <= 60. Return the number of indices i with "
    "0 <= i < n - 1 and a[i] > a[i+1] (signed comparison); the result is 0 when n < 2. Never read "
    "outside a[0..n-1] (reads are checked; when n == 0, a must not be read at all) and do not write "
    "to memory.",
    """int64_t count_desc(const int64_t *a, int64_t n) {
        int64_t c = 0;
        for (int64_t i = 0; i + 1 < n; i++) c += a[i] > a[i + 1];
        return c;
    }""",
    2, inputs=_array_cases("count_desc", 307, _signed_arrays(317, [[1, 0], [0, 1], [-1, 1], [1, -1]])),
    mem=True, round=ROUND,
)  # fmt: skip


# -- 8. fill_down: n = 0 must write nothing ------------------------------------------------------


def _fill_cases() -> tuple:
    rng = random.Random(308)
    specs = [(0, 0), (0, 5), (0, INT64_MIN), (1, 7), (1, INT64_MIN), (2, INT64_MIN + 1), (3, INT64_MIN + 1),
             (5, 0), (8, 3), (64, INT64_MIN + 10), (100, -50), (17, INT64_MAX)]
    specs += [(rng.randint(0, 100), rng.randint(INT64_MIN, INT64_MAX)) for _ in range(20)]
    cases = []
    for i, (n, start) in enumerate(specs):
        lead = 8 * (1 + i % 2)
        mem = rng.randbytes(lead + 8 * n + 16)
        cases.append(MemCase(mem, (Ptr(lead), n, start), ((lead, lead + 8 * n),), (),
                             f"fill_down(a=<room for {n}>, n={n}, start={start})"))
    return tuple(cases)


FILL_DOWN = Task(
    "fill_down", "int64_t fill_down(int64_t *a, int64_t n, int64_t start)",
    "a points to room for n signed 64-bit integers, 0 <= n <= 100. Set a[i] = start - i for "
    "i = 0 .. n-1 (wrapping 64-bit arithmetic) and return n. Write only a[0..n-1] (writes are "
    "checked; when n == 0 nothing may be written) and do not read any memory through a.",
    """int64_t fill_down(int64_t *a, int64_t n, int64_t start) {
        for (int64_t i = 0; i < n; i++) a[i] = (int64_t)((uint64_t)start - (uint64_t)i);
        return n;
    }""",
    3, inputs=_fill_cases(), mem=True, round=ROUND,
    ir_hint="Store with `storel %v, %p`.",
)  # fmt: skip


# -- 9. map_into: loop state across a hostile callback --------------------------------------------


# map_into's callback has fixed points (f(x) == x whenever x is a multiple of 3), so the count of
# dst[i] < src[i] differs from dst[i] <= src[i]; with the default 3x + 1 (never equal to x) a `<=`
# answer would pass unnoticed.
MAP_INTO_F = callback_address("fix3")


def _map_into_cases() -> tuple:
    rng = random.Random(309)
    arrays = [[], [0], [1], [-1], [3], [-3], [INT64_MAX], [INT64_MIN], [INT64_MAX - 1], [INT64_MIN + 2],
              [1, -1, 2, -2], list(range(-6, 7)),
              [INT64_MAX, INT64_MIN, 0, -1, 1, 0x5555555555555555, -0x5555555555555556], [9] * 64]
    for _ in range(20):
        n = rng.randint(0, 64)
        arrays.append([rng.choice([rng.randint(-1000, 1000), rng.randint(INT64_MIN, INT64_MAX)]) for _ in range(n)])
    cases = []
    for i, src in enumerate(arrays):
        n = len(src)
        s = 8 * (1 + i % 2)
        d = s + 8 * n + 16 + 8 * (i % 3)
        mem = bytearray(rng.randbytes(d + 8 * n + 16))
        mem[s:s + 8 * n] = _pack(src)
        cases.append(MemCase(bytes(mem), (Ptr(d), Ptr(s), n, MAP_INTO_F), ((d, d + 8 * n),),
                             ((s, s + 8 * n), (d, d + 8 * n)), f"map_into(dst, src={_show(src)}, n={n}, f)"))
    return tuple(cases)


MAP_INTO = Task(
    "map_into", "int64_t map_into(int64_t *dst, const int64_t *src, int64_t n, int64_t (*f)(int64_t))",
    "src and dst each point to n signed 64-bit integers (0 <= n <= 64; the two arrays do not "
    "overlap). f points to a function `int64_t f(int64_t)` that follows the same ABI. For i = 0 .. "
    "n-1, in order, set dst[i] = f(src[i]), calling f exactly once per element. Return the number of "
    "indices i for which dst[i] < src[i] (signed comparison); 0 when n = 0. " + _CALLBACK_TEXT +
    " Read only src[0..n-1] and dst[0..n-1], and write only dst[0..n-1] (both are checked).",
    """static int64_t f_ref(int64_t x) { return x % 3 == 0 ? x : (int64_t)((uint64_t)x * 3u + 1u); }
    int64_t map_into(int64_t *dst, const int64_t *src, int64_t n, int64_t (*f)(int64_t)) {
        int64_t c = 0;
        for (int64_t i = 0; i < n; i++) { dst[i] = f(src[i]); c += dst[i] < src[i]; }
        return c;
    }
    int64_t map_into_ref(int64_t *dst, const int64_t *src, int64_t n, int64_t unused) {
        (void)unused; return map_into(dst, src, n, f_ref);
    }""",
    4, inputs=_map_into_cases(), mem=True, ref_name="map_into_ref", calls="callback", round=ROUND,
    ir_hint=_CALLBACK_IR_HINT,
)  # fmt: skip


# -- 10. stats_f: more live values than callee-saved registers (x86-64) ---------------------------


def _stats_cases() -> tuple:
    rng = random.Random(310)
    arrays = [[0], [1], [-1], [INT64_MAX], [INT64_MIN], [2, 4, 6], [1, 3, 5], list(range(-5, 6)),
              [INT64_MAX, INT64_MIN, 0, -1, 1, 0x5555555555555555, -0x5555555555555556], [7] * 64]
    for _ in range(20):
        n = rng.randint(1, 64)
        arrays.append([rng.choice([rng.randint(-1000, 1000), rng.randint(INT64_MIN, INT64_MAX)]) for _ in range(n)])
    cases = []
    for i, arr in enumerate(arrays):
        n = len(arr)
        s = 8 * (1 + i % 2)
        o = s + 8 * n + 8 * (1 + i % 3)
        mem = bytearray(rng.randbytes(o + 24 + 16))
        mem[s:s + 8 * n] = _pack(arr)
        cases.append(MemCase(bytes(mem), (Ptr(s), n, CALLBACK_BASE, Ptr(o)), ((o, o + 24),),
                             ((s, s + 8 * n), (o, o + 24)), f"stats_f(a={_show(arr)}, n={n}, f, out)"))
    return tuple(cases)


STATS_F = Task(
    "stats_f", "int64_t stats_f(const int64_t *a, int64_t n, int64_t (*f)(int64_t), int64_t *out)",
    "a points to n signed 64-bit integers (1 <= n <= 64) and out points to 3 int64_t slots. f points "
    "to a function `int64_t f(int64_t)` that follows the same ABI. Let y[i] = f(a[i]), calling f "
    "exactly once per element, in order. Store the minimum of the y[i] (signed) to out[0], their "
    "maximum (signed) to out[1] and their sum (wrapping 64-bit) to out[2], and return the number of "
    "i for which y[i] is odd. " + _CALLBACK_TEXT + " Read only a[0..n-1] and out[0..2], and write "
    "only out[0..2] (both are checked).",
    """static int64_t f_ref(int64_t x) { return (int64_t)((uint64_t)x * 3u + 1u); }
    int64_t stats_f(const int64_t *a, int64_t n, int64_t (*f)(int64_t), int64_t *out) {
        int64_t mn = INT64_MAX, mx = INT64_MIN, odd = 0; uint64_t sum = 0;
        for (int64_t i = 0; i < n; i++) {
            int64_t y = f(a[i]);
            if (y < mn) mn = y;
            if (y > mx) mx = y;
            sum += (uint64_t)y; odd += y & 1;
        }
        out[0] = mn; out[1] = mx; out[2] = (int64_t)sum;
        return odd;
    }
    int64_t stats_f_ref(const int64_t *a, int64_t n, int64_t unused, int64_t *out) {
        (void)unused; return stats_f(a, n, f_ref, out);
    }""",
    4, inputs=_stats_cases(), mem=True, ref_name="stats_f_ref", calls="callback", round=ROUND,
    ir_hint=_CALLBACK_IR_HINT + " Store with `storel %v, %p`.",
)  # fmt: skip


TASKS: tuple[Task, ...] = (
    COUNT_UGT, MAX_BYTE, MADD32, SDIVREM, SHIFT_SAT, BMIN_EMPTY, COUNT_DESC, FILL_DOWN, MAP_INTO, STATS_F,
)
BY_NAME = {t.name: t for t in TASKS}


# -- bait metadata ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bait:
    pitfall: str  # the low-level fact the task turns on
    mistake: str  # the tempting shortcut
    caught_as: tuple[str, ...]  # attempt categories the shortcut lands in
    isas: tuple[str, ...] = ("x86_64", "aarch64")  # where the pitfall exists


BAITS: dict[str, Bait] = {
    "count_ugt": Bait("unsigned vs signed comparison; values straddle 2^63",
                      "signed compare (jg/jle, b.gt/b.le) instead of unsigned (ja/jbe, b.hi/b.ls)",
                      ("wrong_result",)),
    "max_byte": Bait("x86 8-bit register writes do not zero-extend; bytes are unsigned",
                     "accumulate in al/dl with rax's upper bits left as garbage; or movsx/ldrsb + signed "
                     "compare", ("wrong_result",)),
    "madd32": Bait("x86 32-bit ops and AArch64 w-register writes ZERO-extend; the spec needs SIGN extension",
                   "return eax/w0 directly without movsxd/sxtw", ("wrong_result",)),
    "sdivrem": Bait("idiv traps on b == 0 and INT64_MIN / -1 (sdiv does not); C remainder truncates",
                    "bare cqo/idiv (x86 crash); floor-style remainder fix-up (wrong result on both)",
                    ("crash", "wrong_result")),
    "shift_sat": Bait("hardware shifts use the count mod 64; the spec defines counts >= 64",
                      "a single shl/sar (lsl/asr) by n", ("wrong_result",)),
    "bmin_empty": Bait("n == 0 must read nothing", "initialise the minimum from a[0] before testing n",
                       ("oob_read",)),
    "count_desc": Bait("adjacent pairs end at a[n-1]; n - 1 underflows for n == 0",
                       "loop i < n reading a[i+1] (reads a[n]); unsigned n-1 bound", ("oob_read",)),
    "fill_down": Bait("n == 0 must write nothing", "do-while loop that stores a[0] before testing n", ("oob",)),
    "map_into": Bait("caller-saved registers and flags do not survive a call; the stack must be 16-byte "
                     "aligned at the call", "loop counter in rcx/r11 (x86) or x9/x18 (AArch64); an even "
                     "number of pushes before `call`", ("wrong_result", "misaligned_call")),
    "stats_f": Bait("x86-64 has 6 callee-saved registers but 8 values live across the call; the red zone is "
                    "not preserved across a call, and x18 is caller-saved on Linux",
                    "keep the spill-over below rsp (red zone) or in x18 across the call",
                    ("wrong_result", "misaligned_call")),
}


# -- Part B: prompt variants -------------------------------------------------------------------


@dataclass(frozen=True)
class Hint:
    """A plausible-but-WRONG tip. `contradicted_by` is text of the unmodified prompt that
    shows the tip is wrong, so the correct answer stays determinable."""

    text: str
    claim: str  # what is wrong about it
    contradicted_by: str
    misleading: bool = True
    # The tip is about register/stack ABI rules only; on the ir path QBE does the register
    # allocation and calling convention, so such a tip cannot mislead there (it is inert).
    abi_only: bool = False


_X86_RZ = "Anything below rsp may be overwritten by a callee."
_A64_BELOW = "A call clobbers the caller-saved registers x0-x18"
_X86_ALIGN = "rsp must be 16-byte aligned at every call instruction"
_A64_X30 = "A call (bl/blr) overwrites x30, so save x30"

MISLEADING_HINTS: dict[str, dict[str, Hint]] = {
    # Keyed by task name, or by the task's `calls` kind as the fallback for tasks without their
    # own: "callback" (calls a function pointer), "self" (recursive), "leaf" (calls nothing).
    "callback": {
        "x86_64": Hint("Tip: the stack pointer is already 16-byte aligned when your function is entered, "
                       "so you can call f without adjusting rsp.",
                       "rsp is 8 mod 16 at entry, not 16-byte aligned",
                       "rsp is 16-byte aligned before the call, so it is 8 mod 16 at entry", abi_only=True),
        "aarch64": Hint("Tip: x18 is not used by ordinary Linux code, so it is a convenient spare register "
                        "for a value that has to survive the call to f.",
                        "x18 is caller-saved on Linux and the callback clobbers it", _A64_BELOW, abi_only=True),
    },
    "self": {
        "x86_64": Hint("Tip: the stack-alignment rule only matters when calling library code that uses SSE; "
                       "a recursive call to your own function can be made with rsp at any alignment.",
                       "the alignment rule holds at every call, recursive ones included (and it is checked)",
                       _X86_ALIGN, abi_only=True),
        "aarch64": Hint("Tip: a recursive bl to your own entry label comes straight back into the same "
                        "function, so x30 does not need to be saved across it; ret still finds the right "
                        "return address.",
                        "bl overwrites x30; without saving it the outer ret returns into the function itself",
                        _A64_X30, abi_only=True),
    },
    "stats_f": {
        "x86_64": Hint("Tip: the System V ABI gives every function a 128-byte red zone below rsp, so when you "
                       "run out of callee-saved registers you can keep values at [rsp - 8], [rsp - 16], ... "
                       "across the call to f.",
                       "the red zone is only safe in leaf code; a callee may overwrite it", _X86_RZ, abi_only=True),
        "aarch64": Hint("Tip: x18 is not used by ordinary Linux code, so it is a convenient spare register "
                        "for a value that has to survive the call to f.",
                        "x18 is caller-saved on Linux and the callback clobbers it", _A64_BELOW, abi_only=True),
    },
    "leaf": {
        "x86_64": Hint("Tip: this is a leaf function (it calls nothing), so callee-saved registers such as "
                       "rbx and r12-r15 need not be preserved.",
                       "callee-saved registers must be preserved by every function, leaf or not",
                       "Callee-saved: rbx, rbp, r12, r13, r14, r15 (restore them if you use them)", abi_only=True),
        "aarch64": Hint("Tip: this is a leaf function (it calls nothing), so callee-saved registers such as "
                        "x19-x28 need not be preserved.",
                        "callee-saved registers must be preserved by every function, leaf or not",
                        "Callee-saved: x19-x28, x29 (frame pointer) and d8-d15 (restore them if you use them)",
                        abi_only=True),
    },
    "sdivrem": {
        "x86_64": Hint("Tip: idiv already returns the C quotient and remainder for every input, so no special "
                       "cases are needed.",
                       "idiv traps (#DE) for b == 0 and for INT64_MIN / -1",
                       "they must return normally, not trap"),
        "aarch64": Hint("Tip: the C remainder is always non-negative when b > 0, so fix up a negative remainder "
                        "by adding b.",
                        "C's remainder takes the sign of the dividend", "has the sign of a"),
    },
    "shift_sat": {
        "x86_64": Hint("Tip: shl and sar handle any shift count directly, so no special case is needed for "
                       "large n.", "x86 shifts use the count mod 64", "So for n >= 64: x << n is 0"),
        "aarch64": Hint("Tip: lsl and asr handle any shift count directly, so no special case is needed for "
                        "large n.", "AArch64 register shifts use the count mod 64", "So for n >= 64: x << n is 0"),
    },
    "madd32": {
        "x86_64": Hint("Tip: writing a 32-bit register such as eax already extends the value to 64 bits, so "
                       "the 32-bit result can be returned as it is.",
                       "32-bit writes zero-extend; the spec needs sign extension",
                       "return it sign-extended to int64_t"),
        "aarch64": Hint("Tip: writing a w register already extends the value to 64 bits, so the 32-bit "
                        "result can be returned in w0 as it is.",
                        "w writes zero-extend; the spec needs sign extension", "return it sign-extended to int64_t"),
    },
    "count_ugt": {
        isa: Hint("Tip: the elements are 64-bit integers, so an ordinary signed compare-and-branch is fine.",
                  "values >= 2^63 compare wrongly as signed", "comparing as unsigned 64-bit values")
        for isa in ("x86_64", "aarch64")
    },
    "bmin_empty": {
        isa: Hint("Tip: callers never pass an empty array, so load m = a[0] straight away and scan from "
                  "index 1; testing n first is a wasted branch.",
                  "n may be 0 (0 <= n <= 60), and then a[0] must not be read",
                  "when n == 0, a must not be read at all")
        for isa in ("x86_64", "aarch64")
    },
    "fill_down": {
        isa: Hint("Tip: n is never 0 in practice, so a bottom-tested (do-while) loop that stores a[0] "
                  "before its first test of the counter is shortest; no separate check of n is needed.",
                  "n may be 0 (0 <= n <= 100), and then nothing may be written",
                  "when n == 0 nothing may be written")
        for isa in ("x86_64", "aarch64")
    },
}


def misleading_hint_for(task: Task, isa: str) -> Hint:
    """The task's own hint if it has one, else the fallback for its `calls` kind: "callback",
    "self" (recursive tasks, which must never get the "leaf, calls nothing" tip) or "leaf"."""
    if task.name in MISLEADING_HINTS:
        key = task.name
    elif task.calls in ("callback", "self"):
        key = task.calls
    elif task.calls:
        raise ValueError(f"{task.name}: no misleading hint for calls={task.calls!r}")
    else:
        key = "leaf"
    return MISLEADING_HINTS[key][isa]


def _append(prompt: str, text: str) -> str:
    return prompt.rstrip("\n") + "\n\n" + text.strip() + "\n"


def baseline(prompt: str, path: str, isa: str, task: Task) -> str:
    return prompt


TERSE = ("Keep it as short as possible: the fewest instructions you can manage, no comments, no "
         "defensive checks that are not strictly needed.")


def terse(prompt: str, path: str, isa: str, task: Task) -> str:
    return _append(prompt, TERSE)


RUSHED = ("Answer immediately. Do not deliberate, plan or double-check; write down the first version "
          "that comes to mind.")


def rushed(prompt: str, path: str, isa: str, task: Task) -> str:
    return _append(prompt, RUSHED)


def misleading_hint(prompt: str, path: str, isa: str, task: Task) -> str:
    return _append(prompt, misleading_hint_for(task, isa).text)


_DISTRACTOR_PARAS = (
    "Background (release notes of an unrelated project, kept here for reference): version 4.2 of the "
    "report generator moves the page-layout engine to a constraint solver. Tables that span pages now "
    "repeat their header row, footnotes are numbered per chapter instead of per document, and the "
    "default serif font changed from 10.5 pt to 11 pt. The PDF writer embeds only the glyph subsets "
    "that are used, which reduces a typical 300-page manual from 9.1 MB to 2.4 MB.",
    "The configuration loader accepts TOML in addition to YAML. Keys are case-insensitive, and unknown "
    "keys produce a warning rather than an error, so older configuration files keep working. The "
    "`--watch` option re-renders on change with a 250 ms debounce; on network file systems it falls "
    "back to polling every two seconds.",
    "Localisation: date formats follow the CLDR data shipped with the build; right-to-left scripts are "
    "laid out with the bidi algorithm from Unicode 15; hyphenation dictionaries are loaded lazily. "
    "Known issue: widow/orphan control can move a figure caption to the next page when a table "
    "immediately precedes it; the workaround is to set `keep_with_next = false` on the table.",
    "Performance: the median render time of the benchmark corpus fell from 3.8 s to 2.9 s. Most of the "
    "gain comes from caching shaped text runs between layout passes; memory use rose by about 6 %. "
    "Parallel rendering is still experimental and disabled by default.",
)
DISTRACTOR = "\n\n".join(_DISTRACTOR_PARAS * 2)


def distractor(prompt: str, path: str, isa: str, task: Task) -> str:
    return _append(prompt, DISTRACTOR)


LEGACY_STYLE = {
    ("stream", "x86_64"): "House style: our team writes x86 assembly in AT&T syntax (`movq %rdi, %rax`, `$`-prefixed "
                          "immediates, `%`-prefixed registers, `;` for comments). Please follow the house style.",
    ("stream", "aarch64"): "House style: our team writes AArch64 in armasm style: `;` comments, and start with the "
                           "`AREA |.text|, CODE, READONLY` and `EXPORT` header lines. Please follow the house style.",
    ("structured", "x86_64"): "House style: present the answer as a Markdown ```json block and then explain the "
                              "algorithm in a short paragraph after it.",
    ("structured", "aarch64"): "House style: present the answer as a Markdown ```json block and then explain the "
                               "algorithm in a short paragraph after it.",
    ("ir", "x86_64"): "House style: we write QBE with `//` comments and LLVM-style block labels (`entry:` with a "
                      "trailing colon instead of `@entry`). Please follow the house style.",
    ("ir", "aarch64"): "House style: we write QBE with `//` comments and LLVM-style block labels (`entry:` with a "
                       "trailing colon instead of `@entry`). Please follow the house style.",
}


def legacy_style(prompt: str, path: str, isa: str, task: Task) -> str:
    return _append(prompt, LEGACY_STYLE[(path, isa)])


Variant = Callable[[str, str, str, Task], str]

from bca.liveeval.assist import assisted  # noqa: E402 - the information variant (bca/liveeval/assist.py)

VARIANTS: dict[str, Variant] = {
    "baseline": baseline,
    "terse": terse,
    "rushed": rushed,
    "misleading_hint": misleading_hint,
    "distractor": distractor,
    "legacy_style": legacy_style,
    "assisted": assisted,
}

VARIANT_INFO: dict[str, dict] = {
    "baseline": {"description": "the unmodified prompt", "misleading": False, "conflicts_with_format": False},
    "terse": {"description": "demands the shortest code, no comments, no defensive checks",
              "misleading": False, "conflicts_with_format": False},
    "rushed": {"description": "tells the model to answer at once without deliberating",
               "misleading": False, "conflicts_with_format": False},
    "misleading_hint": {"description": "appends a plausible but WRONG tip (MISLEADING_HINTS); the correct ABI "
                                       "text and specification above it are unchanged",
                        "misleading": True, "conflicts_with_format": False,
                        "note": "tips marked abi_only (register/stack ABI rules: the leaf, self, callback and "
                                "stats_f tips) are inert on the ir path, where QBE allocates registers and "
                                "implements the calling convention; there the variant measures nothing"},
    "distractor": {"description": f"appends {len(DISTRACTOR)} characters of irrelevant release notes",
                   "misleading": False, "conflicts_with_format": False},
    # A format-compliance probe, not a coding measurement: on stream and ir a model that follows
    # the house style is rejected by construction (so a compliant model always "fails"), and a
    # repair round only repeats the conflict. Hence one attempt, and not part of `--variant all`.
    "legacy_style": {"description": "asks for a conflicting 'house style' (AT&T, armasm, fenced JSON with prose, "
                                    "LLVM-style labels); measures format compliance, not coding ability. The "
                                    "stream and IR requests are rejected by the pipeline (syntax / compile), so "
                                    "following them fails by construction; the structured one is tolerated (the "
                                    "fence is stripped and the prose after it ignored). One attempt; excluded "
                                    "from 'all' (ask for it by name)",
                     "misleading": False, "conflicts_with_format": True, "max_attempts": 1},
    # Not an adversarial probe: more information for the model (bca/liveeval/assist.py). Opt-in, so
    # `--variant all` and every existing plan are unchanged.
    "assisted": {"description": "adds path/ISA notes (AArch64 constants, x86 addressing, a QBE cheat sheet, "
                                "structured examples, output discipline) and, on repairs, explanations of the "
                                "failure and advisory lints of the previous code; acceptance, tests and limits "
                                "are baseline's",
                 "misleading": False, "conflicts_with_format": False, "opt_in": True},
}

# What `--variant all` expands to: every variant except the format-compliance probes
# (conflicts_with_format) and the opt-in ones (assisted), which must be requested by name.
ALL_VARIANTS_DEFAULT: tuple[str, ...] = tuple(
    k for k in VARIANTS if not VARIANT_INFO[k]["conflicts_with_format"] and not VARIANT_INFO[k].get("opt_in"))

# Recommended subset for a live run (cost): baseline plus the two most informative variants.
RECOMMENDED_VARIANTS = ("baseline", "misleading_hint", "terse")


def feasible_cells(paths, isas) -> list[tuple[Task, str, str]]:
    return [(t, p, i) for t in TASKS for p in paths for i in isas if p not in t.na_paths]


__all__ = [
    "ALL_VARIANTS_DEFAULT", "BAITS", "BY_NAME", "Bait", "Hint", "MAP_INTO_F", "MISLEADING_HINTS",
    "RECOMMENDED_VARIANTS", "TASKS", "VARIANTS",
    "VARIANT_INFO", "feasible_cells", "misleading_hint_for",
]
