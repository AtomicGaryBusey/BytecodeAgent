"""Differential testing: machine code under emulation vs. a natively compiled C reference.

The C reference defines the semantics; it is compiled for the host, so the same reference
checks candidates for any ISA. Signatures are restricted to `int64_t f(int64_t, ...)` or
functions taking one `int64_t *` buffer plus integer arguments.

Reference semantics: references are built with `-fwrapv -fno-strict-aliasing`, so signed
integer arithmetic wraps (two's complement) instead of being undefined — `a + 1 > a` is 0 at
INT64_MAX, exactly what the machine code computes. Operations that are still undefined or
host-specific (division by zero, INT64_MIN / -1, oversized shifts) are *outside the
reference's domain*: the host (aarch64) silently returns 0 for x / 0 where x86 `idiv` traps.
Exclude such inputs with a precondition — `differential(..., where=div_safe(0, 1))` or
`int_inputs(..., where=...)` — rather than letting the host's answer define them.

Candidates run under `bca.emu.run_function`'s hostile defaults: garbage in every
non-argument register, calling-convention checks on return, and (for buffers) canaries
around the buffer that fail the case on any out-of-bounds write.
"""

import ctypes
import hashlib
import os
import random
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from bca.emu import DATA_BASE, DATA_SIZE, MASK64, EmulationError, run_function
from bca.isa import Isa

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

REFERENCE_CFLAGS = ("-O1", "-fwrapv", "-fno-strict-aliasing", "-shared", "-fPIC")
# Bump when the build recipe changes so stale artefacts are never reused.
_CACHE_VERSION = "2"


def cache_dir() -> Path:
    """Per-user reference cache: $BCA_CACHE_DIR, else $XDG_CACHE_HOME/bca, else ~/.cache/bca.

    Created with mode 0700; refused unless it is a real directory owned by the current user
    and not accessible to group/other (a planted directory could otherwise supply a .so that
    gets loaded into this process).
    """
    if os.environ.get("BCA_CACHE_DIR"):
        path = Path(os.environ["BCA_CACHE_DIR"])
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        path = Path(base) / "bca"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"reference cache {path} is not a directory (symlink?)")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"reference cache {path} is owned by uid {st.st_uid}, not {os.getuid()}")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def _build_reference(source: str, flags: Sequence[str]) -> Path:
    full = "#include <stdint.h>\n" + source
    key = "\0".join((_CACHE_VERSION, *flags, full))
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    directory = cache_dir()
    lib = directory / f"ref-{digest}.so"
    if lib.exists():
        return lib
    # Build under a unique temporary name, then publish atomically: a concurrent process
    # sees either no file or a complete one, never a partially written .so.
    fd, tmp = tempfile.mkstemp(prefix=f".ref-{digest}-", suffix=".so", dir=directory)
    os.close(fd)
    try:
        proc = subprocess.run(
            ["gcc", *flags, "-o", tmp, "-x", "c", "-"],
            input=full.encode(),
            capture_output=True,
        )
        if proc.returncode:
            raise subprocess.CalledProcessError(
                proc.returncode, proc.args, proc.stdout, proc.stderr
            )
        os.replace(tmp, lib)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return lib


def compile_c_reference(
    source: str, name: str, nargs: int, *, extra_flags: Sequence[str] = ()
) -> Callable[..., int]:
    """Compile `source` with the host compiler and return `name` as an int64 callable.

    Built with REFERENCE_CFLAGS (`-O1 -fwrapv -fno-strict-aliasing`): signed overflow wraps.
    """
    lib = _build_reference(source, (*REFERENCE_CFLAGS, *extra_flags))
    fn = ctypes.CDLL(str(lib))[name]  # a fresh function object: callers may set argtypes
    fn.restype = ctypes.c_int64
    fn.argtypes = [ctypes.c_int64] * nargs
    return fn


@dataclass
class Mismatch:
    args: tuple[int, ...]
    expected: int | None
    actual: int | None
    error: str | None = None


@dataclass
class DiffReport:
    cases: int
    mismatches: list[Mismatch]
    skipped: int = 0  # inputs rejected by the precondition (never run)

    @property
    def ok(self) -> bool:
        return not self.mismatches


# -- preconditions --------------------------------------------------------------------------


def div_safe(dividend: int = 0, divisor: int = 1) -> Callable[[tuple[int, ...]], bool]:
    """Precondition for signed division: divisor != 0 and not INT64_MIN / -1."""

    def ok(args: tuple[int, ...]) -> bool:
        b = args[divisor]
        return b != 0 and not (b == -1 and args[dividend] == INT64_MIN)

    return ok


