"""Round-2 tasks: harder kernels chosen to exercise the safety net.

Round 1 (bca.liveeval.tasks) had only leaf functions without stack frames, so the ABI checks,
the out-of-bounds guards and the stream abort never met a real model mistake. Every task
here needs at least one of: a stack frame and callee-saved registers (recursion, calls
through a hostile callback), stack-passed arguments, SIMD, bounded reads (read guard),
overlapping writes (write guard), 32-bit arithmetic, flags, or full-range unsigned edge
cases. Each task has a C reference (host-compiled, -fwrapv) and deterministic, bounded
inputs; memory tasks use `difftest.MemCase` (checked with `memory_differential`: return
value + the whole memory block, write guard, and a read guard where the spec bounds reads).

Cells that a pipeline cannot express are marked in `Task.na` with the reason.
"""

from __future__ import annotations

import random

from bca.difftest import INT64_MAX, INT64_MIN, MemCase, Ptr, edge_values, int_inputs
from bca.emu import CALLBACK_BASE, MASK64
from bca.liveeval.tasks import TASKS as ROUND1
from bca.liveeval.tasks import Task

U64 = 1 << 64


def _s64(v: int) -> int:
    v &= MASK64
    return v - U64 if v >> 63 else v


def _pack(values, size: int = 8) -> bytes:
    return b"".join((v & ((1 << (8 * size)) - 1)).to_bytes(size, "little") for v in values)


def _junk(rng: random.Random, n: int) -> bytes:
    return rng.randbytes(n)


def _show(xs, k: int = 8) -> str:
    xs = list(xs)
    return "[" + ", ".join(map(str, xs[:k])) + (f", ... ({len(xs)} elements)" if len(xs) > k else "") + "]"


# -- 1. fib_rec ------------------------------------------------------------------------------

FIB_REC = Task(
    "fib_rec", "int64_t fib_rec(int64_t n)",
    "Return the n-th Fibonacci number: fib_rec(0) = 0, fib_rec(1) = 1, and for n >= 2 "
    "fib_rec(n) = fib_rec(n-1) + fib_rec(n-2). 0 <= n <= 20. The implementation MUST be the naive "
    "doubly recursive one: for n >= 2 the function calls itself twice, with a real call "
    "instruction to its own entry point, and adds the two results (no loop, no memoisation; "
    "the machine code is checked for a call to its own entry). Every recursive call is a "
    "normal ABI call and is checked as one: the stack must be 16-byte aligned at each call, "
    "and any callee-saved register you use must be saved and restored.",
    """int64_t fib_rec(int64_t n) { return n < 2 ? n : fib_rec(n - 1) + fib_rec(n - 2); }""",
    1, inputs=tuple((n,) for n in range(21)),
    requires=("recursion",), calls="self", max_insns=5_000_000, round=2,
    ir_hint="Call the function itself with `%r =l call $fib_rec(l %x)`.",
)  # fmt: skip


# -- 2. map_sum (hostile callback) -----------------------------------------------------------


def _map_sum_cases() -> tuple:
    rng = random.Random(201)
    bufs = [[], [0], [1], [-1], [INT64_MAX], [INT64_MIN], [1, 2, 3], [INT64_MAX, INT64_MIN, 0, -1],
            list(range(-8, 9)), [7] * 64]
    for _ in range(40):
        n = rng.randint(0, 64)
        bufs.append([rng.choice([rng.randint(-1000, 1000), rng.randint(INT64_MIN, INT64_MAX)])
                     for _ in range(n)])
    cases = []
    for i, b in enumerate(bufs):
        lead = 8 * (i % 3)  # a is 8-byte aligned, sometimes not 16-byte aligned
        mem = _junk(rng, lead) + _pack(b) + _junk(rng, 16)
        cases.append(MemCase(mem, (Ptr(lead), len(b), CALLBACK_BASE), (), ((lead, lead + 8 * len(b)),),
                             f"map_sum(a={_show(b)}, n={len(b)}, f)"))
    return tuple(cases)


