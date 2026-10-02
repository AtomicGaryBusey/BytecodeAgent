"""The app battery's opt-in `hex` path: whole programs written as raw machine code and data bytes in a fixed
layout (bca.liveeval.hexpath parse_program / program_image / write_elf; bca.liveeval.apps._app_hex_attempt)."""

import os
import shutil
import subprocess
import tempfile

import pytest

from bca.liveeval import hexpath
from bca.liveeval.apps import APP_EXTRA_PATHS, APP_PATHS, BY_NAME, app_attempt
from bca.liveeval.prompts_apps import build_app_prompt
from bca.liveeval.runner import plan_cells
from bca.liveeval.subject import FakeSubject
from bca.process import run_program

# A tiny program: write the 2-byte prefix "> " from .data, then copy stdin to stdout through the zeroed area
# at 0x600000, then exit(7). Addresses are absolute (movz / imm32), branches hand-checked.
CAT = {
    "aarch64": """.text
d2800020 // mov x0, #1
d2a00a01 // movz x1, #0x50, lsl #16 (0x500000)
d2800042 // mov x2, #2
d2800808 // mov x8, #64 (write)
d4000001 // svc #0
d2800000 // loop: mov x0, #0
d2a00c01 // movz x1, #0x60, lsl #16 (0x600000)
d2820002 // mov x2, #4096
d28007e8 // mov x8, #63 (read)
d4000001 // svc #0
f100001f // cmp x0, #0
540000ed // b.le done (+7 words)
aa0003e2 // mov x2, x0
d2800020 // mov x0, #1
d2a00c01 // movz x1, #0x60, lsl #16
d2800808 // mov x8, #64
d4000001 // svc #0
17fffff4 // b loop (-12 words)
d28000e0 // done: mov x0, #7
d2800ba8 // mov x8, #93 (exit)
d4000001 // svc #0
.data
3e 20
""",
    "x86_64": """.text
bf 01 00 00 00 ; mov edi, 1
be 00 00 50 00 ; mov esi, 0x500000
ba 02 00 00 00 ; mov edx, 2
b8 01 00 00 00 ; mov eax, 1
0f 05          ; syscall
31 ff          ; loop: xor edi, edi
be 00 00 60 00 ; mov esi, 0x600000
ba 00 10 00 00 ; mov edx, 4096
31 c0          ; xor eax, eax
0f 05          ; syscall
48 85 c0       ; test rax, rax
7e 16          ; jle done
48 89 c2       ; mov rdx, rax
bf 01 00 00 00 ; mov edi, 1
be 00 00 60 00 ; mov esi, 0x600000
b8 01 00 00 00 ; mov eax, 1
0f 05          ; syscall
eb d5          ; jmp loop
bf 07 00 00 00 ; done: mov edi, 7
b8 3c 00 00 00 ; mov eax, 60
0f 05          ; syscall
.data
3e20
""",
}
ISAS = ("aarch64", "x86_64")


@pytest.mark.parametrize("isa", ISAS)
def test_parse_layout_and_run(isa):
    prog = hexpath.parse_program(isa, CAT[isa])
    assert prog.data == b"> " and len(prog.lines) == 21
    img = hexpath.program_image(prog, 1 << 20)
    assert img.entry == hexpath.HEX_TEXT_BASE == img.segments[0][0]
    assert img.segments[0][1][:len(prog.text)] == prog.text  # the bytes run exactly as written
    assert img.segments[1] == (hexpath.HEX_DATA_BASE, b"> ", "rw")
    assert img.bss == (hexpath.HEX_BSS_BASE, 1 << 20)
    hexpath.check(isa, prog.lines, hexpath.HEX_TEXT_BASE)
    for chunk in (None, 3):
        r = run_program(img, b"hello\nworld\n", read_chunk=chunk)
        assert r.fault is None and r.status == 7 and r.stdout == b"> hello\nworld\n"


@pytest.mark.parametrize("text,match", [
    (".text\n.globl _start\n", "only markers"),
    (".text\nstart:\n", "is not one 32-bit|is not a byte"),
    (".text\n.text\n", "appears twice"),
    (".data\n3e 2\n", "not data bytes"),
    (".data\n3e 20\n", "no instruction"),
])
@pytest.mark.parametrize("isa", ISAS)
def test_malformed_programs(isa, text, match):
    with pytest.raises(hexpath.HexError, match=match):
        hexpath.parse_program(isa, text)


