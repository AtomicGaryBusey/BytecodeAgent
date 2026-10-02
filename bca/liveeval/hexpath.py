"""The `hex` path: the model writes the machine code itself.

There is no assembler and no encoder between the model's output and the executed bytes: each
instruction line is hexadecimal machine code, and the bytes run exactly as written (nothing is
re-encoded, "fixed" or relocated). The checks before running only *read* the bytes:

- the text must be well formed: AArch64, one 32-bit instruction word per line (8 hex digits, the
  word's value; stored little-endian); x86-64, one instruction per line as its bytes in memory
  order (two hex digits each, space-separated). Comments after `//` or `;` and blank lines are
  ignored.
- every instruction must decode, and stay within the instruction set the prompt allows, the same
  rule the other paths enforce:
  - x86-64: each line must decode (iced-x86) as exactly one instruction of that many bytes, and the
    extensions the emulator cannot run (AVX/EVEX/XOP/ADX/SHA, ...) are refused, with the stream
    path's own check (bca.stream._x86_decode).
  - AArch64: each word must decode (capstone); its disassembly is then given to GNU as with the
    default architecture (ARMv8-A, as the stream path's cross-check uses), and an instruction GNU
    as refuses with "selected processor does not support" (CRC32, LSE atomics, dot product,
    half-precision arithmetic, crypto, SVE, other v8.x extensions) is refused. Any other GNU as
    complaint about capstone's text (a formatting quirk) is ignored, so a valid ARMv8.0
    encoding is never rejected by this check.
Capstone's disassembly of the bytes is used for the records, the fault locator and the repair
feedback only.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from bca.isa import ISAS

_A64_WORD_RE = re.compile(r"(?:0[xX])?([0-9a-fA-F]{8})")
_X86_BYTE_RE = re.compile(r"[0-9a-fA-F]{2}")
_GAS_DIAG_RE = re.compile(r"^\{standard input\}:(\d+): (?:Error|Warning): (.*)$", re.M)
MAX_LINE = 200


class HexError(Exception):
    def __init__(self, message: str, line: int = 0, category: str = "encoding"):
        super().__init__(message)
        self.line = line
        self.category = category


@dataclass(frozen=True)
class HexLine:
    offset: int  # byte offset from the entry point
    data: bytes
    line: int  # 1-based line number in the model's text
    text: str  # the line as written (comment included)


def _strip_comment(s: str) -> str:
    cut = len(s)
    for marker in ("//", ";"):
        i = s.find(marker)
        if i >= 0:
            cut = min(cut, i)
    return s[:cut].strip()


def parse(isa: str, text: str) -> tuple[bytes, list[HexLine]]:
    """The bytes exactly as written, and where each instruction came from. Raises HexError."""
    out = bytearray()
    lines: list[HexLine] = []
    for n, raw in enumerate(text.splitlines(), 1):
        body = _strip_comment(raw)
        if not body:
            continue
        if len(raw) > MAX_LINE:
            raise HexError(f"line {n} is longer than {MAX_LINE} characters", n)
        if isa == "aarch64":
            m = _A64_WORD_RE.fullmatch(body)
            if not m:
                raise HexError(f"line {n}: {body[:60]!r} is not one 32-bit instruction word (exactly 8 hex "
                               "digits, e.g. 8b010000)", n)
            data = int(m.group(1), 16).to_bytes(4, "little")
        else:
            toks = body.split()
            bad = next((t for t in toks if not _X86_BYTE_RE.fullmatch(t)), None)
            if bad is not None:
                raise HexError(f"line {n}: {bad[:30]!r} is not a byte (two hex digits, e.g. 48 01 f0)", n)
            if len(toks) > 15:
                raise HexError(f"line {n}: {len(toks)} bytes; one x86 instruction is at most 15", n)
            data = bytes(int(t, 16) for t in toks)
        lines.append(HexLine(len(out), data, n, raw.rstrip()))
        out += data
    if not out:
        raise HexError("the output contains no instruction", 0, "format")
    return bytes(out), lines


def describe(isa: str, data: bytes, addr: int) -> str | None:
    """The text of one instruction's bytes (None if they do not decode as one): capstone's, or for an
    AArch64 word capstone does not know, GNU objdump's (capstone misses some valid ARMv8.0 encodings,
    e.g. the unprivileged loads `ldtrb`, which GNU as accepts)."""
    insns = list(ISAS[isa].disassembler().disasm(data, addr))
    if len(insns) == 1 and insns[0].size == len(data):
        i = insns[0]
        return f"{i.mnemonic} {i.op_str}".strip()
    if isa == "aarch64" and len(data) == 4:
        return _objdump_a64(data)
    return None


_OBJDUMP_CACHE: dict[bytes, str | None] = {}
_OBJDUMP_LINE_RE = re.compile(r"^\s*0:\s+[0-9a-f]{8}\s+(.*?)\s*$", re.M)


def _objdump_a64(word: bytes) -> str | None:
    """GNU objdump's text for one AArch64 word, None if it is undefined. Branch targets are printed
    as absolute addresses from 0, so such text is used only for the ISA-level check and feedback."""
    if word in _OBJDUMP_CACHE:
        return _OBJDUMP_CACHE[word]
    from bca.stream import _gas_tool

    gas = _gas_tool("aarch64")
    tool = None
    if gas:
        cand = gas[: -len("as")] + "objdump" if gas.endswith("as") else None
        tool = cand if cand and os.path.exists(cand) else None
    text = None
    if tool:
        with tempfile.TemporaryDirectory(prefix="bca-hex-") as tmp:
            path = os.path.join(tmp, "w.bin")
            with open(path, "wb") as f:
                f.write(word)
            proc = subprocess.run([tool, "-D", "-b", "binary", "-m", "aarch64", path], capture_output=True,
                                  timeout=30, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        m = _OBJDUMP_LINE_RE.search(proc.stdout.decode(errors="replace"))
        if m and not m.group(1).startswith(".inst") and "undefined" not in m.group(1):
            text = re.sub(r"\s+", " ", m.group(1).split("//")[0]).strip()
            text = re.sub(r"\s*<.*?>", "", text)  # drop objdump's symbol annotations
    _OBJDUMP_CACHE[word] = text
    return text


def check(isa: str, lines: list[HexLine], base: int) -> None:
    """Every instruction decodes and is within the prompt's instruction set. Raises HexError."""
    if isa == "x86_64":
        from bca.stream import AsmError, _x86_decode

        for hl in lines:
            try:
                _x86_decode(hl.data, base + hl.offset)
            except AsmError as exc:
                raise HexError(f"line {hl.line}: {hl.data.hex(' ')}: {exc}", hl.line) from None
        return
    texts = []
    for hl in lines:
        d = describe(isa, hl.data, base + hl.offset)
        if d is None:
            raise HexError(f"line {hl.line}: {hl.data[::-1].hex()} is not a valid AArch64 instruction word "
                           "(it does not decode)", hl.line)
        texts.append(d)
    _a64_level(lines, texts)


