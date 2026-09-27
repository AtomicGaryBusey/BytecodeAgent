"""WebAssembly text (WAT) via wasmtime/Cranelift.

Two paths:

* `run_wat` / `wat_differential`: compile with the wasmtime Python API and execute natively on
  this host. This is the reference-equivalence check and works for any module.
* `lower_wat`: obtain Cranelift's *native code bytes* for one function (from the compiled
  `.cwasm` ELF: Python API for the host ISA, `wasmtime compile --target` for other ISAs) and
  wrap them in a small adapter so `bca.emu.run_function` can call them with the SysV / AAPCS64
  integer convention.

Functions are selected by **export name** everywhere (`run_wat`, `wat_differential`,
`lower_wat`, `cranelift_disassembly`): the export resolves to a module function index, which
names the Cranelift symbol `wasm[0]::function[N]` (with or without a `$name`).

Cranelift functions use wasmtime's internal "tail" convention, observed on wasmtime 49:

    x86_64 : rdi = callee vmctx, rsi = caller vmctx, params rdx rcx r8 r9, then 8-byte stack
             slots at [rsp+8], [rsp+16], ... on entry (callee pops), result rax
    aarch64: x2  = callee vmctx, x3  = caller vmctx, params x4 x5 x6 x7, then 8-byte stack
             slots at [sp], [sp+8], ... on entry (callee pops), result x2

The adapter accepts as many i32/i64 parameters as the host convention has argument
registers (SysV: 6, AAPCS64: 8), places them per the table above and passes vmctx = 0; it
restores the stack pointer from its own frame, so callee-popped stack arguments are
harmless. Value convention (shared with `run_wat`): an i32 parameter uses the low 32 bits of
the argument; an i32 result is sign-extended to 64 bits; every result is reported as
unsigned 64-bit. Only functions whose parameters and single result are i32/i64 are accepted.

Any code that touches the instance (linear memory, globals, tables, calls through the VM,
fuel/epoch or stack-limit checks, libcalls) faults at a near-null address under Unicorn and
is reported as an EmulationError: only pure scalar functions can run this way. This is a
real execution of Cranelift's bytes, not an emulation of wasm semantics.
"""

import os
import platform
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

from bca.difftest import DiffReport, Mismatch
from bca.emu import MASK64
from bca.ir.elf import SHF_EXECINSTR, Elf, ElfError
from bca.ir.lower import IRCompileError, Lowered, _isa_name
from bca.ir.tools import require_tool
from bca.isa import ISAS

WASM_TARGET = {"x86_64": "x86_64-unknown-linux-gnu", "aarch64": "aarch64-unknown-linux-gnu"}
# Parameters the adapter can pass: the host convention's argument registers.
MAX_ADAPTER_ARGS = {name: len(isa.args) for name, isa in ISAS.items()}
_WASM_REG_PARAMS = 4  # parameters wasmtime's tail convention passes in registers
ADAPTER_SIZE = 128
INT_TYPES = ("i32", "i64")


def host_isa() -> str:
    m = platform.machine().lower()
    return {"arm64": "aarch64", "amd64": "x86_64"}.get(m, m)


@lru_cache(maxsize=64)
def _module(source: str):
    """Compiled module (cached). Instances are *not* cached: see `_instance`."""
    import wasmtime

    engine = wasmtime.Engine()
    try:
        return engine, wasmtime.Module(engine, source)
    except wasmtime.WasmtimeError as exc:
        raise IRCompileError("wat", "wasmtime", str(exc)) from exc


def _instance(source: str):
    """A fresh Store + Instance, so every call starts from the module's initial state
    (globals, memory, tables): runs are independent of each other."""
    import wasmtime

    engine, module = _module(source)
    store = wasmtime.Store(engine)
    try:
        return store, wasmtime.Instance(store, module, [])
    except wasmtime.WasmtimeError as exc:
        raise IRCompileError("wat", "wasmtime", f"instantiation failed: {exc}") from exc


@dataclass(frozen=True)
class WatExport:
    name: str
    index: int  # module function index (imports first), as in wasm[0]::function[N]
    params: tuple[str, ...]
    results: tuple[str, ...]
    imported: bool


