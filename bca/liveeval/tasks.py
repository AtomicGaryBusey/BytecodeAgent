"""The round-1 evaluation tasks (eight easy leaf kernels) and the `Task` type shared with
round 2 (bca.liveeval.tasks2): spec, signature, C reference, inputs and checker.

Every task is `int64_t name(...)` under the SysV x86-64 / AAPCS64 integer ABI. Scalar tasks
are checked with `bca.difftest.differential`, buffer tasks (`int64_t *a, int64_t n`) with
`bca.difftest.buffer_differential` (return value + mutated buffer + out-of-bounds guard).
Inputs are deterministic and bounded where the trip count depends on an argument.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from bca.difftest import INT64_MAX, INT64_MIN, edge_values, int_inputs
from bca.ir.kernels import buffer_inputs


@dataclass(frozen=True)
class Task:
    name: str
    signature: str  # C prototype shown to the model
    spec: str  # natural-language specification shown to the model
    c_ref: str  # reference semantics (host-compiled, -fwrapv)
    nargs: int
    buffer: bool = False
    inputs: tuple = field(default=(), repr=False)  # tuples (scalar), lists (buffer), MemCases (mem)
    # -- round-2 extensions (defaults keep round-1 behaviour and prompts unchanged) --
    mem: bool = False  # inputs are difftest.MemCase: checked with memory_differential
    ref_name: str = ""  # C reference entry point if not `name` (e.g. a wrapper)
    requires: tuple[str, ...] = ()  # static requirements: "recursion", "simd"
    calls: str = ""  # "" (leaf), "self" (recursion), "callback" (hostile f at CALLBACK_BASE)
    ir_hint: str = ""  # extra QBE facts for this task
    na: tuple[tuple[str, str], ...] = ()  # (path, reason) cells that are not applicable
    max_insns: int = 2_000_000
    round: int = 1
    mem_elem: int = 8  # element size used to display memory in failure messages
    mem_signed: bool = True

    @property
    def na_paths(self) -> dict[str, str]:
        return dict(self.na)

    @property
    def reference_name(self) -> str:
        return self.ref_name or self.name


def _sum_inputs():
    vals = [INT64_MIN, -(2**31), -1000, -7, -2, -1, 0, 1, 2, 3, 7, 10, 63, 64, 100, 255,
            500, 999, 1000]
    vals += [a[0] for a in int_inputs(1, 40, seed=11, domain=(-1000, 1000))]
    return tuple(dict.fromkeys((v,) for v in vals))


def _gcd_inputs():
    fixed = [(0, 0), (0, 5), (5, 0), (1, 1), (12, 18), (18, 12), (17, 5), (48, 180),
             (2**62, 2**40), (INT64_MAX, 1), (INT64_MAX, INT64_MAX), (INT64_MAX, 2**62),
             (1071, 462), (2**61 - 1, 2**31 - 1), (6 * 7919, 4 * 7919)]
    rnd = list(int_inputs(2, 60, seed=12, domain=(0, INT64_MAX)))
    rng = random.Random(13)
    rnd += [(rng.randint(0, 10**6) * k, rng.randint(0, 10**6) * k)
            for k in (1, 2, 6, 97, 1 << 20) for _ in range(4)]
    return tuple(dict.fromkeys(fixed + rnd))


def _popcount_inputs():
    rng = random.Random(14)
    vals = edge_values() + [0x5555555555555555, -0x5555555555555556, 0x0F0F0F0F0F0F0F0F,
                            1 << 32, 1 << 62, (1 << 63) - 1, -(1 << 63) + 1, 0xFF00FF00]
    vals += [rng.randint(INT64_MIN, INT64_MAX) for _ in range(60)]
    return tuple(dict.fromkeys((v,) for v in vals))


def _fib_inputs():
    return tuple((n,) for n in range(0, 91))


def _bufs(nonempty: bool, seed: int, n: int = 120) -> tuple:
    bufs = [b for b in buffer_inputs(n, seed=seed) if b or not nonempty]
    rng = random.Random(seed + 100)
    bufs += [[5], [-5, -3], [INT64_MIN, INT64_MIN], [3, 3, 3, 1], [1, 2, 3, 4, 5, 6, 7],
             [7, 6, 5, 4, 3, 2, 1], [INT64_MAX, INT64_MIN, 0, -1, 1]]
    bufs += [[rng.randint(-3, 3) for _ in range(rng.randint(1, 12))] for _ in range(20)]
    return tuple(tuple(b) for b in bufs)


def _prefix_inputs():
    rng = random.Random(21)
    bufs: list[list[int]] = [[], [0], [1], [0, 1, 2], [1, 2, 0], [1, 2, 3], [-1, INT64_MIN, 0, 5],
                             [INT64_MIN], [5, 0, 0, 0], [1] * 40]
    for _ in range(80):
        length = rng.randint(0, 48)
        b = [rng.choice([rng.randint(-100, 100) or 1, rng.randint(INT64_MIN, INT64_MAX) or 1])
             for _ in range(length)]
        if length and rng.random() < 0.75:
            b[rng.randrange(length)] = 0
            if rng.random() < 0.3:
                b[rng.randrange(length)] = 0
        bufs.append(b)
    return tuple(tuple(b) for b in bufs)


TASKS: tuple[Task, ...] = (
    Task(
        "sum_to", "int64_t sum_to(int64_t n)",
        "Return 1 + 2 + ... + n (the sum of the integers from 1 to n inclusive). If n <= 0, "
        "return 0. Callers pass n <= 1000 (n may be any negative value).",
        """int64_t sum_to(int64_t n) {
            int64_t s = 0;
            for (int64_t i = 1; i <= n; i++) s += i;
            return s;
        }""",
        1, inputs=_sum_inputs(),
    ),
    Task(
        "gcd", "int64_t gcd(int64_t a, int64_t b)",
        "Return the greatest common divisor of a and b, which are both non-negative "
        "(0 <= a, b <= INT64_MAX). gcd(x, 0) = gcd(0, x) = x; gcd(0, 0) = 0. Use Euclid's "
        "algorithm.",
        """int64_t gcd(int64_t a, int64_t b) {
            while (b != 0) { int64_t t = a % b; a = b; b = t; }
            return a;
        }""",
        2, inputs=_gcd_inputs(),
    ),
    Task(
        "popcount", "int64_t popcount(int64_t x)",
        "Return the number of 1 bits in x viewed as an unsigned 64-bit value (0..64). Do not "
        "use a population-count instruction (no popcnt / cnt).",
        """int64_t popcount(int64_t x) {
            uint64_t v = (uint64_t)x; int64_t c = 0;
            while (v) { c += v & 1; v >>= 1; }
            return c;
        }""",
        1, inputs=_popcount_inputs(),
    ),
    Task(
        "fib", "int64_t fib(int64_t n)",
        "Return the n-th Fibonacci number with fib(0) = 0, fib(1) = 1, fib(k) = fib(k-1) + "
        "fib(k-2), computed iteratively with wrapping 64-bit arithmetic (modulo 2^64). "
        "0 <= n <= 90.",
        """int64_t fib(int64_t n) {
            uint64_t a = 0, b = 1;
            for (int64_t i = 0; i < n; i++) { uint64_t t = a + b; a = b; b = t; }
            return (int64_t)a;
        }""",
        1, inputs=_fib_inputs(),
    ),
    Task(
        "bmax", "int64_t bmax(int64_t *a, int64_t n)",
        "a points to n signed 64-bit integers, n >= 1. Return the largest element (signed "
        "comparison). Do not write to memory through a.",
        """int64_t bmax(int64_t *a, int64_t n) {
            int64_t m = a[0];
            for (int64_t i = 1; i < n; i++) if (a[i] > m) m = a[i];
            return m;
        }""",
        2, buffer=True, inputs=_bufs(True, 31),
    ),
    Task(
        "reverse", "int64_t reverse(int64_t *a, int64_t n)",
        "a points to n signed 64-bit integers, n >= 0. Reverse the array in place (a[0] swaps "
        "with a[n-1], and so on) and return 0. Write nothing outside a[0..n-1].",
        """int64_t reverse(int64_t *a, int64_t n) {
            for (int64_t i = 0, j = n - 1; i < j; i++, j--) { int64_t t = a[i]; a[i] = a[j]; a[j] = t; }
            return 0;
        }""",
        2, buffer=True, inputs=_bufs(False, 32),
    ),
    Task(
        "bubble_sort", "int64_t bubble_sort(int64_t *a, int64_t n)",
        "a points to n signed 64-bit integers, n >= 0 (n <= 64). Sort the array in place in "
        "ascending order (signed comparison) using bubble sort, and return 0. Write nothing "
        "outside a[0..n-1].",
        """int64_t bubble_sort(int64_t *a, int64_t n) {
            for (int64_t i = 0; i + 1 < n; i++)
                for (int64_t j = 0; j + 1 < n - i; j++)
                    if (a[j] > a[j + 1]) { int64_t t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; }
            return 0;
        }""",
        2, buffer=True, inputs=_bufs(False, 33, 90),
    ),
    Task(
        "count_nonzero_prefix", "int64_t count_nonzero_prefix(int64_t *a, int64_t n)",
        "a points to n signed 64-bit integers, n >= 0. Return the number of leading nonzero "
        "elements: the index of the first element equal to 0, or n if there is none. Do not "
        "read past a[n-1] and do not write to memory.",
        """int64_t count_nonzero_prefix(int64_t *a, int64_t n) {
            int64_t i = 0;
            while (i < n && a[i] != 0) i++;
            return i;
        }""",
        2, buffer=True, inputs=_prefix_inputs(),
    ),
)

BY_NAME = {t.name: t for t in TASKS}
