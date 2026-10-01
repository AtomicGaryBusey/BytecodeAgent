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
    """Capstone's text for one instruction's bytes (None if they do not decode as one)."""
    insns = list(ISAS[isa].disassembler().disasm(data, addr))
    if len(insns) != 1 or insns[0].size != len(data):
        return None
    i = insns[0]
    return f"{i.mnemonic} {i.op_str}".strip()


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