def _leb(buf: bytes, pos: int) -> tuple[int, int]:
    val = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val, pos


def _func_exports(binary: bytes) -> tuple[dict[str, int], int]:
    """(export name -> function index, number of imported functions) from a wasm binary."""
    exports, n_imported, pos = {}, 0, 8
    while pos < len(binary):
        sid = binary[pos]
        size, pos = _leb(binary, pos + 1)
        body, end = pos, pos + size
        if sid == 2:  # imports
            count, body = _leb(binary, body)
            for _ in range(count):
                for _ in range(2):  # module, field names
                    n, body = _leb(binary, body)
                    body += n
                kind = binary[body]
                body += 1
                if kind == 0:  # func: typeidx
                    n_imported += 1
                    _, body = _leb(binary, body)
                elif kind == 1:  # table: reftype + limits
                    body += 1
                    flags, body = _leb(binary, body)
                    _, body = _leb(binary, body)
                    if flags & 1:
                        _, body = _leb(binary, body)
                elif kind == 2:  # memory: limits (+ memory64 / shared flags)
                    flags, body = _leb(binary, body)
                    _, body = _leb(binary, body)
                    if flags & 1:
                        _, body = _leb(binary, body)
                    if flags & 8:  # custom page size
                        _, body = _leb(binary, body)
                elif kind == 3:  # global: valtype + mut
                    body += 2
                else:  # tag or unknown: stop counting; exports still resolve by index
                    break
        elif sid == 7:  # exports
            count, body = _leb(binary, body)
            for _ in range(count):
                n, body = _leb(binary, body)
                name = binary[body : body + n].decode("utf-8")
                body += n
                kind = binary[body]
                idx, body = _leb(binary, body + 1)
                if kind == 0:
                    exports[name] = idx
        pos = end
    return exports, n_imported


@lru_cache(maxsize=64)
def wat_exports(source: str) -> dict[str, WatExport]:
    """Function exports of a WAT module: name -> index and signature."""
    import wasmtime

    _engine, module = _module(source)
    try:
        binary = bytes(wasmtime.wat2wasm(source))
    except wasmtime.WasmtimeError as exc:
        raise IRCompileError("wat", "wasmtime", str(exc)) from exc
    indices, n_imported = _func_exports(binary)
    out = {}
    for ex in module.exports:
        if not isinstance(ex.type, wasmtime.FuncType):
            continue
        idx = indices[ex.name]
        out[ex.name] = WatExport(ex.name, idx, tuple(str(t) for t in ex.type.params),
                                 tuple(str(t) for t in ex.type.results), idx < n_imported)
    return out


def resolve_export(source: str, func: str) -> WatExport:
    """Look up function export `func`; raise IRCompileError if absent."""
    exports = wat_exports(source)
    if func not in exports:
        raise IRCompileError("wat", "wasmtime",
                             f"no exported function named {func!r}; exports: {sorted(exports)}")
    return exports[func]


def _check_integer_signature(ex: WatExport) -> None:
    bad = [t for t in ex.params if t not in INT_TYPES]
    if bad or len(ex.results) != 1 or ex.results[0] not in INT_TYPES:
        raise IRCompileError(
            "wat", "signature",
            f"export {ex.name!r} has type ({', '.join(ex.params)}) -> ({', '.join(ex.results)}); "
            "only i32/i64 parameters and a single i32/i64 result are supported")


def _to_wasm(value: int, ty: str) -> int:
    """Integer argument -> wasm value: an i32 takes the low 32 bits, an i64 the low 64."""
    bits = 32 if ty == "i32" else 64
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def _from_wasm(value: int, ty: str) -> int:
    """wasm result -> unsigned 64-bit; i32 results are sign-extended first."""
    return _to_wasm(value, ty) & MASK64


