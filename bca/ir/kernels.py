"""Reference kernels written once per IR (QBE IL, C, WAT) plus the C semantics they must match.

All arithmetic is modulo 2**64 (unsigned in C) so no kernel relies on signed-overflow UB.
Loop-count kernels (sum, fib) are only meaningful for bounded n; see `bounded_inputs`.
"""

import random
from dataclasses import dataclass

from bca.difftest import edge_values, int_inputs


@dataclass(frozen=True)
class Kernel:
    name: str
    nargs: int
    c_ref: str  # reference semantics, compiled for the host by compile_c_reference
    qbe: str
    c: str  # candidate C (freestanding; bca.ir.lower.C_PRELUDE supplies the int types)
    wat: str | None = None  # scalar kernels only
    buffer: bool = False
    bounded: bool = False  # inputs must be bounded (loop trip count ~ n)


SUM = Kernel(
    "sum", 1,
    c_ref="""int64_t sum(int64_t n) {
        uint64_t s = 0;
        for (int64_t i = 1; i <= n; i++) s += (uint64_t)i;
        return (int64_t)s;
    }""",
    qbe="""
export function l $sum(l %n) {
@start
    %s =l copy 0
    %i =l copy 1
@loop
    %c =w cslel %i, %n
    jnz %c, @body, @end
@body
    %s =l add %s, %i
    %i =l add %i, 1
    jmp @loop
@end
    ret %s
}
""",
    c="""int64_t sum(int64_t n) {
        uint64_t s = 0;
        for (int64_t i = 1; i <= n; i++) s += (uint64_t)i;
        return (int64_t)s;
    }""",
    wat="""(module
  (func $sum (export "sum") (param $n i64) (result i64) (local $s i64) (local $i i64)
    (local.set $i (i64.const 1))
    (block $done
      (loop $l
        (br_if $done (i64.gt_s (local.get $i) (local.get $n)))
        (local.set $s (i64.add (local.get $s) (local.get $i)))
        (local.set $i (i64.add (local.get $i) (i64.const 1)))
        (br $l)))
    (local.get $s)))""",
    bounded=True,
)

GCD = Kernel(
    "gcd", 2,
    c_ref="""int64_t gcd(int64_t x, int64_t y) {
        uint64_t a = (uint64_t)x, b = (uint64_t)y;
        while (b) { uint64_t t = a % b; a = b; b = t; }
        return (int64_t)a;
    }""",
    # Note: jnz tests a *word*; comparing the long explicitly avoids truncating b to 32 bits.
    qbe="""
export function l $gcd(l %a, l %b) {
@start
@loop
    %nz =w cnel %b, 0
    jnz %nz, @body, @end
@body
    %t =l urem %a, %b
    %a =l copy %b
    %b =l copy %t
    jmp @loop
@end
    ret %a
}
""",
    c="""int64_t gcd(int64_t x, int64_t y) {
        uint64_t a = (uint64_t)x, b = (uint64_t)y;
        while (b != 0) { uint64_t t = a % b; a = b; b = t; }
        return (int64_t)a;
    }""",
    wat="""(module
  (func $gcd (export "gcd") (param $a i64) (param $b i64) (result i64) (local $t i64)
    (block $done
      (loop $l
        (br_if $done (i64.eqz (local.get $b)))
        (local.set $t (i64.rem_u (local.get $a) (local.get $b)))
        (local.set $a (local.get $b))
        (local.set $b (local.get $t))
        (br $l)))
    (local.get $a)))""",
)

POPCOUNT = Kernel(
    "popcount", 1,
    c_ref="""int64_t popcount(int64_t v) { return __builtin_popcountll((uint64_t)v); }""",
    qbe="""
export function l $popcount(l %x) {
@start
    %c =l copy 0
@loop
    %nz =w cnel %x, 0
    jnz %nz, @body, @end
@body
    %m =l sub %x, 1
    %x =l and %x, %m
    %c =l add %c, 1
    jmp @loop
@end
    ret %c
}
""",
    c="""int64_t popcount(int64_t v) {
        uint64_t x = (uint64_t)v; int64_t c = 0;
        while (x) { x &= x - 1; c++; }
        return c;
    }""",
    wat="""(module
  (func $popcount (export "popcount") (param $x i64) (result i64) (local $c i64)
    (block $done
      (loop $l
        (br_if $done (i64.eqz (local.get $x)))
        (local.set $x (i64.and (local.get $x) (i64.sub (local.get $x) (i64.const 1))))
        (local.set $c (i64.add (local.get $c) (i64.const 1)))
        (br $l)))
    (local.get $c)))""",
)

