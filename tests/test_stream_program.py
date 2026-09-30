"""Whole-program mode of the stream assembler (StreamAssembler(isa, program=True)), and the
guarantee that function mode (the default) is byte-for-byte unchanged by it."""

import json
import re
import subprocess
from pathlib import Path

import pytest

import bca.stream as stream_mod
from bca.isa import ISAS

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "stream_function_golden.json"


# -- function mode is unchanged ------------------------------------------------------------


def summarise(mod, isa: str, text: str, mode: str | None) -> dict:
    """Everything observable about a function-mode stream over `text`: every event, the final
    code, labels, errors and GNU as substitutions. mode None feeds the text in one piece,
    "lines" one physical line per feed."""
    sa = mod.StreamAssembler(ISAS[isa])
    chunks = [text] if mode is None else re.split(r"(?<=\n)", text)
    events = []
    for chunk in chunks:
        if sa.halted:
            break
        events += sa.feed(chunk)
    prog = sa.finish()

    def ev(e):
        return [e.kind, e.address, e.text, e.data.hex(), e.message, e.line, e.pending]

    return {
        "events": [ev(e) for e in events],
        "code": prog.code.hex(),
        "labels": prog.labels,
        "errors": [ev(e) for e in prog.errors],
        "warnings": [ev(e) for e in prog.warnings],
        "substitutions": [list(s) for s in sa.substitutions],
        "pending": sa.pending_text,
    }


def _golden_cases():
    if not GOLDEN.exists():
        return []
    return sorted(json.loads(GOLDEN.read_text()).items())


@pytest.mark.parametrize("path,case", _golden_cases(), ids=lambda v: v if isinstance(v, str) else "")
def test_function_mode_matches_recorded_golden(path, case):
    """Golden outputs were recorded with the implementation before program mode existed
    (git HEAD cacb394c) on a fixed sample of recorded stream answers."""
    text = (ROOT / path).read_text()
    got = summarise(stream_mod, case["isa"], text, None)
    assert json.loads(json.dumps(got)) == case["whole"]
    if "lines" in case:
        got = summarise(stream_mod, case["isa"], text, "lines")
        assert json.loads(json.dumps(got)) == case["lines"]


def test_function_mode_still_refuses_program_constructs():
    for isa in ("x86_64", "aarch64"):
        for text in [".data\n", ".byte 1\n", ".section .rodata\n", '.ascii "x"\n', "1: nop\n"]:
            sa = stream_mod.StreamAssembler(ISAS[isa])
            events = sa.feed(text)
            assert [e.kind for e in events] == ["error"], (isa, text)
        assert stream_mod.StreamAssembler(ISAS[isa]).base == stream_mod.CODE_BASE


# -- program mode ---------------------------------------------------------------------------

from bca.image import assemble_program, link_program  # noqa: E402
from bca.ir.elf import Elf  # noqa: E402
from bca.process import run_program  # noqa: E402
from bca.stream import MAX_BSS, TEXT_BASE, StreamAssembler, StreamObject  # noqa: E402

X86_PROGRAM = r"""
.intel_syntax noprefix
.globl _start
.section .rodata
msg:    .ascii "Hello; # world // not a comment\n"
msglen: .quad msglen - msg
table:  .quad print, done
hexdig: .ascii "0123456789abcdef"
.data
counter: .long 3
pointer: .quad msg + 1
.equ SYS_WRITE, 1
STDOUT = 1
.bss
.p2align 4
buf:    .zero 64
.text
_start:
    lea rbx, [rip + counter]
1:  call print
    dec dword ptr [rbx]
    jnz 1b
    mov rax, [rip + pointer]        # msg + 1
    movzx eax, byte ptr [rax]       # 'e'
    mov byte ptr [buf], al
    mov byte ptr [buf + 1], '\n'
    mov esi, offset buf
    mov edx, 2
    call out
    mov rcx, 1
    jmp qword ptr [table + rcx*8]
print:
    lea rsi, [rip + msg]
    mov rdx, [rip + msglen]
out:
    mov eax, SYS_WRITE
    mov edi, STDOUT
    syscall
    ret
.p2align 4
done:
    lea rdi, [rip + hexdig]
    movzx edi, byte ptr [rdi + 10]  # 'a' = 97
    mov eax, 60
    syscall
.section .note.GNU-stack,"",@progbits
"""