def run_wat(source: str, func: str, args: Iterable[int] = ()) -> int:
    """Run export `func` natively via wasmtime in a fresh instance.

    i32/i64 parameters and result, with the convention described in the module docstring
    (i32 args use their low 32 bits; the result is returned as unsigned 64-bit, i32 results
    sign-extended). Raises IRCompileError for invalid WAT, a missing export or an unsupported
    signature, and wasmtime.Trap for runtime traps.
    """
    ex = resolve_export(source, func)
    _check_integer_signature(ex)
    args = list(args)
    if len(args) != len(ex.params):
        raise ValueError(f"{func!r} takes {len(ex.params)} argument(s), got {len(args)}")
    store, inst = _instance(source)
    fn = inst.exports(store)[func]
    return _from_wasm(fn(store, *[_to_wasm(a, t) for a, t in zip(args, ex.params)]),
                      ex.results[0])


def wat_differential(source: str, func: str, reference: Callable[..., int],
                     inputs: Iterable[tuple[int, ...]]) -> DiffReport:
    """Like bca.difftest.differential, but the candidate runs natively under wasmtime."""
    import wasmtime

    mismatches, cases = [], 0
    for args in inputs:
        cases += 1
        expected = reference(*args) & MASK64
        try:
            actual = run_wat(source, func, args)
        except wasmtime.Trap as exc:
            mismatches.append(Mismatch(args, expected, None, f"trap: {exc}"))
            continue
        if actual != expected:
            mismatches.append(Mismatch(args, expected, actual))
    return DiffReport(cases, mismatches)


def cranelift_object(source: str, isa: str, *, use_cli: bool | None = None,
                     timings: dict | None = None) -> bytes:
    """Compile WAT to a wasmtime `.cwasm` ELF for `isa` (host: Python API; else the CLI)."""
    isa = _isa_name(isa)
    timings = timings if timings is not None else {}
    if use_cli is None:
        use_cli = isa != host_isa()
    t0 = time.perf_counter()
    if not use_cli:
        import wasmtime

        try:
            blob = bytes(wasmtime.Module(wasmtime.Engine(), source).serialize())
        except wasmtime.WasmtimeError as exc:
            raise IRCompileError("wat", "wasmtime", str(exc)) from exc
        timings["cranelift(py)"] = (time.perf_counter() - t0) * 1e3
        return blob
    cli = require_tool("wasmtime")
    with tempfile.TemporaryDirectory(prefix="bca-wat-") as tmp:
        src, out = os.path.join(tmp, "in.wat"), os.path.join(tmp, "out.cwasm")
        with open(src, "w") as fh:
            fh.write(source)
        proc = subprocess.run([cli, "compile", "--target", WASM_TARGET[isa], src, "-o", out],
                              capture_output=True, timeout=60)
        timings["cranelift(cli)"] = (time.perf_counter() - t0) * 1e3
        if proc.returncode != 0:
            raise IRCompileError("wat", "wasmtime compile", proc.stderr.decode(errors="replace"))
        with open(out, "rb") as fh:
            return fh.read()