# -- inputs ---------------------------------------------------------------------------------


def edge_values() -> list[int]:
    return [0, 1, 2, 3, 7, -1, -2, 63, 64, 255, 2**31 - 1, -(2**31), 2**63 - 1, -(2**63)]


def _pairwise(values: list[list[int]], rng: random.Random) -> list[tuple[int, ...]]:
    """A strength-2 covering array: every value pair of every two arguments appears."""
    k = len(values)
    if k == 1:
        return [(v,) for v in values[0]]
    if k == 2:
        return [(a, b) for a in values[0] for b in values[1]]
    uncovered = {
        (i, a, j, b)
        for i in range(k)
        for j in range(i + 1, k)
        for a in values[i]
        for b in values[j]
    }
    rows = []
    while uncovered:
        # Seed the row with one uncovered pair, then pick each remaining argument's value
        # greedily to cover the most still-uncovered pairs with the values already fixed.
        i, a, j, b = min(uncovered)
        row: list[int | None] = [None] * k
        row[i], row[j] = a, b
        for p in [p for p in range(k) if row[p] is None]:
            best, best_gain = None, -1
            for v in rng.sample(values[p], len(values[p])):
                gain = sum(
                    ((q, row[q], p, v) if q < p else (p, v, q, row[q])) in uncovered
                    for q in range(k)
                    if row[q] is not None
                )
                if gain > best_gain:
                    best, best_gain = v, gain
            row[p] = best
        for p in range(k):
            for q in range(p + 1, k):
                uncovered.discard((p, row[p], q, row[q]))
        rows.append(tuple(row))
    return rows


def _domains(nargs: int, domain) -> list[tuple[int, int]]:
    if domain is None:
        return [(INT64_MIN, INT64_MAX)] * nargs
    if len(domain) == 2 and all(isinstance(x, int) for x in domain):
        return [tuple(domain)] * nargs
    if len(domain) != nargs:
        raise ValueError("domain must be (lo, hi) or one (lo, hi) per argument")
    return [tuple(d) for d in domain]


def int_inputs(
    nargs: int,
    n: int,
    *,
    seed: int = 0,
    lo: int | None = None,
    hi: int | None = None,
    domain: tuple[int, int] | Sequence[tuple[int, int]] | None = None,
    where: Callable[[tuple[int, ...]], bool] | None = None,
    edges: Sequence[int] | None = None,
):
    """Deterministic int64 argument tuples: edge coverage first, then `n` random tuples.

    1. Pairwise edge coverage: every pair of arguments sees every pair of edge values (for
       one argument: every edge; for two: the full product), so each argument independently
       takes every edge value — not just the first argument.
    2. Per-argument sweeps: each argument takes every edge value while the others are small
       random values.
    3. `n` random tuples. By default each value is drawn from a mix of small ([-1000, 1000]),
       full-range 64-bit, and near-edge values. If `lo`/`hi` are given, random values are
       drawn from [lo, hi] only (edges are unaffected, as before).

    `domain=(lo, hi)` (or one pair per argument) bounds *every* value, edges included — use
    it for loop-count kernels where e.g. INT64_MAX would mean 2**63 iterations; the domain's
    endpoints are added as edges. `where` is a precondition: tuples for which it returns
    False are dropped (e.g. `where=div_safe(0, 1)`). `edges` replaces edge_values().
    """
    rng = random.Random(seed)
    doms = _domains(nargs, domain)
    base_edges = list(edge_values() if edges is None else edges)
    per_arg_edges = []
    for dlo, dhi in doms:
        vals = [e for e in base_edges if dlo <= e <= dhi]
        if domain is not None:
            vals += [dlo, dlo + 1, dhi - 1, dhi] + ([0] if dlo <= 0 <= dhi else [])
        seen, uniq = set(), []
        for v in vals:
            if dlo <= v <= dhi and v not in seen:
                seen.add(v)
                uniq.append(v)
        per_arg_edges.append(uniq)
    bounded_random = lo is not None or hi is not None
    small_lo = -1000 if lo is None else lo
    small_hi = 1000 if hi is None else hi

    def clip(v: int, p: int) -> int:
        dlo, dhi = doms[p]
        return min(max(v, dlo), dhi)

    def small(p: int) -> int:
        dlo, dhi = doms[p]
        a, b = max(small_lo, dlo), min(small_hi, dhi)
        return rng.randint(a, b) if a <= b else rng.randint(dlo, dhi)

    def rand_value(p: int) -> int:
        if bounded_random:
            return small(p)
        r = rng.random()
        if r < 0.5:
            return small(p)
        if r < 0.8:
            dlo, dhi = doms[p]
            return rng.randint(dlo, dhi)
        e = rng.choice(per_arg_edges[p]) + rng.randint(-3, 3)
        e = (e - INT64_MIN) % 2**64 + INT64_MIN  # wrap into int64
        return clip(e, p)

    def emit(args):
        return where is None or where(args)

    if nargs == 0:
        yield ()
        return
    for args in _pairwise(per_arg_edges, rng):
        if emit(args):
            yield args
    if nargs > 1:
        for p in range(nargs):
            for e in per_arg_edges[p]:
                args = tuple(e if q == p else small(q) for q in range(nargs))
                if emit(args):
                    yield args
    for _ in range(n):
        args = tuple(rand_value(p) for p in range(nargs))
        if emit(args):
            yield args