def test_prompt_states_the_layout_and_no_assembler():
    task = BY_NAME["wc"]
    for isa in ISAS:
        p = build_app_prompt("hex", isa, task)
        for s in ("0x400000", "0x500000", "0x600000", "no assembler", "`.text`", "`.data`", "Specification:"):
            assert s in p
        assert "GNU assembler" not in p and "_start" not in p
    assert build_app_prompt("asm", "x86_64", task) == build_app_prompt("stream", "x86_64", task)


def test_hex_is_opt_in_for_apps():
    task = BY_NAME["wc"]
    assert "hex" not in APP_PATHS and APP_EXTRA_PATHS == ("hex",)
    assert {p for _t, _b, p, _i in plan_cells([task], None, ["x86_64"])} == {"asm", "stream"}
    assert {p for _t, _b, p, _i in plan_cells([task], ["hex"], ["x86_64"], 1)} == {"hex"}


@pytest.mark.parametrize("isa", ISAS)
def test_pipeline_runs_the_bytes_and_explains_them(isa):
    # the cat program is not wc: a real run, a real wrong_output, and the listing of the model's bytes
    a = app_attempt(FakeSubject([CAT[isa]]), "hex", isa, BY_NAME["wc"], "p")
    assert a.category == "wrong_output" and not a.ok
    assert a.code == hexpath.parse_program(isa, CAT[isa]).text
    assert "decodes as (address, your line" in a.failure and "0x400000 line" in a.failure
    assert not hasattr(a, "image") and not hasattr(a, "hex_note")


def test_pipeline_rejects_undecodable_and_out_of_level_words():
    a = app_attempt(FakeSubject([".text\nffffffff\n"]), "hex", "aarch64", BY_NAME["wc"], "p")
    assert a.category == "encoding" and "does not decode" in a.failure
    a = app_attempt(FakeSubject([".text\n1ac14000\n"]), "hex", "aarch64", BY_NAME["wc"], "p")  # crc32b: v8.1
    assert a.category == "encoding" and "outside the prompt's instruction set" in a.failure
    a = app_attempt(FakeSubject(["Here is the program:\nd4000001\n"]), "hex", "aarch64", BY_NAME["wc"], "p")
    assert a.category == "format"


@pytest.mark.parametrize("isa", ISAS)
def test_elf_container(isa):
    prog = hexpath.parse_program(isa, CAT[isa])
    elf = hexpath.write_elf(prog, 1 << 20)
    assert elf[:4] == b"\x7fELF" and elf[0x1000:0x1000 + len(prog.text)] == prog.text
    assert elf[0x2000:0x2002] == b"> "
    readelf = shutil.which("readelf")
    if readelf:
        with tempfile.NamedTemporaryFile(suffix=".elf") as f:
            f.write(elf)
            f.flush()
            out = subprocess.run([readelf, "-lh", "-W", f.name], capture_output=True).stdout.decode()
        assert "EXEC" in out and "0x0000000000400000" in out and "GNU_STACK" in out


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", "python:3.12-slim"], capture_output=True).returncode == 0


@pytest.mark.skipif(os.uname().machine != "aarch64" or not _docker_ok(), reason="needs an AArch64 host with docker")
def test_native_run_agrees_with_the_emulator():
    prog = hexpath.parse_program("aarch64", CAT["aarch64"])
    stdin = b"line one\nline two\n"
    emu = run_program(hexpath.program_image(prog, 1 << 20), stdin)
    with tempfile.TemporaryDirectory(prefix="hexelf-") as tmp:
        path = os.path.join(tmp, "prog")
        with open(path, "wb") as f:
            f.write(hexpath.write_elf(prog, 1 << 20))
        os.chmod(path, 0o755)
        os.chmod(tmp, 0o755)
        r = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "-v", f"{tmp}:/work:ro",
                            "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL", "--memory", "64m",
                            "--pids-limit", "8", "python:3.12-slim", "/work/prog"],
                           input=stdin, capture_output=True, timeout=120)
    assert (r.returncode, r.stdout) == (emu.status, emu.stdout) == (7, b"> " + stdin)