def _adapter(isa: str, target: int, params: tuple[str, ...], result: str) -> bytes:
    """SysV/AAPCS64 -> wasmtime tail-convention shim that calls offset `target`.

    Host argument i goes to wasm parameter i: the first four in registers, the rest in 8-byte
    stack slots just above the return address. The shim keeps a frame pointer and restores
    sp from it after the call (the callee pops its stack arguments).
    """
    n = len(params)
    if n > MAX_ADAPTER_ARGS[isa]:
        raise IRCompileError("wat", "adapter", f"{n} parameters; the {isa} adapter passes at "
                             f"most {MAX_ADAPTER_ARGS[isa]} (host argument registers)")
    n_stack = max(0, n - _WASM_REG_PARAMS)
    area = -(-n_stack * 8 // 16) * 16
    if isa == "x86_64":
        host = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]
        lines = ["push rbp", "mov rbp, rsp"]  # rsp now 16-byte aligned
        if area:
            lines.append(f"sub rsp, {area}")
        lines += [f"mov qword ptr [rsp + {8 * i}], {host[_WASM_REG_PARAMS + i]}"
                  for i in range(n_stack)]
        lines += ["mov r9, rcx", "mov r8, rdx", "mov rcx, rsi", "mov rdx, rdi",
                  "xor edi, edi", "xor esi, esi", f"call {target:#x}"]
        if result == "i32":
            lines.append("movsxd rax, eax")
        lines += ["mov rsp, rbp", "pop rbp", "ret"]
        fill = b"\xcc"
    else:
        lines = ["stp x29, x30, [sp, #-16]!", "mov x29, sp"]
        if area:
            lines.append(f"sub sp, sp, #{area}")
        lines += [f"str x{_WASM_REG_PARAMS + i}, [sp, #{8 * i}]" for i in range(n_stack)]
        lines += ["mov x7, x3", "mov x6, x2", "mov x5, x1", "mov x4, x0", "mov x2, xzr",
                  "mov x3, xzr", f"bl {target:#x}",
                  "sxtw x0, w2" if result == "i32" else "mov x0, x2",
                  "mov sp, x29", "ldp x29, x30, [sp], #16", "ret"]
        fill = (0).to_bytes(4, "little")  # udf #0
    enc, _ = ISAS[isa].assembler().asm("; ".join(lines), 0)
    shim = bytes(enc)
    assert len(shim) <= ADAPTER_SIZE, len(shim)
    return shim + fill * ((ADAPTER_SIZE - len(shim)) // len(fill))


_FUNC_SYM = re.compile(r"wasm\[0\]::function\[(\d+)\](?:::.*)?$")


def cranelift_function(obj: bytes, isa: str, index: int) -> tuple[bytes, int, int]:
    """Return (whole .text, offset, size) of module function `index` in a `.cwasm` ELF.

    wasmtime names it `wasm[0]::function[index]`, optionally followed by `::$name`.
    """
    elf = Elf(obj)
    syms = {}
    for s in elf.symbols():
        m = _FUNC_SYM.match(s.name)
        if m:
            syms[int(m.group(1))] = s
    if index not in syms:
        raise ElfError(f"no Cranelift code for function index {index}; have "
                       f"{sorted(s.name for s in syms.values())}")
    sym = syms[index]
    sec = elf.sections[sym.shndx]
    if not sec.flags & SHF_EXECINSTR:
        raise ElfError(f"{sym.name} is not in an executable section")
    text = elf.data(sec)
    end = max(s.value + s.size for s in elf.symbols() if s.shndx == sym.shndx)
    return text[:end], sym.value, sym.size


def _lowerable_export(source: str, func: str) -> WatExport:
    ex = resolve_export(source, func)
    if ex.imported:
        raise IRCompileError("wat", "extract", f"export {func!r} is an imported function; "
                             "it has no code in this module")
    return ex


def lower_wat(source: str, isa, func: str, *, use_cli: bool | None = None) -> Lowered:
    """Cranelift native code for export `func`, behind an adapter, runnable by
    bca.emu.run_function.

    `Lowered.entry_offset` is where Cranelift's own function starts in `code`; the
    unadapted bytes are code[entry_offset : entry_offset + len]. See module docstring for
    the calling/value convention and what can and cannot run.
    """
    isa = _isa_name(isa)
    ex = _lowerable_export(source, func)
    _check_integer_signature(ex)
    timings: dict[str, float] = {}
    obj = cranelift_object(source, isa, use_cli=use_cli, timings=timings)
    t0 = time.perf_counter()
    try:
        text, off, _size = cranelift_function(obj, isa, ex.index)
    except ElfError as exc:
        raise IRCompileError("wat", "extract", str(exc)) from exc
    code = _adapter(isa, ADAPTER_SIZE + off, ex.params, ex.results[0]) + text
    timings["extract"] = (time.perf_counter() - t0) * 1e3
    return Lowered(code, isa, "wat", "cranelift", func, ADAPTER_SIZE + off, None, timings)


def cranelift_disassembly(source: str, isa, func: str) -> list[str]:
    """Capstone listing of Cranelift's bytes for export `func` (without the adapter)."""
    isa = _isa_name(isa)
    ex = _lowerable_export(source, func)
    text, off, size = cranelift_function(cranelift_object(source, isa), isa, ex.index)
    cs = ISAS[isa].disassembler()
    return [f"{i.address:#06x}: {i.mnemonic} {i.op_str}".rstrip()
            for i in cs.disasm(text[off : off + size], off)]