A64_PROGRAM = r"""
// the same program for AArch64
.globl _start
.section .rodata
msg:    .ascii "Hello; # world // not a comment\n"
msglen: .quad msglen - msg
table:  .quad print, done
hexdig: .ascii "0123456789abcdef"
.data
counter: .word 3
.balign 8
pointer: .xword msg + 1
.equ SYS_WRITE, 64
STDOUT = 1
.bss
.p2align 4
buf:    .zero 64
.text
_start:
    adrp x19, counter
    add x19, x19, :lo12:counter
1:  bl print
    ldr w0, [x19]
    subs w0, w0, #1
    str w0, [x19]
    b.ne 1b
    adrp x0, pointer
    ldr x0, [x0, :lo12:pointer]
    ldrb w0, [x0]
    adrp x1, buf
    add x1, x1, :lo12:buf
    strb w0, [x1]
    mov w0, '\n'
    strb w0, [x1, #1]
    mov x2, #2
    bl out
    adrp x1, table
    ldr x1, [x1, :lo12:table+8]
    br x1
print:
    adr x1, msg
    ldr x2, msglen
out:
    mov x0, #STDOUT
    mov x8, SYS_WRITE
    svc #0
    ret
.p2align 4
done:
    adrp x0, hexdig
    add x0, x0, :lo12:hexdig
    ldrb w0, [x0, #10]
    mov x8, #93
    svc #0
.section .note.GNU-stack,"",%progbits
"""

PROGRAMS = {"x86_64": X86_PROGRAM, "aarch64": A64_PROGRAM}
EXPECTED_OUT = b"Hello; # world // not a comment\n" * 3 + b"e\n"


def assemble(isa: str, text: str, chunks=None, **kwargs) -> tuple[StreamAssembler, StreamObject]:
    sa = StreamAssembler(ISAS[isa], program=True, **kwargs)
    for chunk in chunks or [text]:
        if sa.halted:
            break
        sa.feed(chunk)
    return sa, sa.finish()


def ok(isa: str, text: str, **kwargs) -> StreamObject:
    sa, obj = assemble(isa, text, **kwargs)
    assert obj.ok, [e.message for e in obj.errors]
    assert not obj.warnings, [(w.text, w.message) for w in obj.warnings]
    return obj


def first_error(isa: str, text: str) -> str:
    _, obj = assemble(isa, text)
    assert obj.errors, "expected an error"
    return obj.errors[0].message


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_multi_section_program_runs_end_to_end(isa):
    obj = ok(isa, PROGRAMS[isa])
    assert obj.bss_size == 64 and obj.symbols["buf"] == (".bss", 0)
    image = obj.link()
    assert image.entry == TEXT_BASE and image.sections[".text"][0] == TEXT_BASE
    res = run_program(image, b"")
    assert res.fault is None, res.fault
    assert (res.stdout, res.status) == (EXPECTED_OUT, 97)


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_stream_image_matches_gnu_as_path(isa):
    """The stream's object links to the same data, bss and data-symbol addresses as GNU as's
    object of the same source (text may differ only in x86 branch sizes)."""
    stream_img = ok(isa, PROGRAMS[isa]).link()
    gas_img = link_program(assemble_program(PROGRAMS[isa], isa), isa)
    assert stream_img.segments[1:] == gas_img.segments[1:]
    assert stream_img.bss == gas_img.bss
    for name in ("msg", "msglen", "table", "counter", "pointer", "buf", "_start"):
        assert stream_img.symbols[name] == gas_img.symbols[name], name
    if isa == "aarch64":  # fixed-size instructions: the whole image is identical
        assert stream_img.segments == gas_img.segments
    for img in (stream_img, gas_img):
        res = run_program(img, b"")
        assert (res.stdout, res.status) == (EXPECTED_OUT, 97)


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_program_mode_is_chunking_invariant(isa, seed):
    import random

    text = PROGRAMS[isa]
    rng = random.Random(seed)
    chunks, i = [], 0
    while i < len(text):
        n = rng.choice([1, 2, 3, 7, 40])
        chunks.append(text[i : i + n])
        i += n
    whole = ok(isa, text)
    _, pieces = assemble(isa, text, chunks)
    assert pieces.ok and pieces.to_elf() == whole.to_elf()


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_elf_object_layout(isa):
    obj = ok(isa, PROGRAMS[isa])
    elf = Elf(obj.to_elf())
    names = {s.name: s for s in elf.sections}
    assert {".text", ".rodata", ".data", ".bss", ".symtab", ".strtab"} <= set(names)
    assert elf.data(names[".text"]) == obj.sections[".text"]
    assert names[".bss"].size == 64 and names[".bss"].type == 8
    syms = {s.name: s for s in elf.symbols() if s.name}
    assert syms["_start"].bind == 1 and syms["msg"].bind == 0  # .globl vs local
    relocs = {(r.symbol, r.type, r.addend) for r in elf.relocations_for(names[".rodata"])}
    abs64 = 1 if isa == "x86_64" else 257
    assert relocs == {("print", abs64, 0), ("done", abs64, 0)}