MAP_SUM = Task(
    "map_sum", "int64_t map_sum(const int64_t *a, int64_t n, int64_t (*f)(int64_t))",
    "a points to n signed 64-bit integers (0 <= n <= 64); f points to a function "
    "`int64_t f(int64_t)` that follows the same ABI. Return f(a[0]) + f(a[1]) + ... + "
    "f(a[n-1]) with wrapping 64-bit arithmetic (0 when n = 0). Call f exactly once per "
    "element, in order; f is opaque (you do not know what it computes). f may clobber every "
    "caller-saved register, the flags and the stack below the stack pointer, and it checks "
    "that the stack is 16-byte aligned at each call. Do not read outside a[0..n-1] and do not "
    "write to memory through a.",
    """static int64_t f_ref(int64_t x) { return (int64_t)((uint64_t)x * 3u + 1u); }
    int64_t map_sum(const int64_t *a, int64_t n, int64_t (*f)(int64_t)) {
        uint64_t s = 0;
        for (int64_t i = 0; i < n; i++) s += (uint64_t)f(a[i]);
        return (int64_t)s;
    }
    int64_t map_sum_ref(const int64_t *a, int64_t n, int64_t unused) { (void)unused; return map_sum(a, n, f_ref); }""",
    3, inputs=_map_sum_cases(), mem=True, ref_name="map_sum_ref", calls="callback", round=2,
    ir_hint="Call through the pointer with `%r =l call %f(l %x)` (an indirect call through a temporary).",
)  # fmt: skip


# -- 3. sum8 (stack-passed arguments on x86-64) -------------------------------------------------

SUM8 = Task(
    "sum8",
    "int64_t sum8(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f, int64_t g, int64_t h)",
    "Return a - 2*b + 3*c - 4*d + 5*e - 6*f + 7*g - 8*h, computed with wrapping 64-bit "
    "(two's complement) arithmetic. All eight arguments may be any int64_t value.",
    """int64_t sum8(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f, int64_t g, int64_t h) {
        return a - 2*b + 3*c - 4*d + 5*e - 6*f + 7*g - 8*h;
    }""",
    8, inputs=tuple(dict.fromkeys(int_inputs(8, 60, seed=203, edges=[0, 1, -1, 2, INT64_MAX, INT64_MIN,
                                                                      2**31, -(2**31) - 1]))),
    round=2,
)  # fmt: skip


# -- 4. dot_i32_simd ---------------------------------------------------------------------------


def _dot_cases() -> tuple:
    rng = random.Random(204)
    lens = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 100, 127, 128, 255,
            999, 1000] + [rng.randint(0, 1000) for _ in range(10)]
    i32 = (-(2**31), 2**31 - 1)
    cases = []
    for i, n in enumerate(lens):
        mode = i % 3
        def val():
            if mode == 0:
                return rng.randint(-100, 100)
            if mode == 1 or rng.random() < 0.3:
                return rng.choice([i32[0], i32[1], -1, 0, 1])
            return rng.randint(*i32)
        a = [val() for _ in range(n)]
        b = [val() for _ in range(n)]
        off_a = 4 * (i % 4)  # only 4-byte aligned
        off_b = ((off_a + 4 * n + 15) & ~15) + 16 + 4 * ((i + 1) % 4)
        mem = bytearray(_junk(rng, off_b + 4 * n + 32))
        mem[off_a:off_a + 4 * n] = _pack(a, 4)
        mem[off_b:off_b + 4 * n] = _pack(b, 4)
        cases.append(MemCase(bytes(mem), (Ptr(off_a), Ptr(off_b), n), (),
                             ((off_a, off_a + 4 * n), (off_b, off_b + 4 * n)),
                             f"dot_i32_simd(a={_show(a)}, b={_show(b)}, n={n}) "
                             f"[a % 16 = {off_a % 16}, b % 16 = {off_b % 16}]"))
    return tuple(cases)


_NO_SIMD = (
    ("structured", "neither structured encoder (x86-64 iced / AArch64 bit encoder) has vector "
                   "instructions or registers in its schema"),
    ("ir", "QBE IL has no vector types or SIMD operations"),
)

