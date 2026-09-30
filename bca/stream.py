"""Streaming per-instruction assembler: arbitrary text chunks in, machine code out, live.

An LLM's output arrives as chunks split anywhere. `StreamAssembler.feed` buffers them and,
as soon as a statement is complete, encodes that one instruction at its final address and
returns an `Event` for it, so a caller can abort generation on the first bad line.

Statement rules (both ISAs):
  * A statement ends at a newline, or at `;` (keystone/GAS statement separator). With
    `semicolon_comments=True` a `;` starts a comment instead (NASM habit for x86).
  * Comments: `#` and `//` run to end of line on x86-64, `//` on AArch64 (where `#` is the
    immediate prefix); `/* ... */` may appear anywhere and may span lines (a newline inside
    it still ends the statement). A `;` inside a comment is part of the comment.
  * Any run of spaces/tabs is one separator (`mov\\teax, 1` == `mov eax, 1`); other control
    characters and non-ASCII text are errors.
  * `name:` at the start of a statement defines a label (`[A-Za-z_.$][A-Za-z0-9_.$]*`); an
    instruction may follow it. Anything else before a `:` (`@x:`, `"x":`, `a::`) is refused,
    as are label names that are register names or operand keywords (`x1`, `b1`, `w0`, `eq`,
    `lsl`, `rax`, `bl`, `ptr`, ...) and names starting with `__bca_` (reserved).
  * Blank statements, Markdown code fences and a few no-op directives (`.text`, `.globl`,
    ...) are skipped; every other directive is an error.
  * Limits: a physical line may hold at most MAX_LINE characters, at most MAX_BUFFER
    characters may be pending, and a stream at most MAX_CODE bytes of code; exceeding one is
    an error event (an over-long line is dropped up to its newline).

Numbers and expressions follow GNU as (the reference assembler), on both ISAs:
  * `123` decimal, `0x7b` hex, `0b1111011` binary, a leading zero means octal (`010` == 8;
    `09` is an error). The Intel `7bh` suffix is not supported. A one-character literal (`'/'`, `'A'`) is
    its value, as in GNU as; strings, escapes and multi-character literals are refused.
  * 64-bit two's complement: `/` and `%` are signed and truncate toward zero on the 64-bit
    value (0xffffffffffffffff / 6 == 0, as in GNU as), `>>` is a logical shift, `& | ^ ~`
    act on the 64-bit pattern. Precedence, tightest first: unary `- ~ +`;
    `* / % << >>`; `| & ^`; `+ -`; left to right within a level (so `1 + 2 << 3` == 17 and
    `256 - 2>>0*96` == 64, as in GNU as and unlike llvm-mc's Intel parser).
  * Where GNU as would only warn or silently wrap, the stream refuses: shift counts outside
    0..63, division by zero, literals wider than 64 bits, and `+ - * <<` / unary minus
    results outside [-2**63, 2**64) (`0x7fffffffffffffff * 3`, `-0x8000000000000001`).
    Every accepted expression therefore has exactly GNU as's value.
  * Every constant the stream hands to keystone is evaluated here first, so keystone's own
    (LLVM) expression rules never matter.

Operand semantics (GNU as):
  * x86-64 (Intel syntax): a bare label or `size ptr label` operand is a MEMORY operand
    (`mov rax, lbl` loads from lbl, absolute disp32); `offset lbl` is its address as an
    immediate; a difference of labels (`b - a`) is a constant; `[...]` groups and
    displacements outside them combine (`8[rbx]`, `[rbx][rcx*4][8]`). `.` and `$` are the
    statement's own address. A direct branch/call target may be any label expression.
  * AArch64: PC-relative targets (b/bl, b.cond, cbz/cbnz, tbz/tbnz, adr, adrp, ldr
    literal) must be label or `.` expressions: a plain number is refused because GNU as and
    llvm-mc read it as a PC-relative *offset* while keystone reads it as an *address*.
    `:lo12:expr` is the only relocation specifier; labels may appear nowhere else.

Trust model. keystone (an old LLVM fork) is the primary encoder, but it is treated as
untrusted, because it has three kinds of bugs:
  * Crashes: some inputs make it call LLVM's fatal-error handler, which kills the process.
    Every keystone call therefore runs in a persistent worker subprocess (`_KeystoneWorker`);
    if the worker dies the statement gets an error event naming the LLVM message and the
    worker is restarted transparently on the next call. No input can kill the host.
  * Hangs: some inputs (a stray top-level `:`) never return. Such shapes are refused before
    keystone sees them; anything else gets a short per-call timeout, paid at most once per
    statement text.
  * Silent wrong code: truncated immediates, wrapped branch/displacement fields, `rip`
    encoded as `rax`, wrong registers, dropped label addends, inline literal pools. Every
    committed instruction is therefore checked twice before `feed` returns:
      1. In-process semantic checks: x86 immediates, memory displacements and branch
         targets decoded with iced-x86 must equal the values evaluated here; AArch64
         PC-relative fields are computed here from the evaluated target (never by keystone)
         and re-decoded, `mov #imm` results are decoded and compared, and every statement
         must be exactly one instruction (x86 <= 15 bytes, AArch64 exactly 4).
      2. GNU as, an implementation independent of LLVM, assembles the ORIGINAL statement
         text (labels as real symbols, `.` meaning its own address) at the statement's
         address: the statements of one `feed` are laid out with `.org` in one section per
         address window, referenced labels are defined relative to the window, and the
         relocations GNU as leaves (absolute addresses, adrp/:lo12:, absolute branch
         targets) are applied here for the real load address. The bytes must be identical
         or decode identically (iced-x86 with operand sizes / capstone); any GNU as error
         or warning (e.g. "shortened") rejects the statement. Results for address- and
         label-independent statements are cached by text. Cost: one `as` process (~2 ms)
         per feed that completes new unseen statements.
    A check failure is an ordinary error event for the statement; nothing after it stays
    committed (its text goes back to `pending_text`, exactly as for an encoding error). If
    GNU as cannot run (killed, timeout), the statement gets a retryable error; tool failures
    are never cached. Pass `crosscheck=False` to skip step 2 (e.g. without binutils).
  Any unexpected exception while processing a statement becomes an error event for that
  statement; the committed state is unchanged.

Fixup strategy for labels:
  * x86-64: operands are parsed and evaluated here and keystone only sees numbers
    (`jmp 0x100010`, `mov rax, qword ptr [0x100008]`, `lea rax, [rip + 0x1f]`). A forward
    branch is encoded against a far placeholder, which forces the long rel32 form; when the
    label is defined it is re-encoded at the same size (keystone if it agrees on the size,
    otherwise iced-x86 keeping the rel32 opcode). RIP-relative memory operands always use
    disp32, so they patch in place. Forward references elsewhere are errors.
  * AArch64: every instruction is 4 bytes. The PC-relative target operand is evaluated here,
    keystone only encodes the instruction against a nearby placeholder symbol, and the
    offset field is then written here with range and alignment checks. `:lo12:expr` is
    substituted with its low 12 bits (patched later for forward labels); other relocation
    specifiers and `ldr xN, =value` literal pools are rejected.
  * Labels still unresolved at `finish()` are errors.

Program mode (`StreamAssembler(isa, program=True)`): a whole static program instead of one
function. Everything above still holds for instructions; in addition:
  * Sections: `.text`, `.data`, `.bss` and `.section NAME` for .text/.rodata/.data/.bss (with
    their default flags only), plus `.section .note.GNU-stack` (which may hold nothing).
    Instructions go in .text only. .text is laid out at TEXT_BASE (0x400000, where
    bca.image.link_program puts it), so references between .text locations are resolved
    in the stream exactly as in function mode.
  * Data directives (in .rodata/.data): `.byte`, `.short`/`.hword`/`.2byte`, `.word` (2 bytes
    on x86-64, 4 on AArch64), `.long`/`.int`/`.4byte`, `.quad`/`.8byte` (and `.xword`/`.dword`
    on AArch64), `.ascii`, `.asciz`/`.string`, `.zero`, `.space`/`.skip` (optional fill byte);
    alignment `.align` (bytes on x86-64, a power of two on AArch64, as in GNU as), `.balign`,
    `.p2align` (optional fill and maximum skip; at most page alignment) in every section
    (.text pads with NOPs); .bss takes only zero fill and alignment. `.globl`/`.global` mark
    symbols global; `.type`, `.size`, `.file` and `.intel_syntax noprefix` are accepted
    without effect. Constants: `.equ name, expr`, `.set`, `.equiv`, `name = expr`, where expr
    is a number or a difference of labels defined earlier in the current section (`len = .
    - msg`); they must be defined before use and are substituted into later operands.
    GNU as local labels (`1:`, referenced as `1f` / `1b`) work in every section. Everything
    else is refused with a message, in particular anything that reads files, expands text or
    emits unverified bytes (`.incbin`, `.include`, `.macro`, `.rept`, `.irp`, `.if`, `.inst`,
    `.fill`, `.org`, `.eqv`, `.comm`/`.lcomm`, section stacks, `.cfi_*`, ...).
  * Strings (`"..."`, escapes \\\\ \\" \\' \\n \\t \\r \\b \\f, octal \\0 to \\377, \\xHH) and
    character literals with those escapes (`'\\n'`, `'#'`) are understood; the splitter keeps
    `;`, `#` and `//` inside them.
  * Labels may be defined in any section. A reference to a label in another section (or not
    yet placed) cannot be resolved in the stream; it becomes a relocation record, as GNU as
    would emit it: x86-64 `[rip + label]` (R_X86_64_PC32), absolute `offset label` / `[label]`
    / `[label + reg*8]` (R_X86_64_32/32S/64); AArch64 `adrp` (ADR_PREL_PG_HI21), `add ...,
    :lo12:label` (ADD_ABS_LO12_NC), `ldr/str ..., [xN, :lo12:label]` (LDSTn_ABS_LO12_NC), `adr`
    (ADR_PREL_LO21), `ldr xN, label` (LD_PREL_LO19); data `.quad/.long label+k` (ABS64/ABS32,
    R_X86_64_64/32). A difference of two labels already defined in the same section is a
    constant. Branches into data sections are refused.
  * Cross-check: every instruction, directive and relocation is assembled by GNU as in its
    own section: bytes must match exactly (data) or as in function mode (instructions), and
    the relocations GNU as emits against the not-yet-placed labels must equal the stream's
    records (type, offset, addend). Alignment padding in .text takes GNU as's NOP bytes; a
    constant's value is checked as GNU as's `.quad expr`. Metadata directives with no effect
    on the output (.globl, .type, .size, .file, section switches) are not sent to GNU as.
  * Limits: MAX_CODE bytes of .text, MAX_DATA bytes of .rodata + .data, MAX_BSS bytes of .bss.
  * `finish()` returns a StreamObject: sections, symbols, relocations and errors, with
    `to_elf()` (a relocatable ELF object in GNU as's layout) and `link()` (through
    bca.image.link_program, into the same Image the `asm` path produces).
"""

import atexit
import itertools
import json
import os
import platform
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

import iced_x86
import unicorn

from bca.emu import (
    CODE_BASE,
    CODE_SIZE,
    DATA_BASE,
    DATA_SIZE,
    MASK64,
    RETURN_SENTINEL,
    STACK_BASE,
    STACK_SIZE,
    EmulationError,
    run_function,
)
from bca.isa import AARCH64, X86_64, Isa

# x86 forward branches are provisionally aimed this far ahead to force the rel32 form.
_X86_FAR = 0x1000_0000
# Fallback placeholder distance for rel8-only branches (loop, jrcxz, ...).
_X86_NEAR = 0x40

MAX_LINE = 4096  # characters per physical line
MAX_BUFFER = 4 << 20  # characters of unconsumed input
MAX_CODE = 16 << 20  # bytes of machine code per stream
MAX_DATA = 4 << 20  # program mode: bytes of .rodata + .data
MAX_BSS = 1 << 24  # program mode: bytes of .bss (bca.image.link_program's default limit)
TEXT_BASE = 0x400000  # program mode: where bca.image.link_program places .text
_PAGE = 0x1000
_MAX_MESSAGE = 600  # characters of any diagnostic quoted from a tool
_MAX_SHOWN = 120  # characters of statement text quoted in a message
_MAX_DEPTH = 32  # nesting of parentheses / unary operators in one expression
_MAX_TOKENS = 512  # tokens in one expression
_X86_MAX_INSN = 15

_LABEL_RE = re.compile(r"([A-Za-z_.$][\w.$]*)\s*:(?!:)")
_MNEMONIC_RE = re.compile(r"[A-Za-z_][\w.]*(?: |$)")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
# A printable one-character literal, minus quote, backslash and the comment/separator characters.
_CHAR_LIT_RE = re.compile(r"'([ !$-&(-\[\]-~])'")
_IGNORED_DIRECTIVES = {".text", ".globl", ".global", ".type", ".size", ".file", ".intel_syntax"}
_RESERVED_PREFIX = "__bca_"


@dataclass
class Event:
    """One observable step of the stream.

    kind is one of: "insn" (instruction committed; `pending` names an unresolved forward
    label whose provisional bytes will be patched), "patch" (a pending instruction got its
    final bytes), "label", "warning" (disassembly mismatch), "error".
    """

    kind: str
    address: int
    text: str
    data: bytes = b""
    message: str = ""
    line: int = 0
    pending: str | None = None


@dataclass
class Instruction:
    address: int
    text: str
    data: bytes
    line: int
    pending: str | None = None


@dataclass
class Program:
    isa: Isa
    base: int
    code: bytes
    instructions: list[Instruction]
    labels: dict[str, int]
    errors: list[Event]
    warnings: list[Event]
    events: list[Event]

    @property
    def ok(self) -> bool:
        return not self.errors

    def text_at(self, address: int) -> str | None:
        for insn in self.instructions:
            if insn.address == address:
                return insn.text
        return None


@dataclass(frozen=True)
class Relocation:
    """A reference only the linker can resolve: the field at `section` + `offset` gets ELF
    relocation `type` (R_X86_64_* / R_AARCH64_* number) for `symbol` + `addend`."""

    section: str
    offset: int
    type: int
    symbol: str
    addend: int
    line: int = 0


@dataclass
class DataItem:
    """A committed directive that placed bytes in .rodata/.data or space in .bss."""

    section: str
    offset: int
    text: str
    data: bytes  # empty in .bss
    size: int
    line: int


@dataclass
class StreamObject:
    """finish() in program mode: a whole program as sections, symbols and relocations.

    `to_elf()` is a relocatable ELF object laid out as GNU as would (one .text, .rodata,
    .data, .bss; local labels STB_LOCAL, `.globl` ones STB_GLOBAL; RELA against the labels);
    `link()` runs it through bca.image.link_program."""

    isa: Isa
    base: int  # address .text was resolved for (TEXT_BASE)
    sections: dict[str, bytes]  # ".text", ".rodata", ".data"
    bss_size: int
    symbols: dict[str, tuple[str, int]]  # label -> (section, offset in it)
    globals: list[str]
    relocations: list[Relocation]
    alignments: dict[str, int]
    instructions: list[Instruction]
    items: list[DataItem]
    errors: list[Event]
    warnings: list[Event]
    events: list[Event]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def code(self) -> bytes:
        return self.sections[".text"]

    def to_elf(self) -> bytes:
        if not self.ok:
            raise ValueError(f"program has errors: {self.errors[0].message}")
        return _write_elf(self)

    def link(self, entry_symbol: str = "_start", bss_size_limit: int = MAX_BSS):
        """The bca.image.Image of this program (raises bca.image.LinkError)."""
        from bca.image import LinkError, link_program

        image = link_program(self.to_elf(), self.isa.name, entry_symbol=entry_symbol,
                             bss_size_limit=bss_size_limit)  # fmt: skip
        # .text was resolved in the stream for self.base: the linker must agree.
        text = image.sections.get(".text")
        if self.sections[".text"] and (text is None or text[0] != self.base):
            raise LinkError(f".text linked at {text and hex(text[0])}, expected {self.base:#x}")
        return image


class AsmError(Exception):
    pass


class _KeystoneDied(AsmError):
    """keystone crashed or hung on the statement: do not ask it again about the same text."""


class StreamHalted(RuntimeError):
    """feed() was called after an error with abort_on_error=True; call retract() first."""


@dataclass
class _Fixup:
    index: int  # into StreamAssembler._insns
    label: str
    form: str  # "x86_branch" | "x86_riprel" | "x86_abs" | "a64_pcrel" | "a64_lo12"
    placeholder: int
    addend: int = 0  # program mode: constant added to the label (x86 forms)
    kind: str = ""  # program mode, x86_abs: "imm" | "mem" | "mem32"


@dataclass
class _Check:
    """What the GNU as cross-check needs to assemble a statement independently."""

    text: str  # the statement as written (labels stripped, whitespace normalised)
    addr: int
    labels: dict[str, int]  # labels it refers to
    cacheable: bool  # bytes depend on neither its address nor label values
    # Program mode: the section it is assembled in (None: function mode), the labels GNU as
    # must leave undefined, and the relocations (offset, type, symbol, addend) expected
    # against them.
    section: str | None = None
    externs: frozenset = frozenset()
    relocs: tuple = ()
    expect: bytes | None = None  # program mode: the bytes a constant's `.quad` must produce


@dataclass
class _Encoded:
    data: bytes
    fixup: _Fixup | None = None
    check: _Check | None = None


@dataclass
class _Undo:
    """Everything one committed statement changed, so it can be un-committed exactly."""

    code_len: int
    insn_count: int
    labels: list[str]
    fixup: _Fixup | None = None
    patched: list[tuple[int, _Fixup, bytes]] = field(default_factory=list)
    prog: tuple | None = None  # program mode: _program_snapshot() before the statement


def _clip(text: str, limit: int = _MAX_SHOWN) -> str:
    return text if len(text) <= limit else text[:limit] + f"...[{len(text) - limit} more]"


class _Splitter:
    """Cuts buffered text into complete statements, honouring comments and separators.
    Linear: `pos` is the start of unconsumed text and nothing before it is re-scanned."""

    def __init__(self, comment_markers: tuple[str, ...], semicolon_separates: bool,
                 strings: bool = False):
        alts = {*comment_markers, "/*"} | ({";"} if semicolon_separates else set())
        special = "|".join(re.escape(a) for a in sorted(alts, key=len)[::-1])
        if strings:  # program mode: "..." and 'c' / '\c' are opaque
            special = r"""'(?:\\[^\n]|[^\\\n])'|"|""" + special
        self._special = re.compile(special)
        self.semicolon_separates = semicolon_separates
        self.buf = ""
        self.pos = 0
        self.line = 1
        self.in_block = False  # inside /* */ at `pos`
        self.discard = False  # dropping the rest of an over-long line

    @property
    def pending(self) -> str:
        return self.buf[self.pos :]

    def push(self, chunk: str) -> None:
        if self.pos:
            self.buf, self.pos = self.buf[self.pos :], 0
        self.buf += chunk

    def reset(self) -> None:
        self.buf, self.pos, self.in_block, self.discard = "", 0, False, False

    def state(self) -> tuple:
        return (self.buf, self.pos, self.line, self.in_block, self.discard)

    def restore(self, state: tuple) -> None:
        self.buf, self.pos, self.line, self.in_block, self.discard = state

    def pop(self, final: bool = False) -> tuple[str, str, int] | None:
        """("stmt", text, line), ("long", "", line) for a dropped over-long line, or None
        when more input is needed."""
        buf = self.buf
        while self.discard:
            nl = buf.find("\n", self.pos)
            if nl < 0:
                self.pos = len(buf)
                return None
            self.pos, self.line, self.discard = nl + 1, self.line + 1, False
        nl = buf.find("\n", self.pos)
        end = nl if nl >= 0 else len(buf)
        if end - self.pos > MAX_LINE:
            line, head = self.line, buf[self.pos : self.pos + 60]
            if nl >= 0:
                self.pos, self.line = nl + 1, self.line + 1
            else:
                self.pos, self.discard = len(buf), not final
            return "long", head, line
        parts: list[str] = []
        i, in_block = self.pos, self.in_block
        while i < end:
            if in_block:
                j = buf.find("*/", i, end)
                if j < 0:
                    break
                in_block, i = False, j + 2
                parts.append(" ")
                continue
            m = self._special.search(buf, i, end)
            if m is None:
                parts.append(buf[i:end])
                break
            parts.append(buf[i : m.start()])
            if m[0] == '"':  # a string runs to its closing quote (or the end of the line)
                j = m.end()
                while j < end and buf[j] != '"':
                    j += 2 if buf[j] == "\\" else 1
                i = min(j + 1, end)
                parts.append(buf[m.start() : i])
            elif m[0][0] == "'":  # a character literal
                parts.append(m[0])
                i = m.end()
            elif m[0] == "/*":
                in_block, i = True, m.end()
                parts.append(" ")
            elif m[0] == ";" and self.semicolon_separates:
                self.pos, self.in_block = m.end(), in_block
                return "stmt", "".join(parts), self.line
            else:  # line comment
                break
        if nl < 0 and (not final or self.pos == len(buf)):
            return None
        line = self.line
        self.in_block = in_block
        if nl >= 0:
            self.pos, self.line = nl + 1, self.line + 1
        else:
            self.pos = len(buf)
        return "stmt", "".join(parts), line