# (statement, relocation type, addend) for a reference to a label in .data
X86_RELOCS = [
    ("lea rsi, [rip + var]", 2, -4),
    ("lea rsi, [rip + var + 8]", 2, 4),
    ("lea rsi, var[rip]", 2, -4),
    ("mov dword ptr [rip + var], 5", 2, -8),
    ("mov esi, offset var", 10, 0),
    ("mov rsi, offset var + 3", 11, 3),
    ("movabs rsi, offset var", 1, 0),
    ("push offset var", 11, 0),
    ("mov eax, [var]", 11, 0),
    ("mov rax, var", 11, 0),
    ("mov byte ptr [var + 1], 10", 11, 1),
    ("mov rax, [var + rcx*8]", 11, 0),
    ("jmp qword ptr [var + rax*8]", 11, 0),
    ("add rax, offset var", 11, 0),
]
A64_RELOCS = [
    ("adrp x0, var", 275, 0),
    ("adrp x0, var + 16", 275, 16),
    ("add x0, x0, :lo12:var", 277, 0),
    ("add x0, x0, #:lo12:var+4", 277, 4),
    ("ldr x1, [x0, :lo12:var]", 286, 0),
    ("ldr w1, [x0, :lo12:var]", 285, 0),
    ("ldrh w1, [x0, :lo12:var]", 284, 0),
    ("ldrb w1, [x0, :lo12:var]", 278, 0),
    ("ldrsw x1, [x0, :lo12:var]", 285, 0),
    ("str x1, [x0, :lo12:var]", 286, 0),
    ("ldr q1, [x0, :lo12:var]", 299, 0),
    ("adr x0, var", 274, 0),
    ("ldr x0, var", 273, 0),
]


@pytest.mark.parametrize("isa,stmt,rtype,addend",
                         [("x86_64", *r) for r in X86_RELOCS] + [("aarch64", *r) for r in A64_RELOCS])
@pytest.mark.parametrize("order", ["backward", "forward"])
def test_cross_section_references_become_checked_relocations(isa, stmt, rtype, addend, order):
    data = ".data\n.p2align 4\nvar: .zero 32\n"
    code = f".text\n_start: nop\n{stmt}\nnop\n"
    obj = ok(isa, data + code if order == "backward" else code + data)
    [reloc] = obj.relocations
    assert (reloc.section, reloc.type, reloc.symbol, reloc.addend) == (".text", rtype, "var", addend)
    if isa == "aarch64":
        assert reloc.offset == 4
    obj.link()  # the linker accepts and applies it


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_data_directives_match_gnu_as(isa):
    word = 2 if isa == "x86_64" else 4
    text = rf"""
.data
a:  .byte 1, -1, 255, 'a', '\n', '#', ';', '\'', '\\', '"', '\0', 0x7f
    .word 0x1234, -2
    .short 5
    .hword 6
    .int 7
    .long 0xffffffff
    .2byte 8
    .4byte 9
    .8byte 10
    .quad 0xffffffffffffffff, -1, 1 + 2 << 3
    .ascii "a\tb\"c\\d\101\x41\0 ; # // /* x */"
    .string "s"
    .asciz "z", "y"
    .space 3, 0x41
    .skip 2
    .zero 1
    .p2align 3, 0xcc
b:  .quad b - a, . - a
    .quad a
    .long a + 4
    .balign 16
    .p2align 4,,3
"""
    obj = ok(isa, text)
    data = obj.sections[".data"]
    assert data.startswith(bytes([1, 255, 255, 97, 10, 35, 59, 39, 92, 34, 0, 127]))
    assert data[12 : 12 + word] == (0x1234).to_bytes(word, "little")
    assert b'a\tb"c\\dAA\0 ; # // /* x */s\0z\0y\0AAA\0\0\0' in data
    assert len(data) % 16 == 0