DOT_I32_SIMD = Task(
    "dot_i32_simd", "int64_t dot_i32_simd(const int32_t *a, const int32_t *b, int64_t n)",
    "a and b each point to n signed 32-bit integers (0 <= n <= 1000; n need not be a multiple "
    "of 4 or 8). Return the sum over i of (int64_t)a[i] * (int64_t)b[i], with wrapping 64-bit "
    "arithmetic for the sum. a and b are only 4-byte aligned (not 16). The implementation MUST "
    "use SIMD vector arithmetic in its main loop (x86-64: SSE2/SSSE3/SSE4.1 on xmm registers, "
    "e.g. pmovsxdq/pmuldq/paddq; AArch64: NEON on v registers, e.g. smull/smlal), several "
    "elements per iteration, plus a scalar loop for the remaining elements; the machine code is "
    "checked for vector arithmetic instructions. No AVX. Do not read outside a[0..n-1] and "
    "b[0..n-1] (not even with a vector load that runs past the end) and do not write to memory.",
    """int64_t dot_i32_simd(const int32_t *a, const int32_t *b, int64_t n) {
        int64_t s = 0;
        for (int64_t i = 0; i < n; i++) s += (int64_t)a[i] * (int64_t)b[i];
        return s;
    }""",
    3, inputs=_dot_cases(), mem=True, requires=("simd",), na=_NO_SIMD, round=2, mem_elem=4,
)  # fmt: skip


# -- 5. binary_search (read guard) ------------------------------------------------------------


def _bsearch_cases() -> tuple:
    rng = random.Random(205)
    cases = []

    def add(arr, key):
        mem = _junk(rng, 16) + _pack(arr) + _junk(rng, 16)
        cases.append(MemCase(mem, (Ptr(16), len(arr), key), (), ((16, 16 + 8 * len(arr)),),
                             f"binary_search(a={_show(arr)}, n={len(arr)}, key={key})"))

    add([], 0)
    add([], 5)
    add([5], 5)
    add([5], 4)
    add([5], 6)
    add([INT64_MIN, INT64_MAX], INT64_MAX)
    add([INT64_MIN, INT64_MAX], INT64_MIN)
    add([INT64_MIN, 0, INT64_MAX], 1)
    add([INT64_MIN + 1, -1, 0, INT64_MAX - 1], INT64_MAX)
    add([INT64_MIN + 1, -1, 0, INT64_MAX - 1], INT64_MIN)
    for _ in range(50):
        n = rng.choice([rng.randint(1, 8), rng.randint(1, 200)])
        wide = rng.random() < 0.3
        vals = sorted(set(rng.randint(INT64_MIN, INT64_MAX) if wide else rng.randint(-10**4, 10**4)
                          for _ in range(n)))
        r = rng.random()
        if r < 0.5:
            key = vals[rng.choice([0, len(vals) - 1, rng.randrange(len(vals))])]
        elif r < 0.65:
            key = vals[0] - 1 if vals[0] > INT64_MIN else vals[-1] + 1
        elif r < 0.8:
            key = vals[-1] + 1 if vals[-1] < INT64_MAX else vals[0] - 1
        else:
            key = rng.randint(vals[0], vals[-1])
        add(vals, key)
    return tuple(cases)


BINARY_SEARCH = Task(
    "binary_search", "int64_t binary_search(const int64_t *a, int64_t n, int64_t key)",
    "a points to n signed 64-bit integers sorted in strictly increasing order (0 <= n <= 200; "
    "values may be anywhere in the int64_t range, including INT64_MIN and INT64_MAX). Return "
    "the index i with a[i] == key, or -1 if key is not present. Use binary search (O(log n) "
    "element reads). Never read outside a[0..n-1] (reads are checked; n may be 0, in which "
    "case a must not be read at all) and do not write to memory.",
    """int64_t binary_search(const int64_t *a, int64_t n, int64_t key) {
        int64_t lo = 0, hi = n - 1;
        while (lo <= hi) {
            int64_t mid = lo + (hi - lo) / 2;
            if (a[mid] == key) return mid;
            if (a[mid] < key) lo = mid + 1; else hi = mid - 1;
        }
        return -1;
    }""",
    3, inputs=_bsearch_cases(), mem=True, round=2,
)  # fmt: skip


# -- 6. memmove_i64 (overlap, write guard) ---------------------------------------------------