class StreamAssembler:
    def __init__(
        self,
        isa: Isa,
        base: int | None = None,
        *,
        abort_on_error: bool = True,
        semicolon_comments: bool = False,
        crosscheck: bool = True,
        program: bool = False,
    ):
        """base: address of the first instruction (default CODE_BASE; TEXT_BASE, where the
        linker puts .text, in program mode). program: whole-program mode (module docstring)."""
        self.isa = isa
        self._program = program
        self.base = (TEXT_BASE if program else CODE_BASE) if base is None else base
        self.abort_on_error = abort_on_error
        markers = ("#", "//") if isa is X86_64 else ("//",)
        if semicolon_comments:
            markers += (";",)
        self._splitter = _Splitter(markers, not semicolon_comments, strings=program)
        self._x86 = isa is X86_64
        self._cs = isa.disassembler()
        self._gas = _gas_tool(isa.name) if crosscheck else None
        if crosscheck and self._gas is None:
            raise RuntimeError(
                f"GNU as for {isa.name} not found (install binutils for it, set "
                f"BCA_GAS_{isa.name.upper()}, or pass crosscheck=False)"
            )
        _KEYSTONE.start()  # warm the worker up while the first chunks arrive
        self.events: list[Event] = []  # full log, including retracted errors
        self.halted: Event | None = None  # the error that stopped the stream
        # Bumped whenever committed code is removed, so a LiveRunner knows to restart.
        self.generation = 0
        self._skipped: list[Event] = []  # errors of statements dropped with abort off
        self._reset_state()

    # -- public API -------------------------------------------------------------------

    @property
    def address(self) -> int:
        """Address the next instruction will be placed at."""
        return self.base + len(self._code)

    @property
    def pending_text(self) -> str:
        """Buffered text not yet consumed (after a halt: everything after the bad line)."""
        return self._splitter.pending

    def feed(self, chunk: str) -> list[Event]:
        if self.halted:
            raise StreamHalted(self.halted.message)
        if not isinstance(chunk, str):
            raise TypeError(f"feed() takes str, not {type(chunk).__name__}")
        if len(self._splitter.buf) - self._splitter.pos + len(chunk) > MAX_BUFFER:
            ev = self._error_event(
                f"input limit exceeded: more than {MAX_BUFFER} characters would be pending; "
                "chunk dropped", "", 0
            )  # fmt: skip
            self._record_error(ev)
            self.events.append(ev)
            return [ev]
        self._splitter.push(chunk)
        return self._drain(final=False)

    def finish(self) -> "Program | StreamObject":
        """Flush the last statement and report unresolved labels. The stream stays open:
        a caller may feed a missing label and call finish() again. In program mode the
        result is a StreamObject."""
        if not self.halted:
            self._drain(final=True)
        if self._program:
            return self._finish_program()
        errors = list(self._skipped) + ([self.halted] if self.halted else [])
        unresolved = []
        for fx in self._fixups:
            insn = self._insns[fx.index]
            ev = Event(
                "error",
                insn.address,
                insn.text,
                message=f"undefined label {fx.label!r} (line {insn.line}: {_clip(insn.text)!r})",
                line=insn.line,
            )
            unresolved.append(ev)
        errors += unresolved
        return Program(
            isa=self.isa,
            base=self.base,
            code=bytes(self._code),
            instructions=[replace(i) for i in self._insns],
            labels=dict(self._labels),
            errors=errors,
            warnings=[ev for _, ev in self._warns],
            events=self.events + unresolved,
        )

    def retract(self, statements: int = 0, *, keep_tail: bool = False) -> int:
        """Recover from an error: drop the failed statement and (unless keep_tail) all
        buffered text after it, then also un-commit the last `statements` statements.
        Without keep_tail the stream starts afresh (an unfinished comment or the unarrived
        rest of an over-long line is forgotten); with it, it continues as if the failed
        statement had been deleted. Returns the address where streaming resumes."""
        self.halted = None
        if not keep_tail:
            self._splitter.reset()
        if statements:
            self._rollback(max(0, len(self._committed) - statements))
            self.generation += 1
        return self.address

    def instruction_at(self, address: int) -> Instruction | None:
        idx = self._by_addr.get(address)
        return None if idx is None else self._insns[idx]

    def code(self) -> bytes:
        return bytes(self._code)

    # -- statement processing ---------------------------------------------------------

    def _reset_state(self) -> None:
        self._code = bytearray()
        # (line, keystone hex, GNU as hex, keystone text, GNU as text) for each statement whose
        # same-size GNU as encoding replaced keystone's (see _run_crosscheck)
        self.substitutions: list[tuple[int, str, str, str | None, str | None]] = []
        self._insns: list[Instruction] = []
        self._by_addr: dict[int, int] = {}
        self._labels: dict[str, int] = {}
        self._fixups: list[_Fixup] = []
        self._committed: list[tuple[str, int]] = []
        self._undo: list[_Undo] = []
        # (statement number, instruction index, check) awaiting the GNU as cross-check.
        self._unchecked: list[tuple[int, int, _Check]] = []
        self._warns: list[tuple[int, Event]] = []  # (statement number, warning)
        # Program mode (all empty in function mode, where .text is the only section).
        self._section = ".text"
        self._data = {".rodata": bytearray(), ".data": bytearray()}
        self._bss = 0
        self._symtab: dict[str, tuple[str, int]] = {}  # labels outside .text: (section, offset)
        self._globals: list[str] = []
        self._relocs: list[Relocation] = []
        self._items: list[DataItem] = []
        self._align = dict.fromkeys(_PROGRAM_SECTIONS, 1)
        self._numeric: dict[int, int] = {}  # GNU as local labels: N -> definitions so far
        self._consts: dict[str, int] = {}  # .equ / .set / = constants

    def _rollback(self, keep: int) -> None:
        """Un-commit every statement after the first `keep`, restoring the exact state."""
        while len(self._committed) > keep:
            undo = self._undo.pop()
            self._committed.pop()
            if undo.fixup is not None:
                self._fixups = [f for f in self._fixups if f is not undo.fixup]
            for pos, fx, old in reversed(undo.patched):
                insn = self._insns[fx.index]
                off = insn.address - self.base
                self._code[off : off + len(old)] = old
                insn.data, insn.pending = old, fx.label
                self._fixups.insert(pos, fx)
            for insn in self._insns[undo.insn_count :]:
                self._by_addr.pop(insn.address, None)
            del self._insns[undo.insn_count :]
            del self._code[undo.code_len :]
            for name in undo.labels:
                self._labels.pop(name, None)
                self._symtab.pop(name, None)
            if undo.prog is not None:
                self._program_restore(undo.prog)
        self._unchecked = [u for u in self._unchecked if u[0] < keep]
        self._warns = [w for w in self._warns if w[0] < keep]

    def _error_event(self, message: str, text: str, line: int) -> Event:
        if self._program:
            message, text = _pretty_names(message), _pretty_names(text)
        where = f" (line {line}: {_clip(text)!r})" if line else ""
        msg = f"{self.isa.name}: {_clip(message, _MAX_MESSAGE)}{where}"
        return Event("error", self.address, _clip(text, MAX_LINE), message=msg, line=line)

    def _record_error(self, ev: Event) -> None:
        if self.abort_on_error:
            self.halted = ev
        else:
            self._skipped.append(ev)

    def _drain(self, final: bool) -> list[Event]:
        out: list[Event] = []
        # Per committed statement of this call: (statement number, splitter state right
        # after it was cut, index of its first event in `out`, text, line).
        marks: list[tuple[int, tuple, int, str, int]] = []
        while True:
            while not self.halted and (stmt := self._splitter.pop(final)) is not None:
                kind, text, line = stmt
                after = self._splitter.state()
                number, start = len(self._committed), len(out)
                if kind == "long":
                    message = f"line longer than {MAX_LINE} characters; dropped"
                    events = [self._error_event(message, text + "...", line)]
                else:
                    events = self._process(text, line)
                if len(self._committed) > number:
                    marks.append((number, after, start, text, line))
                out += events
                for ev in events:
                    if ev.kind == "error":
                        self._record_error(ev)
            failure = self._run_crosscheck()
            if failure is None:
                break
            # Undo the failing statement and everything committed after it in this call;
            # its successors go back into the buffer as if the stream had stopped there.
            number, message = failure
            mark = next((m for m in marks if m[0] == number), None)
            if mark is None:  # defensive: cannot happen, checks are drained per call
                mark = (number, self._splitter.state(), len(out), "", 0)
            _, after, start, text, line = mark
            dropped = out[start:]
            self._skipped = [e for e in self._skipped if all(e is not d for d in dropped)]
            self._rollback(number)
            self._splitter.restore(after)
            marks = [m for m in marks if m[0] < number]
            ev = self._error_event(message, text.strip(), line)
            out = out[:start] + [ev]
            self.halted = None
            self._record_error(ev)
        self.events += out
        return out

    def _process(self, raw: str, line: int) -> list[Event]:
        """Handle one statement. Returns its events; on error nothing is committed."""
        text = raw.strip()
        if not text or text.startswith("```"):
            return []
        added: list[str] = []
        try:
            if self._program:
                return self._process_program_statement(text, line, added)
            return self._process_statement(text, line, added)
        except Exception as exc:  # noqa: BLE001 - any bug must cost one statement, not state
            for name in added:
                self._labels.pop(name, None)
                self._symtab.pop(name, None)
            return [self._error_event(
                f"internal error while assembling ({type(exc).__name__}: {exc}); "
                "statement rejected", text, line)]  # fmt: skip

    def _process_statement(self, text: str, line: int, added: list[str]) -> list[Event]:
        addr = self.address

        def error(message: str) -> list[Event]:
            return [self._error_event(message, text, line)]

        if not text.isascii():
            return error("non-ASCII characters")
        # A one-character literal ('/', 'A') is a number to GNU as on both ISAs: pass its value on.
        text = _CHAR_LIT_RE.sub(lambda m: str(ord(m[1])), text)
        if re.search(r"[\"'\\]", text):
            return error("strings, escapes and this character literal are not supported; write the "
                         "character's numeric value instead (e.g. 47 for '/')")
        text = _WS_RE.sub(" ", text)
        if _CTRL_RE.search(text):
            return error("control characters")
        labels = []
        while m := _LABEL_RE.match(text):
            labels.append(m[1])
            text = text[m.end() :].lstrip()
        for name in labels:
            if name in self._labels or labels.count(name) > 1:
                return error(f"duplicate label {name!r}")
            if _refused_label(self.isa, name):
                return error(
                    f"label name {name!r} is a register name or operand keyword that GNU as "
                    "does not read as a label; rename the label"
                )
            if name.startswith(_RESERVED_PREFIX) or name in (".", "$"):
                return error(f"label name {name!r} is reserved")
        if text.startswith("."):
            if text.split()[0] not in _IGNORED_DIRECTIVES:
                return error(f"unsupported directive {text.split()[0]!r}")
            text = ""
        elif text and not _MNEMONIC_RE.match(text):
            return error(
                "unrecognised statement syntax (a label is `name:` with name made of letters, "
                "digits, _ . $; an instruction starts with its mnemonic)"
            )
        if text:
            try:
                _reject_stray_colons(self.isa, text)
            except AsmError as exc:
                return error(str(exc))
        if len(self._code) >= MAX_CODE:
            return error(f"program too large (more than {MAX_CODE} bytes)")

        # Resolve earlier fixups against the new labels, then encode the instruction with
        # the labels visible (so `x: jmp x` is a backward reference).
        patches: list[tuple[_Fixup, bytes, _Check | None]] = []
        try:
            for fx in self._fixups:
                if fx.label in labels:
                    try:
                        patches.append((fx, *self._patch(fx, addr)))
                    except AsmError as exc:
                        insn = self._insns[fx.index]
                        return error(f"cannot resolve {fx.label!r} for {insn.text!r}: {exc}")
            self._labels.update(dict.fromkeys(labels, addr))
            added += labels
            encoded = self._encode(text, addr) if text else None
            if encoded is not None and len(self._code) + len(encoded.data) > MAX_CODE:
                raise AsmError(f"program too large (more than {MAX_CODE} bytes)")
        except AsmError as exc:
            for name in added:
                del self._labels[name]
            added.clear()
            return error(str(exc))
        return self._commit(text, line, addr, labels, patches, encoded)

    def _commit(self, text, line, addr, labels, patches, encoded) -> list[Event]:
        """Nothing in here can fail on valid state."""
        undo = _Undo(len(self._code), len(self._insns), list(labels))
        self._committed.append((text, line))
        number = len(self._committed) - 1
        events = [Event("label", addr, name, line=line) for name in labels]
        for fx, data, check in patches:
            insn = self._insns[fx.index]
            pos = next(i for i, f in enumerate(self._fixups) if f is fx)
            del self._fixups[pos]
            undo.patched.append((pos, fx, insn.data))
            off = insn.address - self.base
            self._code[off : off + len(data)] = data
            insn.data, insn.pending = data, None
            self._queue_check(number, fx.index, check)
            events.append(
                Event("patch", insn.address, insn.text, data, f"{fx.label} = {addr:#x}", insn.line)
            )
            events += self._warn(number, insn)
        if encoded is not None:
            insn = Instruction(addr, text, encoded.data, line)
            if encoded.fixup:
                encoded.fixup.index = len(self._insns)
                insn.pending = encoded.fixup.label
                self._fixups.append(encoded.fixup)
                undo.fixup = encoded.fixup
            else:
                self._queue_check(number, len(self._insns), encoded.check)
            self._by_addr[addr] = len(self._insns)
            self._insns.append(insn)
            self._code += encoded.data
            events.append(Event("insn", addr, text, encoded.data, line=line, pending=insn.pending))
            events += self._warn(number, insn)
        self._undo.append(undo)
        return events

    def _warn(self, number: int, insn: Instruction) -> list[Event]:
        try:
            warnings = self._crosscheck(insn)
        except Exception:  # noqa: BLE001 - advisory only
            return []
        self._warns += [(number, w) for w in warnings]
        return warnings

    # -- program mode -------------------------------------------------------------------

    def _program_snapshot(self) -> tuple:
        return (self._section, len(self._data[".rodata"]), len(self._data[".data"]), self._bss,
                len(self._relocs), len(self._items), len(self._globals), dict(self._align),
                dict(self._numeric), dict(self._consts))  # fmt: skip

    def _program_restore(self, snap: tuple) -> None:
        section, rodata, data, bss, relocs, items, globs, align, numeric, consts = snap
        self._section, self._bss, self._align, self._numeric = section, bss, align, numeric
        self._consts = consts
        del self._data[".rodata"][rodata:]
        del self._data[".data"][data:]
        del self._relocs[relocs:]
        del self._items[items:]
        del self._globals[globs:]

    def _section_size(self, section: str) -> int:
        if section == ".text":
            return len(self._code)
        if section == ".bss":
            return self._bss
        return len(self._data.get(section, b""))

    def _process_program_statement(self, raw: str, line: int, added: list[str]) -> list[Event]:
        addr = self.address

        def error(message: str) -> list[Event]:
            return [self._error_event(message, raw, line)]

        if not raw.isascii():
            return error("non-ASCII characters")
        try:
            text, strings = _mask_strings(raw)
            text = _PROGRAM_CHAR_RE.sub(lambda m: str(_char_value(m[1])), text)
        except AsmError as exc:
            return error(str(exc))
        if re.search(r"[\"'\\]", _STRING_TOKEN_RE.sub("", text)):
            return error("unsupported quote, escape or character literal (a character literal "
                         "is one character or escape in single quotes: 'a', '\\n')")  # fmt: skip
        if any(_CTRL_RE.search(s.replace("\t", "")) for s in strings):
            return error("control characters in a string (write them as escapes: \\n, \\t, \\0)")
        text = _WS_RE.sub(" ", text)
        if _CTRL_RE.search(text):
            return error("control characters")
        labels, numeric, generated = [], {}, set()
        while m := _LABEL_RE.match(text) or _NUMERIC_LABEL_RE.match(text):
            if m.re is _NUMERIC_LABEL_RE:  # GNU as local label `N:`, referenced as Nb / Nf
                n = int(m[1])
                numeric[n] = numeric.get(n, self._numeric.get(n, 0)) + 1
                labels.append(_numeric_name(n, numeric[n] - 1))
                generated.add(labels[-1])
            else:
                labels.append(m[1])
            text = text[m.end() :].lstrip()
        if text:
            try:
                text = _NUMERIC_REF_RE.sub(lambda r: self._numeric_ref(r, numeric), text)
            except AsmError as exc:
                return error(str(exc))
        for name in labels:
            if name in generated:
                continue
            if (name in self._labels or name in self._symtab or name in self._consts
                    or labels.count(name) > 1):  # fmt: skip
                return error(f"duplicate label {name!r}")
            if _refused_label(self.isa, name):
                return error(
                    f"label name {name!r} is a register name or operand keyword that GNU as "
                    "does not read as a label; rename the label"
                )
            if name.startswith(_RESERVED_PREFIX) or name in (".", "$"):
                return error(f"label name {name!r} is reserved")
        section = self._section
        if labels and section == _NOTE_SECTION:
            return error(f"nothing may be placed in {section}")
        try:
            assignment = _parse_assignment(text)
        except AsmError as exc:
            return error(str(exc))
        if assignment is None and text and self._consts:
            text = self._substitute_consts(text)
        if assignment is not None:
            if strings:
                return error("a constant must be a number or a difference of labels, not a string")
        elif text and not text.startswith("."):
            if not _MNEMONIC_RE.match(text):
                return error(
                    "unrecognised statement syntax (a label is `name:` with name made of letters, "
                    "digits, _ . $; an instruction starts with its mnemonic)"
                )
            if strings:
                return error("strings are only allowed in .ascii, .asciz and .string")
            if section != ".text":
                return error(f"instructions belong in .text (the current section is {section})")
            try:
                _reject_stray_colons(self.isa, text)
            except AsmError as exc:
                return error(str(exc))
            if len(self._code) >= MAX_CODE:
                return error(f"program too large (more than {MAX_CODE} bytes of code)")

        patches: list[tuple[_Fixup, bytes, _Check | None, tuple]] = []
        try:
            if labels:
                offset = self._section_size(section)
                for fx in self._fixups:
                    if fx.label in labels:
                        try:
                            if section == ".text":
                                patches.append((fx, *self._patch(fx, addr), ()))
                            else:
                                insn = self._insns[fx.index]
                                patches.append((fx, *self._reloc_records(
                                    insn.text, insn.address, insn.data, fx, section)))  # fmt: skip
                        except AsmError as exc:
                            insn = self._insns[fx.index]
                            return error(f"cannot resolve {fx.label!r} for {insn.text!r} "
                                         f"(line {insn.line}): {exc}")  # fmt: skip
                if section == ".text":
                    self._labels.update(dict.fromkeys(labels, addr))
                else:
                    self._symtab.update(dict.fromkeys(labels, (section, offset)))
                added += labels
            content = None
            if assignment is not None:
                content = self._program_constant(*assignment, labels)
            elif text.startswith("."):
                content = self._program_directive(text, strings, line, addr)
            elif text:
                encoded = self._encode(text, addr)
                if len(self._code) + len(encoded.data) > MAX_CODE:
                    raise AsmError(f"program too large (more than {MAX_CODE} bytes of code)")
                relocs: tuple = ()
                fx = encoded.fixup
                if fx is not None and fx.label in self._symtab:
                    where = self._symtab[fx.label][0]
                    data, check, relocs = self._reloc_records(text, addr, encoded.data, fx, where)
                    encoded = _Encoded(data, check=check)
                content = ("insn", encoded, relocs)
        except AsmError as exc:
            for name in added:
                self._labels.pop(name, None)
                self._symtab.pop(name, None)
            added.clear()
            return error(str(exc))
        return self._commit_program(text, line, addr, labels, patches, content, numeric)

    def _numeric_ref(self, m: re.Match, numeric: dict[int, int]) -> str:
        n = int(m[1])
        count = numeric.get(n, self._numeric.get(n, 0))
        if m[2].lower() == "b":
            if not count:
                raise AsmError(f"local label {m[0]!r} refers back to `{n}:`, which is not defined yet")
            return _numeric_name(n, count - 1)
        return _numeric_name(n, count)

    def _commit_program(self, text, line, addr, labels, patches, content, numeric) -> list[Event]:
        """Nothing in here can fail on valid state."""
        undo = _Undo(len(self._code), len(self._insns), list(labels),
                     prog=self._program_snapshot())  # fmt: skip
        self._numeric.update(numeric)
        self._committed.append((text, line))
        number = len(self._committed) - 1
        section = self._section
        here = addr if section == ".text" else self._section_size(section)
        events = [Event("label", here, name, message="" if section == ".text" else section,
                        line=line) for name in labels]  # fmt: skip
        for fx, data, check, relocs in patches:
            insn = self._insns[fx.index]
            pos = next(i for i, f in enumerate(self._fixups) if f is fx)
            del self._fixups[pos]
            undo.patched.append((pos, fx, insn.data))
            off = insn.address - self.base
            self._code[off : off + len(data)] = data
            insn.data, insn.pending = data, None
            for r_off, rtype, symbol, addend in relocs:
                self._relocs.append(Relocation(".text", off + r_off, rtype, symbol, addend, insn.line))
            self._queue_check(number, fx.index, check)
            how = f"{addr:#x}" if not relocs else f"{section}+{here:#x} (relocation)"
            events.append(Event("patch", insn.address, insn.text, data, f"{fx.label} = {how}",
                                insn.line))  # fmt: skip
            events += self._warn(number, insn)
        kind = content[0] if content else None
        if kind in ("insn", "text"):
            encoded = content[1]
            insn = Instruction(addr, text, encoded.data, line)
            if encoded.fixup:
                encoded.fixup.index = len(self._insns)
                insn.pending = encoded.fixup.label
                self._fixups.append(encoded.fixup)
                undo.fixup = encoded.fixup
            else:
                self._queue_check(number, len(self._insns), encoded.check)
            if kind == "insn":
                for r_off, rtype, symbol, addend in content[2]:
                    self._relocs.append(Relocation(".text", addr - self.base + r_off, rtype, symbol,
                                                   addend, line))  # fmt: skip
            else:
                self._align[".text"] = max(self._align[".text"], content[2])
            self._by_addr[addr] = len(self._insns)
            self._insns.append(insn)
            self._code += encoded.data
            events.append(Event("insn", addr, text, encoded.data, line=line, pending=insn.pending))
            if kind == "insn":
                events += self._warn(number, insn)
        elif kind == "data":
            item, check, relocs, align = content[1:]
            index = len(self._items)
            self._items.append(item)
            if item.section == ".bss":
                self._bss += item.size
            else:
                self._data[item.section] += item.data
            for r_off, rtype, symbol, addend in relocs:
                self._relocs.append(Relocation(item.section, item.offset + r_off, rtype, symbol,
                                               addend, line))  # fmt: skip
            self._align[item.section] = max(self._align[item.section], align)
            self._queue_check(number, index, check)
            events.append(Event("data", item.offset, item.text, item.data, item.section, line))
        elif kind == "section":
            self._section = content[1]
        elif kind == "const":
            self._consts[content[1]] = content[2]
            self._queue_check(number, -1, content[3])
            events.append(Event("const", content[2] & MASK64, content[1], line=line,
                                message=f"{content[1]} = {content[2]}"))  # fmt: skip
        elif kind == "globl":
            self._globals += [n for n in dict.fromkeys(content[1]) if n not in self._globals]
        self._undo.append(undo)
        return events

    def _reloc_records(self, text: str, addr: int, data: bytes, fx: _Fixup, where: str):
        """A .text instruction whose label lives in section `where`: its bytes with the
        relocated field zeroed (as GNU as leaves it), the GNU as check, and the relocation
        (offset in the instruction, type, label, addend)."""
        label = fx.label
        if fx.form == "x86_branch":
            raise AsmError(f"branch target {label!r} is in {where}, not in .text")
        buf = bytearray(data)
        if self._x86:
            decoder = iced_x86.Decoder(64, data, ip=addr)
            ins = decoder.decode()
            co = decoder.get_constant_offsets(ins)
            if fx.form == "x86_riprel":
                off, width = co.displacement_offset, co.displacement_size
                rtype, addend = 2, fx.addend - (len(data) - off)  # R_X86_64_PC32
                if width != 4:
                    raise AsmError(f"unexpected rip-relative encoding {data.hex()}")
            elif fx.kind == "imm":
                off, width = co.immediate_offset, co.immediate_size
                kinds = [ins.op_kind(i) for i in range(ins.op_count)
                         if ins.op_kind(i) in _X86_IMM_BITS]  # fmt: skip
                # R_X86_64_64, R_X86_64_32 (zero-extended imm32), R_X86_64_32S (sign-extended)
                rtype = {8: 1, 4: 10 if kinds[:1] == [iced_x86.OpKind.IMMEDIATE32] else 11}.get(width)
                addend = fx.addend
            else:
                off, width = co.displacement_offset, co.displacement_size
                rtype = {8: 1, 4: 10 if fx.kind == "mem32" else 11}.get(width)
                addend = fx.addend
            if rtype is None:
                raise AsmError(f"the address of {label!r} does not fit this {8 * width}-bit field")
            buf[off : off + width] = bytes(width)
        else:
            word = int.from_bytes(data, "little")
            off = 0
            try:
                if fx.form == "a64_pcrel":
                    kind = _a64_pcrel_kind(word)
                    rtype = {"adrp": 275, "adr": 274, "ldr literal": 273}.get(kind)
                    if rtype is None:
                        raise AsmError(f"branch target {label!r} is in {where}, not in .text")
                    mnemonic, ops = _split_statement(text, False)
                    expr = ops[_a64_target_index(mnemonic, ops)]
                    mask = (3 << 29 | 0x7FFFF << 5) if kind in ("adr", "adrp") else 0x7FFFF << 5
                else:
                    rtype = _a64_lo12_type(word)
                    if rtype is None:
                        raise AsmError(f":lo12:{label} is not supported in this instruction")
                    expr = _LO12_RE.search(text)[1]
                    mask = 0xFFF << 10
                addend, sym, _ = _eval_label(expr, x86=False, labels=self._labels, here=addr,
                                             forward=True)  # fmt: skip
            except _ExprError as exc:
                raise AsmError(str(exc)) from None
            if sym != label:
                raise AsmError(f"internal error: relocation for {sym!r}, expected {label!r}")
            buf[0:4] = (word & ~mask).to_bytes(4, "little")
        relocs = ((off, rtype, label, addend),)
        check = replace(self._check(text, addr, self._labels, False), section=".text",
                        externs=frozenset({label}), relocs=relocs)  # fmt: skip
        return bytes(buf), check, relocs

    def _substitute_consts(self, text: str) -> str:
        """Replace constants (.equ/.set/=) in the operands by their values."""
        head = text.split(" ", 1)[0] if text.startswith(".") else _split_statement(text, self._x86)[0]

        def sub(m: re.Match) -> str:
            v = self._consts.get(m[0])
            return m[0] if v is None else str(v) if v >= 0 else f"({v})"

        return head + _IDENT_RE.sub(sub, text[len(head) :])

    def _program_constant(self, name: str, expr: str, labels: list[str]):
        """`.equ name, expr` / `.set` / `name = expr`: a number, or a difference of labels
        defined earlier in the current section (`. - msg`)."""
        if (name in self._labels or name in self._symtab or name in self._consts
                or name in labels or _NUMERIC_NAME_RE.fullmatch(name)):  # fmt: skip
            raise AsmError(f"{name!r} is already defined (constants cannot be redefined)")
        if _reserved_name(self.isa, name) or name.startswith(_RESERVED_PREFIX) or name in (".", "$"):
            raise AsmError(f"{name!r} is a register name, keyword or reserved name")
        if any(fx.label == name for fx in self._fixups):
            raise AsmError(f"{name!r} was used before this definition; define constants first")
        expr = self._substitute_consts("x " + expr)[2:]
        section = self._section if self._section in _PROGRAM_SECTIONS else ".data"
        here = self._section_size(section)
        used: dict[str, int] = {}
        value, reloc = self._data_value(expr, 8, here, section, used)
        if reloc is not None:
            raise AsmError(f"constant {name!r} would be the address of {reloc[0]!r}; only numbers "
                           "and differences of labels in the same section are constants")  # fmt: skip
        cacheable = not used and "." not in [m[0] for m in _IDENT_RE.finditer(expr)]
        check = _Check(f".quad {expr}", here, used, cacheable, section=".data",
                       expect=(value & MASK64).to_bytes(8, "little"))  # fmt: skip
        return ("const", name, value, check)

    def _program_directive(self, text: str, strings: list[str], line: int, addr: int):
        """Parse one directive; returns the content to commit (see _commit_program)."""
        head, _, args = text.partition(" ")
        name, args = head.lower(), args.strip()
        section = self._section
        full = _unmask(text, strings)  # what GNU as sees
        if name in _REFUSED_DIRECTIVES or name.startswith((".cfi_", ".if")):
            raise AsmError(_REFUSED_DIRECTIVES.get(name, f"{head} is not supported"))
        if name in (".text", ".data", ".bss"):
            if args:
                raise AsmError(f"{head} takes no operands (subsections are not supported)")
            return ("section", name)
        if name == ".section":
            return ("section", _parse_section(args, strings))
        if name in (".globl", ".global"):
            names = [a.strip() for a in args.split(",")]
            for n in names:
                if not _SYMBOL_RE.fullmatch(n) or n.startswith(_RESERVED_PREFIX):
                    raise AsmError(f"{head}: {n!r} is not a label name")
            return ("globl", names)
        if name in (".type", ".size", ".file"):
            return ("none",)
        if name == ".intel_syntax":
            if not self._x86 or args.lower() != "noprefix":
                raise AsmError("only `.intel_syntax noprefix` (x86-64) is supported")
            return ("none",)
        if name not in _ALIGN_DIRECTIVES | _FILL_DIRECTIVES | _STRING_DIRECTIVES and \
                _data_width(self._x86, name) is None:  # fmt: skip
            raise AsmError(f"unsupported directive {head!r}")
        if section == _NOTE_SECTION:
            raise AsmError(f"nothing may be placed in {section}")
        if name in _ALIGN_DIRECTIVES:
            return self._program_align(name, head, args, full, line, addr)
        if section == ".text":
            raise AsmError(f"{head} is not allowed in .text (only instructions and alignment); "
                           "put data in .data or .rodata")  # fmt: skip
        if section == ".bss" and name not in _FILL_DIRECTIVES:
            raise AsmError(f"{head} is not allowed in .bss (only .zero/.space/.skip and alignment)")
        offset = self._section_size(section)
        relocs: list[tuple[int, int, str, int]] = []
        used: dict[str, int] = {}
        ops = _split_args(args)
        if name in _FILL_DIRECTIVES:
            if not 1 <= len(ops) <= (1 if name == ".zero" else 2):
                raise AsmError(f"{head} takes a size" + ("" if name == ".zero" else " and an optional fill byte"))
            size = self._const(ops[0], head)
            fill = self._const(ops[1], head) if len(ops) == 2 else 0
            if size < 0:
                raise AsmError(f"{head}: negative size {size}")
            if not -128 <= fill <= 255 or (section == ".bss" and fill):
                raise AsmError(f"{head}: fill value {fill} is not a byte" if section != ".bss"
                               else ".bss can only hold zeros")  # fmt: skip
            self._reserve(section, size)
            data = b"" if section == ".bss" else bytes([fill & 0xFF]) * size
        elif name in _STRING_DIRECTIVES:
            if not ops or not all(_STRING_TOKEN_RE.fullmatch(o) for o in ops):
                raise AsmError(f"{head} takes one or more strings in double quotes")
            data = b"".join(_decode_string(strings[int(o[1:-1])]) +
                            (b"\0" if name != ".ascii" else b"") for o in ops)  # fmt: skip
            size = len(data)
            self._reserve(section, size)
        else:
            width = _data_width(self._x86, name)
            if not ops:
                raise AsmError(f"{head} takes one or more values")
            out = bytearray()
            for op in ops:
                value, reloc = self._data_value(op, width, offset + len(out), section, used)
                if reloc is not None:
                    relocs.append((len(out), _DATA_RELOC[self.isa.name][width], *reloc))
                    value = 0
                out += (value & ((1 << 8 * width) - 1)).to_bytes(width, "little")
            data, size = bytes(out), len(out)
            both = sorted(set(used) & {r[2] for r in relocs})
            if both:
                raise AsmError(f"{both[0]!r} is used both in a label difference and as an address in "
                               "one directive; split it into two directives")  # fmt: skip
            self._reserve(section, size)
        ids = [m[0] for m in _IDENT_RE.finditer(_STRING_TOKEN_RE.sub("", text[len(head):]))]
        check = _Check(full, offset, used, not ids and not relocs, section=section,
                       externs=frozenset(r[2] for r in relocs), relocs=tuple(relocs))  # fmt: skip
        return ("data", DataItem(section, offset, full, data, size, line), check, tuple(relocs), 1)

    def _program_align(self, name, head, args, full, line, addr):
        section = self._section
        ops = _split_args(args)
        if not 1 <= len(ops) <= 3 or not ops[0]:
            raise AsmError(f"{head} takes an alignment, an optional fill byte and an optional "
                           "maximum skip")  # fmt: skip
        n = self._const(ops[0], head)
        if name == ".p2align" or (name == ".align" and not self._x86):
            if not 0 <= n <= 12:
                raise AsmError(f"{head} {n}: the power of two must be 0..12 (at most page alignment)")
            n = 1 << n
        elif not 1 <= n <= _PAGE or n & (n - 1):
            raise AsmError(f"{head} {n}: the alignment must be a power of two from 1 to {_PAGE} bytes"
                           + (" (on AArch64 `.align` takes a power of two)" if not self._x86 else ""))
        fill, most = 0, None
        if len(ops) == 3:
            most = self._const(ops[2], head)
            if most < 0:
                raise AsmError(f"{head}: negative maximum skip {most}")
        if len(ops) >= 2 and ops[1]:
            if section == ".text":
                raise AsmError("a fill value is not allowed in .text (it is padded with NOPs)")
            fill = self._const(ops[1], head)
            if not -128 <= fill <= 255 or (section == ".bss" and fill):
                raise AsmError(f"{head}: fill value {fill} is not a byte" if section != ".bss"
                               else ".bss can only hold zeros")  # fmt: skip
        offset = self._section_size(section)
        pad = -offset % n
        if most is not None and pad > most:
            pad = 0  # GNU as: no alignment when more than `most` bytes would be skipped
        self._reserve(section, pad)
        if section == ".text":
            filler = b"\x90" * pad if self._x86 else _A64_NOP * (pad // 4)
            check = _Check(full, addr, {}, False, section=".text")
            return ("text", _Encoded(filler, check=check), n)
        data = b"" if section == ".bss" else bytes([fill & 0xFF]) * pad
        check = _Check(full, offset, {}, False, section=section)
        return ("data", DataItem(section, offset, full, data, pad, line), check, (), n)

    def _reserve(self, section: str, size: int) -> None:
        """Resource limits: refuse a directive that would grow a section past its cap."""
        if section == ".text":
            if len(self._code) + size > MAX_CODE:
                raise AsmError(f"program too large (more than {MAX_CODE} bytes of code)")
        elif section == ".bss":
            if self._bss + size > MAX_BSS:
                raise AsmError(f".bss too large (more than {MAX_BSS} bytes)")
        elif len(self._data[".rodata"]) + len(self._data[".data"]) + size > MAX_DATA:
            raise AsmError(f"data too large (more than {MAX_DATA} bytes in .rodata and .data)")

    def _const(self, expr: str, head: str) -> int:
        try:
            return _eval_const(expr, self._x86)
        except _ExprError as exc:
            raise AsmError(f"{head}: bad constant {_clip(expr, 60)!r}: {exc}") from None

    def _data_value(self, expr: str, width: int, here: int, section: str, used: dict):
        """(value, None) for a constant, or (0, (label, addend)) for an address the linker
        fills in. A difference of labels defined earlier in the same section (or `.`) is a
        constant."""
        try:
            lin = _eval_lin(expr, x86=self._x86, here=here, forward=True)
        except _ExprError as exc:
            raise AsmError(f"bad value {_clip(expr, 60)!r}: {exc}") from None
        terms = dict(lin.terms)
        dot = terms.pop(("sec",), 0)
        names = {k[1]: v for k, v in terms.items()}
        total = dot + sum(names.values())
        if total == 1 and not dot and len(names) == 1 and next(iter(names.values())) == 1:
            if width not in (4, 8):
                raise AsmError(f"a label address needs 4 or 8 bytes (.long/.quad), not {width}")
            return 0, (next(iter(names)), lin.c)
        if total != 0:
            raise AsmError(
                f"unsupported label arithmetic in {_clip(expr, 60)!r} (allowed: a constant, "
                "label + constant, or a difference of labels defined earlier in the same section)"
            )
        value = lin.c  # `.` is already in c
        for n, coef in names.items():
            where = self._symtab.get(n)
            if section == ".text" and n in self._labels:
                where = (".text", self._labels[n] - self.base)
            if where is None or where[0] != section:
                raise AsmError(f"{n!r} in {_clip(expr, 60)!r}: a label difference needs both labels "
                               f"defined earlier in this section ({section})")  # fmt: skip
            used[n] = where[1]
            value += coef * where[1]
        bits = 8 * width
        if not -(1 << (bits - 1)) <= value < 1 << bits:
            raise AsmError(f"value {_fmt(value)} does not fit in {width} byte(s)")
        return value, None

    def _program_verify(self, index: int, chk: _Check, theirs: bytes, their_relocs) -> str | None:
        """Compare GNU as's bytes and relocations with a committed program-mode statement."""
        if chk.expect is not None:  # a constant
            if theirs != chk.expect:
                return (f"GNU as evaluates the constant to {int.from_bytes(theirs, 'little'):#x}, "
                        f"the stream to {int.from_bytes(chk.expect, 'little'):#x}")  # fmt: skip
            return None
        mine = sorted(chk.relocs)
        got = sorted(tuple(r) for r in their_relocs)
        if mine != got:
            return (f"GNU as emits relocations {_fmt_relocs(self.isa, got)}, the stream "
                    f"{_fmt_relocs(self.isa, mine)}")  # fmt: skip
        if chk.section != ".text":
            item = self._items[index]
            want = bytes(item.size) if item.section == ".bss" else item.data
            if theirs != want:
                return (f"GNU as assembles this directive to {len(theirs)} bytes "
                        f"({_clip(theirs.hex(), 80) or '-'}), the stream to {len(want)} "
                        f"({_clip(want.hex(), 80) or '-'})")  # fmt: skip
            return None
        insn = self._insns[index]
        if theirs == insn.data:
            return None
        directive = insn.text.startswith(".")
        a = self._disasm_text(insn.data, insn.address)
        b = self._disasm_text(theirs, insn.address)
        if not directive and not chk.relocs and a is not None and a == b:
            return None
        if len(theirs) == len(insn.data) and (not chk.relocs or directive):
            if directive and not _is_padding(self.isa, theirs, insn.address):
                return f"GNU as pads with {_clip(theirs.hex(), 80)}, which is not NOP padding"
            off = insn.address - self.base
            self._code[off : off + len(theirs)] = theirs
            self._insns[index] = replace(insn, data=theirs)
            if not directive:
                self.substitutions.append((insn.line, insn.data.hex(), theirs.hex(), a, b))
            return None
        return (f"keystone encoded {_clip(insn.data.hex(), 80)} ({a}) but GNU as encodes "
                f"{_clip(theirs.hex(), 80)} ({b})"
                + (", a different size" if len(theirs) != len(insn.data) else ""))

    def _finish_program(self) -> StreamObject:
        errors = list(self._skipped) + ([self.halted] if self.halted else [])
        unresolved = []
        for fx in self._fixups:
            insn = self._insns[fx.index]
            unresolved.append(Event("error", insn.address, insn.text, message=(
                f"undefined label {fx.label!r} (line {insn.line}: {_clip(insn.text)!r})"),
                line=insn.line))  # fmt: skip
        defined = set(self._labels) | set(self._symtab)
        reported = set()
        for r in self._relocs:
            if r.symbol not in defined and r.symbol not in reported:
                reported.add(r.symbol)
                unresolved.append(Event("error", r.offset, r.symbol, message=(
                    f"undefined label {r.symbol!r} (line {r.line}, {r.section})"), line=r.line))
        for g in self._globals:
            if g not in defined:
                unresolved.append(Event("error", 0, g, message=(
                    f"`.globl {g}` names a label that is never defined")))  # fmt: skip
        for ev in unresolved:
            ev.message, ev.text = _pretty_names(ev.message), _pretty_names(ev.text)
        errors += unresolved
        symbols = {n: (".text", a - self.base) for n, a in self._labels.items()}
        symbols.update(self._symtab)
        return StreamObject(
            isa=self.isa,
            base=self.base,
            sections={".text": bytes(self._code), ".rodata": bytes(self._data[".rodata"]),
                      ".data": bytes(self._data[".data"])},  # fmt: skip
            bss_size=self._bss,
            symbols=symbols,
            globals=list(self._globals),
            relocations=list(self._relocs),
            alignments=dict(self._align),
            instructions=[replace(i) for i in self._insns],
            items=[replace(i) for i in self._items],
            errors=errors,
            warnings=[ev for _, ev in self._warns],
            events=self.events + unresolved,
        )

    # -- keystone (isolated worker) -----------------------------------------------------

    def _asm(self, text: str, addr: int) -> bytes:
        ok, data, _ = _KEYSTONE.call(self.isa.name, text, addr)
        if not ok:
            if _KeystoneWorker.died(data):
                raise _KeystoneDied(data)
            raise AsmError(self._explain(data, text))
        if not data:
            raise AsmError("assembler produced no bytes")
        return data

    def _explain(self, message: str, text: str) -> str:
        """keystone's (often generic) message, plus GNU as's diagnostic when it has one."""
        if not self._gas:
            return message
        try:
            diag = _gas_diagnose(self._gas, self.isa, text)
        except Exception:  # noqa: BLE001 - the explanation is optional
            diag = None
        return f"{message}; GNU as: {diag}" if diag else message

    def _discover(self, text: str, addr: int, placeholder: int) -> tuple[bytes, list[str]]:
        """Find the symbols a statement uses by assembling it with every symbol resolved to
        `placeholder`. Returns those bytes and the symbol names."""
        # With a resolver installed keystone reads bare decimal literals as hex (1000 ->
        # 0x1000), so literals are rewritten as hex for this pass (octal kept octal).
        ok, data, seen = _KEYSTONE.call(self.isa.name, _hex_literals(text), addr, placeholder)
        if not ok:
            if _KeystoneWorker.died(data):
                raise _KeystoneDied(data)
            unknown = [n for n in seen if n not in self._labels]
            hint = f" (undefined symbol {unknown[0]!r}?)" if unknown else ""
            raise AsmError(f"{data}{hint}")
        if not data:
            raise AsmError("assembler produced no bytes")
        return data, seen

    def _encode(self, text: str, addr: int) -> _Encoded:
        _hex_literals(text)  # rejects malformed octal literals up front, for both ISAs
        if self._x86:
            return self._encode_x86(text, addr, self._labels)
        return self._encode_a64(text, addr)

    def _check(self, text: str, addr: int, labels: dict[str, int], cacheable: bool) -> _Check:
        names = [m[0] for m in _IDENT_RE.finditer(text)]
        used = {n: labels[n] for n in names if n in labels}
        here = "." in names or (self._x86 and "$" in names)
        return _Check(text, addr, used, cacheable and not used and not here)

    # -- x86-64 -------------------------------------------------------------------------

    def _encode_x86(
        self,
        text: str,
        addr: int,
        labels: dict[str, int],
        *,
        want_len: int | None = None,
        provisional: bytes | None = None,
    ) -> _Encoded:
        """Encode an x86 statement with `labels` known. With want_len (patching a pending
        instruction), the result must have that size and no label may still be unknown."""
        _x86_reject_rip_register(text)
        _x86_reject_unverifiable(text)
        head, ops = _split_statement(text, True)
        branch = _x86_direct_branch(head, ops)
        if branch is not None:
            return self._x86_branch(text, head, branch, addr, labels, want_len, provisional)
        canon = [_x86_operand(op, labels, addr, self._program) for op in ops]
        forward = list(dict.fromkeys(o.fwd for o in canon if o.fwd))
        if len(forward) > 1:
            raise AsmError(f"more than one forward label: {', '.join(forward)}")
        if forward and want_len is not None:
            raise AsmError(f"label {forward[0]!r} is still undefined")
        rip = next((o for o in canon if o.kind == "mem" and o.riprel), None)

        def render(disp: int | None = None) -> str:
            texts = [o.render(disp if o is rip else None) for o in canon]
            return f"{head} {', '.join(texts)}" if texts else head

        end = None
        if rip is not None and rip.target is not None:
            first = self._asm(render(0), addr)
            end = addr + len(first)
            target = addr if rip.fwd else rip.target
            data = self._asm(render(_s64(target - end)), addr)
            if len(data) != len(first):
                raise AsmError(f"RIP-relative encoding changed size ({len(first)} -> {len(data)})")
        else:
            data = self._asm(render(), addr)
            if (self._program and head.lower() != "movabs"
                    and any(o.kind == "mem" and not (o.base or o.index) for o in canon)):  # fmt: skip
                data = _x86_force_absolute(data, addr)
        if want_len is not None and len(data) != want_len:
            raise AsmError(f"re-encoding changed size ({want_len} -> {len(data)})")
        if forward:
            # Provisional bytes are not checked: they are never executed or exposed as
            # final, and are re-encoded and checked when the label arrives.
            _x86_check_length(data, addr)
            if self._program:
                owner = next(o for o in canon if o.fwd)
                form = "x86_riprel" if owner.riprel else "x86_abs"
                kind = "imm" if owner.kind == "imm" else "mem32" if owner.addr32 else "mem"
                return _Encoded(data, _Fixup(-1, forward[0], form, 0, owner.addend, kind))
            return _Encoded(data, _Fixup(-1, forward[0], "x86_riprel", 0))
        _x86_verify(data, addr, canon)
        return _Encoded(data, check=self._check(text, addr, labels, cacheable=True))

    def _x86_branch(self, text, head, branch, addr, labels, want_len, provisional) -> _Encoded:
        keyword, expr = branch
        try:
            value, fwd, _ = _eval_label(expr, x86=True, labels=labels, here=addr, forward=True)
        except _ExprError as exc:
            raise AsmError(f"bad branch target {expr!r}: {exc}") from None
        if fwd is not None:
            if want_len is not None:
                raise AsmError(f"label {fwd!r} is still undefined")
            for placeholder in (addr + _X86_FAR, addr + _X86_NEAR):
                try:
                    data = self._asm(f"{head} {keyword}{placeholder:#x}", addr)
                    _x86_check_branch(data, addr, placeholder, head)
                except _KeystoneDied:
                    raise
                except AsmError:
                    if placeholder == addr + _X86_NEAR:
                        raise
                    continue  # rel8-only branch: retry with a near placeholder
                return _Encoded(data, _Fixup(-1, fwd, "x86_branch", placeholder))
            raise AssertionError("unreachable")
        target = value & MASK64
        check = self._check(text, addr, labels, cacheable=False)
        try:
            data = self._asm(f"{head} {keyword}{target:#x}", addr)
        except _KeystoneDied:
            raise
        except AsmError:
            if provisional is None:
                raise
            data = b""
        if want_len is not None and len(data) != want_len:
            # Keystone chose another size; keep the provisional opcode and move the target.
            ins = iced_x86.Decoder(64, provisional, ip=addr).decode()
            ins.near_branch64 = target
            enc = iced_x86.Encoder(64)
            try:
                enc.encode(ins, addr)
            except ValueError as exc:
                raise AsmError(
                    f"branch target {target:#x} out of range for {head}: {exc}"
                ) from None
            data = bytes(enc.take_buffer())
            if len(data) != want_len:
                raise AsmError(f"size changed ({want_len} -> {len(data)})")
        _x86_check_branch(data, addr, target, head)
        return _Encoded(data, check=check)

    # -- AArch64 ------------------------------------------------------------------------

    def _encode_a64(self, text: str, addr: int) -> _Encoded:
        st = _a64_prepare(text, addr, self._labels)
        if st.target_index is None:
            try:
                data = self._asm(st.ks_text, addr)
            except _KeystoneDied:
                raise
            except AsmError:
                try:
                    _, names = self._discover(st.ks_text, addr, addr + 4)
                except AsmError:
                    names = []
                unknown = [n for n in names if n not in self._labels]
                if unknown:
                    raise AsmError(
                        f"forward reference to {unknown[0]!r} in a non-PC-relative operand"
                    ) from None
                raise
            _a64_check_single(data)
            _a64_check_mov(st, data)
            if st.forward:
                return _Encoded(data, _Fixup(-1, st.forward, "a64_lo12", 0))
            return _Encoded(data, check=self._check(text, addr, self._labels, not st.has_lo12))
        # keystone only sees a placeholder symbol one instruction ahead (it mis-encodes
        # tbz/tbnz with a zero or negative offset); the offset field is written here.
        data, names = self._discover(st.ks_text, addr, addr + 4)
        others = [n for n in names if n != _A64_TARGET_SYM]
        if others:
            raise AsmError(
                f"unknown symbol {others[0]!r} (labels may only be used as the branch or "
                "literal target, or in :lo12:)"
            )
        _a64_check_single(data)
        word = int.from_bytes(data, "little")
        if _a64_pcrel_kind(word) is None:
            raise AsmError(f"unexpected encoding {data.hex()} for a PC-relative instruction")
        if st.forward:
            return _Encoded(data, _Fixup(-1, st.forward, "a64_pcrel", addr + 4))
        placed = self._a64_place(word, addr, st)
        return _Encoded(placed, check=self._check(text, addr, self._labels, False))

    def _a64_place(self, word: int, addr: int, st: "_A64Statement") -> bytes:
        """Write the PC-relative field for the statement's target, then decode it back."""
        kind = _a64_pcrel_kind(word)
        data = _a64_set_offset(word, addr, st.target).to_bytes(4, "little")
        want = (st.target & ~0xFFF if kind == "adrp" else st.target) & MASK64
        decoded = next(self._cs.disasm(data, addr), None)
        m = decoded and re.search(r"#(0x[0-9a-f]+|\d+)$", decoded.op_str)
        ok = bool(m) and int(m[1], 0) == want
        if ok and kind == "adrp":
            ok = decoded.mnemonic == "adrp" and decoded.op_str.split(",")[0] == st.ops[0].lower()
        if not ok:
            shown = f"{decoded.mnemonic} {decoded.op_str}" if decoded else data.hex()
            raise AsmError(f"target {want:#x} does not round-trip (decodes as {shown})")
        return data

    # -- fixups -------------------------------------------------------------------------

    def _patch(self, fx: _Fixup, target: int) -> tuple[bytes, _Check | None]:
        """Final bytes (same size) and cross-check for a pending instruction once its
        label is known."""
        insn = self._insns[fx.index]
        labels = {**self._labels, fx.label: target}
        if fx.form in ("a64_pcrel", "a64_lo12"):
            st = _a64_prepare(insn.text, insn.address, labels)
            check = self._check(insn.text, insn.address, labels, False)
            if fx.form == "a64_pcrel":
                word = int.from_bytes(insn.data, "little")
                return self._a64_place(word, insn.address, st), check
            data = self._asm(st.ks_text, insn.address)
            _a64_check_single(data)
            _a64_check_mov(st, data)
            return data, check
        enc = self._encode_x86(
            insn.text, insn.address, labels, want_len=len(insn.data), provisional=insn.data
        )
        return enc.data, enc.check

    # -- cross-checks -------------------------------------------------------------------

    def _queue_check(self, number: int, index: int, check: _Check | None) -> None:
        if check is not None and self._gas is not None:
            if self._program and check.section is None:
                check.section = ".text"
            self._unchecked.append((number, index, check))

    def _run_crosscheck(self) -> tuple[int, str] | None:
        """Assemble every newly final instruction with GNU as. Returns (statement number,
        message) for the first statement that fails, if any."""
        items, self._unchecked = self._unchecked, []
        if not items:
            return None
        try:
            results = _gas_check(self._gas, self.isa, [chk for _, _, chk in items])
        except _ToolFailure as exc:
            return items[0][0], (
                f"GNU as cross-check could not run ({exc}); the statement was not verified "
                "and was not committed (transient: feed it again)"
            )
        except Exception as exc:  # noqa: BLE001
            return items[0][0], (
                f"internal error in the GNU as cross-check ({type(exc).__name__}: {exc})"
            )
        for (number, index, chk), (theirs, err, *extra) in zip(items, results, strict=False):
            if err:
                return number, f"rejected by the GNU as cross-check: {err}"
            if chk.section is not None:
                problem = self._program_verify(index, chk, theirs, extra[0] if extra else [])
                if problem:
                    return number, problem
                continue
            insn = self._insns[index]
            if theirs != insn.data:
                a = self._disasm_text(insn.data, insn.address)
                b = self._disasm_text(theirs, insn.address)
                if a is not None and a == b:
                    continue
                if len(theirs) == len(insn.data):
                    # Same size, different encoding (e.g. AArch64 `uxtw x0, w2`: keystone ubfx,
                    # GNU as `mov w0, w2`). GNU as is the reference and assembled this statement at
                    # its final address with the final label values: use its bytes. Nothing after
                    # it moves, and a silent keystone miscoding is corrected rather than scored.
                    off = insn.address - self.base
                    self._code[off : off + len(theirs)] = theirs
                    self._insns[index] = replace(insn, data=theirs)
                    self.substitutions.append((insn.line, insn.data.hex(), theirs.hex(), a, b))
                    continue
                return number, (
                    f"keystone encoded {insn.data.hex()} ({a}) but GNU as encodes "
                    f"{theirs.hex()} ({b}), a different size"
                )
        if len(results) < len(items):
            return items[len(results)][0], "internal error: statement left unverified"
        return None

    def _disasm_text(self, data: bytes, addr: int) -> str | None:
        """Canonical disassembly, for deciding whether two encodings are the same
        instruction (iced's Intel syntax with explicit operand sizes on x86; branch sizes
        and movabs/mov moffs forms print alike)."""
        if self._x86:
            decoder, out = iced_x86.Decoder(64, data, ip=addr), []
            for ins in decoder:
                if ins.code == iced_x86.Code.INVALID:
                    return None
                text = _X86_CMP_FORMATTER.format(ins)
                if ins.mnemonic == iced_x86.Mnemonic.XCHG:  # operand order is immaterial
                    head, _, ops = text.partition(" ")
                    text = f"{head} {','.join(sorted(ops.split(',')))}"
                out.append(text)
            return "; ".join(out)
        decoded = list(self._cs.disasm(data, addr))
        if sum(d.size for d in decoded) != len(data):
            return None
        return "; ".join(f"{d.mnemonic} {d.op_str}".strip() for d in decoded)

    def _crosscheck(self, insn: Instruction) -> list[Event]:
        """Disassemble committed bytes: lengths must round-trip, mnemonics should agree."""
        decoded = list(self._cs.disasm(insn.data, insn.address))
        size = sum(d.size for d in decoded)
        problems = []
        if size != len(insn.data):
            problems.append(f"capstone decoded {size} of {len(insn.data)} bytes")
        elif not _same_mnemonic(self.isa, insn.text, decoded[0].mnemonic):
            problems.append(f"disassembles as {decoded[0].mnemonic} {decoded[0].op_str}")
        elif not insn.pending:
            # A bare label as branch/PC-relative target must show up as the decoded address.
            head, ops = _split_statement(insn.text, self._x86)
            pcrel = (_x86_direct_branch(head, ops) is not None if self._x86
                     else _a64_target_index(head, ops) is not None)  # fmt: skip
            last = ops[-1].split(None, 1)[-1].strip() if ops and pcrel else ""
            target = self._labels.get(last)
            if target is not None and decoded[0].mnemonic == "adrp":
                target &= ~0xFFF  # adrp addresses the label's page
            if target is not None and f"{target:#x}" not in decoded[0].op_str:
                problems.append(f"target of {last!r} decodes as {decoded[0].op_str}")
        return [
            Event("warning", insn.address, insn.text, insn.data, p, insn.line) for p in problems
        ]


# -- keystone worker process ----------------------------------------------------------

# keystone calls LLVM's fatal-error handler (abort) on some inputs, e.g. an AArch64
# `b.ne .+0x200000` or `add x0, x0, #:lo12:sym`. It runs in this child process so an abort
# costs one statement, not the host. Handles live for the whole process and are never
# freed (freeing a resolver-enabled handle breaks the radix of later handles: bca.isa).
_KS_WORKER_SRC = r"""
import json, sys
import keystone
MAX = 64
ARCH = {"x86_64": (keystone.KS_ARCH_X86, keystone.KS_MODE_64),
        "aarch64": (keystone.KS_ARCH_ARM64, keystone.KS_MODE_LITTLE_ENDIAN)}
plain = {k: keystone.Ks(*v) for k, v in ARCH.items()}
seen, value = [], [0]
def resolve(name, out):
    name = name.decode()
    if name not in seen:
        seen.append(name)
    out[0] = value[0]
    return True
resolver = {}
for k, v in ARCH.items():
    ks = keystone.Ks(*v)
    ks.sym_resolver = resolve
    resolver[k] = ks
out = sys.stdout
for line in sys.stdin:
    isa, text, addr, placeholder = json.loads(line)
    del seen[:]
    value[0] = placeholder or 0
    ks = plain[isa] if placeholder is None else resolver[isa]
    try:
        data, _ = ks.asm(text, addr)
        data = bytes(data or b"")
        if len(data) > MAX:
            reply = [False, "encoding of %d bytes is too long" % len(data), seen]
        else:
            reply = [True, data.hex(), seen]
    except keystone.KsError as exc:
        reply = [False, str(exc), seen]
    out.write(json.dumps(reply) + "\n")
    out.flush()
"""


class _KeystoneWorker:
    """A persistent keystone subprocess, restarted transparently after it dies."""

    timeout = 5.0  # one statement; generous even on a loaded machine
    hang_memo = 300.0  # seconds a timed-out statement is refused without asking again
    _DIED = ("keystone crashed", "keystone hung")

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._stderr = None
        self._pid = 0
        self._lock = threading.Lock()
        self.restarts = 0
        self._hung: dict[tuple[str, str], tuple[str, float]] = {}  # timed-out statements

    @classmethod
    def died(cls, message) -> bool:
        return isinstance(message, str) and message.startswith(cls._DIED)

    def start(self) -> None:
        with self._lock:
            self._ensure()

    def _ensure(self) -> subprocess.Popen:
        if self._pid != os.getpid():  # forked: the pipes belong to the parent
            self._proc, self._pid = None, os.getpid()
        if self._proc is None:
            self._stderr = tempfile.TemporaryFile()
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _KS_WORKER_SRC],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
            )
        return self._proc

    def call(
        self, isa: str, text: str, addr: int, placeholder: int | None = None
    ) -> tuple[bool, bytes | str, list[str]]:
        """Assemble with the plain handle (placeholder None) or the resolver handle, which
        resolves every symbol to `placeholder` and reports the names it saw. Returns
        (ok, bytes or error message, symbols)."""
        with self._lock:
            hung = self._hung.get((isa, text))
            if hung and time.monotonic() - hung[1] < self.hang_memo:
                return False, hung[0], []
            proc = self._ensure()
            reply, timed_out = b"", False
            try:
                proc.stdin.write((json.dumps([isa, text, addr, placeholder]) + "\n").encode())
                proc.stdin.flush()
                if select.select([proc.stdout], [], [], self.timeout)[0]:
                    reply = proc.stdout.readline()
                else:
                    timed_out = True
            except OSError:
                pass
            if not reply:
                message = self._died(proc, timed_out)
                if timed_out:
                    if len(self._hung) > 10_000:
                        self._hung.clear()
                    self._hung[(isa, text)] = (message, time.monotonic())
                return False, message, []
            ok, payload, seen = json.loads(reply)
            return ok, bytes.fromhex(payload) if ok else payload, seen

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or self._pid != os.getpid():
                return
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()

    def _died(self, proc: subprocess.Popen, timed_out: bool) -> str:
        if timed_out:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # output closed but still alive: finish it
            proc.kill()
            proc.wait()
        self._stderr.seek(0)
        tail = self._stderr.read()[-4000:].decode(errors="replace")
        lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
        self._proc = None
        self.restarts += 1
        if timed_out:
            return (
                f"keystone hung (no answer within {self.timeout:g} s); statement rejected, "
                "assembler restarted"
            )
        detail = next((ln for ln in lines if "LLVM ERROR" in ln), None)
        detail = detail or (lines[-1] if lines else f"exit status {proc.returncode}")
        return f"keystone crashed ({_clip(detail, 300)}); statement rejected, assembler restarted"


