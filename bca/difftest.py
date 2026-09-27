"""Differential testing: machine code under emulation vs. a natively compiled C reference.

The C reference defines the semantics; it is compiled for the host, so the same reference
checks candidates for any ISA. Signatures are restricted to `int64_t f(int64_t, ...)` or
functions taking one `int64_t *` buffer plus integer arguments.
"""

import ctypes
import hashlib
import random
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from bca.emu import DATA_BASE, MASK64, EmulationError, run_function
from bca.isa import Isa

_CACHE = Path(tempfile.gettempdir()) / "bca-ref-cache"


def compile_c_reference(source: str, name: str, nargs: int) -> Callable[..., int]:
    """Compile `source` with the host compiler and return `name` as an int64 callable."""
    _CACHE.mkdir(exist_ok=True)
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    lib = _CACHE / f"ref-{digest}.so"
    if not lib.exists():
        src = _CACHE / f"ref-{digest}.c"
        src.write_text("#include <stdint.h>\n" + source)
        subprocess.run(
            ["gcc", "-O1", "-shared", "-fPIC", "-o", str(lib), str(src)],
            check=True,
            capture_output=True,
        )
    fn = getattr(ctypes.CDLL(str(lib)), name)
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

    @property
    def ok(self) -> bool:
        return not self.mismatches


def edge_values() -> list[int]:
    return [0, 1, 2, 3, 7, -1, -2, 63, 64, 255, 2**31 - 1, -(2**31), 2**63 - 1, -(2**63)]


def int_inputs(nargs: int, n: int, *, seed: int = 0, lo: int = -1000, hi: int = 1000):
    """Edge-value combinations first, then seeded random ("hidden") inputs."""
    rng = random.Random(seed)
    edges = edge_values()
    for i in range(min(n, len(edges) ** max(nargs, 1))):
        yield tuple(edges[(i // len(edges) ** k) % len(edges)] for k in range(nargs))
    for _ in range(n):
        yield tuple(rng.randint(lo, hi) for _ in range(nargs))


def differential(
    isa: Isa,
    code: bytes,
    reference: Callable[..., int],
    inputs: Iterable[tuple[int, ...]],
    *,
    max_insns: int = 1_000_000,
) -> DiffReport:
    """Compare int64 results of `code` (emulated) and `reference` (native) on each input."""
    mismatches, cases = [], 0
    for args in inputs:
        cases += 1
        expected = reference(*args) & MASK64
        try:
            actual = run_function(isa, code, list(args), max_insns=max_insns).ret
        except EmulationError as exc:
            mismatches.append(Mismatch(args, expected, None, str(exc)))
            continue
        if actual != expected:
            mismatches.append(Mismatch(args, expected, actual))
    return DiffReport(cases, mismatches)


def buffer_differential(
    isa: Isa,
    code: bytes,
    reference_source: str,
    name: str,
    buffers: Iterable[list[int]],
) -> DiffReport:
    """For `int64_t f(int64_t *buf, int64_t n)`: compare return value and mutated buffer."""
    ref = compile_c_reference(reference_source, name, 2)
    ref.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64]
    mismatches, cases = [], 0
    for values in buffers:
        cases += 1
        arr = (ctypes.c_int64 * max(len(values), 1))(*values)
        expected = ref(arr, len(values)) & MASK64
        exp_buf = b"".join((v & MASK64).to_bytes(8, "little") for v in arr[: len(values)])
        data = b"".join((v & MASK64).to_bytes(8, "little") for v in values) or b"\0" * 8
        try:
            res = run_function(isa, code, [DATA_BASE, len(values)], data=data)
        except EmulationError as exc:
            mismatches.append(Mismatch(tuple(values), expected, None, str(exc)))
            continue
        if res.ret != expected or res.data[: len(exp_buf)] != exp_buf:
            mismatches.append(Mismatch(tuple(values), expected, res.ret, "buffer differs"))
    return DiffReport(cases, mismatches)