def _memmove_cases() -> tuple:
    rng = random.Random(206)
    specs = [(0, 0, 0), (0, 3, 0), (3, 0, 0), (0, 0, 5), (0, 1, 1), (1, 0, 1), (0, 1, 10),
             (1, 0, 10), (0, 5, 3), (5, 0, 3), (2, 2, 7), (0, 1, 100), (1, 0, 100), (0, 50, 60),
             (50, 0, 60), (0, 64, 64), (64, 0, 64)]
    for _ in range(30):
        n = rng.randint(0, 100)
        d = rng.choice([rng.randint(-8, 8), rng.randint(-n - 4, n + 4)])
        specs.append((max(0, -d), max(0, d), n))  # (src, dst) element offsets
    cases = []
    for s, d, n in specs:
        length = max(s, d) + n + 2
        vals = [rng.randint(INT64_MIN, INT64_MAX) if rng.random() < 0.3 else rng.randint(-99, 99)
                for _ in range(length)]
        lead = 8
        mem = _junk(rng, lead) + _pack(vals) + _junk(rng, 16)
        so, do = lead + 8 * s, lead + 8 * d
        reads = tuple(sorted({(so, so + 8 * n), (do, do + 8 * n)}))
        kind = "overlap, dst after src" if s < d < s + n else "overlap, dst before src" if d < s < d + n else \
            "same" if s == d else "disjoint"
        cases.append(MemCase(mem, (Ptr(do), Ptr(so), n), ((do, do + 8 * n),), reads,
                             f"memmove_i64(dst=buf+{d}, src=buf+{s}, n={n}) [{kind}; buf={_show(vals)}]"))
    return tuple(cases)


MEMMOVE_I64 = Task(
    "memmove_i64", "int64_t memmove_i64(int64_t *dst, const int64_t *src, int64_t n)",
    "Copy n 64-bit elements from src to dst and return 0 (0 <= n <= 100). The two ranges may "
    "overlap in either direction (dst before or after src, by any number of elements); the "
    "result must be as if src[0..n-1] were first copied to a temporary array and then to "
    "dst[0..n-1] (like memmove). Write only dst[0..n-1] (writes are checked); read only "
    "src[0..n-1] and dst[0..n-1]. Do not call any function and do not use the stack for a "
    "temporary copy of the array.",
    """int64_t memmove_i64(int64_t *dst, const int64_t *src, int64_t n) {
        if (dst < src) { for (int64_t i = 0; i < n; i++) dst[i] = src[i]; }
        else { for (int64_t i = n - 1; i >= 0; i--) dst[i] = src[i]; }
        return 0;
    }""",
    3, inputs=_memmove_cases(), mem=True, round=2,
)  # fmt: skip


# -- 7. crc32 (bytes, 32-bit arithmetic) -----------------------------------------------------


def _crc_cases() -> tuple:
    rng = random.Random(207)
    datas = [b"", b"\x00", b"\xff", b"a", b"123456789", b"\x00" * 16, b"\xff" * 33,
             bytes(range(256)), b"The quick brown fox jumps over the lazy dog"]
    datas += [rng.randbytes(rng.randint(0, 300)) for _ in range(25)]
    cases = []
    for i, d in enumerate(datas):
        off = 16 + i % 4
        mem = _junk(rng, off) + d + _junk(rng, 16)
        shown = d[:12].hex() + ("..." if len(d) > 12 else "")
        cases.append(MemCase(mem, (Ptr(off), len(d)), (), ((off, off + len(d)),),
                             f"crc32(p=bytes.fromhex('{shown}'), n={len(d)})" +
                             (f" [p = {d.decode()!r}]" if d.isascii() and d.isalnum() and d else "")))
    return tuple(cases)


