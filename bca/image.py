"""Whole programs: assemble one GNU as source file and link it into a fixed-address image.

`assemble_program(source, isa)` runs GNU as on the complete file (x86-64 gets
`.intel_syntax noprefix` prepended) and returns the relocatable object. `link_program(obj, isa)`
is a tiny static linker for exactly one such object with a fixed layout:

    .text (every executable section, in object order)        at 0x400000        r-x
    .rodata (read-only allocated sections, then the GOT)     next page boundary r--
    .data (writable PROGBITS sections)                       next page boundary rw-
    .bss (NOBITS sections, then COMMON symbols)              next page boundary rw- (zeroed)

The tail of the text page after the last byte of code is filled with trap instructions, so a
program that runs off the end of its code stops there (bca.process reports it as `no_exit`).

Mergeable sections (`.rodata.str1.1`, `.rodata.cst8`, ...) are laid out as ordinary sections
(merging is only a size optimisation). GOT-relative relocations (`@GOTPCREL`, `:got:`) get a
read-only GOT in the rodata segment. TLS, IFUNC, undefined and weak-undefined symbols, and
relocation types outside the table below are rejected with LinkError.
"""

import os
import re
import resource
import subprocess
import tempfile
from dataclasses import dataclass, field

from bca.ir import elf as _elf
from bca.ir.elf import MACHINES, SHF_ALLOC, SHF_EXECINSTR, SHT_NOBITS, SHT_REL, SHT_RELA, Elf, ElfError

TEXT_BASE = 0x400000
PAGE = 0x1000
SEGMENT_LIMIT = 1 << 26  # text / rodata / data each at most 64 MiB
ASM_TIMEOUT = 20.0
ASM_OBJECT_LIMIT = 1 << 26
ASM_MEMORY_LIMIT = 1 << 30  # GNU as address space

SHF_WRITE = 0x1
SHT_NOTE = 7
SHF_TLS = 0x400
SHN_ABS = 0xFFF1
SHN_COMMON = 0xFFF2
SHN_LORESERVE = 0xFF00
STT_SECTION = 3
STT_FILE = 4
STT_TLS = 6
STT_GNU_IFUNC = 10
STB_WEAK = 2

_TRAP = {"x86_64": b"\xcc", "aarch64": b"\0\0\0\0"}  # int3 / udf #0


