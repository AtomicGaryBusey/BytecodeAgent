"""Per-backend lowering latency: `python -m bca.ir.bench [repeats]`.

Reports the median end-to-end wall time (source text -> runnable raw bytes) over all
kernels, plus the median of each pipeline stage.
"""

import statistics
import sys
import time
from collections import defaultdict

from bca.ir.kernels import KERNELS
from bca.ir.lower import lower_c, lower_qbe
from bca.ir.tools import find_tool
from bca.ir.wasm import host_isa, lower_wat

ISAS = ("x86_64", "aarch64")


def backends(isa: str):
    if find_tool("qbe"):
        if find_tool(f"gas-{isa}"):
            yield "qbe+gas", lambda k: lower_qbe(k.qbe, isa, k.name, assembler="gas"), False
        if find_tool("llvm-mc"):
            yield "qbe+llvm-mc", lambda k: lower_qbe(k.qbe, isa, k.name, assembler="llvm-mc"), False
    if find_tool(f"tcc-{isa}"):
        yield "c/tcc", lambda k: lower_c(k.c, isa, k.name, compiler="tcc"), False
    if find_tool("clang"):
        yield "c/clang -O2", lambda k: lower_c(k.c, isa, k.name, compiler="clang"), False
    if isa == host_isa():
        yield "wat/cranelift (py api)", lambda k: lower_wat(k.wat, isa, k.name, use_cli=False), True
    if find_tool("wasmtime"):
        yield "wat/cranelift (cli)", lambda k: lower_wat(k.wat, isa, k.name, use_cli=True), True


def measure(repeats: int = 5) -> list[tuple[str, str, float, dict[str, float]]]:
    rows = []
    for isa in ISAS:
        for name, fn, scalar_only in backends(isa):
            walls, stages = [], defaultdict(list)
            for k in KERNELS:
                if scalar_only and k.wat is None:
                    continue
                fn(k)  # warm caches (page cache, dynamic linker)
                for _ in range(repeats):
                    t0 = time.perf_counter()
                    low = fn(k)
                    walls.append((time.perf_counter() - t0) * 1e3)
                    for stage, ms in low.timings_ms.items():
                        stages[stage].append(ms)
            rows.append((isa, name, statistics.median(walls),
                         {s: statistics.median(v) for s, v in stages.items()}))
    return rows


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"{'isa':8} {'backend':24} {'median ms':>9}  stages (median ms)")
    for isa, name, wall, stages in measure(repeats):
        detail = ", ".join(f"{s} {ms:.1f}" for s, ms in stages.items())
        print(f"{isa:8} {name:24} {wall:9.1f}  {detail}")


if __name__ == "__main__":
    main()
