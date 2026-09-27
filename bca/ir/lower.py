"""Lower portable IR (QBE IL, C, WAT) to raw, self-contained function bytes per ISA.

Every backend ends in an ELF object; `bca.ir.elf.extract_function` links it into one flat
blob that `bca.emu.run_function` can execute at CODE_BASE (entry at offset 0). Relocations to
symbols defined in the object (constant pools, jump tables, local helpers) are applied;
anything external is rejected with an IRCompileError at stage "extract".
"""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from bca.emu import CODE_BASE
from bca.ir.elf import ElfError, Linked, extract_function, trampoline_target
from bca.ir.tools import find_tool, require_tool
from bca.isa import ISAS, Isa

QBE_TARGET = {"x86_64": "amd64_sysv", "aarch64": "arm64"}
TRIPLE = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu"}

# Prepended to C sources: no system headers are used for cross compilation.
C_PRELUDE = """\
typedef signed char int8_t; typedef unsigned char uint8_t;
typedef short int16_t; typedef unsigned short uint16_t;
typedef int int32_t; typedef unsigned int uint32_t;
typedef long int64_t; typedef unsigned long uint64_t;
typedef long intptr_t; typedef unsigned long uintptr_t; typedef unsigned long size_t;
#define INT64_MIN (-9223372036854775807L - 1)
#define INT64_MAX 9223372036854775807L
#define UINT64_MAX 18446744073709551615UL
#line 1 "input.c"
"""

CLANG_FLAGS = [
    "-O2",
    "-ffreestanding",
    "-fno-pic",
    "-fno-builtin",
    "-fno-stack-protector",
    "-fno-asynchronous-unwind-tables",
    "-fno-exceptions",
    "-nostdinc",
]

LANGS = ("qbe", "c", "wat")
C_COMPILERS = ("clang", "tcc")


class IRCompileError(RuntimeError):
    """The IR could not be lowered; `stage` names the failing tool, `detail` its diagnostics."""

    def __init__(self, lang: str, stage: str, detail: str):
        self.lang, self.stage, self.detail = lang, stage, detail.strip()
        super().__init__(f"{lang} lowering failed at {stage}:\n{self.detail}")


@dataclass
class Lowered:
    code: bytes
    isa: str
    lang: str
    compiler: str
    func: str
    entry_offset: int
    asm: str | None = None  # intermediate assembly text, when the backend produces one
    timings_ms: dict[str, float] = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)  # object sections linked into `code`
    relocations: int = 0  # relocations applied while linking
    position_dependent: bool = False  # True: bytes are only valid when loaded at CODE_BASE

    @property
    def total_ms(self) -> float:
        return sum(self.timings_ms.values())


def _isa_name(isa: Isa | str) -> str:
    name = isa.name if isinstance(isa, Isa) else isa
    if name not in ISAS:
        raise ValueError(f"unknown ISA {name!r}; expected one of {sorted(ISAS)}")
    return name


def _run(lang: str, stage: str, argv: list[str], stdin: bytes | None, timings: dict) -> bytes:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise IRCompileError(lang, stage, f"timed out: {' '.join(argv)}") from exc
    timings[stage] = timings.get(stage, 0.0) + (time.perf_counter() - t0) * 1e3
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace") or proc.stdout.decode(errors="replace")
        raise IRCompileError(lang, stage, msg or f"exit status {proc.returncode}")
    return proc.stdout


def _extract(lang: str, obj: bytes, isa: str, func: str, timings: dict) -> Linked:
    t0 = time.perf_counter()
    try:
        out = extract_function(obj, isa, func, base=CODE_BASE)
    except ElfError as exc:
        raise IRCompileError(lang, "extract", str(exc)) from exc
    # Belt and braces (the linker verifies this too): offset 0 must enter `func`.
    start = 0 if out.entry_offset == 0 else trampoline_target(isa, out.code)
    if start != out.entry_offset:
        raise IRCompileError(lang, "extract", f"internal link error: offset 0 enters at "
                             f"{start!r}, but {func!r} is at offset {out.entry_offset:#x}")
    timings["extract"] = (time.perf_counter() - t0) * 1e3
    return out


ASSEMBLERS = ("gas", "llvm-mc")


def pick_assembler(isa: Isa | str) -> str:
    """'gas' if a GNU assembler for `isa` is installed, else 'llvm-mc'."""
    return "gas" if find_tool(f"gas-{_isa_name(isa)}") else "llvm-mc"