_KEYSTONE = _KeystoneWorker()
atexit.register(_KEYSTONE.close)


# -- GNU as cross-check ---------------------------------------------------------------

_GAS_NAMES = {"x86_64": ["x86_64-linux-gnu-as"], "aarch64": ["aarch64-linux-gnu-as"]}
_GAS_NATIVE = {"x86_64": ("x86_64", "amd64"), "aarch64": ("aarch64", "arm64")}
_GAS_HEADER = {"x86_64": [".intel_syntax noprefix", ".text"], "aarch64": [".text"]}
_GAS_DIAG_RE = re.compile(r"^\{standard input\}:(\d+): (Error|Warning): (.*)$", re.M)
_GAS_TIMEOUT = 20.0
# isa name -> path of GNU as (None: not found); tests may point it elsewhere.
_GAS_PATH: dict[str, str | None] = {}
# (isa, statement) -> GNU as bytes, for statements whose bytes depend on nothing else.
# Only successes are cached: a failure may be transient or depend on context.
_GAS_CACHE: dict[tuple[str, str], bytes] = {}
_GAS_LOCK = threading.Lock()
_GAS_WINDOW_GAP = 0x1000
_GAS_TMP: list = []  # [pid, directory]
_GAS_COUNTER = itertools.count()


class _ToolFailure(Exception):
    """GNU as did not produce a usable answer (not a statement error)."""