FIB = Kernel(
    "fib", 1,
    c_ref="""int64_t fib(int64_t n) {
        uint64_t a = 0, b = 1;
        for (int64_t i = 0; i < n; i++) { uint64_t t = a + b; a = b; b = t; }
        return (int64_t)a;
    }""",
    qbe="""
export function l $fib(l %n) {
@start
    %a =l copy 0
    %b =l copy 1
    %i =l copy 0
@loop
    %c =w csltl %i, %n
    jnz %c, @body, @end
@body
    %t =l add %a, %b
    %a =l copy %b
    %b =l copy %t
    %i =l add %i, 1
    jmp @loop
@end
    ret %a
}
""",
    c="""int64_t fib(int64_t n) {
        uint64_t a = 0, b = 1;
        for (int64_t i = 0; i < n; i++) { uint64_t t = a + b; a = b; b = t; }
        return (int64_t)a;
    }""",
    wat="""(module
  (func $fib (export "fib") (param $n i64) (result i64)
    (local $a i64) (local $b i64) (local $t i64) (local $i i64)
    (local.set $b (i64.const 1))
    (block $done
      (loop $l
        (br_if $done (i64.ge_s (local.get $i) (local.get $n)))
        (local.set $t (i64.add (local.get $a) (local.get $b)))
        (local.set $a (local.get $b))
        (local.set $b (local.get $t))
        (local.set $i (i64.add (local.get $i) (i64.const 1)))
        (br $l)))
    (local.get $a)))""",
    bounded=True,
)

BUFMAX = Kernel(
    "bufmax", 2,
    c_ref="""int64_t bufmax(int64_t *p, int64_t n) {
        int64_t m = INT64_MIN;
        for (int64_t i = 0; i < n; i++) if (p[i] > m) m = p[i];
        return m;
    }""",
    qbe="""
export function l $bufmax(l %p, l %n) {
@start
    %m =l shl 1, 63
    %i =l copy 0
@loop
    %c =w csltl %i, %n
    jnz %c, @body, @end
@body
    %off =l mul %i, 8
    %addr =l add %p, %off
    %v =l loadl %addr
    %g =w csgtl %v, %m
    jnz %g, @take, @next
@take
    %m =l copy %v
@next
    %i =l add %i, 1
    jmp @loop
@end
    ret %m
}
""",
    c="""int64_t bufmax(int64_t *p, int64_t n) {
        int64_t m = INT64_MIN;
        for (int64_t i = 0; i < n; i++) if (p[i] > m) m = p[i];
        return m;
    }""",
    buffer=True,
)

BUFREV = Kernel(
    "bufrev", 2,
    c_ref="""int64_t bufrev(int64_t *p, int64_t n) {
        for (int64_t i = 0, j = n - 1; i < j; i++, j--) { int64_t t = p[i]; p[i] = p[j]; p[j] = t; }
        return n;
    }""",
    qbe="""
export function l $bufrev(l %p, l %n) {
@start
    %i =l copy 0
    %j =l sub %n, 1
@loop
    %c =w csltl %i, %j
    jnz %c, @body, @end
@body
    %oi =l mul %i, 8
    %ai =l add %p, %oi
    %oj =l mul %j, 8
    %aj =l add %p, %oj
    %x =l loadl %ai
    %y =l loadl %aj
    storel %y, %ai
    storel %x, %aj
    %i =l add %i, 1
    %j =l sub %j, 1
    jmp @loop
@end
    ret %n
}
""",
    c="""int64_t bufrev(int64_t *p, int64_t n) {
        int64_t i = 0, j = n - 1;
        while (i < j) { int64_t t = p[i]; p[i] = p[j]; p[j] = t; i++; j--; }
        return n;
    }""",
    buffer=True,
)

SCALAR_KERNELS = (SUM, GCD, POPCOUNT, FIB)
BUFFER_KERNELS = (BUFMAX, BUFREV)
KERNELS = SCALAR_KERNELS + BUFFER_KERNELS

LOOP_BOUND = 1000


def bounded_inputs(n: int, *, seed: int = 0, bound: int = LOOP_BOUND):
    """Single-arg inputs: every edge value (clamped into [-2**63, bound]) then random ones.

    Large positive edge values would make the trip count ~2**63; they are kept only if
    <= bound. Negative edge values (incl. INT64_MIN) are kept: they exit immediately.
    """
    edges = [e for e in edge_values() if e <= bound] + [bound, bound - 1, 92, 93, 94]
    yield from ((e,) for e in edges)
    for args in int_inputs(1, n, seed=seed, lo=-bound, hi=bound):
        if args[0] <= bound:
            yield args


def kernel_inputs(k: Kernel, n: int = 150, *, seed: int = 0) -> list[tuple[int, ...]]:
    if k.bounded:
        return list(bounded_inputs(max(n, 200), seed=seed))
    # Edge-value combinations + small random inputs, then full-range random inputs.
    rng = random.Random(seed + 1)
    wide = [tuple(rng.randint(-(2**63), 2**63 - 1) for _ in range(k.nargs)) for _ in range(n // 2)]
    return list(int_inputs(k.nargs, n, seed=seed)) + wide


def buffer_inputs(n: int = 220, *, seed: int = 0) -> list[list[int]]:
    rng = random.Random(seed)
    edges = edge_values()
    bufs: list[list[int]] = [[], [0], [-1], [2**63 - 1], [-(2**63)], edges, edges[::-1]]
    while len(bufs) < n:
        length = rng.randint(0, 64)
        pool = rng.choice(["small", "wide", "edge"])
        if pool == "small":
            bufs.append([rng.randint(-50, 50) for _ in range(length)])
        elif pool == "wide":
            bufs.append([rng.randint(-(2**63), 2**63 - 1) for _ in range(length)])
        else:
            bufs.append([rng.choice(edges) for _ in range(length)])
    return bufs