# -- differential runs ----------------------------------------------------------------------


def differential(
    isa: Isa,
    code: bytes,
    reference: Callable[..., int],
    inputs: Iterable[tuple[int, ...]],
    *,
    max_insns: int = 1_000_000,
    where: Callable[[tuple[int, ...]], bool] | None = None,
    abi: bool = True,
    seed: int = 0,
    **run_kwargs,
) -> DiffReport:
    """Compare int64 results of `code` (emulated) and `reference` (native) on each input.

    `where` is the reference's precondition: inputs outside it are skipped (never passed to
    the reference, whose host behaviour there is not a specification). Each case runs with
    fresh register garbage derived from (`seed`, case index); any write to the data region
    fails the case; `abi=False` disables calling-convention checks. Extra keyword arguments
    go to `run_function`.
    """
    run_kwargs.setdefault("guard", (0, 0))  # a scalar function has no business writing data
    mismatches, cases, skipped = [], 0, 0
    for args in inputs:
        args = tuple(args)
        if where is not None and not where(args):
            skipped += 1
            continue
        cases += 1
        expected = reference(*args) & MASK64
        try:
            actual = run_function(
                isa, code, list(args), max_insns=max_insns, abi=abi,
                seed=(seed * 0x9E3779B1 + cases) & MASK64, **run_kwargs,
            ).ret  # fmt: skip
        except EmulationError as exc:
            mismatches.append(Mismatch(args, expected, None, str(exc)))
            continue
        if actual != expected:
            mismatches.append(Mismatch(args, expected, actual))
    return DiffReport(cases, mismatches, skipped)


# Buffers are placed one page into the data region, with canary garbage on both sides.
BUFFER_OFFSET = 0x1000


def buffer_differential(
    isa: Isa,
    code: bytes,
    reference_source: str,
    name: str,
    buffers: Iterable[list[int]],
    *,
    max_insns: int = 1_000_000,
    abi: bool = True,
    seed: int = 0,
) -> DiffReport:
    """For `int64_t f(int64_t *buf, int64_t n)`: compare return value and mutated buffer.

    The buffer sits at DATA_BASE + BUFFER_OFFSET surrounded by pseudo-random canaries; a
    write anywhere in the data region outside buf[0..n) fails the case (OutOfBoundsWrite),
    and reads past either end see junk rather than zeros.
    """
    ref = compile_c_reference(reference_source, name, 2)
    ref.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64]
    mismatches, cases = [], 0
    for values in buffers:
        cases += 1
        if BUFFER_OFFSET + 8 * len(values) > DATA_SIZE:
            raise ValueError("buffer too large for the data region")
        arr = (ctypes.c_int64 * max(len(values), 1))(*values)
        expected = ref(arr, len(values)) & MASK64
        exp_buf = b"".join((v & MASK64).to_bytes(8, "little") for v in arr[: len(values)])
        data = b"".join((v & MASK64).to_bytes(8, "little") for v in values)
        try:
            res = run_function(
                isa, code, [DATA_BASE + BUFFER_OFFSET, len(values)], data=data,
                data_offset=BUFFER_OFFSET, guard=(BUFFER_OFFSET, BUFFER_OFFSET + len(data)),
                max_insns=max_insns, abi=abi, seed=(seed * 0x9E3779B1 + cases) & MASK64,
            )  # fmt: skip
        except EmulationError as exc:
            mismatches.append(Mismatch(tuple(values), expected, None, str(exc)))
            continue
        if res.ret != expected or res.data != exp_buf:
            mismatches.append(Mismatch(tuple(values), expected, res.ret, "buffer differs"))
    return DiffReport(cases, mismatches)
