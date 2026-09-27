"""Minimal ELF64 little-endian reader plus a tiny static linker for one relocatable object.

`extract_function` turns a compiler's `.o` into a single flat blob whose entry is offset 0:
the function's code section first, then every allocatable section it (transitively)
references through relocations (constant pools, jump tables, read-only data, local helper
functions in other sections), with all relocations to *defined* symbols applied for a load
address of `base` (CODE_BASE by default). Relocations against undefined symbols (external
calls, libc, compiler runtime helpers like memcpy/__udivti3) or of unsupported types are
rejected with RelocationError, naming each one.
"""

import struct
from dataclasses import dataclass

EM_X86_64 = 62
EM_AARCH64 = 183
MACHINES = {"x86_64": EM_X86_64, "aarch64": EM_AARCH64}

SHT_SYMTAB = 2
SHT_RELA = 4
SHT_REL = 9
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHT_NOBITS = 8
SHN_UNDEF = 0


class ElfError(ValueError):
    pass


class RelocationError(ElfError):
    """A relocation could not be resolved (undefined symbol, unsupported type, overflow)."""


@dataclass
class Section:
    index: int
    name: str
    type: int
    flags: int
    offset: int
    size: int
    link: int
    info: int
    align: int
    entsize: int


@dataclass
class Symbol:
    name: str
    value: int
    size: int
    type: int
    bind: int
    shndx: int


@dataclass
class Reloc:
    offset: int
    type: int
    symbol: str
    addend: int
    sym_index: int = 0