CRC32 = Task(
    "crc32", "int64_t crc32(const uint8_t *p, int64_t n)",
    "Return the standard CRC-32 (as used by zlib/PNG/Ethernet) of the n bytes at p "
    "(0 <= n <= 300), as a value in [0, 2^32) (the upper 32 bits of the result must be 0). "
    "Compute it bitwise without a table: crc = 0xFFFFFFFF; for each byte: crc ^= byte; then 8 "
    "times: if (crc & 1) crc = (crc >> 1) ^ 0xEDB88320 else crc >>= 1; finally return crc ^ "
    "0xFFFFFFFF. All arithmetic is on unsigned 32-bit values. Example: the 9 bytes "
    "\"123456789\" give 0xCBF43926. Do not read outside p[0..n-1] and do not write to memory.",
    """int64_t crc32(const uint8_t *p, int64_t n) {
        uint32_t crc = 0xFFFFFFFFu;
        for (int64_t i = 0; i < n; i++) {
            crc ^= p[i];
            for (int k = 0; k < 8; k++) crc = (crc & 1) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
        }
        return (int64_t)(crc ^ 0xFFFFFFFFu);
    }""",
    2, inputs=_crc_cases(), mem=True, round=2, mem_elem=1, mem_signed=False,
    ir_hint="Load a byte zero-extended with `%b =w loadub %p` (or `=l loadub`); 32-bit "
            "operations use the `w` class (`%c =w xor %c, %b`, `%c =w shr %c, 1`); extend a `w` "
            "to `l` with `extuw`.",
)  # fmt: skip


# -- 8. mulhi_u64 and add_overflow (flags) --------------------------------------------------

_U_EDGES = [0, 1, 2, 3, -1, -2, INT64_MAX, INT64_MIN, 2**32 - 1, 2**32, 2**32 + 1, 2**31,
            0x5555555555555555, -0x5555555555555556]

MULHI_U64 = Task(
    "mulhi_u64", "uint64_t mulhi_u64(uint64_t a, uint64_t b)",
    "Return the high 64 bits of the full 128-bit product a * b, where a and b are unsigned "
    "64-bit values (any value from 0 to 2^64 - 1). Example: mulhi_u64(2^64 - 1, 2^64 - 1) = "
    "2^64 - 2.",
    """int64_t mulhi_u64(int64_t a, int64_t b) {
        return (int64_t)(uint64_t)(((unsigned __int128)(uint64_t)a * (uint64_t)b) >> 64);
    }""",
    2, inputs=tuple(int_inputs(2, 120, seed=208, edges=_U_EDGES)), round=2,
    ir_hint="QBE has no high-multiply instruction: build the 128-bit product from 32-bit halves "
            "(`and`, `shr` (logical), `mul`, `add`) with explicit carries.",
)  # fmt: skip


def _addov_cases() -> tuple:
    rng = random.Random(209)
    edges = [0, 1, -1, 2, INT64_MAX, INT64_MIN, INT64_MAX - 1, INT64_MIN + 1, 2**62, -(2**62)]
    pairs = list(int_inputs(2, 80, seed=210, edges=edges))
    cases = []
    for a, b in dict.fromkeys(pairs):
        mem = _junk(rng, 24)
        cases.append(MemCase(mem, (a, b, Ptr(8)), ((8, 16),), ((8, 16),), f"add_overflow(a={a}, b={b}, out)"))
    return tuple(cases)


ADD_OVERFLOW = Task(
    "add_overflow", "int64_t add_overflow(int64_t a, int64_t b, int64_t *out)",
    "Store the wrapped (two's complement, modulo 2^64) sum a + b to *out, then return 1 if the "
    "exact mathematical sum does not fit in int64_t (signed overflow), otherwise 0. Always "
    "store *out. Write only the 8 bytes at out; the only memory you may read is *out.",
    """int64_t add_overflow(int64_t a, int64_t b, int64_t *out) {
        int64_t s; int o = __builtin_add_overflow(a, b, &s); *out = s; return o;
    }""",
    3, inputs=_addov_cases(), mem=True, round=2,
    ir_hint="Store with `storel %v, %out`.",
)  # fmt: skip


# -- 9. isqrt (full unsigned range) ----------------------------------------------------------


def _isqrt_inputs() -> tuple:
    rng = random.Random(211)
    vals = {0, 1, 2, 3, 4, 5, 8, 9, 10, 15, 16, 17, 99, 100, 101, U64 - 1, U64 - 2, 1 << 63,
            (1 << 63) - 1, (1 << 62), (1 << 32), (1 << 32) - 1, (1 << 32) + 1}
    for k in [2**32 - 1, 2**32 - 2, 2**31, 2**31 - 1, 3037000499, 3037000500, 4294967295, 65536, 12345]:
        for d in (-1, 0, 1):
            if 0 <= k * k + d < U64:
                vals.add(k * k + d)
    for _ in range(60):
        k = rng.randint(0, 2**32 - 1)
        vals.update(v for v in (k * k - 1, k * k, k * k + 2 * k) if 0 <= v < U64)
        vals.add(rng.randint(0, U64 - 1))
    return tuple((_s64(v),) for v in sorted(vals))