def assemble(text: str, isa: Isa | str, *, lang: str = "asm", assembler: str | None = None,
             timings: dict | None = None) -> bytes:
    """Assemble GNU-syntax assembly into an ELF object (GNU as or llvm-mc)."""
    name = _isa_name(isa)
    timings = timings if timings is not None else {}
    assembler = assembler or pick_assembler(name)
    if assembler == "llvm-mc":
        argv = [require_tool("llvm-mc"), f"--triple={TRIPLE[name]}", "-filetype=obj", "-o", "-"]
        return _run(lang, "llvm-mc", argv, text.encode(), timings)
    if assembler != "gas":
        raise ValueError(f"unknown assembler {assembler!r}; expected one of {ASSEMBLERS}")
    with tempfile.TemporaryDirectory(prefix="bca-as-") as tmp:  # gas cannot write to a pipe
        out = os.path.join(tmp, "out.o")
        _run(lang, "gas", [require_tool(f"gas-{name}"), "-o", out, "-"], text.encode(), timings)
        with open(out, "rb") as fh:
            return fh.read()


def lower_qbe(source: str, isa: Isa | str, func: str, *, assembler: str | None = None) -> Lowered:
    name = _isa_name(isa)
    timings: dict[str, float] = {}
    asm = _run("qbe", "qbe", [require_tool("qbe"), "-t", QBE_TARGET[name], "-"],
               source.encode(), timings).decode()
    assembler = assembler or pick_assembler(name)
    obj = assemble(asm, name, lang="qbe", assembler=assembler, timings=timings)
    ln = _extract("qbe", obj, name, func, timings)
    return Lowered(ln.code, name, "qbe", f"qbe+{assembler}", func, ln.entry_offset, asm, timings,
                   ln.sections, ln.relocations, ln.position_dependent)


def lower_c(source: str, isa: Isa | str, func: str, *, compiler: str = "clang") -> Lowered:
    name = _isa_name(isa)
    timings: dict[str, float] = {}
    text = C_PRELUDE + source
    if compiler == "clang":
        argv = [require_tool("clang"), f"--target={TRIPLE[name]}", *CLANG_FLAGS,
                "-c", "-x", "c", "-", "-o", "-"]
        obj = _run("c", "clang", argv, text.encode(), timings)
    elif compiler == "tcc":
        tcc = require_tool("tcc-x86_64" if name == "x86_64" else "tcc-aarch64")
        # tcc needs real files (it seeks its output); keep them in a private temp dir.
        with tempfile.TemporaryDirectory(prefix="bca-tcc-") as tmp:
            src, out = os.path.join(tmp, "input.c"), os.path.join(tmp, "out.o")
            with open(src, "w") as fh:
                fh.write(text)
            _run("c", "tcc", [tcc, "-nostdinc", "-c", src, "-o", out], None, timings)
            with open(out, "rb") as fh:
                obj = fh.read()
    else:
        raise ValueError(f"unknown C compiler {compiler!r}; expected one of {C_COMPILERS}")
    ln = _extract("c", obj, name, func, timings)
    return Lowered(ln.code, name, "c", compiler, func, ln.entry_offset, None, timings,
                   ln.sections, ln.relocations, ln.position_dependent)


def compile_ir(source: str, lang: str, isa: Isa | str, func_name: str, *,
               compiler: str | None = None) -> Lowered:
    """Lower `source` in `lang` ('qbe', 'c', 'wat') for `isa`; returns bytes plus metadata.

    For 'c', `compiler` is 'clang' (default) or 'tcc'. For 'qbe', `compiler` may name the
    assembler ('gas' or 'llvm-mc'; default: gas when installed for `isa`). For 'wat', the result is Cranelift's
    code for `func_name` behind a SysV/AAPCS64 adapter (see bca.ir.wasm.lower_wat).
    """
    if lang == "qbe":
        return lower_qbe(source, isa, func_name, assembler=compiler)
    if lang == "c":
        return lower_c(source, isa, func_name, compiler=compiler or "clang")
    if lang == "wat":
        from bca.ir.wasm import lower_wat

        return lower_wat(source, isa, func_name)
    raise ValueError(f"unknown IR language {lang!r}; expected one of {LANGS}")


def lower(source: str, lang: str, isa: Isa | str, func_name: str, *,
          compiler: str | None = None) -> bytes:
    """Raw function bytes for `bca.emu.run_function` (entry at offset 0)."""
    return compile_ir(source, lang, isa, func_name, compiler=compiler).code