def test_numeric_local_labels():
    obj = ok("x86_64", """
_start: xor eax, eax
1:  inc eax
    cmp eax, 3
    jb 1b
    jmp 1f
    ud2
1:  jmp 2f
2:  mov edi, eax
    mov eax, 60
    syscall
""")
    assert run_program(obj.link(), b"").status == 3
    assert "refers back to `7:`" in first_error("x86_64", "_start: jmp 7b\n")
    assert "undefined label 'local label 3'" in first_error("x86_64", "_start: jmp 3f\n")
    assert "undefined label" in first_error("aarch64", "_start: b 3f\n")


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_constants(isa):
    text = """
.equ A, 5
.set B, A * 2
C = -3
.section .rodata
s: .ascii "abcdef"
slen = . - s
.data
.quad slen, A + B, C
"""
    obj = ok(isa, text)
    assert obj.sections[".data"] == b"".join(v.to_bytes(8, "little", signed=True) for v in (6, 15, -3))
    assert "already defined" in first_error(isa, ".equ A, 1\n.equ A, 2\n")
    assert "only numbers" in first_error(isa, ".data\nx: .byte 1\n.equ P, x\n")
    assert "define constants first" in first_error(isa, "_start: b N\n.equ N, 4\n" if isa == "aarch64"
                                                   else "_start: jmp N\n.equ N, 4\n")


REFUSED = [
    '.incbin "/etc/passwd"', '.include "x.s"', ".macro m", ".endm", ".rept 3", ".irp r, 1",
    ".if 1", ".ifdef x", ".else", ".endif", ".inst 0xd503201f", ".fill 4, 1, 0", ".org 0x10",
    ".eqv X, 1", "X == 1", ".comm buf, 4", ".lcomm buf, 4", ".pushsection .data", ".previous",
    ".cfi_startproc", ".rodata", ".section .text.startup", ".section .data,\"awx\"",
    ".att_syntax", ".byte 1", ".octa 1", ".float 1.0", ".data\n.byte 256", ".data\n.short a",
    ".bss\n.byte 1", ".bss\n.zero 4, 1", ".data\nnop", ".p2align 13", ".balign 3",
    ".data\n.ascii \"\\q\"", ".data\n.ascii \"\\x414\"", ".data\n.ascii \"x", "mov eax, \"a\"",
    ".data\nx: .byte 1\n.text\ncall x", ".data\nx: .quad y - x", ".align 4, 0, 1, 2",
]


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
@pytest.mark.parametrize("text", REFUSED)
def test_refused_constructs_name_their_line(isa, text):
    if isa == "aarch64" and text in ("mov eax, \"a\"",):
        text = 'mov x0, "a"'
    if isa == "aarch64" and "call x" in text:
        text = text.replace("call x", "bl x")
    src = "_start: nop\n" + text + "\nnop\n"
    sa, obj = assemble(isa, src)
    assert not obj.ok
    err = obj.errors[0]
    assert err.line == 1 + text.count("\n") + 1 or "undefined" in err.message, err.message
    assert f"(line {err.line}:" in err.message


def test_early_abort_keeps_the_rest_pending():
    sa = StreamAssembler(ISAS["x86_64"], program=True)
    events = sa.feed('.data\nmsg: .asciz "hi"\n.incbin "/etc/passwd"\n.byte 1\n.text\nnop\n')
    assert events[-1].kind == "error" and events[-1].line == 3
    assert sa.halted is not None and sa.pending_text == ".byte 1\n.text\nnop\n"
    with pytest.raises(stream_mod.StreamHalted):
        sa.feed("nop\n")
    assert sa.finish().sections[".data"] == b"hi\0"


def test_resource_limits(monkeypatch):
    monkeypatch.setattr(stream_mod, "MAX_DATA", 64)
    monkeypatch.setattr(stream_mod, "MAX_BSS", 128)
    assert "data too large" in first_error("x86_64", ".data\n.zero 40\n.section .rodata\n.zero 30\n")
    assert "data too large" in first_error("x86_64", '.data\n.zero 60\n.ascii "hello"\n')
    assert "data too large" in first_error("x86_64", ".data\n.byte 1\n.p2align 7\n")
    assert ".bss too large" in first_error("aarch64", ".bss\n.zero 100\n.space 29\n")
    assert "negative size" in first_error("aarch64", ".data\n.zero -1\n")
    ok("x86_64", ".data\n.zero 64\n.bss\n.zero 128\n")
    assert MAX_BSS == 1 << 24