ISQRT = Task(
    "isqrt", "uint64_t isqrt(uint64_t n)",
    "Return floor(sqrt(n)) for an unsigned 64-bit n (any value from 0 to 2^64 - 1, e.g. "
    "isqrt(2^64 - 1) = 4294967295), i.e. the largest r with r*r <= n. Use integer arithmetic "
    "only (binary search or Newton's method; no floating point). Beware of overflow: r*r and "
    "intermediate sums can exceed 64 bits near the top of the range, and n is unsigned.",
    """int64_t isqrt(int64_t sn) {
        uint64_t n = (uint64_t)sn, lo = 0, hi = 4294967295u;
        if (hi * hi <= n) return (int64_t)hi;
        while (lo < hi) {
            uint64_t mid = lo + (hi - lo + 1) / 2;
            if (mid * mid <= n) lo = mid; else hi = mid - 1;
        }
        return (int64_t)lo;
    }""",
    1, inputs=_isqrt_inputs(), round=2,
)  # fmt: skip


# -- 10. collatz_max (nested loops, longer code) ---------------------------------------------

COLLATZ_MAX = Task(
    "collatz_max", "int64_t collatz_max(int64_t n)",
    "For every k in 1..n, count the Collatz steps of k: the number of times x -> (x even ? "
    "x/2 : 3x+1) must be applied, starting from x = k, until x == 1 (k = 1 takes 0 steps, "
    "k = 6 takes 8, k = 27 takes 111). Return the maximum step count over k = 1..n. "
    "1 <= n <= 3000 (intermediate values stay far below 2^63).",
    """int64_t collatz_max(int64_t n) {
        int64_t best = 0;
        for (int64_t k = 1; k <= n; k++) {
            uint64_t x = (uint64_t)k; int64_t s = 0;
            while (x != 1) { x = (x & 1) ? 3 * x + 1 : x >> 1; s++; }
            if (s > best) best = s;
        }
        return best;
    }""",
    1, inputs=tuple((n,) for n in (1, 2, 3, 5, 6, 7, 9, 18, 25, 27, 54, 73, 97, 129, 171, 231, 313,
                                   327, 649, 703, 871, 1161, 2223, 2463, 2919, 3000)),
    max_insns=10_000_000, round=2,
)  # fmt: skip


ROUND2: tuple[Task, ...] = (
    FIB_REC, MAP_SUM, SUM8, DOT_I32_SIMD, BINARY_SEARCH, MEMMOVE_I64, CRC32, MULHI_U64,
    ADD_OVERFLOW, ISQRT, COLLATZ_MAX,
)
ROUNDS = {"round1": ROUND1, "round2": ROUND2}
ALL_TASKS: tuple[Task, ...] = ROUND1 + ROUND2
ALL_BY_NAME = {t.name: t for t in ALL_TASKS}


def select_tasks(spec: str) -> list[Task]:
    """`round1`, `round2`, `all`, or comma-separated task names (rounds may be mixed in)."""
    out: list[Task] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if part == "all":
            out += ALL_TASKS
        elif part in ROUNDS:
            out += ROUNDS[part]
        elif part in ALL_BY_NAME:
            out.append(ALL_BY_NAME[part])
        else:
            raise ValueError(f"unknown task or task set {part!r}; have round1, round2, all, "
                             + ", ".join(ALL_BY_NAME))
    return list(dict.fromkeys(out))


def feasible_cells(tasks, paths, isas) -> list[tuple[Task, str, str]]:
    return [(t, p, i) for t in tasks for p in paths for i in isas if p not in t.na_paths]


__all__ = ["ALL_BY_NAME", "ALL_TASKS", "ROUND2", "ROUNDS", "feasible_cells", "select_tasks"]