def _gas_tool(isa_name: str) -> str | None:
    if isa_name not in _GAS_PATH:
        found = os.environ.get(f"BCA_GAS_{isa_name.upper()}") or None
        search = os.pathsep.join(
            [os.environ.get("PATH", ""), os.path.expanduser("~/.local/bin"), "/usr/bin"]
        )
        for name in _GAS_NAMES[isa_name] if not found else ():
            found = shutil.which(name, path=search)
            if found:
                break
        if not found and platform.machine().lower() in _GAS_NATIVE[isa_name]:
            found = shutil.which("as", path=search)
        _GAS_PATH[isa_name] = found
    return _GAS_PATH[isa_name]


def _gas_dir() -> str:
    if not _GAS_TMP or _GAS_TMP[0] != os.getpid():
        path = tempfile.mkdtemp(prefix="bca-gas-")
        _GAS_TMP[:] = [os.getpid(), path]
        atexit.register(shutil.rmtree, path, True)
    return _GAS_TMP[1]


def _gas_check(tool: str, isa: Isa, checks: list[_Check]) -> list[tuple[bytes | None, str | None]]:
    """GNU as's bytes (or error) for each statement, in order, stopping after the first
    error. Raises _ToolFailure when GNU as could not give an answer."""
    n = len(checks)
    results: list[tuple[bytes | None, str | None] | None] = [None] * n
    with _GAS_LOCK:
        if len(_GAS_CACHE) > 100_000:
            _GAS_CACHE.clear()
        for i, c in enumerate(checks):
            if c.cacheable and _cache_key(isa, c) in _GAS_CACHE:
                results[i] = (_GAS_CACHE[_cache_key(isa, c)], None)
    todo = [i for i in range(n) if results[i] is None]
    solo: set[int] = set()
    while todo:
        windows: list[list[int]] = []
        for i in sorted(todo, key=lambda i: (checks[i].section or "", checks[i].addr)):
            last = windows[-1][-1] if windows else None
            if (last is None or i in solo or last in solo
                    or checks[i].addr - checks[last].addr > _GAS_WINDOW_GAP
                    or _separate(checks, windows[-1], i)):  # fmt: skip
                windows.append([i])
            else:
                windows[-1].append(i)
        running: list[tuple[list[int], dict]] = []
        try:
            for w in windows:
                running.append((w, _gas_start(tool, isa, [checks[i] for i in w])))
            outcomes = []
            for w, job in running:
                outcomes.append((w, _gas_finish(isa, job)))
                job["done"] = True
        finally:
            for _, job in running:  # after a failure: reap every other process
                if not job.get("done"):
                    job["proc"].kill()
                    job["proc"].communicate()
                    try:
                        os.unlink(job["obj"])
                    except OSError:
                        pass
        for w, outcome in outcomes:
            if outcome == "split":
                solo.update(w)
                continue
            for i, res in zip(w, outcome, strict=True):
                if res is not None:
                    results[i] = res
        failed = [i for i in range(n) if results[i] is not None and results[i][1]]
        first = min(failed) if failed else n
        todo = [i for i in range(first) if results[i] is None]
    out = []
    for i, res in enumerate(results):
        if res is None:
            break
        out.append(res)
        if res[1]:
            break
        if checks[i].cacheable:
            with _GAS_LOCK:
                _GAS_CACHE[_cache_key(isa, checks[i])] = res[0]
    return out