class AsmError(ValueError):
    """GNU as rejected the program. The message carries the program's own line numbers."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


class LinkError(ValueError):
    """The object cannot be linked into an image."""


@dataclass
class Image:
    isa: str
    entry: int
    segments: list[tuple[int, bytes, str]]  # (addr, bytes, "rx" / "r" / "rw"), page aligned
    symbols: dict[str, int]
    bss: tuple[int, int]  # (addr, size); size 0 when there is no bss
    text_end: int = 0  # first address after the code proper (the trap fill starts here)
    sections: dict[str, tuple[int, int]] = field(default_factory=dict)  # name -> (addr, size)

    def segment_of(self, addr: int) -> tuple[int, int, str] | None:
        """(start, end, perms) of the mapped region containing `addr` (bss included)."""
        for base, data, perms in self.segments:
            if base <= addr < base + _page_up(len(data)):
                return base, base + _page_up(len(data)), perms
        if self.bss[1] and self.bss[0] <= addr < self.bss[0] + _page_up(self.bss[1]):
            return self.bss[0], self.bss[0] + _page_up(self.bss[1]), "rw"
        return None


def _page_up(n: int) -> int:
    return (n + PAGE - 1) & ~(PAGE - 1)


# -- assembling ------------------------------------------------------------------------------

_GAS_DIAG_RE = re.compile(r"^\{standard input\}:(\d+): (Error|Warning|Fatal error): (.*)$", re.M)
# The prompt's "Not allowed" list, the same set the stream path refuses (bca.stream), is rejected
# before GNU as runs, wherever the name appears outside a string or comment. Refusing macros and
# repetition blocks also closes name building (`.inc\()bin` inside a `.macro`): with no macro
# expansion, GNU as reads every directive name as written, so a program cannot reach the host
# (`.incbin` / `.include`). Backslashes outside strings, character literals and comments are
# refused too.
# Strings, character literals and comments, scanned left to right as GNU as does (x86-64: `#` to
# the end of the line; AArch64: `//`, or `#` first on a line; both: `/* */`). Masked before the
# check, so a comment or string may mention a refused name.
_LITERAL = r'"(?:\\.|[^"\\\n])*"|\'\\?.|/\*.*?(?:\*/|\Z)'
_MASK_RE = {"x86_64": re.compile(_LITERAL + r"|#[^\n]*", re.S),
            "aarch64": re.compile(_LITERAL + r"|//[^\n]*|^[ \t]*#[^\n]*", re.S | re.M)}
_DIRECTIVE_RE = re.compile(r"(?<![\w.$])\.[A-Za-z_][\w.$]*")
_ASM_ONLY_REFUSED = {".incbin", ".include", ".altmacro", ".noaltmacro"}


def _refused_directive(name: str) -> str | None:
    from bca.stream import _REFUSED_DIRECTIVES

    name = name.lower()
    if name == ".rodata":  # `.section .rodata` is allowed; GNU as rejects a bare `.rodata` itself
        return None
    if name in _REFUSED_DIRECTIVES:
        return _REFUSED_DIRECTIVES[name]
    if name in _ASM_ONLY_REFUSED:
        return f"`{name}` is not allowed"
    if name.startswith(".cfi_"):
        return "`.cfi_*` directives are not supported"
    if name.startswith(".if"):
        return "conditional assembly is not supported"
    return None


def _check_source(source: str, isa_name: str, prompt_rules: bool = True) -> None:
    masked = _MASK_RE[isa_name].sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)
    for m in _DIRECTIVE_RE.finditer(masked):
        why = _refused_directive(m.group(0))
        if why:
            line = source.count("\n", 0, m.start()) + 1
            raise AsmError(f"line {line}: {why}", line)
    bs = masked.find("\\")
    if bs >= 0:
        line = source.count("\n", 0, bs) + 1
        raise AsmError(f"line {line}: a backslash outside a string or character literal is not "
                       "allowed", line)
    if prompt_rules:
        _check_layout(masked, isa_name)


_LABEL_PREFIX_RE = re.compile(r"\s*(?:[A-Za-z_.$][\w.$]*|\d+)\s*:(?!:)")
_WORD_RE = re.compile(r"[A-Za-z_.$][\w.$]*")
_PROMPT_SECTIONS = (".text", ".rodata", ".data", ".bss", ".note.GNU-stack")


def _check_layout(masked: str, isa_name: str) -> None:
    """The prompt's placement and constant rules, as the stream path enforces them (bca.stream
    program mode): instructions only in .text; only alignment in .text besides instructions; only
    .zero/.space/.skip and alignment in .bss; every constant defined before its first use."""
    from bca.stream import _ASSIGN_RE, _data_width

    x86 = isa_name == "x86_64"
    data = {".ascii", ".asciz", ".string", ".zero", ".space", ".skip"}
    fills = {".zero", ".space", ".skip"}
    section, seen = ".text", set()
    for n, raw in enumerate(masked.split("\n"), 1):
        for stmt in raw.split(";"):
            while m := _LABEL_PREFIX_RE.match(stmt):
                seen.add(m.group(0).strip()[:-1].strip())
                stmt = stmt[m.end():]
            stmt = stmt.strip()
            if not stmt:
                continue
            words = _WORD_RE.findall(stmt)
            a = _ASSIGN_RE.fullmatch(re.sub(r"\s+", " ", stmt))
            if a:
                name = a[1] or a[2]
                if name in seen:
                    raise AsmError(f"line {n}: {name!r} was used before this definition; define "
                                   "constants first", n)
                seen.update(words)
                continue
            seen.update(words)
            head = stmt.split(None, 1)[0].lower()
            if head.startswith("."):
                if head in (".text", ".data", ".bss"):
                    section = head
                elif head == ".section":
                    section = (stmt.split(None, 1)[1:] or [""])[0].split(",")[0].strip()
                    if section not in _PROMPT_SECTIONS:
                        raise AsmError(f"line {n}: section {section!r} is not supported (use .text, "
                                       ".rodata, .data or .bss)", n)
                elif head in data or _data_width(x86, head) is not None:
                    if section == ".note.GNU-stack":
                        raise AsmError(f"line {n}: nothing may be placed in {section}", n)
                    if section == ".text":
                        raise AsmError(f"line {n}: {head} is not allowed in .text (only "
                                       "instructions and alignment); put data in .data or .rodata", n)
                    if section == ".bss" and head not in fills:
                        raise AsmError(f"line {n}: {head} is not allowed in .bss (only "
                                       ".zero/.space/.skip and alignment)", n)
            elif section != ".text":
                raise AsmError(f"line {n}: instructions belong in .text (the current section is "
                               f"{section})", n)


def _gas(isa_name: str) -> str:
    from bca.stream import _gas_tool

    tool = _gas_tool(isa_name)
    if tool is None:
        raise RuntimeError(f"GNU as for {isa_name} not found (install binutils for it or set "
                           f"BCA_GAS_{isa_name.upper()})")
    return tool


def _limits() -> None:  # runs in the GNU as child
    resource.setrlimit(resource.RLIMIT_FSIZE, (ASM_OBJECT_LIMIT, ASM_OBJECT_LIMIT))
    resource.setrlimit(resource.RLIMIT_AS, (ASM_MEMORY_LIMIT, ASM_MEMORY_LIMIT))
    cpu = int(ASM_TIMEOUT) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))


def assemble_program(source: str, isa_name: str, *, prompt_rules: bool = True) -> bytes:
    """Assemble a whole program with GNU as; returns the relocatable ELF object.

    Raises AsmError with GNU as's diagnostics (line numbers are the source's own, 1-based).
    prompt_rules=False skips the app prompt's placement and constant rules (the stream path's; for
    linker tests); file inclusion, macros and the other refused directives are always refused.
    """
    if isa_name not in MACHINES:
        raise ValueError(f"unknown ISA {isa_name!r}")
    _check_source(source, isa_name, prompt_rules)
    header = [".intel_syntax noprefix"] if isa_name == "x86_64" else []
    text = "\n".join(header + [source]) + "\n"
    with tempfile.TemporaryDirectory(prefix="bca-prog-") as tmp:
        obj = os.path.join(tmp, "prog.o")
        try:
            proc = subprocess.run([_gas(isa_name), "-o", obj, "--"], input=text.encode(),
                                  capture_output=True, timeout=ASM_TIMEOUT, cwd=tmp,
                                  preexec_fn=_limits, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        except subprocess.TimeoutExpired:
            raise AsmError(f"GNU as timed out after {ASM_TIMEOUT:.0f} s") from None
        diag = proc.stderr.decode(errors="replace")
        errors = []
        first_line = None
        for m in _GAS_DIAG_RE.finditer(diag):
            if m.group(2) == "Warning":
                continue
            line = int(m.group(1)) - len(header)
            first_line = first_line or line
            errors.append(f"line {line}: {m.group(3)}")
        if proc.returncode != 0 or errors:
            if not errors:
                reason = (f"killed by signal {-proc.returncode}" if proc.returncode < 0
                          else f"exit status {proc.returncode}")
                tail = diag.strip().splitlines()[-3:]
                errors = [f"GNU as failed ({reason}; limits: object {ASM_OBJECT_LIMIT >> 20} MiB, "
                          f"memory {ASM_MEMORY_LIMIT >> 20} MiB)"
                          + (": " + " / ".join(tail) if tail else "")]
            shown = errors[:20] + ([f"(+{len(errors) - 20} more errors)"] if len(errors) > 20 else [])
            raise AsmError("\n".join(shown), first_line)
        with open(obj, "rb") as f:
            return f.read()


# -- relocations -----------------------------------------------------------------------------

# x86-64 types beyond bca.ir.elf's table: (width, pc_relative, signed)
_X86_SMALL = {12: (2, False, False), 13: (2, True, True), 14: (1, False, False), 15: (1, True, True)}
_X86_GOT = {9, 41, 42}  # GOTPCREL, GOTPCRELX, REX_GOTPCRELX: PC32 to a GOT slot
_X86_EXTRA_NAMES = {12: "R_X86_64_16", 13: "R_X86_64_PC16", 14: "R_X86_64_8", 15: "R_X86_64_PC8"}
# AArch64 types beyond bca.ir.elf's table.
_A64_EXTRA_NAMES = {259: "ABS16", 262: "PREL16", 263: "MOVW_UABS_G0", 264: "MOVW_UABS_G0_NC",
                    265: "MOVW_UABS_G1", 266: "MOVW_UABS_G1_NC", 267: "MOVW_UABS_G2",
                    268: "MOVW_UABS_G2_NC", 269: "MOVW_UABS_G3", 270: "MOVW_SABS_G0",
                    271: "MOVW_SABS_G1", 272: "MOVW_SABS_G2", 273: "LD_PREL_LO19",
                    276: "ADR_PREL_PG_HI21_NC"}
_A64_BASE = {257, 258, 260, 261, 274, 275, 277, 278, 279, 280, 282, 283, 284, 285, 286, 299}
_A64_GOT = {311, 312}  # ADR_GOT_PAGE, LD64_GOT_LO12_NC
_A64_LDST_SIZE = {284: 2, 285: 4, 286: 8, 299: 16}


def reloc_name(isa_name: str, rtype: int) -> str:
    if isa_name == "x86_64":
        return _X86_EXTRA_NAMES.get(rtype) or _elf.reloc_name(isa_name, rtype)
    if rtype in _A64_EXTRA_NAMES:
        return "R_AARCH64_" + _A64_EXTRA_NAMES[rtype]
    return _elf.reloc_name(isa_name, rtype)


def _fits(v: int, bits: int, signed: bool) -> bool:
    return _elf._fits(v, bits, signed)


def _apply(isa_name: str, buf: bytearray, off: int, rtype: int, S: int, A: int, P: int,
           got: int | None) -> None:
    """Patch one relocation. `got` is the address of the symbol's GOT slot (GOT types)."""
    if isa_name == "x86_64":
        if rtype in _X86_GOT:
            _elf._apply_x86(buf, off, 2, got, A, P)
        elif rtype in _elf._X86:
            _elf._apply_x86(buf, off, rtype, S, A, P)
        elif rtype in _X86_SMALL:
            width, pcrel, signed = _X86_SMALL[rtype]
            val = S + A - (P if pcrel else 0)
            if not (_fits(val, 8 * width, True) or (not signed and _fits(val, 8 * width, False))):
                raise LinkError(f"{reloc_name(isa_name, rtype)} overflow: {val:#x}")
            buf[off:off + width] = (val & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
        else:
            raise LinkError(f"unsupported relocation {reloc_name(isa_name, rtype)}")
        return
    insn = int.from_bytes(buf[off:off + 4], "little")

    def put(v: int) -> None:
        buf[off:off + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    if rtype in _A64_LDST_SIZE and (S + A) % _A64_LDST_SIZE[rtype]:
        raise LinkError(f"{reloc_name(isa_name, rtype)}: address {S + A:#x} is not "
                        f"{_A64_LDST_SIZE[rtype]}-byte aligned (the :lo12: offset would be truncated)")
    if rtype in _A64_BASE:
        _elf._apply_a64(buf, off, rtype, S, A, P)
    elif rtype == 311:
        _elf._apply_a64(buf, off, 275, got, 0, P)
    elif rtype == 312:
        _elf._apply_a64(buf, off, 286, got, 0, P)
    elif rtype in (259, 262):
        val = S + A - (P if rtype == 262 else 0)
        if not (_fits(val, 16, True) or (rtype == 259 and _fits(val, 16, False))):
            raise LinkError(f"{reloc_name(isa_name, rtype)} overflow: {val:#x}")
        buf[off:off + 2] = (val & 0xFFFF).to_bytes(2, "little")
    elif 263 <= rtype <= 272:
        val = S + A
        group = {263: 0, 264: 0, 265: 1, 266: 1, 267: 2, 268: 2, 269: 3, 270: 0, 271: 1, 272: 2}[rtype]
        check = rtype in (263, 265, 267)
        if check and not _fits(val, 16 * (group + 1), False):
            raise LinkError(f"{reloc_name(isa_name, rtype)} overflow: {val:#x}")
        if rtype >= 270:  # signed: movz for >= 0, movn for < 0
            if not _fits(val, 16 * (group + 1) + 1, True):
                raise LinkError(f"{reloc_name(isa_name, rtype)} overflow: {val:#x}")
            if val < 0:
                insn &= ~(0x3 << 29)  # opc 00 = movn
                val = ~val
            else:
                insn = (insn & ~(0x3 << 29)) | (0x2 << 29)  # opc 10 = movz
        imm = (val >> (16 * group)) & 0xFFFF
        put((insn & ~(0xFFFF << 5)) | (imm << 5))
    elif rtype == 273:
        val = S + A - P
        if val & 3 or not _fits(val >> 2, 19, True):
            raise LinkError(f"{reloc_name(isa_name, rtype)} out of range: {val:#x}")
        put((insn & ~(0x7FFFF << 5)) | (((val >> 2) & 0x7FFFF) << 5))
    elif rtype == 276:
        val = ((S + A) >> 12) - (P >> 12)
        put((insn & ~((3 << 29) | (0x7FFFF << 5))) | ((val & 3) << 29) | (((val >> 2) & 0x7FFFF) << 5))
    else:
        raise LinkError(f"unsupported relocation {reloc_name(isa_name, rtype)}")


def _reloc_width(isa_name: str, rtype: int) -> int:
    if isa_name == "x86_64":
        return {1: 8, 24: 8, 12: 2, 13: 2, 14: 1, 15: 1}.get(rtype, 4)
    return {257: 8, 260: 8, 259: 2, 262: 2}.get(rtype, 4)


def _is_got(isa_name: str, rtype: int) -> bool:
    return rtype in (_X86_GOT if isa_name == "x86_64" else _A64_GOT)


# -- linking ---------------------------------------------------------------------------------

def link_program(obj: bytes, isa_name: str, *, entry_symbol: str = "_start",
                 bss_size_limit: int = 1 << 24) -> Image:
    """Link one relocatable object into an Image at the fixed layout (see module docstring)."""
    if isa_name not in MACHINES:
        raise ValueError(f"unknown ISA {isa_name!r}")
    try:
        elf = Elf(obj)
        syms = elf.symbols()
    except (ElfError, IndexError, ValueError) as exc:  # struct.error is a ValueError
        raise LinkError(f"not a valid ELF object: {exc}") from None
    if elf.machine != MACHINES[isa_name]:
        raise LinkError(f"object is for e_machine {elf.machine}, expected {MACHINES[isa_name]} ({isa_name})")

    text, rodata, data, bss = [], [], [], []
    for sec in elf.sections:
        if not sec.flags & SHF_ALLOC or sec.size == 0 and sec.type != SHT_NOBITS:
            continue
        if sec.type in (SHT_RELA, SHT_REL, SHT_NOTE):
            continue  # notes (.note.gnu.property) are metadata, never loaded
        if sec.flags & SHF_TLS:
            raise LinkError(f"thread-local section {sec.name} is not supported")
        if sec.flags & SHF_EXECINSTR:
            if sec.type == SHT_NOBITS:
                raise LinkError(f"executable NOBITS section {sec.name}")
            text.append(sec)
        elif sec.type == SHT_NOBITS:
            if sec.size:
                bss.append(sec)
        elif sec.flags & SHF_WRITE:
            data.append(sec)
        else:
            rodata.append(sec)

    # Relocations of every allocated section; GOT slots for GOT-relative ones.
    relocs = {}
    got_syms: list[int] = []
    for sec in text + rodata + data + bss:
        relocs[sec.index] = elf.relocations_for(sec)
        if relocs[sec.index] and sec.type == SHT_NOBITS:
            raise LinkError(f"relocations in NOBITS section {sec.name}")
        for r in relocs[sec.index]:
            if _is_got(isa_name, r.type) and r.sym_index not in got_syms:
                got_syms.append(r.sym_index)

    addr: dict[int, int] = {}
    sections: dict[str, tuple[int, int]] = {}

    def place(secs, start: int) -> tuple[int, bytearray]:
        blob = bytearray()
        for sec in secs:
            align = max(sec.align, 1)
            if align & (align - 1) or align > PAGE:
                raise LinkError(f"section {sec.name}: unsupported alignment {align}")
            blob.extend(b"\0" * (-len(blob) % align))
            addr[sec.index] = start + len(blob)
            sections.setdefault(sec.name, (start + len(blob), sec.size))
            if sec.type != SHT_NOBITS:
                blob.extend(elf.data(sec))
            else:
                blob.extend(b"\0" * sec.size)
            if len(blob) > SEGMENT_LIMIT:
                raise LinkError(f"segment too large (> {SEGMENT_LIMIT >> 20} MiB) at section {sec.name}")
        return start, blob

    if not text:
        raise LinkError("no code: the object has no executable section")
    text_addr, text_blob = place(text, TEXT_BASE)
    text_end = text_addr + len(text_blob)
    ro_addr = _page_up(text_end + 1)  # +1: always leave room for at least one trap
    _, ro_blob = place(rodata, ro_addr)
    got_addr = {}
    if got_syms:
        ro_blob.extend(b"\0" * (-len(ro_blob) % 8))
        for si in got_syms:
            got_addr[si] = ro_addr + len(ro_blob)
            ro_blob.extend(b"\0" * 8)
    data_addr = _page_up(ro_addr + len(ro_blob))
    _, data_blob = place(data, data_addr)
    bss_addr = _page_up(data_addr + len(data_blob))
    bss_size = 0
    for sec in bss:
        align = max(sec.align, 1)
        if align & (align - 1) or align > PAGE:
            raise LinkError(f"section {sec.name}: unsupported alignment {align}")
        bss_size += -bss_size % align
        addr[sec.index] = bss_addr + bss_size
        sections.setdefault(sec.name, (bss_addr + bss_size, sec.size))
        bss_size += sec.size
        if bss_size > bss_size_limit:
            break

    # Symbol addresses.
    def resolve(si: int) -> int:
        s = syms[si]
        if s.type == STT_TLS:
            raise LinkError(f"thread-local symbol {s.name!r} is not supported")
        if s.type == STT_GNU_IFUNC:
            raise LinkError(f"IFUNC symbol {s.name!r} is not supported")
        if s.shndx == 0:
            weak = " (weak)" if s.bind == STB_WEAK else ""
            raise LinkError(f"undefined symbol {s.name!r}{weak}: the program must define every "
                            "symbol it uses (there is no libc or other library)")
        if s.shndx == SHN_ABS:
            return s.value
        if s.shndx == SHN_COMMON:
            return common[si]
        if s.shndx >= SHN_LORESERVE or s.shndx >= len(elf.sections):
            raise LinkError(f"symbol {s.name!r} has unsupported section index {s.shndx:#x}")
        if s.shndx not in addr:
            name = elf.sections[s.shndx].name
            raise LinkError(f"symbol {s.name or name!r} lives in non-allocated section {name}")
        return addr[s.shndx] + s.value

    common: dict[int, int] = {}
    for si, s in enumerate(syms):
        if s.shndx == SHN_COMMON:  # .comm: value is the alignment
            align = max(s.value, 1)
            if align & (align - 1) or align > PAGE:
                raise LinkError(f"common symbol {s.name!r}: unsupported alignment {align}")
            bss_size += -bss_size % align
            common[si] = bss_addr + bss_size
            bss_size += s.size
    if bss_size > bss_size_limit:
        raise LinkError(f".bss is {bss_size} bytes, over the limit of {bss_size_limit} bytes")

    symbols: dict[str, int] = {}
    for si, s in enumerate(syms):
        if not s.name or s.type in (STT_SECTION, STT_FILE) or s.shndx == 0 or s.name[0] == "$":
            continue
        if s.shndx < SHN_LORESERVE and s.shndx not in addr:
            continue  # symbols in non-allocated sections (debug info)
        symbols.setdefault(s.name, resolve(si))

    entry_syms = [si for si, s in enumerate(syms) if s.name == entry_symbol and s.shndx != 0]
    if not entry_syms:
        raise LinkError(f"entry point {entry_symbol!r} is not defined (define it as a label, e.g. "
                        f"`.globl {entry_symbol}` / `{entry_symbol}:` in .text)")
    entry = resolve(entry_syms[0])
    if not text_addr <= entry < text_end:
        raise LinkError(f"entry point {entry_symbol!r} ({entry:#x}) is not in an executable section")
    if isa_name == "aarch64" and entry & 3:
        raise LinkError(f"entry point {entry_symbol!r} ({entry:#x}) is not 4-byte aligned")

    blobs = {"text": (text_addr, text_blob), "ro": (ro_addr, ro_blob), "data": (data_addr, data_blob)}

    def blob_for(address: int) -> tuple[int, bytearray]:
        for base, blob in blobs.values():
            if base <= address < base + len(blob):
                return base, blob
        raise LinkError(f"internal link error: no segment holds {address:#x}")

    for si, slot in got_addr.items():
        ro_blob[slot - ro_addr:slot - ro_addr + 8] = resolve(si).to_bytes(8, "little")
    for sec in text + rodata + data:
        for r in relocs[sec.index]:
            if r.sym_index >= len(syms):
                raise LinkError(f"relocation at {sec.name}+{r.offset:#x} names bad symbol #{r.sym_index}")
            name = r.symbol or "?"
            if r.offset + _reloc_width(isa_name, r.type) > sec.size:
                raise LinkError(f"relocation at {sec.name}+{r.offset:#x} is outside the section")
            try:
                S = resolve(r.sym_index)
                P = addr[sec.index] + r.offset
                base, blob = blob_for(P)
                _apply(isa_name, blob, P - base, r.type, S, r.addend, P, got_addr.get(r.sym_index))
            except (LinkError, _elf.RelocationError) as exc:
                raise LinkError(f"{exc} (symbol {name!r} at {sec.name}+{r.offset:#x})") from None

    # Trap fill from the end of the code to the end of its page.
    tail = _page_up(len(text_blob) + 1) - len(text_blob)
    trap = _TRAP[isa_name]
    text_blob.extend((trap * (tail // len(trap) + 1))[:tail])

    segments = [(text_addr, bytes(text_blob), "rx")]
    if ro_blob:
        segments.append((ro_addr, bytes(ro_blob), "r"))
    if data_blob:
        segments.append((data_addr, bytes(data_blob), "rw"))
    return Image(isa=isa_name, entry=entry, segments=segments, symbols=symbols,
                 bss=(bss_addr, bss_size), text_end=text_end, sections=sections)