def test_crosscheck_catches_a_wrong_data_encoding(monkeypatch):
    real = stream_mod._decode_string
    monkeypatch.setattr(stream_mod, "_decode_string", lambda body: real(body).replace(b"\n", b"\r"))
    msg = first_error("x86_64", '.data\nmsg: .ascii "a\\n"\n')
    assert "GNU as assembles this directive to 2 bytes (610a), the stream to 2 (610d)" in msg


def test_crosscheck_catches_a_wrong_relocation(monkeypatch):
    monkeypatch.setitem(stream_mod._DATA_RELOC["aarch64"], 8, 258)  # ABS32 for a .quad
    msg = first_error("aarch64", ".data\nx: .quad x\n")
    assert "GNU as emits relocations [R_AARCH64_ABS64 x+0x0 at +0]" in msg


def test_crosscheck_failure_on_a_relocation_rolls_back_the_label(monkeypatch):
    real = StreamAssembler._reloc_records

    def off_by_one(self, *args):
        data, check, relocs = real(self, *args)
        (o, t, s, a), = relocs
        bad = ((o, t, s, a + 1),)
        return data, stream_mod.replace(check, relocs=bad), bad

    monkeypatch.setattr(StreamAssembler, "_reloc_records", off_by_one)
    sa = StreamAssembler(ISAS["x86_64"], program=True)
    sa.feed("_start: lea rsi, [rip + msg]\n.data\n")
    events = sa.feed("msg: .byte 1\n")
    assert [e.kind for e in events] == ["error"] and "relocations" in events[0].message
    assert sa._section == ".data" and "msg" not in sa._symtab and not sa._relocs
    assert sa.pending_text == "" and sa._fixups and sa._insns[0].pending == "msg"


def test_finish_reports_undefined_labels_and_globals():
    _, obj = assemble("x86_64", ".globl main\n.data\n.quad nowhere\n.text\n_start: lea rax, [rip + gone]\n")
    messages = [e.message for e in obj.errors]
    assert any("undefined label 'gone'" in m for m in messages)
    assert any("undefined label 'nowhere' (line 3" in m for m in messages)
    assert any("`.globl main`" in m for m in messages)
    with pytest.raises(ValueError):
        obj.to_elf()


def test_link_errors_come_from_the_linker():
    from bca.image import LinkError

    with pytest.raises(LinkError, match="entry point"):
        ok("x86_64", "main: ret\n").link()


def test_retract_restores_sections():
    sa = StreamAssembler(ISAS["aarch64"], program=True)
    sa.feed(".data\nx: .word 1\n.bss\ny: .zero 8\n")
    assert sa._section == ".bss"
    sa.retract(2)
    assert sa._section == ".data" and sa._bss == 0 and "y" not in sa._symtab
    sa.feed(".text\n_start: adrp x0, x\n")
    obj = sa.finish()
    assert obj.ok and obj.bss_size == 0 and [r.symbol for r in obj.relocations] == ["x"]


# -- the canned application solutions, through the stream --------------------------------------

APPS = sorted((ROOT / "tests" / "fixtures" / "apps").glob("*.s"))


@pytest.mark.parametrize("path", [p for p in APPS if "broken" not in p.name], ids=lambda p: p.name)
def test_canned_app_solutions_pass_through_the_stream(path):
    from bca.apps.tasks import APP_TASKS

    task_name, isa = path.name.split(".")[:2]
    task = next(t for t in APP_TASKS if t.name == task_name)
    src = path.read_text()
    obj = ok(isa, src, chunks=[src[i : i + 29] for i in range(0, len(src), 29)])
    image = obj.link(bss_size_limit=max(task.bss_size, 1 << 20))
    gas_image = link_program(assemble_program(src, isa), isa)
    assert image.segments[1:] == gas_image.segments[1:] and image.bss == gas_image.bss
    for test in task.tests():
        res = run_program(image, test.stdin, max_insns=task.max_insns)
        assert (res.stdout, res.status) == (test.stdout, test.status), test.label