def _a64_level(lines: list[HexLine], texts: list[str]) -> None:
    from bca.stream import _gas_tool

    tool = _gas_tool("aarch64")
    if tool is None:
        raise RuntimeError("GNU as for aarch64 not found (needed for the hex path's ISA-level check)")
    src = ".text\n" + "\n".join(texts) + "\n"
    with tempfile.TemporaryDirectory(prefix="bca-hex-") as tmp:
        proc = subprocess.run([tool, "-o", os.path.join(tmp, "x.o"), "--"], input=src.encode(),
                              capture_output=True, timeout=30, cwd=tmp, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    for m in _GAS_DIAG_RE.finditer(proc.stderr.decode(errors="replace")):
        if "selected processor does not support" in m.group(2):
            k = int(m.group(1)) - 2  # line 1 is .text
            if 0 <= k < len(lines):
                hl = lines[k]
                raise HexError(f"line {hl.line}: {hl.data[::-1].hex()} decodes as `{texts[k]}`, which is outside "
                               "the prompt's instruction set (ARMv8.0-A with FP and NEON)", hl.line)


def listing(isa: str, code: bytes, lines: list[HexLine], base: int) -> str:
    """What the bytes mean, line by line: offset, the hex as written, capstone's disassembly."""
    out = []
    for hl in lines:
        word = hl.data[::-1].hex() if isa == "aarch64" else hl.data.hex(" ")
        d = describe(isa, hl.data, base + hl.offset)
        out.append(f"  +{hl.offset:#06x} line {hl.line:>3}: {word:<24} {d if d is not None else '(does not decode)'}")
    return "\n".join(out)


# -- whole programs (the app battery's opt-in `hex` path) -------------------------------------------
#
# A fixed static layout the prompt states exactly, so the model can compute every address itself:
# code at HEX_TEXT_BASE (the entry point is its first byte), the initialised writable data the model
# gives at HEX_DATA_BASE, and the task's zeroed scratch area at HEX_BSS_BASE. The model writes a
# `.text` section of instruction lines (as above) and optionally a `.data` section of data bytes; no
# other directive, no label. The harness copies the bytes into place unchanged (and, for a native
# run, into an ELF container: write_elf); nothing assembles, links or relocates them.

HEX_TEXT_BASE = 0x400000
HEX_DATA_BASE = 0x500000
HEX_BSS_BASE = 0x600000
HEX_SECTION_MAX = 1 << 20  # text and data each at most 1 MiB (the gap to the next section)
DATA_LINE_MAX = 400


@dataclass(frozen=True)
class HexProgram:
    isa: str
    text: bytes
    lines: list[HexLine]  # the instruction lines (offsets from HEX_TEXT_BASE)
    data: bytes


def _data_bytes(body: str, n: int) -> bytes:
    out = bytearray()
    for tok in body.split():
        t = tok[2:] if tok[:2] in ("0x", "0X") else tok
        if not t or len(t) % 2 or not all(c in "0123456789abcdefABCDEF" for c in t):
            raise HexError(f"line {n}: {tok[:30]!r} is not data bytes (pairs of hex digits in memory order, "
                           "e.g. 48 65 6c 6c 6f 0a or 48656c6c6f0a)", n)
        out += bytes.fromhex(t)
    return bytes(out)


def parse_program(isa: str, text: str) -> HexProgram:
    """A whole program in the hex format: instruction lines (before any marker, or after `.text`) and
    data lines (after `.data`). Raises HexError."""
    section = ".text"
    code_src: list[str] = []
    data = bytearray()
    seen: set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        body = _strip_comment(raw)
        if body.startswith("."):
            name = body.split()[0].lower()
            if name not in (".text", ".data") or len(body.split()) != 1:
                raise HexError(f"line {n}: {body[:40]!r}: the only markers are `.text` and `.data`, each on a "
                               "line of its own (no other directive, no labels)", n)
            if name in seen:
                raise HexError(f"line {n}: {name} appears twice; give each section once", n)
            seen.add(name)
            section = name
            code_src.append("")  # keep the line numbering
            continue
        if section == ".text":
            code_src.append(raw)
            continue
        code_src.append("")
        if body:
            if len(raw) > DATA_LINE_MAX:
                raise HexError(f"line {n} is longer than {DATA_LINE_MAX} characters", n)
            data += _data_bytes(body, n)
    code, lines = parse(isa, "\n".join(code_src))
    if len(code) > HEX_SECTION_MAX:
        raise HexError(f".text is {len(code)} bytes, over the limit of {HEX_SECTION_MAX}", 0, "format")
    if len(data) > HEX_SECTION_MAX:
        raise HexError(f".data is {len(data)} bytes, over the limit of {HEX_SECTION_MAX}", 0, "format")
    return HexProgram(isa, code, lines, bytes(data))


def program_image(prog: HexProgram, bss_size: int):
    """The bca.image.Image of a hex program: the bytes at the stated addresses, unchanged (the code
    page is filled with traps after the last instruction, as the linker does for the asm path)."""
    from bca.image import PAGE, _TRAP, Image

    text = bytearray(prog.text)
    tail = (-(len(text) + 1)) % PAGE + 1  # always at least one trap after the code
    trap = _TRAP[prog.isa]
    text += (trap * (tail // len(trap) + 1))[:tail]
    segments = [(HEX_TEXT_BASE, bytes(text), "rx")]
    sections = {".text": (HEX_TEXT_BASE, len(prog.text))}
    if prog.data:
        segments.append((HEX_DATA_BASE, prog.data, "rw"))
        sections[".data"] = (HEX_DATA_BASE, len(prog.data))
    if bss_size:
        sections[".bss"] = (HEX_BSS_BASE, bss_size)
    return Image(isa=prog.isa, entry=HEX_TEXT_BASE, segments=segments, symbols={},
                 bss=(HEX_BSS_BASE, bss_size), text_end=HEX_TEXT_BASE + len(prog.text), sections=sections)


_ELF_MACHINE = {"x86_64": 62, "aarch64": 183}


def write_elf(prog: HexProgram, bss_size: int) -> bytes:
    """A static ELF executable (ET_EXEC) holding the program's bytes unchanged: one PT_LOAD per
    segment (code r-x at HEX_TEXT_BASE, data rw- at HEX_DATA_BASE, the zeroed area rw- at
    HEX_BSS_BASE with no file bytes) and a PT_GNU_STACK (non-executable stack); no section headers.
    The entry point is the first code byte."""
    import struct

    page = 0x1000
    segs = [(HEX_TEXT_BASE, prog.text, len(prog.text), 5)]  # PF_R | PF_X
    if prog.data:
        segs.append((HEX_DATA_BASE, prog.data, len(prog.data), 6))  # PF_R | PF_W
    if bss_size:
        segs.append((HEX_BSS_BASE, b"", bss_size, 6))
    nph = len(segs) + 1
    out = bytearray(page)  # the headers' page
    phdrs = []
    for vaddr, blob, memsz, flags in segs:
        off = len(out) if blob else 0
        if blob:
            out += blob
            out += b"\0" * (-len(out) % page)
        phdrs.append(struct.pack("<IIQQQQQQ", 1, flags, off, vaddr, vaddr, len(blob), memsz, page))
    phdrs.append(struct.pack("<IIQQQQQQ", 0x6474E551, 6, 0, 0, 0, 0, 0, 16))  # PT_GNU_STACK, rw-
    ehdr = struct.pack("<16sHHIQQQIHHHHHH",
                       b"\x7fELF\x02\x01\x01" + b"\0" * 9,  # 64-bit, little-endian, System V
                       2, _ELF_MACHINE[prog.isa], 1,  # ET_EXEC, machine, EV_CURRENT
                       HEX_TEXT_BASE, 64, 0,  # entry, program headers at 64, no section headers
                       0, 64, 56, nph, 64, 0, 0)
    out[0:64] = ehdr
    out[64:64 + 56 * nph] = b"".join(phdrs)
    return bytes(out)


def program_listing(isa: str, prog: HexProgram, limit: int = 700) -> str:
    """What the code bytes mean, by absolute address: address, your line, the hex, the disassembly."""
    out = []
    for hl in prog.lines[:limit]:
        word = hl.data[::-1].hex() if isa == "aarch64" else hl.data.hex(" ")
        d = describe(isa, hl.data, HEX_TEXT_BASE + hl.offset)
        out.append(f"  {HEX_TEXT_BASE + hl.offset:#08x} line {hl.line:>4}: {word:<24} "
                   f"{d if d is not None else '(does not decode)'}")
    if len(prog.lines) > limit:
        out.append(f"  ... ({len(prog.lines) - limit} more instructions not shown)")
    return "\n".join(out)