class Elf:
    def __init__(self, blob: bytes):
        if blob[:4] != b"\x7fELF" or blob[4] != 2 or blob[5] != 1:
            raise ElfError("not a little-endian ELF64 object")
        self.blob = bytes(blob)
        (self.machine,) = struct.unpack_from("<H", blob, 18)
        shoff, = struct.unpack_from("<Q", blob, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", blob, 0x3A)
        raw = []
        for i in range(shnum):
            raw.append(struct.unpack_from("<IIQQQQIIQQ", blob, shoff + i * shentsize))
        strtab = raw[shstrndx]
        self.sections = [
            Section(i, self._str(strtab[4], r[0]), r[1], r[2], r[4], r[5], r[6], r[7], r[8], r[9])
            for i, r in enumerate(raw)
        ]

    def _str(self, table_off: int, idx: int) -> str:
        end = self.blob.index(b"\0", table_off + idx)
        return self.blob[table_off + idx : end].decode(errors="replace")

    def data(self, sec: Section) -> bytes:
        return self.blob[sec.offset : sec.offset + sec.size]

    def symbols(self) -> list[Symbol]:
        out = []
        for sec in self.sections:
            if sec.type != SHT_SYMTAB:
                continue
            stroff = self.sections[sec.link].offset
            for off in range(sec.offset, sec.offset + sec.size, 24):
                name, info, _other, shndx, value, size = struct.unpack_from("<IBBHQQ", self.blob, off)
                out.append(Symbol(self._str(stroff, name), value, size, info & 0xF, info >> 4, shndx))
        return out

    def relocations_for(self, target: Section) -> list[Reloc]:
        syms = self.symbols()
        out = []
        for sec in self.sections:
            if sec.type not in (SHT_RELA, SHT_REL) or sec.info != target.index:
                continue
            step = 24 if sec.type == SHT_RELA else 16
            for off in range(sec.offset, sec.offset + sec.size, step):
                if sec.type == SHT_RELA:
                    r_off, r_info, addend = struct.unpack_from("<QQq", self.blob, off)
                else:
                    (r_off, r_info), addend = struct.unpack_from("<QQ", self.blob, off), 0
                sym_idx = r_info >> 32
                name = syms[sym_idx].name if sym_idx < len(syms) else f"#{sym_idx}"
                if not name and sym_idx < len(syms) and syms[sym_idx].shndx < len(self.sections):
                    name = self.sections[syms[sym_idx].shndx].name  # section symbol
                out.append(Reloc(r_off, r_info & 0xFFFFFFFF, name, addend, sym_idx))
        return out



# x86_64 relocation types: (width, pc_relative, signed range check)
_X86 = {
    1: (8, False),   # R_X86_64_64
    2: (4, True),    # R_X86_64_PC32
    4: (4, True),    # R_X86_64_PLT32 (local definition: same as PC32)
    10: (4, False),  # R_X86_64_32
    11: (4, False),  # R_X86_64_32S
    24: (8, True),   # R_X86_64_PC64
}
_X86_NAMES = {1: "R_X86_64_64", 2: "R_X86_64_PC32", 4: "R_X86_64_PLT32", 9: "R_X86_64_GOTPCREL",
              10: "R_X86_64_32", 11: "R_X86_64_32S", 24: "R_X86_64_PC64",
              41: "R_X86_64_GOTPCRELX", 42: "R_X86_64_REX_GOTPCRELX"}
_A64_NAMES = {257: "ABS64", 258: "ABS32", 260: "PREL64", 261: "PREL32", 274: "ADR_PREL_LO21",
              275: "ADR_PREL_PG_HI21", 277: "ADD_ABS_LO12_NC", 278: "LDST8_ABS_LO12_NC",
              279: "TSTBR14", 280: "CONDBR19", 282: "JUMP26", 283: "CALL26",
              284: "LDST16_ABS_LO12_NC", 285: "LDST32_ABS_LO12_NC", 286: "LDST64_ABS_LO12_NC",
              299: "LDST128_ABS_LO12_NC", 311: "ADR_GOT_PAGE", 312: "LD64_GOT_LO12_NC"}
_A64_LDST_SHIFT = {278: 0, 284: 1, 285: 2, 286: 3, 299: 4}
# Relocation types whose result depends on the absolute load address, not just on the
# distance between P and S. AArch64 ADRP (page delta: (S>>12)-(P>>12)) and the :lo12:
# ADD/LDST fixups encode bits of the absolute address, so code using them is only valid when
# loaded at `base` (or at least at the same offset within a 4 KiB page).
_ABSOLUTE = {("x86_64", 1), ("x86_64", 10), ("x86_64", 11),
             ("aarch64", 257), ("aarch64", 258), ("aarch64", 275), ("aarch64", 277),
             *(("aarch64", t) for t in _A64_LDST_SHIFT)}


def reloc_name(isa_name: str, rtype: int) -> str:
    if isa_name == "x86_64":
        return _X86_NAMES.get(rtype, f"x86_64 type {rtype}")
    return "R_AARCH64_" + _A64_NAMES.get(rtype, f"type {rtype}")


def _fits(value: int, bits: int, signed: bool = True) -> bool:
    if signed:
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    return 0 <= value < (1 << bits)


def _apply_x86(buf: bytearray, off: int, rtype: int, S: int, A: int, P: int) -> None:
    width, pcrel = _X86[rtype]
    val = S + A - P if pcrel else S + A
    if rtype == 10 and not _fits(val, 32, signed=False):
        raise RelocationError(f"R_X86_64_32 overflow: {val:#x}")
    if width == 4 and rtype != 10 and not _fits(val, 32):
        raise RelocationError(f"{_X86_NAMES[rtype]} overflow: {val:#x}")
    buf[off : off + width] = (val & ((1 << (8 * width)) - 1)).to_bytes(width, "little")


def _apply_a64(buf: bytearray, off: int, rtype: int, S: int, A: int, P: int) -> None:
    insn = int.from_bytes(buf[off : off + 4], "little")

    def put(v: int) -> None:
        buf[off : off + 4] = (v & 0xFFFFFFFF).to_bytes(4, "little")

    if rtype == 257:
        buf[off : off + 8] = ((S + A) & ((1 << 64) - 1)).to_bytes(8, "little")
    elif rtype in (258, 261):
        val = S + A - (P if rtype == 261 else 0)
        if not _fits(val, 32) and not (rtype == 258 and _fits(val, 32, signed=False)):
            raise RelocationError(f"{reloc_name('aarch64', rtype)} overflow: {val:#x}")
        buf[off : off + 4] = (val & 0xFFFFFFFF).to_bytes(4, "little")
    elif rtype == 260:
        buf[off : off + 8] = ((S + A - P) & ((1 << 64) - 1)).to_bytes(8, "little")
    elif rtype in (274, 275):
        val = S + A - P if rtype == 274 else ((S + A) >> 12) - (P >> 12)
        if not _fits(val, 21):
            raise RelocationError(f"{reloc_name('aarch64', rtype)} overflow: {val:#x}")
        put((insn & ~((3 << 29) | (0x7FFFF << 5))) | ((val & 3) << 29) | (((val >> 2) & 0x7FFFF) << 5))
    elif rtype == 277 or rtype in _A64_LDST_SHIFT:
        imm = ((S + A) & 0xFFF) >> _A64_LDST_SHIFT.get(rtype, 0)
        put((insn & ~(0xFFF << 10)) | (imm << 10))
    elif rtype in (282, 283, 280, 279):
        val = S + A - P
        bits, shift = {282: (26, 0), 283: (26, 0), 280: (19, 5), 279: (14, 5)}[rtype]
        if val & 3 or not _fits(val >> 2, bits):
            raise RelocationError(f"{reloc_name('aarch64', rtype)} out of range: {val:#x}")
        mask = ((1 << bits) - 1) << shift
        put((insn & ~mask) | (((val >> 2) << shift) & mask))
    else:
        raise RelocationError(f"unsupported relocation {reloc_name('aarch64', rtype)}")


def _entry_trampoline(isa_name: str, target: int, pad: int) -> bytes:
    """A jump from offset 0 to blob offset `target`, padded to `pad` bytes."""
    if isa_name == "x86_64":
        rel = target - 5
        if not _fits(rel, 32):
            raise RelocationError(f"entry trampoline out of range: {target:#x}")
        jmp = b"\xe9" + rel.to_bytes(4, "little", signed=True)
        return jmp + b"\x90" * (pad - len(jmp))
    if target & 3 or not _fits(target >> 2, 26):
        raise RelocationError(f"entry trampoline out of range: {target:#x}")
    insn = 0x14000000 | ((target >> 2) & 0x3FFFFFF)  # b <target>
    nop = (0xD503201F).to_bytes(4, "little")
    return insn.to_bytes(4, "little") + nop * ((pad - 4) // 4)


def trampoline_target(isa_name: str, code: bytes) -> int | None:
    """Blob offset the entry trampoline at offset 0 jumps to (None if `code` has none).

    Decodes the exact instruction `_entry_trampoline` emits; used as an independent check that
    the bytes really enter the requested function.
    """
    if isa_name == "x86_64":
        if len(code) < 5 or code[0] != 0xE9:
            return None
        return 5 + int.from_bytes(code[1:5], "little", signed=True)
    if len(code) < 4:
        return None
    insn = int.from_bytes(code[:4], "little")
    if insn & 0xFC000000 != 0x14000000:
        return None
    imm = insn & 0x3FFFFFF
    return (imm - (1 << 26) if imm >> 25 else imm) << 2


def verify_entry(elf: "Elf", isa_name: str, func: str, code: bytes, entry: int,
                 text_relocs: list[Reloc]) -> None:
    """Independent post-link check: `code` really enters `func`.

    Re-resolves `func` in the object and checks that (a) the bytes at `entry` are the
    function's own bytes (ignoring bytes patched by relocations) and (b) execution starting at
    offset 0 reaches `entry` (directly, or through the trampoline). Raises ElfError, so a
    mis-link is never returned as runnable bytes.
    """
    sym = next(s for s in elf.symbols() if s.name == func and s.shndx != SHN_UNDEF)
    sec = elf.sections[sym.shndx]
    orig = elf.data(sec)
    n = min(max(sym.size, 1), 64, len(orig) - sym.value, len(code) - entry)
    patched = set()
    for r in text_relocs:
        patched.update(range(r.offset, r.offset + 8))
    for i in range(n):
        if sym.value + i not in patched and code[entry + i] != orig[sym.value + i]:
            raise ElfError(f"{func}: internal link error: bytes at entry offset {entry:#x} are "
                           f"not {func}'s code (mismatch at +{i:#x})")
    start = 0 if entry == 0 else trampoline_target(isa_name, code)
    if start != entry:
        raise ElfError(f"{func}: internal link error: offset 0 enters at {start!r}, "
                       f"but {func} resolved to offset {entry:#x}")


@dataclass
class Linked:
    code: bytes
    entry_offset: int  # offset of the function's first instruction within `code`
    sections: list[str]  # sections laid out, in order
    relocations: int  # relocations applied
    position_dependent: bool  # absolute relocations: bytes valid only at `base`


def extract_function(obj: bytes, isa_name: str, func: str, *, base: int = 0x100000) -> Linked:
    """Link `func` from relocatable object `obj` into a flat blob loaded at `base`.

    The result starts executing at offset 0 (a jump is prepended if `func` is not first in
    its section).
    """
    elf = Elf(obj)
    want = MACHINES[isa_name]
    if elf.machine != want:
        raise ElfError(f"object is for e_machine {elf.machine}, expected {want} ({isa_name})")
    syms = elf.symbols()
    matches = [s for s in syms if s.name == func and s.shndx != SHN_UNDEF]
    if not matches:
        names = sorted({s.name for s in syms if s.type == 2 and s.name})
        raise ElfError(f"function {func!r} not defined in object (functions: {names})")
    sym = matches[0]
    if sym.shndx >= len(elf.sections):
        raise ElfError(f"symbol {func!r} has special section index {sym.shndx:#x}")
    text = elf.sections[sym.shndx]
    if not text.flags & SHF_EXECINSTR:
        raise ElfError(f"symbol {func!r} is in non-executable section {text.name}")

    # Collect the sections reachable from the code section via relocations.
    order, relocs, pending, bad = [text], {}, [text], []
    while pending:
        sec = pending.pop()
        relocs[sec.index] = elf.relocations_for(sec)
        for r in relocs[sec.index]:
            target = syms[r.sym_index] if r.sym_index < len(syms) else None
            if target is None or target.shndx == SHN_UNDEF or target.shndx >= len(elf.sections):
                bad.append(f"undefined symbol {r.symbol or '?'!r} at {sec.name}+{r.offset:#x} "
                           f"({reloc_name(isa_name, r.type)})")
                continue
            tsec = elf.sections[target.shndx]
            if not tsec.flags & SHF_ALLOC:
                bad.append(f"{r.symbol!r} lives in non-allocated section {tsec.name}")
            elif all(s.index != tsec.index for s in order):
                order.append(tsec)
                pending.append(tsec)
    if bad:
        raise RelocationError(
            f"{func}: {len(bad)} unresolvable relocation(s): " + "; ".join(bad[:8])
            + (f" (+{len(bad) - 8} more)" if len(bad) > 8 else "")
            + ". Raw function bytes must be self-contained: no calls to external functions "
            "or runtime helpers (memcpy, __divti3, ...)."
        )

    # Lay out: [trampoline] code section, then referenced sections at their alignment. The
    # trampoline is sized first and filled in only after layout, so its target is the
    # function's final address however far alignment pushes the code section.
    pad = 0
    if sym.value != 0:
        pad = 16 if isa_name == "x86_64" else 4
    blob = bytearray(b"\0" * pad)
    addr = {}
    for sec in order:
        align = max(sec.align, 1)
        blob.extend(b"\0" * (-len(blob) % align))
        addr[sec.index] = base + len(blob)
        blob.extend(b"\0" * sec.size if sec.type == SHT_NOBITS else elf.data(sec))
    entry = addr[text.index] - base + sym.value
    if pad:
        head = _entry_trampoline(isa_name, entry, pad)
        blob[:pad] = head
        # Fill the alignment gap before the code section with traps, not zeros.
        gap = addr[text.index] - base - pad
        fill = b"\xcc" if isa_name == "x86_64" else b"\0\0\0\0"  # int3 / udf #0
        blob[pad : pad + gap] = (fill * (gap // len(fill) + 1))[:gap]

    applied, absolute = 0, False
    for sec in order:
        for r in relocs[sec.index]:
            target = syms[r.sym_index]
            S = addr[target.shndx] + target.value
            P = addr[sec.index] + r.offset
            off = P - base
            try:
                if isa_name == "x86_64":
                    if r.type not in _X86:
                        raise RelocationError(f"unsupported relocation {reloc_name(isa_name, r.type)}")
                    _apply_x86(blob, off, r.type, S, r.addend, P)
                else:
                    _apply_a64(blob, off, r.type, S, r.addend, P)
            except RelocationError as exc:
                raise RelocationError(f"{func}: {exc} for {r.symbol!r} at {sec.name}+{r.offset:#x}") from None
            applied += 1
            absolute |= (isa_name, r.type) in _ABSOLUTE
    code = bytes(blob)
    verify_entry(elf, isa_name, func, code, entry, relocs[text.index])
    return Linked(code, entry, [s.name for s in order], applied, absolute)