def _gas_start(tool: str, isa: Isa, checks: list[_Check]) -> dict:
    """Start GNU as on one address window: statements laid out at their offsets from the
    lowest address, labels defined relative to it."""
    base = min(c.addr for c in checks)
    header = _GAS_HEADER[isa.name]
    if checks[0].section is not None:  # program mode: page-aligned base keeps .align exact
        base &= ~(_PAGE - 1)
        header = header[:-1] + [_GAS_SECTION_HEADER[checks[0].section]]
    src = [*header, "__bca_w:"]
    owner: list[object] = [None] * len(src)
    defined: set[str] = set()
    for j, c in enumerate(checks):
        for name, value in c.labels.items():
            if name not in defined:
                defined.add(name)
                src.append(f"{name} = __bca_w + ({value - base})")
                owner.append(j)
    prev = None
    for j in sorted(range(len(checks)), key=lambda j: checks[j].addr):
        src += [f".org {checks[j].addr - base:#x}", f"__bca_s{j}:", checks[j].text, f"__bca_e{j}:"]
        owner += [("org", prev), None, j, None]
        prev = j
    obj = os.path.join(_gas_dir(), f"{os.getpid()}-{next(_GAS_COUNTER)}.o")
    try:
        proc = subprocess.Popen(
            [tool, "-o", obj, "--"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise _ToolFailure(f"cannot start {tool}: {exc}") from None
    return {"proc": proc, "src": "\n".join(src) + "\n", "owner": owner, "obj": obj,
            "checks": checks, "base": base}  # fmt: skip


def _gas_finish(isa: Isa, job: dict):
    """Per statement of the window: (bytes, None), (None, error) or None (undecided); or
    "split" when statements overlap and must be assembled one by one."""
    proc, obj, checks = job["proc"], job["obj"], job["checks"]
    try:
        try:
            _, err = proc.communicate(job["src"].encode(), timeout=_GAS_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise _ToolFailure(f"GNU as timed out after {_GAS_TIMEOUT:g} s") from None
        except OSError as exc:
            proc.kill()
            proc.wait()
            raise _ToolFailure(f"GNU as I/O error: {exc}") from None
        stderr = err.decode(errors="replace")
        located: dict[int, str] = {}
        split = False
        for m in _GAS_DIAG_RE.finditer(stderr):
            ln = int(m[1]) - 1
            own = job["owner"][ln] if 0 <= ln < len(job["owner"]) else None
            if isinstance(own, tuple):
                if own[1] is not None:
                    split = True  # the previous statement grew into this one's address
                continue
            if own is not None:
                msg = m[3].strip() if m[2] == "Error" else f"warning: {m[3].strip()}"
                located.setdefault(own, _clip(msg, _MAX_MESSAGE))
        if split and len(checks) > 1 and not located:
            return "split"
        if located:
            return [(None, located[j]) if j in located else None for j in range(len(checks))]
        if proc.returncode != 0:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
            how = (f"killed by signal {-proc.returncode}" if proc.returncode < 0
                   else f"exit status {proc.returncode}")  # fmt: skip
            raise _ToolFailure(f"GNU as failed ({how}) {_clip(detail, 200)}".strip())
        try:
            with open(obj, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            raise _ToolFailure(f"GNU as wrote no object: {exc}") from None
        return _gas_extract(isa, blob, checks, job["base"])
    finally:
        try:
            os.unlink(obj)
        except OSError:
            pass


def _gas_extract(isa: Isa, blob: bytes, checks: list[_Check], base: int):
    from bca.ir.elf import Elf, ElfError

    try:
        elf = Elf(blob)
        section = checks[0].section
        text_sec = next(s for s in elf.sections if s.name == (section or ".text"))
        symbols = elf.symbols()
        relocs = elf.relocations_for(text_sec)
    except (ElfError, StopIteration, struct.error, IndexError, ValueError) as exc:
        raise _ToolFailure(f"cannot read GNU as output: {exc}") from None
    text = bytearray(text_sec.size if text_sec.type == 8 else elf.data(text_sec))  # 8: NOBITS
    left: dict[int, list] = {}  # program mode: relocations against the extern labels
    where = {s.name: s.value for s in symbols if s.shndx == text_sec.index and s.name}
    out: list[tuple[bytes | None, str | None]] = []
    spans = []
    for j in range(len(checks)):
        lo, hi = where.get(f"__bca_s{j}"), where.get(f"__bca_e{j}")
        if lo is None or hi is None or not lo <= hi <= len(text):
            raise _ToolFailure("GNU as output lacks the statement markers")
        spans.append((lo, hi))
    errors: dict[int, str] = {}
    for r in relocs:
        owner = next((j for j, (lo, hi) in enumerate(spans) if lo <= r.offset < hi), None)
        if owner is None:
            raise _ToolFailure(f"relocation outside every statement at {r.offset:#x}")
        sym = symbols[r.sym_index] if r.sym_index < len(symbols) else None
        if section is not None and sym is not None and sym.shndx == 0 and r.symbol in checks[owner].externs:
            lo = spans[owner][0]
            left.setdefault(owner, []).append((r.offset - lo, r.type, r.symbol, r.addend))
            continue
        if r.sym_index == 0 or sym is None:
            s_val = 0
        elif sym.shndx == text_sec.index:
            s_val = base + sym.value
        elif sym.shndx == 0xFFF1:  # SHN_ABS
            s_val = sym.value
        else:
            errors.setdefault(owner, f"refers to undefined symbol {r.symbol!r}")
            continue
        try:
            _apply_reloc(isa, text, r.offset, r.type, s_val, r.addend, base + r.offset)
        except AsmError as exc:
            errors.setdefault(owner, str(exc))
    for j, (lo, hi) in enumerate(spans):
        if j in errors:
            out.append((None, errors[j]))
        elif section is not None:
            out.append((bytes(text[lo:hi]), None, left.get(j, [])))
        else:
            out.append((bytes(text[lo:hi]), None))
    return out


def _apply_reloc(isa: Isa, buf: bytearray, off: int, rtype: int, S: int, A: int, P: int) -> None:
    """Resolve one relocation GNU as left, with the linker's range and alignment checks."""
    if isa is X86_64:
        spec = {1: (8, False, "u64"), 24: (8, True, "u64"), 2: (4, True, "s"), 4: (4, True, "s"),
                10: (4, False, "u"), 11: (4, False, "s"), 12: (2, False, "su"),
                13: (2, True, "s"), 14: (1, False, "su"), 15: (1, True, "s")}  # fmt: skip
        if rtype not in spec:
            raise AsmError(f"GNU as needs x86-64 relocation type {rtype}; cannot verify")
        width, pcrel, rng = spec[rtype]
        val = (S + A - P) if pcrel else (S + A)
        bits, v = 8 * width, _s64(val)
        ok = {"s": -(1 << (bits - 1)) <= v < 1 << (bits - 1),
              "su": -(1 << (bits - 1)) <= v < 1 << bits,
              "u": 0 <= val & MASK64 < 1 << bits, "u64": True}[rng]  # fmt: skip
        if not ok:
            raise AsmError(f"value {_fmt(v)} does not fit relocation type {rtype}")
        buf[off : off + width] = (val & ((1 << bits) - 1)).to_bytes(width, "little")
        return
    insn = int.from_bytes(buf[off : off + 4], "little")
    value = (S + A) & MASK64
    rel = _s64(value - P)

    def put(word: int) -> None:
        buf[off : off + 4] = (word & 0xFFFFFFFF).to_bytes(4, "little")

    def field(v: int, bits: int, what: str) -> int:
        if not -(1 << (bits - 1)) <= v < 1 << (bits - 1):
            raise AsmError(f"{what} out of range ({v:#x})")
        return v & ((1 << bits) - 1)

    if rtype == 257:
        buf[off : off + 8] = value.to_bytes(8, "little")
    elif rtype in (274, 275, 276):  # ADR_PREL_LO21, ADR_PREL_PG_HI21(_NC)
        v = rel if rtype == 274 else _s64((value & ~0xFFF) - (P & ~0xFFF)) >> 12
        v = v & 0x1FFFFF if rtype == 276 else field(v, 21, "adr/adrp target")
        put(insn & ~((3 << 29) | (0x7FFFF << 5)) | (v & 3) << 29 | (v >> 2 & 0x7FFFF) << 5)
    elif rtype in (277, 278, 284, 285, 286, 299):  # ADD / LDSTn _ABS_LO12_NC
        shift = {277: 0, 278: 0, 284: 1, 285: 2, 286: 3, 299: 4}[rtype]
        lo = value & 0xFFF
        if lo & ((1 << shift) - 1):
            raise AsmError(f":lo12: value {lo:#x} is not {1 << shift}-byte aligned for this access")
        put(insn & ~(0xFFF << 10) | (lo >> shift) << 10)
    elif rtype in (273, 279, 280, 282, 283):  # LD_PREL_LO19, TSTBR14, CONDBR19, JUMP/CALL26
        bits, shift = {273: (19, 5), 279: (14, 5), 280: (19, 5), 282: (26, 0), 283: (26, 0)}[rtype]
        if rel & 3:
            raise AsmError(f"PC-relative target {value:#x} is not 4-byte aligned")
        v = field(rel >> 2, bits, "PC-relative target")
        put(insn & ~(((1 << bits) - 1) << shift) | v << shift)
    else:
        raise AsmError(f"GNU as needs AArch64 relocation type {rtype}; cannot verify")


def _gas_diagnose(tool: str, isa: Isa, text: str) -> str | None:
    """GNU as's first complaint about a statement on its own (unknown symbols are fine)."""
    src = "\n".join([*_GAS_HEADER[isa.name], text]) + "\n"
    obj = os.path.join(_gas_dir(), f"{os.getpid()}-{next(_GAS_COUNTER)}.o")
    try:
        proc = subprocess.run([tool, "-o", obj, "--"], input=src.encode(), capture_output=True,
                              timeout=_GAS_TIMEOUT)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.unlink(obj)
        except OSError:
            pass
    m = _GAS_DIAG_RE.search(proc.stderr.decode(errors="replace"))
    return _clip(m[3].strip(), 300) if m else None


def _elf_text(obj: bytes) -> tuple[bytes, dict[str, int], list[int]]:
    """(.text bytes, .text symbol offsets, .text relocation offsets) of an ELF64 LE file."""
    shoff = struct.unpack_from("<Q", obj, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", obj, 0x3A)
    # name, type, flags, addr, offset, size, link, info, addralign, entsize
    secs = [struct.unpack_from("<IIQQQQIIQQ", obj, shoff + i * shentsize) for i in range(shnum)]

    def name(off: int, table: tuple) -> str:
        start = table[4] + off
        return obj[start : obj.index(b"\0", start)].decode()

    text_idx = next(i for i, s in enumerate(secs) if name(s[0], secs[shstrndx]) == ".text")
    text = obj[secs[text_idx][4] : secs[text_idx][4] + secs[text_idx][5]]
    symbols: dict[str, int] = {}
    relocs: list[int] = []
    for s in secs:
        if s[1] == 2:  # SHT_SYMTAB
            for off in range(s[4], s[4] + s[5], 24):
                st_name, _, _, shndx, value, _ = struct.unpack_from("<IBBHQQ", obj, off)
                if shndx == text_idx:
                    symbols[name(st_name, secs[s[6]])] = value
        elif s[1] in (4, 9) and s[7] == text_idx and s[9]:  # SHT_RELA / SHT_REL
            relocs += [struct.unpack_from("<Q", obj, o)[0] for o in range(s[4], s[4] + s[5], s[9])]
    return text, symbols, relocs


# -- expressions (GNU as semantics) ---------------------------------------------------


class _ExprError(Exception):
    pass


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>0[xX][0-9a-fA-F]+|0[bB][01]+(?![\w.$@])|[0-9]+(?![\w.$@]))"
    r"|(?P<id>[A-Za-z_.$][\w.$@]*)|(?P<op><<|>>|[-+*/%&|^~()]))"
)
# GNU as binary operator ranks (expr.c op_rank), the same for both ISAs.
_GAS_PREC = {"*": 3, "/": 3, "%": 3, "<<": 3, ">>": 3, "|": 2, "&": 2, "^": 2, "+": 1, "-": 1}


def _s64(v: int) -> int:
    """Wrap to a signed 64-bit value (GNU as offsetT)."""
    v &= MASK64
    return v - (1 << 64) if v >> 63 else v


def _exact(v: int) -> int:
    """Results of + - * << and unary - are exact; GNU as silently wraps one that leaves the
    64-bit range (signed or unsigned), the stream refuses it."""
    if not -(1 << 63) <= v < 1 << 64:
        raise _ExprError("arithmetic overflow: the result does not fit in 64 bits")
    return v


def _parse_number(s: str) -> int:
    low = s.lower()
    if low.startswith("0x"):
        value = int(low, 16)
    elif low.startswith("0b"):
        value = int(low[2:], 2)
    elif len(low) > 1 and low[0] == "0":
        if not set(low) <= set("01234567"):
            raise _ExprError(f"invalid octal literal {s!r} (a leading 0 means octal)")
        value = int(low, 8)
    else:
        value = int(low)
    if value >> 64:
        raise _ExprError(f"number {_clip(s, 40)} does not fit in 64 bits")
    return value


def _gas_binop(op: str, x: int, y: int) -> int:
    """GNU as on the 64-bit values, except that + - * << refuse to overflow (_exact)."""
    if op == "+":
        return _exact(x + y)
    if op == "-":
        return _exact(x - y)
    if op == "*":
        return _exact(x * y)
    x, y = _s64(x), _s64(y)  # GNU as operates on offsetT: 0xffffffffffffffff is -1
    if op in ("/", "%"):
        if y == 0:
            raise _ExprError("division by zero")
        q = abs(x) // abs(y)
        q = q if (x < 0) == (y < 0) else -q  # truncate toward zero, like C
        return _s64(q) if op == "/" else _s64(x - q * y)
    if op in ("<<", ">>"):
        if not 0 <= y < 64:
            raise _ExprError(f"shift count {y} out of range (0..63)")
        return _exact(x << y) if op == "<<" else _s64((x & MASK64) >> y)
    return _s64({"&": x & y, "|": x | y, "^": x ^ y}[op])


class _Lin:
    """A value c + sum(coef * atom): atoms are ("sec",) for "some label / `.`" (label values
    are already in c), ("reg", name) for x86 registers and ("fwd", name) for a label that is
    not defined yet."""

    __slots__ = ("c", "terms")

    def __init__(self, c: int, terms: dict | None = None):
        self.c = c
        self.terms = terms or {}

    def add(self, other: "_Lin", sign: int) -> "_Lin":
        terms = dict(self.terms)
        for k, v in other.terms.items():
            terms[k] = terms.get(k, 0) + sign * v
            if not terms[k]:
                del terms[k]
        return _Lin(_exact(self.c + sign * other.c), terms)

    def scale(self, k: int) -> "_Lin":
        return _Lin(_exact(self.c * k), {a: v * k for a, v in self.terms.items() if v * k})


def _describe(terms: dict) -> str:
    kinds = {k[0] for k in terms}
    if "reg" in kinds:
        return "a register"
    return "a label" if kinds else "a constant"


def _combine(op: str, a: _Lin, b: _Lin) -> _Lin:
    if op == "+":
        return a.add(b, 1)
    if op == "-":
        return a.add(b, -1)
    if op == "*" and not (a.terms and b.terms):
        return b.scale(a.c) if not a.terms else a.scale(b.c)
    if a.terms or b.terms:
        raise _ExprError(f"operator {op!r} cannot be applied to {_describe(a.terms or b.terms)}")
    return _Lin(_gas_binop(op, a.c, b.c))


def _unary(op: str, a: _Lin) -> _Lin:
    if op == "+":
        return a
    if op == "-":
        return _Lin(0).add(a, -1)
    if a.terms:
        raise _ExprError(f"operator '~' cannot be applied to {_describe(a.terms)}")
    return _Lin(_s64(~a.c))


def _tokenize(expr: str) -> list[tuple[str, object]]:
    toks: list[tuple[str, object]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            if expr[pos:].strip():
                raise _ExprError(f"cannot parse {_clip(expr[pos:].strip(), 40)!r}")
            break
        pos = m.end()
        if m["num"] is not None:
            toks.append(("num", _parse_number(m["num"])))
        elif m["id"] is not None:
            toks.append(("id", m["id"]))
        else:
            toks.append(("op", m["op"]))
        if len(toks) > _MAX_TOKENS:
            raise _ExprError(f"expression longer than {_MAX_TOKENS} tokens")
    return toks


def _eval_lin(
    expr: str,
    *,
    x86: bool,
    labels: dict[str, int] | None = None,
    here: int | None = None,
    forward: bool = False,
    registers: bool = False,
) -> _Lin:
    """Evaluate an expression with GNU as semantics, iteratively (no recursion: nesting is
    bounded by _MAX_DEPTH). Labels come from `labels`; `.` (and `$` on x86) is `here`; with
    `forward` unknown names become forward-label atoms; with `registers` x86 register names
    become register atoms."""
    expr = expr.strip()
    if expr.startswith("#"):
        expr = expr[1:]
    toks = _tokenize(expr)
    labels = labels or {}

    def atom(kind: str, v) -> _Lin:
        if kind == "num":
            return _Lin(v)
        if v == "." or (x86 and v == "$"):
            if here is None:
                raise _ExprError("current location not available")
            return _Lin(here, {("sec",): 1})
        if registers and v.lower() in _X86_REGISTERS:
            return _Lin(0, {("reg", v.lower()): 1})
        if v in labels:
            return _Lin(labels[v], {("sec",): 1})
        if forward and not (x86 and _refused_label(X86_64, v)):
            return _Lin(0, {("fwd", v): 1})
        raise _ExprError(f"unknown symbol {v!r}")

    vals: list[_Lin] = []
    ops: list[tuple[str, str]] = []  # ("(", "("), ("u", op), ("b", op)
    expect, depth = True, 0

    def reduce() -> None:
        kind, op = ops.pop()
        if kind == "u":
            vals.append(_unary(op, vals.pop()))
        else:
            b = vals.pop()
            vals.append(_combine(op, vals.pop(), b))

    for kind, v in toks:
        if expect:
            if kind == "op" and v in ("-", "+", "~", "("):
                depth += 1
                if depth > _MAX_DEPTH:
                    raise _ExprError(f"expression nested more than {_MAX_DEPTH} levels deep")
                ops.append(("(", v) if v == "(" else ("u", v))
            elif kind == "op":
                raise _ExprError(f"unexpected {v!r}")
            else:
                vals.append(atom(kind, v))
                expect = False
                while ops and ops[-1][0] == "u":  # unary operators bind tightest
                    reduce()
                    depth -= 1
        elif kind == "op" and v == ")":
            while ops and ops[-1][0] != "(":
                reduce()
            if not ops:
                raise _ExprError("unbalanced ')'")
            ops.pop()
            depth -= 1
            while ops and ops[-1][0] == "u":
                reduce()
                depth -= 1
        elif kind == "op" and v in _GAS_PREC:
            while ops and ops[-1][0] == "b" and _GAS_PREC[ops[-1][1]] >= _GAS_PREC[v]:
                reduce()
            ops.append(("b", v))
            expect = True
        else:
            raise _ExprError(f"unexpected {v!r}")
    if expect:
        raise _ExprError("incomplete expression")
    while ops:
        if ops[-1][0] == "(":
            raise _ExprError("missing ')'")
        reduce()
    return vals[0]


def _eval_label(
    expr: str, *, x86: bool, labels: dict[str, int], here: int | None, forward: bool = False
) -> tuple[int, str | None, bool]:
    """A target-like expression: (value, unresolved label or None, refers to a label/`.`).
    With a forward label the value is its addend."""
    lin = _eval_lin(expr, x86=x86, labels=labels, here=here, forward=forward)
    sec = lin.terms.pop(("sec",), 0)
    fwd = [k for k in lin.terms if k[0] == "fwd"]
    if len(fwd) != len(lin.terms):
        raise _ExprError("registers are not allowed here")
    if fwd:
        if len(fwd) > 1:
            raise _ExprError(f"more than one undefined label ({fwd[0][1]}, {fwd[1][1]})")
        if lin.terms[fwd[0]] != 1 or sec:
            raise _ExprError(f"unsupported arithmetic on the undefined label {fwd[0][1]!r}")
        return lin.c, fwd[0][1], True
    if sec not in (0, 1):
        raise _ExprError("invalid arithmetic on labels (only label + constant, label - label)")
    return lin.c, None, sec == 1


def _eval_const(expr: str, x86: bool) -> int:
    lin = _eval_lin(expr, x86=x86)
    if lin.terms:
        raise _ExprError("not a constant")
    return lin.c


def _try_eval(expr: str, x86: bool) -> int | None:
    try:
        return _eval_const(expr, x86)
    except (_ExprError, ValueError):
        return None


def _fmt(v: int) -> str:
    return f"{v:#x}" if v >= 0 else f"-{-v:#x}"


_DECIMAL_RE = re.compile(r"(?<![\w.$])\d+(?![\w.$])")
_IDENT_RE = re.compile(r"(?<![\w.$@])[A-Za-z_.$][\w.$@]*")


def _hex_literals(text: str) -> str:
    """Rewrite decimal (and leading-zero octal) literals as hex, for the resolver handle."""

    def conv(m: re.Match) -> str:
        s = m[0]
        if len(s) > 1 and s[0] == "0":
            if not set(s) <= set("01234567"):
                raise AsmError(f"invalid octal literal {s!r} (a leading 0 means octal)")
            return f"{int(s, 8):#x}"
        return f"{int(s):#x}"

    return _DECIMAL_RE.sub(conv, text)


def _split_statement(text: str, x86: bool) -> tuple[str, list[str]]:
    """Mnemonic (with any x86 prefixes) and the top-level comma-separated operands."""
    head, rest = [], text.strip()
    while rest:
        parts = rest.split(None, 1)
        word, rest = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        head.append(word)
        if not (x86 and word.lower() in _X86_PREFIXES):
            break
    ops, depth, start = [], 0, 0
    for i, ch in enumerate(rest):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            ops.append(rest[start:i].strip())
            start = i + 1
    if rest[start:].strip() or ops:
        ops.append(rest[start:].strip())
    return " ".join(head), ops


def _reject_stray_colons(isa: Isa, text: str) -> None:
    """keystone never returns on some statements with a stray top-level `:` (`nop ::`,
    `a: :`). The only colons an instruction needs are segment overrides (x86) and
    relocation specifiers (AArch64)."""
    if ":" not in text:
        return
    if isa is X86_64:
        rest = re.sub(r"(?i)(?<![\w.$@])(?:cs|ds|es|fs|gs|ss)\s*:", "", text)
    else:
        rest = re.sub(r":[A-Za-z0-9_]+:", "", text)
    if ":" in rest:
        raise AsmError("unexpected ':' (only segment overrides / :lo12: may contain one)")


# -- x86-64 ---------------------------------------------------------------------------

_NEAR_BRANCH = (
    iced_x86.OpKind.NEAR_BRANCH16,
    iced_x86.OpKind.NEAR_BRANCH32,
    iced_x86.OpKind.NEAR_BRANCH64,
)
_X86_REGISTERS = {k.lower() for k in dir(iced_x86.Register) if k.isupper()} - {"none"}
# iced names the low bytes of r8-r15 "r8l".."r15l"; assemblers (and models) write "r8b".."r15b".
_X86_REGISTERS |= {f"r{i}b" for i in range(8, 16)}
_X86_KEYWORDS = {"ptr", "byte", "word", "dword", "qword", "tbyte", "tword", "xword", "oword",
                 "fword", "mmword", "xmmword", "ymmword", "zmmword", "offset", "short", "near",
                 "far", "rel", "abs", "st", ".", "$"}  # fmt: skip
# GNU as reads these as a symbol wherever one can go; every other keyword (the size names,
# `near`, `far`, `offset`, `short`, `st`) it misreads as the keyword or rejects.
_X86_REFUSED_KEYWORDS = _X86_KEYWORDS - {"ptr", "rel", "abs", "tword", "xword"}
_X86_PREFIXES = {"lock", "rep", "repe", "repz", "repne", "repnz", "bnd", "notrack", "xacquire",
                 "xrelease", "data16", "data32", "addr32"}  # fmt: skip
_X86_CONDS = ["a", "ae", "b", "be", "c", "e", "g", "ge", "l", "le", "na", "nae", "nb", "nbe",
              "nc", "ne", "ng", "nge", "nl", "nle", "no", "np", "ns", "nz", "o", "p", "pe",
              "po", "s", "z"]  # fmt: skip
_X86_BRANCHES = {"jmp", "call", "loop", "loope", "loopz", "loopne", "loopnz", "jrcxz", "jecxz",
                 "jcxz", "xbegin"} | {"j" + c for c in _X86_CONDS}  # fmt: skip
_X86_IMM_BITS = {
    iced_x86.OpKind.IMMEDIATE8: 8,
    iced_x86.OpKind.IMMEDIATE8_2ND: 8,
    iced_x86.OpKind.IMMEDIATE16: 16,
    iced_x86.OpKind.IMMEDIATE32: 32,
    iced_x86.OpKind.IMMEDIATE64: 64,
    iced_x86.OpKind.IMMEDIATE8TO16: 16,
    iced_x86.OpKind.IMMEDIATE8TO32: 32,
    iced_x86.OpKind.IMMEDIATE8TO64: 64,
    iced_x86.OpKind.IMMEDIATE32TO64: 64,
}
_X86_STRING_MEMORY = {v for k, v in vars(iced_x86.OpKind).items() if k.startswith("MEMORY_")}
_BRACKETS_RE = re.compile(r"\[[^\]]*\]")
_X86_FORMATTER = iced_x86.Formatter(iced_x86.FormatterSyntax.INTEL)
_X86_CMP_FORMATTER = iced_x86.Formatter(iced_x86.FormatterSyntax.INTEL)
_X86_CMP_FORMATTER.memory_size_options = iced_x86.MemorySizeOptions.ALWAYS
_X86_CMP_FORMATTER.show_branch_size = False
_X86_SIZE_RE = re.compile(
    r"(?i)(?:byte|word|dword|qword|tbyte|tword|xword|oword|fword|mmword|xmmword|ymmword|zmmword)"
    r"\s+ptr\b\s*"
)
_X86_SEG_RE = re.compile(r"(?i)(?:cs|ds|es|fs|gs|ss)\s*:\s*")
_X86_RAW_RE = re.compile(r"(?i)st(?:\s*\(\s*[0-7]\s*\))?|.*[{}].*")


def _x86_reject_rip_register(text: str) -> None:
    """keystone encodes `rip` outside a memory operand as rax (`jmp rip`, `push rip`)."""
    outside = _BRACKETS_RE.sub("[]", text)
    m = re.search(r"(?<![\w.$@])(rip|eip)(?![\w.$@])", outside, re.I)
    if m:
        raise AsmError(f"{m[1]} can only be used as a base register ([{m[1]} + disp])")


# MASM-style operators and character literals: keystone accepts some of them, but they
# defeat the independent operand evaluation, so they are refused rather than trusted.
# (A label may still be called `low` or `ne`: only operator positions are refused.)
_X86_BINARY_WORDS = {"shl", "shr", "and", "or", "xor", "mod", "eq", "ne", "lt", "le", "gt", "ge"}
_X86_UNARY_WORDS = {"not", "high", "low", "highword", "lowword"}


def _x86_reject_unverifiable(text: str) -> None:
    for op in _split_statement(text, True)[1]:
        if "'" in op or '"' in op:
            raise AsmError("character literals are not supported; write the number")
        for m in _IDENT_RE.finditer(op):
            word = m[0].lower()
            if word not in _X86_BINARY_WORDS and word not in _X86_UNARY_WORDS:
                continue
            before = op[: m.start()].rstrip()[-1:]  # previous non-blank character
            after = op[m.end() :].lstrip()[:1]
            operand_follows = bool(re.match(r"[\w.$(]", after))
            binary = word in _X86_BINARY_WORDS and before and before not in "[+-*(,"
            if operand_follows and (binary or word in _X86_UNARY_WORDS):
                raise AsmError(f"operator {m[0]!r} is not supported; use <<, >>, &, |, ^, ~, %")


def _branch_operand(op: str) -> tuple[str, str]:
    m = re.match(r"(?i)(short|near(?:\s+ptr)?)\s+", op.strip())
    if not m:
        return "", op.strip()
    return m[1].lower().split()[0] + " ", op.strip()[m.end() :]


def _x86_direct_branch(head: str, ops: list[str]) -> tuple[str, str] | None:
    """(keyword, target expression) when the statement is a direct branch/call."""
    if head.split()[-1].lower() not in _X86_BRANCHES or len(ops) != 1:
        return None
    keyword, target = _branch_operand(ops[0])
    bare = _IDENT_RE.fullmatch(target) and not _refused_label(X86_64, target)  # `jmp ptr`: a label
    if "[" in target or (not bare and re.search(r"(?i)\bptr\b", target)):
        return None  # indirect through memory
    if target.lower() in _X86_REGISTERS:
        return None
    return keyword, target


@dataclass
class _X86Op:
    kind: str  # "imm" | "mem" | "raw"
    text: str = ""  # raw / imm canonical text
    value: int = 0  # imm value
    size: str = ""  # "qword ptr " etc.
    seg: str = ""  # "fs:" etc.
    base: str | None = None
    index: str | None = None
    scale: int = 1
    disp: int = 0  # mem: displacement (riprel with target: unused)
    riprel: bool = False
    target: int | None = None  # riprel: absolute target (label / `.` forms)
    addr32: bool = False
    fwd: str | None = None
    addend: int = 0  # program mode: the constant added to the forward label `fwd`

    def render(self, disp: int | None = None) -> str:
        if self.kind != "mem":
            return self.text
        disp = self.disp if disp is None else disp
        parts = [self.base] if self.base else []
        if self.index:
            parts.append(f"{self.index}*{self.scale}")
        inner = " + ".join(parts)
        if disp or not parts:
            inner = (f"{inner} {'-' if disp < 0 else '+'} {abs(disp):#x}" if parts
                     else _fmt(disp))  # fmt: skip
        return f"{self.size}{self.seg}[{inner}]"


def _x86_operand(op: str, labels: dict[str, int], addr: int, program: bool = False) -> _X86Op:
    """Parse and evaluate one non-branch operand with GNU as semantics. In program mode an
    unknown label may also be used as an absolute address (`offset lbl`, `[lbl]`,
    `[lbl + rax*8]`): the operand then holds a 32-bit placeholder until it is resolved or
    becomes a relocation."""
    s = op.strip()
    if s.lower() in _X86_REGISTERS or _X86_RAW_RE.fullmatch(s):
        for m in _IDENT_RE.finditer(s):
            if m[0] in labels or m[0] in (".", "$"):
                raise AsmError(f"label {m[0]!r} in an unsupported operand form {s!r}")
        return _X86Op("raw", text=s)
    size = ""
    if m := _X86_SIZE_RE.match(s):
        size, s = m[0].rstrip() + " ", s[m.end() :]
    seg = ""
    if m := _X86_SEG_RE.match(s):
        seg, s = m[0].replace(" ", ""), s[m.end() :]
    offset = False
    if m := re.match(r"(?i)offset\b\s*", s):
        offset, s = True, s[m.end() :]
    memory = "[" in s or "]" in s
    if memory:
        if offset:
            raise AsmError(f"'offset' cannot be combined with a memory operand ({op!r})")
        if s.count("[") != s.count("]") or re.search(r"\[[^\]]*\[", s):
            raise AsmError(f"malformed memory operand {op!r}")
        s = s.replace("[", "+(").replace("]", ")")
    try:
        lin = _eval_lin(s, x86=True, labels=labels, here=addr, forward=True, registers=memory)
    except _ExprError as exc:
        raise AsmError(f"cannot evaluate operand {_clip(op, 60)!r}: {exc}") from None
    terms = dict(lin.terms)
    sec = terms.pop(("sec",), 0)
    fwd = [k[1] for k in terms if k[0] == "fwd"]
    regs = [(k[1], v) for k, v in terms.items() if k[0] == "reg"]
    if len(fwd) > 1 or (fwd and (terms[("fwd", fwd[0])] != 1 or sec)):
        raise AsmError(f"unsupported arithmetic on an undefined label in {op!r}")
    if sec not in (0, 1):
        raise AsmError(f"invalid arithmetic on labels in {op!r}")
    if not memory:
        if fwd and program:
            if seg:
                raise AsmError(f"a segment override cannot be combined with a label ({op!r})")
            if offset:
                return _X86Op("imm", text=f"{size}{_fmt(lin.c + _X86_ABS_PLACEHOLDER)}",
                              value=lin.c, fwd=fwd[0], addend=lin.c)  # fmt: skip
            return _X86Op("mem", size=size, disp=lin.c + _X86_ABS_PLACEHOLDER, fwd=fwd[0],
                          addend=lin.c)  # fmt: skip
        if fwd:
            raise AsmError(f"forward reference to {fwd[0]!r} in a non-PC-relative operand")
        if (sec and not offset) or seg:  # GNU as: a label (or seg:addr) is a memory reference
            if offset:
                raise AsmError(f"'offset' cannot be combined with a segment override ({op!r})")
            return _X86Op("mem", size=size, seg=seg, disp=lin.c)
        return _X86Op("imm", text=f"{size}{_fmt(lin.c)}", value=lin.c)
    rip = [r for r, _ in regs if r in ("rip", "eip")]
    if rip:
        if len(regs) != 1 or regs[0][1] != 1:
            raise AsmError(f"{rip[0]} cannot be scaled or combined with other registers")
        target = lin.c if (sec or fwd) else None
        return _X86Op("mem", size=size, seg=seg, base=rip[0], disp=lin.c, riprel=True,
                      target=target, addr32=rip[0] == "eip",
                      fwd=fwd[0] if fwd else None, addend=lin.c)  # fmt: skip
    if fwd and not program:
        raise AsmError(f"forward reference to {fwd[0]!r} in a non-PC-relative operand")
    base = index = None
    scale = 1
    for reg, coef in regs:
        # `[r + r*k]` / `[r + r]`: the linear form merged base and index of the same register
        # into one coefficient; GNU as encodes base=r, index=r*k. Recover that from the text.
        mentions = len(re.findall(rf"(?<![\w.$]){re.escape(reg)}(?![\w.$])", s, flags=re.I))
        if mentions == 2 and coef - 1 in (1, 2, 4, 8) and base is None and index is None:
            base, index, scale = reg, reg, coef - 1
        elif coef == 1 and base is None:
            base = reg
        elif index is None and coef in (1, 2, 4, 8):
            index, scale = reg, coef
        else:
            raise AsmError(f"invalid address expression {op!r}")
    if index and scale == 1 and index in ("rsp", "esp"):
        base, index = index, base
    addr32 = any(re.fullmatch(r"e..|r\d+d", r) for r, _ in regs)
    if fwd:  # program mode: absolute label address plus registers
        return _X86Op("mem", size=size, seg=seg, base=base, index=index, scale=scale,
                      disp=lin.c + _X86_ABS_PLACEHOLDER, addr32=addr32, fwd=fwd[0],
                      addend=lin.c)  # fmt: skip
    return _X86Op("mem", size=size, seg=seg, base=base, index=index, scale=scale, disp=lin.c,
                  addr32=addr32)  # fmt: skip


def _x86_decode(data: bytes, addr: int) -> iced_x86.Instruction:
    ins = iced_x86.Decoder(64, data, ip=addr).decode()
    if ins.code == iced_x86.Code.INVALID or ins.len != len(data):
        raise AsmError(f"encoding {data.hex()} does not decode as one instruction")
    _x86_check_supported(ins)
    return ins


# Instruction-set extensions the emulator (Unicorn) cannot run. They assemble and pass the GNU
# as cross-check, but Unicorn raises UC_ERR_INSN_INVALID for them (ADX, AVX2/ymm, FMA, F16C,
# SHA, AVX-512, TBM, XOP, 3DNow!), or, worse, silently miscomputes them (VEX-encoded 128-bit AVX
# such as `vpaddd xmm0, xmm1, xmm2`). A correct program using them would be scored as a crash or a
# wrong result, so they are refused up front. VEX-encoded BMI1/BMI2 (andn, bextr, blsi, blsr,
# blsmsk, bzhi, mulx, pdep, pext, rorx, sarx, shlx, shrx) execute correctly and stay allowed.
# The supported level is stated to the model in bca.liveeval.prompts.ISA_LEVEL.
_CF = iced_x86.CpuidFeature
_EK = iced_x86.EncodingKind
_X86_VEX_OK = {_CF.BMI1, _CF.BMI2}
_X86_NO_FEATURE = {
    _CF.ADX: "ADX instructions are not supported by the emulator; use adc",
    _CF.SHA: "SHA instructions are not supported by the emulator",
    _CF.SHA512: "SHA instructions are not supported by the emulator",
    _CF.SM3: "SM3 instructions are not supported by the emulator",
    _CF.SM4: "SM4 instructions are not supported by the emulator",
    _CF.TBM: "TBM instructions are not supported by the emulator",
}
_X86_NO_ENCODING = {
    _EK.EVEX: "AVX-512 (EVEX) instructions are not supported by the emulator",
    _EK.MVEX: "MVEX instructions are not supported by the emulator",
    _EK.XOP: "XOP instructions are not supported by the emulator",
    _EK.D3NOW: "3DNow! instructions are not supported by the emulator",
}


def _x86_check_supported(ins: iced_x86.Instruction) -> None:
    what = _X86_FORMATTER.format_mnemonic(ins)
    for feature in ins.cpuid_features():
        if feature in _X86_NO_FEATURE:
            raise AsmError(f"{what}: {_X86_NO_FEATURE[feature]}")
    if ins.encoding in _X86_NO_ENCODING:
        raise AsmError(f"{what}: {_X86_NO_ENCODING[ins.encoding]}; use SSE (up to SSE4.2)")
    if ins.encoding == _EK.VEX and not set(ins.cpuid_features()) <= _X86_VEX_OK:
        raise AsmError(f"{what}: AVX/AVX2/FMA (VEX-encoded vector) instructions are not supported by the "
                       "emulator (it rejects or miscomputes them); use the SSE form (up to SSE4.2)")


_C = iced_x86.Code
_X86_MOFFS = {_C.MOV_AL_MOFFS8: (_C.MOV_R8_RM8, True), _C.MOV_AX_MOFFS16: (_C.MOV_R16_RM16, True),
              _C.MOV_EAX_MOFFS32: (_C.MOV_R32_RM32, True), _C.MOV_RAX_MOFFS64: (_C.MOV_R64_RM64, True),
              _C.MOV_MOFFS8_AL: (_C.MOV_RM8_R8, False), _C.MOV_MOFFS16_AX: (_C.MOV_RM16_R16, False),
              _C.MOV_MOFFS32_EAX: (_C.MOV_RM32_R32, False),
              _C.MOV_MOFFS64_RAX: (_C.MOV_RM64_R64, False)}  # fmt: skip


def _x86_force_absolute(data: bytes, addr: int) -> bytes:
    """keystone may reach an absolute address rip-relatively; GNU as (and a relocation)
    needs the absolute disp32 form. Program mode re-encodes it that way."""
    ins = iced_x86.Decoder(64, data, ip=addr).decode()
    if ins.code in _X86_MOFFS:  # mov al/ax/eax/rax <-> [moffs64]: use the ModRM form
        code, load = _X86_MOFFS[ins.code]
        reg = ins.op0_register if load else ins.op1_register
        mem = iced_x86.MemoryOperand(displ=ins.memory_displacement, displ_size=8)
        ins = (iced_x86.Instruction.create_reg_mem(code, reg, mem) if load
               else iced_x86.Instruction.create_mem_reg(code, mem, reg))  # fmt: skip
        target = ins.memory_displacement
    elif ins.code == iced_x86.Code.INVALID or not ins.is_ip_rel_memory_operand:
        return data
    else:
        target = ins.ip_rel_memory_address
        ins.memory_base = iced_x86.Register.NONE
        ins.memory_displacement = target
        ins.memory_displ_size = 8  # 64-bit addressing, sign-extended disp32
    enc = iced_x86.Encoder(64)
    try:
        enc.encode(ins, addr)
    except ValueError as exc:
        raise AsmError(f"cannot encode absolute address {target:#x}: {exc}") from None
    return bytes(enc.take_buffer())


def _x86_check_length(data: bytes, addr: int) -> None:
    if len(data) > _X86_MAX_INSN:
        raise AsmError(f"encodes to {len(data)} bytes; one x86 instruction is at most 15")
    _x86_decode(data, addr)


def _x86_check_branch(data: bytes, addr: int, target: int, head: str) -> None:
    _x86_check_length(data, addr)
    ins = _x86_decode(data, addr)
    if ins.op_count == 0 or ins.op0_kind not in _NEAR_BRANCH:
        raise AsmError(f"expected a direct branch, got {_X86_FORMATTER.format(ins)}")
    if target & MASK64 != ins.near_branch64:
        raise AsmError(
            f"branch target {target & MASK64:#x} out of range for {head} "
            f"(the encoding would jump to {ins.near_branch64:#x})"
        )


def _x86_verify(data: bytes, addr: int, ops: list[_X86Op]) -> None:
    """Semantic checks of final x86 bytes against the evaluated operands."""
    _x86_check_length(data, addr)
    ins = _x86_decode(data, addr)
    if ins.op_count and ins.op0_kind in _NEAR_BRANCH:
        raise AsmError(f"encoded as a branch ({_X86_FORMATTER.format(ins)})")
    mems = [o for o in ops if o.kind == "mem"]
    kinds = [ins.op_kind(i) for i in range(ins.op_count)]
    if any(k in _X86_STRING_MEMORY for k in kinds):
        # movs/cmps/lods/stos/scas/ins/outs: the memory operands are implicit rsi/rdi
        # (GNU as checks the registers); nothing to encode but the operand size.
        if any(o.disp or o.index or o.riprel or not o.base for o in mems):
            raise AsmError("string instruction operands must be plain [rsi]/[rdi]")
        mems = []
    if len(mems) > 1:
        raise AsmError("more than one memory operand")
    if mems:
        o = mems[0]
        if iced_x86.OpKind.MEMORY not in [ins.op_kind(i) for i in range(ins.op_count)]:
            shown = _X86_FORMATTER.format(ins)
            raise AsmError(f"memory operand missing from the encoding ({shown})")
        if o.riprel:
            want = (o.target if o.target is not None else addr + ins.len + o.disp) & MASK64
            got = ins.ip_rel_memory_address if ins.is_ip_rel_memory_operand else None
            if o.addr32 and got is not None:
                want &= 0xFFFF_FFFF
            if got != want:
                raise AsmError(
                    f"rip-relative address {want:#x} does not fit the encoding "
                    f"(it would address {got if got is None else hex(got)})"
                )
        elif ins.is_ip_rel_memory_operand:
            # keystone may reach an absolute address (no base/index) rip-relatively; that
            # is the same access at this fixed address.
            if o.base or o.index or ins.ip_rel_memory_address != o.disp & MASK64:
                raise AsmError(
                    f"address {_fmt(o.disp)} encoded as rip-relative "
                    f"{ins.ip_rel_memory_address:#x}"
                )
        else:
            mask = 0xFFFF_FFFF if o.addr32 else MASK64
            if o.disp & mask != ins.memory_displacement & mask:
                raise AsmError(
                    f"displacement {_fmt(o.disp)} does not fit the encoding (it would address "
                    f"{ins.memory_displacement & mask:#x})"
                )
    consts = [o for o in ops if o.kind == "imm"]
    imms = [i for i in range(ins.op_count) if ins.op_kind(i) in _X86_IMM_BITS]
    if len(consts) != len(imms):
        return
    for o, i in zip(consts, imms, strict=True):
        bits = _X86_IMM_BITS[ins.op_kind(i)]
        got = ins.immediate(i) & ((1 << bits) - 1)
        value = o.value
        if not -(1 << (bits - 1)) <= value < 1 << bits or (value - got) % (1 << bits):
            raise AsmError(
                f"immediate {_fmt(value)} does not fit the encoded {bits}-bit field "
                f"(it would be {got:#x})"
            )


# -- AArch64 --------------------------------------------------------------------------

_A64_CONDS = "eq ne cs hs cc lo mi pl vs vc hi ls ge lt gt le al nv".split()
_A64_KEYWORDS = set(_A64_CONDS) | {
    "lsl", "lsr", "asr", "ror", "msl", "uxtb", "uxth", "uxtw", "uxtx", "sxtb", "sxth",
    "sxtw", "sxtx", "sp", "wsp", "xzr", "wzr", "lr", "fp", "ip0", "ip1", "nzcv",
}  # fmt: skip
_A64_REG_RE = re.compile(r"[xwbhsdqvzp]\d{1,2}", re.I)
_A64_BCOND = {"b" + c for c in _A64_CONDS}
_A64_TARGET_SYM = "__bca_t"
_LO12_RE = re.compile(r"#?\s*:lo12:\s*([^,\]]+)", re.I)
_A64_IMM_RE = re.compile(r"#([^,\]!]+)")
_PLAIN_LITERAL_RE = re.compile(r"-?\s*(?:0[xX][0-9a-fA-F]+|[1-9][0-9]*|0)")
_FLOAT_RE = re.compile(r"[-+]?\s*(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?\s*\d+[eE][-+]?\d+")


def _refused_label(isa: Isa, name: str) -> bool:
    """A label name GNU as does not read as a label everywhere a label can go. AArch64: none
    (GNU as reads `lt`, `lsl`, `sp`, even `x0` as a symbol wherever a symbol is expected, and
    as the keyword or register elsewhere). x86-64: register names and the keywords GNU as
    misreads or rejects as a symbol (_X86_REFUSED_KEYWORDS); `ptr`, `rel`, `abs`, `tword`
    and `xword` are labels to GNU as."""
    if isa is not X86_64:
        return name in (".", "$")
    low = name.lower()
    return low in _X86_REGISTERS or low in _X86_REFUSED_KEYWORDS


def _reserved_name(isa: Isa, name: str) -> bool:
    low = name.lower()
    if isa is X86_64:
        return low in _X86_REGISTERS or low in _X86_KEYWORDS
    return bool(_A64_REG_RE.fullmatch(low)) or low in _A64_KEYWORDS


@dataclass
class _A64Statement:
    mnemonic: str
    ops: list[str]
    target_index: int | None  # operand holding the PC-relative target
    target: int | None  # its value, once known
    forward: str | None  # unresolved label (target or :lo12:)
    ks_text: str  # what keystone assembles
    has_lo12: bool = False


def _a64_target_index(mnemonic: str, ops: list[str]) -> int | None:
    m = mnemonic.lower()
    if m in ("b", "bl") or m.startswith("b.") or m in _A64_BCOND:
        idx = 0
    elif m in ("cbz", "cbnz", "adr", "adrp"):
        idx = 1
    elif m in ("tbz", "tbnz"):
        idx = 2
    elif m in ("ldr", "ldrsw", "prfm") and len(ops) == 2 and not ops[1].startswith("["):
        idx = 1
    else:
        return None
    return idx if len(ops) == idx + 1 else None


def _a64_immediates(op: str) -> str:
    """Replace each `#expr` by its GNU as value, so keystone's expression rules never
    matter (plain decimal/hex literals and floating-point immediates stay as written)."""

    def sub(m: re.Match) -> str:
        expr = m[1].strip()
        if _FLOAT_RE.fullmatch(expr):
            return m[0]
        try:
            value = _eval_const(expr, False)  # validates plain literals too
        except _ExprError as exc:
            raise AsmError(f"bad immediate {_clip(expr, 60)!r}: {exc}") from None
        if _PLAIN_LITERAL_RE.fullmatch(expr):
            return m[0]
        return "#" + _fmt(value) + m[1][len(m[1].rstrip()) :]

    return _A64_IMM_RE.sub(sub, op)


def _a64_prepare(text: str, addr: int, labels: dict[str, int]) -> _A64Statement:
    """Parse an AArch64 statement: evaluate its PC-relative target and `:lo12:` values and
    constant immediates (GNU as semantics), and build the keystone text."""
    mnemonic, ops = _split_statement(text, False)
    if any(op.startswith("=") for op in ops):
        raise AsmError(
            "literal pools (`ldr Xn, =value`) are not supported: build the constant "
            "with mov/movk"
        )
    for spec in re.findall(r":([A-Za-z0-9_]+):", text):
        if spec.lower() != "lo12":
            raise AsmError(f"relocation specifier ':{spec}:' is not supported (only :lo12:label)")
    forward: list[str] = []

    def lo12(m: re.Match) -> str:
        try:
            value, sym, _ = _eval_label(m[1], x86=False, labels=labels, here=addr, forward=True)
        except _ExprError as exc:
            raise AsmError(f"bad :lo12: expression {_clip(m[1].strip(), 60)!r}: {exc}") from None
        if sym:
            forward.append(sym)
            return "#0"
        return f"#{value & 0xFFF:#x}"

    has_lo12 = bool(_LO12_RE.search(text))
    ops = [_LO12_RE.sub(lo12, op) for op in ops]
    if len(set(forward)) > 1:
        raise AsmError(f"more than one forward label: {', '.join(dict.fromkeys(forward))}")
    idx = _a64_target_index(mnemonic, ops)
    for i, op in enumerate(ops):
        if i == idx:
            continue
        for m in _IDENT_RE.finditer(op):
            if (m[0] in labels and not _reserved_name(AARCH64, m[0])) or m[0] == ".":
                raise AsmError(
                    f"{m[0]!r} can only be used as a PC-relative target or in :lo12: "
                    "(GNU as would need a relocation)"
                )
    ops = [op if i == idx else _a64_immediates(op) for i, op in enumerate(ops)]

    def join(operands: list[str]) -> str:
        return f"{mnemonic} {', '.join(operands)}" if operands else mnemonic

    if idx is None:
        return _A64Statement(mnemonic, ops, None, None, forward[0] if forward else None,
                             join(ops), has_lo12)  # fmt: skip
    if forward:
        raise AsmError(":lo12: cannot be combined with a PC-relative target")
    operand = ops[idx].strip()
    try:
        value, sym, reloc = _eval_label(operand, x86=False, labels=labels, here=addr, forward=True)
    except _ExprError as exc:
        raise AsmError(f"bad target expression {_clip(operand, 60)!r}: {exc}") from None
    if not sym and not reloc:
        raise AsmError(
            f"numeric PC-relative target {operand!r} is ambiguous (GNU as and llvm-mc read it "
            "as an offset, keystone as an address); write a label or `.+offset`"
        )
    ks_ops = ops[:idx] + [_A64_TARGET_SYM] + ops[idx + 1 :]
    return _A64Statement(mnemonic, ops, idx, None if sym else value, sym, join(ks_ops), has_lo12)


def _a64_check_single(data: bytes) -> None:
    if len(data) != 4:
        raise AsmError(
            f"encodes to {len(data)} bytes; an AArch64 statement must be exactly one "
            "instruction (no literal pools or multi-instruction expansions)"
        )


def _a64_check_mov(st: _A64Statement, data: bytes) -> None:
    """keystone truncates `mov Rd, #imm` (`mov w0, #0x100000000` -> 0): decode the value."""
    if st.mnemonic.lower() != "mov" or len(st.ops) != 2:
        return
    want = _try_eval(st.ops[1], False)
    if want is None:
        return
    reg = st.ops[0].strip().lower()
    width = 32 if reg.startswith("w") else 64
    word = int.from_bytes(data, "little")
    got = _a64_mov_value(word)
    if got is None:
        raise AsmError(f"mov immediate encoded as an unexpected instruction ({data.hex()})")
    if not -(1 << (width - 1)) <= want < 1 << width or (want - got) % (1 << width):
        raise AsmError(f"immediate {st.ops[1]} does not fit {reg} (it would be {got:#x})")


def _a64_mov_value(word: int) -> int | None:
    """Register value written by a movz/movn/orr-immediate (the `mov #imm` aliases)."""
    width = 64 if word >> 31 else 32
    mask = (1 << width) - 1
    op = word >> 23 & 0xFF
    if op in (0xA5, 0x25):  # movz, movn
        value = (word >> 5 & 0xFFFF) << 16 * (word >> 21 & 3)
        return value & mask if op == 0xA5 else ~value & mask
    if op == 0x64 and word >> 5 & 31 == 31:  # orr Rd, zr, #bitmask
        n, immr, imms = word >> 22 & 1, word >> 16 & 63, word >> 10 & 63
        length = (n << 6 | (~imms & 63)).bit_length() - 1
        if length < 1:
            return None
        esize = 1 << length
        s, r = imms & (esize - 1), immr & (esize - 1)
        welem = (1 << (s + 1)) - 1
        elem = (welem >> r | welem << (esize - r)) & ((1 << esize) - 1)
        return sum(elem << i for i in range(0, width, esize))
    return None


# (mask, value, field shift, field bits, byte scale); adr/adrp split the field as
# immlo (bits 29-30) and immhi (bits 5-23).
_A64_PCREL = {
    "b/bl": (0x7C000000, 0x14000000, 0, 26, 4),
    "b.cond": (0xFF000010, 0x54000000, 5, 19, 4),
    "cbz/cbnz": (0x7E000000, 0x34000000, 5, 19, 4),
    "tbz/tbnz": (0x7E000000, 0x36000000, 5, 14, 4),
    "ldr literal": (0x3B000000, 0x18000000, 5, 19, 4),
    "adr": (0x9F000000, 0x10000000, 5, 21, 1),
    "adrp": (0x9F000000, 0x90000000, 5, 21, 4096),
}


def _a64_pcrel_kind(word: int) -> str | None:
    for kind, (mask, value, *_rest) in _A64_PCREL.items():
        if word & mask == value:
            return kind
    return None


def _a64_set_offset(word: int, addr: int, target: int) -> int:
    """Point the PC-relative field of `word` (at `addr`) at `target`, with range checks.
    `target` is a 64-bit value (GNU as wraps address arithmetic)."""
    kind = _a64_pcrel_kind(word)
    assert kind is not None
    _, _, shift, bits, scale = _A64_PCREL[kind]
    target &= MASK64
    if kind == "adrp":
        units = _s64((target & ~0xFFF) - (addr & ~0xFFF)) >> 12  # 64-bit page delta
    else:
        delta = _s64(target - addr)
        if delta % scale:
            raise AsmError(f"{kind} target {target:#x} is not {scale}-byte aligned")
        units = delta // scale
    limit = 1 << (bits - 1)
    if not -limit <= units < limit:
        raise AsmError(
            f"{kind} target {target:#x} out of range "
            f"(offset {_s64(target - addr):+#x}, limit ±{limit * scale:#x})"
        )
    fld = units & ((1 << bits) - 1)
    if kind in ("adr", "adrp"):
        return word & ~(0x7FFFF << 5) & ~(3 << 29) | (fld >> 2) << 5 | (fld & 3) << 29
    fmask = ((1 << bits) - 1) << shift
    return word & ~fmask | fld << shift


# -- mnemonic comparison --------------------------------------------------------------
_X86_CC = [{"e", "z"}, {"ne", "nz"}, {"b", "c", "nae"}, {"ae", "nb", "nc"}, {"be", "na"},
           {"a", "nbe"}, {"l", "nge"}, {"ge", "nl"}, {"le", "ng"}, {"g", "nle"},
           {"p", "pe"}, {"np", "po"}]  # fmt: skip
_A64_CC = [{"hs", "cs"}, {"lo", "cc"}]
_ALIASES = [
    {"mov", "movabs"}, {"sal", "shl"}, {"movsx", "movsxd"}, {"ret", "retq"},
    # AArch64 preferred-alias printing by capstone.
    {"mov", "movz", "orr", "movn"}, {"cmp", "subs"}, {"cmn", "adds"}, {"tst", "ands"},
    {"mul", "madd"}, {"mneg", "msub"}, {"neg", "sub"}, {"negs", "subs"}, {"mvn", "orn"},
    {"lsl", "lsr", "ubfm", "ubfx", "ubfiz"}, {"asr", "sbfm", "sbfx", "sxtw", "sxth", "sxtb"},
    {"uxtw", "uxth", "uxtb", "ubfm"}, {"cset", "csinc", "cinc"}, {"csetm", "csinv", "cinv"},
    {"cneg", "csneg"}, {"ror", "extr"}, {"smull", "smaddl"}, {"umull", "umaddl"},
    # Unscaled-offset forms and inverted-immediate forms keystone may pick.
    {"ldr", "ldur"}, {"ldrb", "ldurb"}, {"ldrh", "ldurh"}, {"ldrsb", "ldursb"},
    {"ldrsh", "ldursh"}, {"ldrsw", "ldursw"}, {"str", "stur"}, {"strb", "sturb"},
    {"strh", "sturh"}, {"orn", "orr"}, {"bic", "and"}, {"bics", "ands"}, {"cmp", "cmn"},
    {"negs", "cmp"},
]  # fmt: skip


def _canon_cc(isa: Isa, mnemonic: str) -> str:
    if isa is X86_64:
        for prefix in ("cmov", "set", "j"):
            if mnemonic.startswith(prefix):
                for group in _X86_CC:
                    if mnemonic[len(prefix) :] in group:
                        return prefix + min(group)
    elif mnemonic.startswith("b.") or mnemonic in _A64_BCOND:
        mnemonic = "b." + mnemonic.removeprefix("b.").removeprefix("b")
        for group in _A64_CC:
            if mnemonic[2:] in group:
                return "b." + min(group)
    return mnemonic


def _same_mnemonic(isa: Isa, text: str, decoded: str) -> bool:
    src = _canon_cc(isa, text.split(None, 1)[0].lower())
    got = _canon_cc(isa, decoded.split(None, 1)[0].lower())
    return src == got or any(src in g and got in g for g in _ALIASES)


# -- program mode ---------------------------------------------------------------------

_PROGRAM_SECTIONS = (".text", ".rodata", ".data", ".bss")
_NOTE_SECTION = ".note.GNU-stack"
# x86 absolute label operands are encoded against this address until resolved or relocated:
# it forces the 32-bit immediate / displacement forms GNU as uses for a symbol.
_X86_ABS_PLACEHOLDER = 0x1000_0000
_GAS_SECTION_HEADER = {".text": ".text", ".rodata": '.section .rodata,"a",@progbits',
                       ".data": ".data", ".bss": ".bss"}  # fmt: skip
# (flags, type) each section may be declared with
_SECTION_FLAGS = {".text": ("ax", "progbits"), ".rodata": ("a", "progbits"),
                  ".data": ("aw", "progbits"), ".bss": ("aw", "nobits"),
                  _NOTE_SECTION: ("", "progbits")}  # fmt: skip
_NUMERIC_LABEL_RE = re.compile(r"(\d+)\s*:(?!:)")
_NUMERIC_REF_RE = re.compile(r"(?<![\w.$@])(\d+)([fbFB])(?![\w.$@])")
_NUMERIC_NAME_RE = re.compile(r"__bca_L(\d+)_(\d+)")


def _numeric_name(n: int, k: int) -> str:
    """Internal name of the k-th definition of GNU as local label `n:`."""
    return f"__bca_L{n}_{k}"


def _pretty_names(message: str) -> str:
    return _NUMERIC_NAME_RE.sub(lambda m: f"local label {m[1]}", message)


_PROGRAM_CHAR_RE = re.compile(r"'(\\[^\n]|[^\\'\n])'")
_STRING_TOKEN_RE = re.compile(r'"\d+"')
_ASSIGN_RE = re.compile(r"(?i)(?:\.(?:equ|set|equiv) ([A-Za-z_.$][\w.$]*) ?,|([A-Za-z_.$][\w.$]*) ?=(?!=))(.*)")


def _parse_assignment(text: str) -> tuple[str, str] | None:
    """(name, expression) for `.equ name, expr`, `.set name, expr`, `.equiv` or `name = expr`."""
    m = _ASSIGN_RE.fullmatch(text)
    if m is None:
        if re.match(r"[A-Za-z_.$][\w.$]* ?==", text):
            raise AsmError("`==` is not supported: use `name = value` or .equ")
        return None
    if not m[3].strip():
        raise AsmError("missing value in constant definition")
    return m[1] or m[2], m[3].strip()
_SYMBOL_RE = re.compile(r"[A-Za-z_.$][\w.$]*")
_ESCAPES = {"n": 10, "t": 9, "r": 13, "b": 8, "f": 12, "\\": 92, '"': 34, "'": 39}
_ALIGN_DIRECTIVES = {".align", ".balign", ".p2align"}
_FILL_DIRECTIVES = {".zero", ".space", ".skip"}
_STRING_DIRECTIVES = {".ascii", ".asciz", ".string"}
_DATA_WIDTHS = {".byte": 1, ".short": 2, ".hword": 2, ".2byte": 2, ".long": 4, ".int": 4,
                ".4byte": 4, ".quad": 8, ".8byte": 8}  # fmt: skip
_DATA_RELOC = {"x86_64": {4: 10, 8: 1}, "aarch64": {4: 258, 8: 257}}  # R_*_32 / R_*_64 (ABS)
_A64_NOP = bytes.fromhex("1f2003d5")
_REFUSED_DIRECTIVES = {
    ".incbin": "`.incbin` (reading a file) is not allowed",
    ".include": "`.include` (reading a file) is not allowed",
    ".rodata": "GNU as has no `.rodata` directive: write `.section .rodata`",
    ".inst": "`.inst` (raw instruction words) is not allowed: write the instruction",
    ".fill": "`.fill` is not supported: use .zero/.space or write the values",
    ".org": "`.org` is not supported",
    ".att_syntax": "only Intel syntax (`.intel_syntax noprefix`) is supported",
    **dict.fromkeys((".macro", ".endm", ".exitm", ".purgem", ".altmacro", ".noaltmacro"),
                    "macros are not supported: write every statement out"),
    **dict.fromkeys((".rept", ".irp", ".irpc", ".endr"),
                    "repetition blocks (.rept/.irp/.irpc) are not supported: write every "
                    "statement out"),
    **dict.fromkeys((".else", ".elseif", ".endif"), "conditional assembly is not supported"),
    ".eqv": "`.eqv` (lazy symbol) is not supported: use .equ",
    **dict.fromkeys((".comm", ".lcomm"), "`.comm`/`.lcomm` are not supported: reserve space "
                    "with a label and .zero in .bss"),
    **dict.fromkeys((".pushsection", ".popsection", ".previous", ".subsection"),
                    "section stacks are not supported: switch with .text/.data/.bss/.section"),
}  # fmt: skip
for _d in (".if", ".ifdef", ".ifndef", ".ifc", ".ifnc", ".ifeq", ".ifne", ".ifb", ".ifnb"):
    _REFUSED_DIRECTIVES[_d] = "conditional assembly is not supported"


def _data_width(x86: bool, name: str) -> int | None:
    if name == ".word":
        return 2 if x86 else 4
    if not x86 and name in (".xword", ".dword"):
        return 8
    return _DATA_WIDTHS.get(name)


def _cache_key(isa: Isa, c: _Check) -> tuple[str, str]:
    if c.section in (None, ".text"):
        return isa.name, c.text
    return isa.name, f"{c.section}\n{c.text}"


def _separate(checks: list[_Check], window: list[int], i: int) -> bool:
    """Program mode: must check i start a new GNU as window? Sections differ, or a label
    one statement needs undefined (a relocation) would be defined for another."""
    c = checks[i]
    if c.section is None:
        return False
    if c.section != checks[window[-1]].section or c.expect is not None:
        return True
    if any(checks[j].expect is not None for j in window):
        return True
    defined = set().union(*(checks[j].labels for j in window))
    externs = set().union(*(checks[j].externs for j in window))
    return bool(c.externs & defined or externs & set(c.labels))


def _mask_strings(text: str) -> tuple[str, list[str]]:
    """Replace each "..." by a token `"N"`; returns the text and the raw string bodies."""
    out, strings = [], []
    k = start = 0
    while k < len(text):
        ch = text[k]
        if ch == "'":
            m = _PROGRAM_CHAR_RE.match(text, k)
            k = m.end() if m else k + 1
            continue
        if ch == '"':
            j = k + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= len(text):
                raise AsmError("unterminated string")
            out += [text[start:k], f'"{len(strings)}"']
            strings.append(text[k + 1 : j])
            k = start = j + 1
            continue
        k += 1
    out.append(text[start:])
    return "".join(out), strings


def _unmask(text: str, strings: list[str]) -> str:
    return _STRING_TOKEN_RE.sub(lambda m: f'"{strings[int(m[0][1:-1])]}"', text)


def _decode_string(body: str) -> bytes:
    """GNU as string escapes (the unambiguous subset)."""
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ord(ch))
            i += 1
            continue
        e = body[i + 1] if i + 1 < len(body) else ""
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
        elif e and e in "01234567":
            digits = re.match(r"[0-7]{1,3}", body[i + 1 :])[0]
            if int(digits, 8) > 255:
                raise AsmError(f"octal escape \\{digits} is larger than a byte")
            out.append(int(digits, 8))
            i += 1 + len(digits)
        elif e in ("x", "X"):
            m = re.match(r"[0-9a-fA-F]*", body[i + 2 :])
            if not 1 <= len(m[0]) <= 2:
                raise AsmError("\\x takes one or two hex digits (GNU as reads every hex digit "
                               "that follows)")  # fmt: skip
            out.append(int(m[0], 16))
            i += 2 + len(m[0])
        else:
            raise AsmError(f"unsupported escape \\{e} (supported: \\n \\t \\r \\b \\f \\\\ "
                           "\\\" \\' \\ooo \\xhh)")  # fmt: skip
    return bytes(out)


def _char_value(body: str) -> int:
    data = _decode_string(body)
    if len(data) != 1:
        raise AsmError(f"character literal '{body}' is not one character")
    return data[0]


def _split_args(args: str) -> list[str]:
    """Top-level comma-separated operands (strings are masked already)."""
    if not args.strip():
        return []
    return _split_statement("x " + args, False)[1]


def _parse_section(args: str, strings: list[str]) -> str:
    parts = [p.strip() for p in args.split(",")] if args else [""]
    name = parts[0]
    if name not in _SECTION_FLAGS:
        raise AsmError(f"section {name!r} is not supported (use .text, .rodata, .data or .bss)")
    flags, kind = _SECTION_FLAGS[name]
    if len(parts) > 3:
        raise AsmError(f"too many operands for .section {name}")
    if len(parts) > 1:
        token = _STRING_TOKEN_RE.fullmatch(parts[1])
        if not token or strings[int(parts[1][1:-1])] != flags:
            raise AsmError(f'section {name} must have its default flags "{flags}"')
    if len(parts) > 2 and parts[2] not in (f"@{kind}", f"%{kind}"):
        raise AsmError(f"section {name} must have its default type @{kind}")
    return name


def _a64_lo12_type(word: int) -> int | None:
    """R_AARCH64_*_ABS_LO12_NC type for the instruction that holds a :lo12: field."""
    if word >> 23 & 0x3F == 0x22:  # add/sub (immediate)
        return 277
    if word >> 24 & 0x3B == 0x39:  # load/store register (unsigned immediate)
        size, vector, opc = word >> 30, word >> 26 & 1, word >> 22 & 3
        if vector and size == 0 and opc >= 2:
            return 299  # 128-bit q register
        return {0: 278, 1: 284, 2: 285, 3: 286}[size]
    return None


def _is_padding(isa: Isa, data: bytes, addr: int) -> bool:
    """NOPs, optionally after one jump to the end of the padding (GNU as's form for long
    x86 padding)."""
    if isa is not X86_64:
        return len(data) % 4 == 0 and data == _A64_NOP * (len(data) // 4)
    insns = list(iced_x86.Decoder(64, data, ip=addr))
    if insns and insns[0].mnemonic == iced_x86.Mnemonic.JMP and insns[0].op0_kind in _NEAR_BRANCH:
        if insns[0].near_branch64 != addr + len(data):
            return False
        insns = insns[1:]
    return all(ins.mnemonic == iced_x86.Mnemonic.NOP for ins in insns)


def _fmt_relocs(isa: Isa, relocs) -> str:
    from bca.ir.elf import reloc_name

    shown = [f"{reloc_name(isa.name, t)} {s}{a:+#x} at +{o}" for o, t, s, a in relocs]
    return "[" + ", ".join(shown) + "]" if shown else "none"


def _write_elf(obj: StreamObject) -> bytes:
    """A relocatable ELF64 object (ET_REL) with GNU as's section set."""
    from bca.ir.elf import MACHINES

    names = [".text", ".rodata", ".data", ".bss"]
    index = {n: i + 1 for i, n in enumerate(names)}
    strtab = bytearray(b"\0")

    def string(table: bytearray, s: str) -> int:
        off = len(table)
        table += s.encode() + b"\0"
        return off

    glob = set(obj.globals)
    ordered = [n for n in obj.symbols if n not in glob] + [n for n in obj.symbols if n in glob]
    symtab = bytearray(24)  # the null symbol
    for n in names:  # section symbols (STB_LOCAL, STT_SECTION)
        symtab += struct.pack("<IBBHQQ", 0, 3, 0, index[n], 0, 0)
    sym_index = {}
    for n in ordered:
        section, offset = obj.symbols[n]
        sym_index[n] = len(symtab) // 24
        bind = 1 if n in glob else 0
        symtab += struct.pack("<IBBHQQ", string(strtab, n), bind << 4, 0, index[section], offset, 0)
    first_global = 1 + len(names) + sum(1 for n in ordered if n not in glob)
    rela = {n: bytearray() for n in names[:3]}
    for r in obj.relocations:
        rela[r.section] += struct.pack("<QQq", r.offset, sym_index[r.symbol] << 32 | r.type, r.addend)
    min_align = 4 if obj.isa is AARCH64 else 1
    # (name, type, flags, payload or size, link, info, align, entsize)
    secs = [
        (".text", 1, 6, obj.sections[".text"], 0, 0, max(obj.alignments[".text"], min_align), 0),
        (".rodata", 1, 2, obj.sections[".rodata"], 0, 0, obj.alignments[".rodata"], 0),
        (".data", 1, 3, obj.sections[".data"], 0, 0, obj.alignments[".data"], 0),
        (".bss", 8, 3, obj.bss_size, 0, 0, obj.alignments[".bss"], 0),
        *((f".rela{n}", 4, 0x40, bytes(rela[n]), 8, index[n], 8, 24) for n in names[:3]),
        (".symtab", 2, 0, bytes(symtab), 9, first_global, 8, 24),
        (".strtab", 3, 0, bytes(strtab), 0, 0, 1, 0),
    ]
    shstrtab = bytearray(b"\0")
    name_offs = [string(shstrtab, s[0]) for s in secs]
    shstr_name = string(shstrtab, ".shstrtab")
    secs.append((".shstrtab", 3, 0, bytes(shstrtab), 0, 0, 1, 0))
    name_offs.append(shstr_name)
    body = bytearray(64)
    headers = [bytes(64)]
    for (_, stype, flags, payload, link, info, align, entsize), noff in zip(secs, name_offs, strict=True):
        if isinstance(payload, int):  # NOBITS
            offset, size = len(body), payload
        else:
            body += bytes(-len(body) % max(align, 1))
            offset, size = len(body), len(payload)
            body += payload
        headers.append(struct.pack("<IIQQQQIIQQ", noff, stype, flags, 0, offset, size, link, info,
                                   align, entsize))  # fmt: skip
    body += bytes(-len(body) % 8)
    shoff = len(body)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    body[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 1, MACHINES[obj.isa.name], 1, 0, 0, shoff,
                            0, 64, 0, 0, 64, len(headers), len(headers) - 1)  # fmt: skip
    return bytes(body) + b"".join(headers)


# -- execution ------------------------------------------------------------------------


@dataclass
class TraceStep:
    pc: int
    text: str | None
    changed: dict[str, int] = field(default_factory=dict)


def trace_program(
    program: Program, args: Sequence[int] = (), *, data: bytes = b"", max_insns: int = 100_000
) -> tuple[int, list[TraceStep]]:
    """Run a finished program with bca.emu tracing; returns (ret, per-step deltas)."""
    if not program.ok:
        raise ValueError(f"program has errors: {program.errors[0].message}")
    if program.base != CODE_BASE:
        raise ValueError("programs must be assembled at CODE_BASE to run")
    res = run_function(program.isa, program.code, list(args), data=data, max_insns=max_insns,
                       trace=True)  # fmt: skip
    text = {insn.address: insn.text for insn in program.instructions}
    return res.ret, [TraceStep(s.pc, text.get(s.pc), s.changed) for s in res.steps]


class LiveRunner:
    """Executes the code-so-far of a StreamAssembler while the stream is still arriving.

    `advance()` single-steps from the current pc for as long as the next instruction is
    committed and final (no pending fixup), then waits for more text. The machine layout
    and calling convention match bca.emu.run_function, so the steps equal trace_program's.
    """

    def __init__(
        self,
        asm: StreamAssembler,
        args: Sequence[int] = (),
        *,
        data: bytes = b"",
        max_steps: int = 100_000,
    ):
        if asm.base != CODE_BASE:
            raise ValueError("LiveRunner requires base == CODE_BASE")
        if len(args) > len(asm.isa.args):
            raise ValueError(f"{asm.isa.name} supports {len(asm.isa.args)} register args")
        self.asm, self.args, self.data, self.max_steps = asm, list(args), data, max_steps
        self._reset()

    def _reset(self) -> None:
        isa = self.asm.isa
        uc = unicorn.Uc(isa.uc_arch, isa.uc_mode)
        uc.mem_map(CODE_BASE, CODE_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        uc.mem_map(DATA_BASE, DATA_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        uc.mem_map(STACK_BASE, STACK_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        uc.mem_map(RETURN_SENTINEL, 0x1000, unicorn.UC_PROT_EXEC)
        if self.data:
            uc.mem_write(DATA_BASE, bytes(self.data))
        for reg, value in zip(isa.args, self.args, strict=False):
            uc.reg_write(reg, value & MASK64)
        sp = STACK_BASE + STACK_SIZE - 0x100
        if isa is X86_64:
            sp -= 8
            uc.mem_write(sp, RETURN_SENTINEL.to_bytes(8, "little"))
        else:
            from unicorn.arm64_const import UC_ARM64_REG_LR

            uc.reg_write(UC_ARM64_REG_LR, RETURN_SENTINEL)
        uc.reg_write(isa.sp, sp)
        uc.reg_write(isa.pc, CODE_BASE)
        self.uc = uc
        self.steps: list[TraceStep] = []
        self.done = False
        self._written = b""
        self._generation = self.asm.generation

    @property
    def ret(self) -> int | None:
        return self.uc.reg_read(self.asm.isa.ret) & MASK64 if self.done else None

    def _regs(self) -> dict[str, int]:
        return {name: self.uc.reg_read(reg) for name, reg in self.asm.isa.trace_regs}

    def _sync_code(self) -> None:
        code = self.asm.code()
        if len(code) > CODE_SIZE:
            raise EmulationError(f"code ({len(code)} bytes) exceeds the {CODE_SIZE}-byte region")
        if code != self._written:
            self.uc.mem_write(CODE_BASE, code)
            # Drop translated blocks that may cover bytes written or patched just now.
            self.uc.ctl_remove_cache(CODE_BASE, CODE_BASE + max(len(code), 1))
            self._written = code

    def advance(self) -> list[TraceStep]:
        """Execute as far as the committed code allows; returns the new steps."""
        if self.asm.generation != self._generation:
            self._reset()  # committed code was retracted: replay from the start
            new_from = 0
        else:
            new_from = len(self.steps)
        self._sync_code()
        isa = self.asm.isa
        while not self.done:
            pc = self.uc.reg_read(isa.pc)
            if pc == RETURN_SENTINEL:
                self.done = True
                break
            insn = self.asm.instruction_at(pc)
            if insn is None or insn.pending:
                break  # wait for the stream to catch up
            if len(self.steps) >= self.max_steps:
                raise EmulationError(f"{isa.name}: exceeded {self.max_steps} live steps")
            before = self._regs()
            try:
                self.uc.emu_start(pc, RETURN_SENTINEL, count=1)
            except unicorn.UcError as exc:
                raise EmulationError(f"{isa.name}: {exc} at pc={pc:#x}") from exc
            after = self._regs()
            self.steps.append(
                TraceStep(pc, insn.text, {k: v for k, v in after.items() if before[k] != v})
            )
        return self.steps[new_from:]


def assemble_stream(isa: Isa, chunks: Iterable[str], **kwargs) -> Program:
    """Feed every chunk and finish; convenience for tests and batch use."""
    sa = StreamAssembler(isa, **kwargs)
    for chunk in chunks:
        if sa.halted:
            break
        sa.feed(chunk)
    return sa.finish()


__all__ = [
    "MAX_BSS",
    "MAX_BUFFER",
    "MAX_CODE",
    "MAX_DATA",
    "MAX_LINE",
    "TEXT_BASE",
    "AsmError",
    "DataItem",
    "Event",
    "Instruction",
    "LiveRunner",
    "Program",
    "Relocation",
    "StreamAssembler",
    "StreamObject",
    "StreamHalted",
    "TraceStep",
    "assemble_stream",
    "trace_program",
]
