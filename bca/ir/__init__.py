"""Portable IR -> native machine code.

The model writes a small portable language (QBE IL, freestanding C, or WAT); an existing
compiler lowers it to x86-64 / AArch64; the function's raw bytes are extracted from the
ELF object and validated under Unicorn by the same differential harness as other paths.

    lower(source, lang, isa, func_name, compiler=None) -> bytes
    compile_ir(...) -> Lowered (bytes + asm + per-stage timings)
    run_wat(source, func, args) -> int     # native wasmtime execution (host)
"""

from bca.ir.elf import ElfError, RelocationError, extract_function
from bca.ir.lower import (
    C_COMPILERS,
    LANGS,
    IRCompileError,
    Lowered,
    assemble,
    compile_ir,
    lower,
    lower_c,
    lower_qbe,
)
from bca.ir.tools import ToolMissing, find_tool
from bca.ir.wasm import cranelift_disassembly, lower_wat, run_wat, wat_differential

__all__ = [
    "C_COMPILERS", "LANGS", "ElfError", "IRCompileError", "Lowered", "RelocationError",
    "ToolMissing", "assemble", "compile_ir", "cranelift_disassembly", "extract_function",
    "find_tool", "lower", "lower_c", "lower_qbe", "lower_wat", "run_wat", "wat_differential",
]
