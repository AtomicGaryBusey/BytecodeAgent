"""bca.image (assemble + link a whole program) and bca.process (run it as a Linux process)."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from bca.image import TEXT_BASE, AsmError, LinkError, assemble_program, link_program
from bca.process import ENTRY_SP, STACK_LO, ProcFault, run_program
from bca.stream import _gas_tool

FIXTURES = Path(__file__).parent / "fixtures" / "process"
ISAS = [i for i in ("x86_64", "aarch64") if _gas_tool(i)]
pytestmark = pytest.mark.skipif(not ISAS, reason="GNU as not installed")


def build(src: str, isa: str, **kw):
    return link_program(assemble_program(src, isa), isa, **kw)


def fixture(name: str, isa: str):
    return build((FIXTURES / f"{name}.{isa}.s").read_text(), isa)


def run(src: str, isa: str, stdin: bytes = b"", **kw):
    return run_program(build(src, isa), stdin, **kw)


# Tiny programs: {isa: source}. EXIT(n) appended by `prog`.
EXIT = {"x86_64": "mov eax, 60\nmov edi, {n}\nsyscall\n", "aarch64": "mov x0, #{n}\nmov x8, #93\nsvc #0\n"}


def prog(isa: str, x86: str, a64: str, tail: str = "", exit_code: int | None = 0) -> str:
    body = x86 if isa == "x86_64" else a64
    end = EXIT[isa].format(n=exit_code) if exit_code is not None else ""
    return ".globl _start\n.text\n_start:\n" + body + "\n" + end + tail


both = pytest.mark.parametrize("isa", ISAS)


# -- happy paths ---------------------------------------------------------------------------

@both
def test_hello(isa):
    im = fixture("hello", isa)
    assert im.entry == TEXT_BASE and im.segments[0][2] == "rx"
    r = run_program(im, b"")
    assert r.fault is None, r.fault
    assert (r.stdout, r.stderr, r.status, r.syscalls) == (b"hello, world\n", b"", 0, 2)
    assert r.insns == {"x86_64": 8, "aarch64": 9}[isa]


@both
@pytest.mark.parametrize("chunk", [None, 1, 3, 4096, 5000])
def test_cat_chunking(isa, chunk):
    im = fixture("cat", isa)
    data = bytes((i * 7 + i // 256) & 0xFF for i in range(10_000))
    r = run_program(im, data, read_chunk=chunk)
    assert r.fault is None, r.fault
    assert r.stdout == data and r.status == 0
    per_read = min(chunk or 4096, 4096)
    assert r.syscalls == 2 * -(-len(data) // per_read) + 2  # read+write per chunk, EOF read, exit


@both
def test_cat_empty(isa):
    r = run_program(fixture("cat", isa), b"")
    assert (r.stdout, r.status, r.fault, r.syscalls) == (b"", 0, None, 2)


@both
@pytest.mark.parametrize("code,expect", [(0, 0), (42, 42), (255, 255), (300, 44)])
def test_exit_status(isa, code, expect):
    if isa == "x86_64":
        src = prog(isa, f"mov edi, {code}\nmov eax, 231\nsyscall", "", exit_code=None)
    else:
        src = prog(isa, "", f"mov x0, #{code}\nmov x8, #94\nsvc #0", exit_code=None)
    r = run(src, isa)
    assert (r.status, r.fault) == (expect, None)


@both
def test_bss_zeroed_and_data_initialised(isa):
    im = fixture("bss", isa)
    assert im.bss[1] >= 65536 + 256 and im.bss[0] % 4096 == 0
    assert im.symbols["cblock"] % 32 == 0
    r = run_program(im, b"")
    assert (r.status, r.fault) == (0, None)


@both
@pytest.mark.parametrize("n,out", [(b"0\n", b"0\n"), (b"1\n", b"1\n"), (b"20\n", b"6765\n")])
def test_functions_across_sections(isa, n, out):
    r = run_program(fixture("recurse", isa), n)
    assert r.fault is None, r.fault
    assert (r.stdout, r.status) == (out, 0)


@both
def test_entry_state(isa):
    # exit 0 iff sp is 16-aligned, [sp] = argc = 0, argv[0] = envp[0] = auxv[0] = NULL, and the
    # general registers are zero.
    x86 = """
    mov rax, rsp
    and rax, 15
    or rax, [rsp]
    or rax, [rsp + 8]
    or rax, [rsp + 16]
    or rax, [rsp + 24]
    or rax, rbx
    or rax, rcx
    or rax, rdx
    or rax, rsi
    or rax, rdi
    or rax, rbp
    or rax, r8
    or rax, r15
    mov edi, eax
    mov eax, 60
    syscall
    """
    a64 = """
    mov x9, sp
    and x10, x9, #15
    ldp x11, x12, [x9]
    orr x10, x10, x11
    orr x10, x10, x12
    ldp x11, x12, [x9, #16]
    orr x10, x10, x11
    orr x10, x10, x12
    orr x10, x10, x0
    orr x10, x10, x1
    orr x10, x10, x7
    orr x10, x10, x19
    orr x10, x10, x29
    orr x10, x10, x30
    mov x0, x10
    mov x8, #93
    svc #0
    """
    r = run(prog(isa, x86, a64, exit_code=None), isa)
    assert (r.status, r.fault) == (0, None)
    assert ENTRY_SP % 16 == 0


@both
def test_syscall_return_values_and_clobbers(isa):
    # write returns n; read at EOF returns 0; x86 syscall sets rcx to the return rip.
    x86 = """
    mov eax, 1
    mov edi, 2
    lea rsi, [rip + msg]
    mov edx, 3
    syscall
    lea rcx, [rip + after]
    after:
    cmp rax, 3
    jne bad
    xor eax, eax
    xor edi, edi
    lea rsi, [rip + msg]
    mov edx, 10
    syscall
    here:
    lea rdx, [rip + here]
    cmp rcx, rdx
    jne bad
    test rax, rax
    jne bad
    """
    a64 = """
    mov x0, #2
    adr x1, msg
    mov x2, #3
    mov x8, #64
    svc #0
    cmp x0, #3
    b.ne bad
    mov x0, #0
    adrp x1, buf
    add x1, x1, :lo12:buf
    mov x2, #10
    mov x8, #63
    svc #0
    cbnz x0, bad
    """
    tail = "bad:\n" + EXIT[isa].format(n=1) + "msg: .ascii \"err\"\n.bss\nbuf: .zero 16\n"
    r = run(prog(isa, x86, a64, tail), isa)
    assert (r.status, r.fault, r.stderr, r.stdout) == (0, None, b"err", b"")


@both
def test_deterministic(isa):
    im = fixture("recurse", isa)
    a, b = run_program(im, b"15\n"), run_program(im, b"15\n")
    assert (a.stdout, a.status, a.insns, a.syscalls) == (b.stdout, b.status, b.insns, b.syscalls)


# -- relocations -----------------------------------------------------------------------------

@both
def test_relocation_kinds(isa):
    """Every relocation kind GNU as emits for ordinary code, checked by the running program."""
    x86 = """
    lea rax, [rip + val]            # PC32
    mov rax, [rax]
    cmp rax, 1234
    jne bad
    mov eax, OFFSET val             # 32 (zero-extended absolute)
    cmp qword ptr [rax], 1234
    jne bad
    mov rax, OFFSET val             # 32S
    cmp qword ptr [rax], 1234
    jne bad
    movabs rax, OFFSET val          # 64
    cmp qword ptr [rax], 1234
    jne bad
    mov rax, qword ptr [rip + val@GOTPCREL]   # GOTPCREL(X)
    cmp qword ptr [rax], 1234
    jne bad
    call ext_func                   # PLT32 to a global in another section
    cmp rax, 99
    jne bad
    mov rax, qword ptr [rip + fptr] # .data holding an absolute text address (64)
    call rax
    cmp rax, 99
    jne bad
    lea rcx, [rip + table]          # PC-relative jump table (.long a - table, PC32 in rodata)
    movsxd rdx, dword ptr [rcx + 4]
    add rdx, rcx
    jmp rdx
    """
    x86_tail = """
    bad:
    mov eax, 60
    mov edi, 1
    syscall
    t0: jmp bad
    t1:
    mov rax, [rip + str_ptr]
    cmp byte ptr [rax], 'q'
    jne bad
    mov eax, 60
    xor edi, edi
    syscall
    .section .text.other, "ax", @progbits
    .globl ext_func
    ext_func: mov eax, 99
    ret
    .section .rodata
    .p2align 2
    table: .long t0 - table, t1 - table
    .section .rodata.str1.1, "aMS", @progbits, 1
    s1: .asciz "q"
    .data
    .p2align 3
    val: .quad 1234
    fptr: .quad ext_func
    str_ptr: .quad s1
    """
    a64 = """
    adrp x0, val                    // ADR_PREL_PG_HI21
    add x0, x0, :lo12:val           // ADD_ABS_LO12_NC
    ldr x1, [x0]
    cmp x1, #1234
    b.ne bad
    adrp x0, val
    ldr x1, [x0, :lo12:val]         // LDST64_ABS_LO12_NC
    cmp x1, #1234
    b.ne bad
    adrp x0, w32
    ldr w1, [x0, :lo12:w32]         // LDST32
    adrp x0, h16
    ldrh w2, [x0, :lo12:h16]        // LDST16
    adrp x0, b8
    ldrb w3, [x0, :lo12:b8]         // LDST8
    add w1, w1, w2
    add w1, w1, w3
    cmp w1, #111
    b.ne bad
    adrp x0, q128
    ldr q0, [x0, :lo12:q128]        // LDST128
    fmov x1, d0
    cmp x1, #5
    b.ne bad
    movz x0, #:abs_g1:val           // MOVW_UABS_G1 / G0_NC
    movk x0, #:abs_g0_nc:val
    ldr x1, [x0]
    cmp x1, #1234
    b.ne bad
    ldr x0, lit                     // literal in the same section (resolved by as)
    ldr x1, [x0]
    cmp x1, #1234
    b.ne bad
    ldr x0, lit_ro                  // LD_PREL_LO19 into .rodata
    ldr x1, [x0]
    cmp x1, #1234
    b.ne bad
    adr x0, far_label               // ADR_PREL_LO21 across sections
    ldr w1, [x0]
    cmp w1, #7
    b.ne bad
    adrp x0, :got:val               // ADR_GOT_PAGE / LD64_GOT_LO12_NC
    ldr x0, [x0, :got_lo12:val]
    ldr x1, [x0]
    cmp x1, #1234
    b.ne bad
    bl ext_func                     // CALL26 across sections
    cmp x0, #99
    b.ne bad
    adrp x0, fptr
    ldr x0, [x0, :lo12:fptr]        // .data holding an ABS64 text address
    blr x0
    cmp x0, #99
    b.ne bad
    adrp x0, rel32
    add x0, x0, :lo12:rel32
    ldrsw x1, [x0]                  // PREL32 in .rodata
    add x1, x1, x0
    ldr x1, [x1]
    cmp x1, #1234
    b.ne bad
    mov x1, #1
    tbnz x1, #0, tb_ok              // TSTBR14 to another section
    b bad
    """
    a64_tail = """
    bad:
    mov x0, #1
    mov x8, #93
    svc #0
    .p2align 3
    lit: .quad val
    .section .text.other, "ax", %progbits
    .globl ext_func
    ext_func: mov x0, #99
    ret
    tb_ok:
    adrp x0, str_ptr
    ldr x0, [x0, :lo12:str_ptr]
    ldrb w0, [x0]
    cmp w0, #'q'
    b.ne bad                        // CONDBR19 to another section
    cbz x1, bad                     // CONDBR19 (cbz) to another section
    b.eq done                       // CONDBR19 (JUMP26 for b below)
    1: b bad                        // JUMP26 across sections
    done:
    mov x0, #0
    mov x8, #93
    svc #0
    far_label: .word 7
    .section .rodata
    .p2align 4
    q128: .quad 5, 0
    lit_ro: .quad val
    rel32: .word val - .
    w32: .word 100
    h16: .hword 10
    b8: .byte 1
    .section .rodata.str1.1, "aMS", %progbits, 1
    s1: .asciz "q"
    .data
    .p2align 3
    val: .quad 1234
    fptr: .quad ext_func
    str_ptr: .quad s1
    """
    src = prog(isa, x86, a64, x86_tail if isa == "x86_64" else a64_tail, exit_code=None)
    r = run(src, isa)
    assert (r.status, r.fault) == (0, None)


# -- link and assembly errors ----------------------------------------------------------------

@both
def test_link_errors(isa):
    call = "call" if isa == "x86_64" else "bl"
    with pytest.raises(LinkError, match="undefined symbol 'missing'"):
        build(prog(isa, f"{call} missing", f"{call} missing"), isa)
    with pytest.raises(LinkError, match="entry point '_start' is not defined"):
        build(".text\nmain: nop\n", isa)
    with pytest.raises(LinkError, match="not in an executable section"):
        build(".globl _start\n.text\nnop\n.data\n_start: .quad 0\n", isa)
    with pytest.raises(LinkError, match="over the limit"):
        build(prog(isa, "nop", "nop", ".bss\nbig: .zero 4096\n"), isa, bss_size_limit=1000)
    with pytest.raises(LinkError, match="not a valid ELF"):
        link_program(b"\x7fELF" + b"\0" * 10, isa)


@pytest.mark.skipif("aarch64" not in ISAS, reason="no aarch64 assembler")
def test_misaligned_lo12_is_rejected():
    isa = "aarch64"
    src = prog(isa, "", "adrp x0, v\nldr x1, [x0, :lo12:v]", ".data\n.byte 1\nv: .quad 5\n")
    with pytest.raises(LinkError, match="not 8-byte aligned"):
        build(src, isa)


@both
def test_asm_error_line_numbers(isa):
    src = "  .globl _start\n  .text\n_start:\n  nop\n  frobnicate x1\n"
    with pytest.raises(AsmError) as exc:
        assemble_program(src, isa)
    assert exc.value.line == 5 and "line 5:" in str(exc.value)


@both
@pytest.mark.parametrize("bad", ['.incbin "/etc/passwd"', '.include "/etc/hostname"',
                                 '.INCBIN "/etc/passwd"', '.macro m a\n.\\a "/etc/passwd"\n.endm\nm incbin',
                                 '.irp d, incbin\n.\\d "/etc/passwd"\n.endr', '.altmacro'])
def test_host_file_access_is_refused(isa, bad):
    with pytest.raises(AsmError, match="not allowed"):
        assemble_program(".globl _start\n_start:\n" + bad + "\n", isa)


def test_no_files_left_behind():
    tmp = tempfile.gettempdir()
    before = {p for p in os.listdir(tmp) if p.startswith("bca-prog-")}
    for isa in ISAS:
        assemble_program(".globl _start\n_start: nop\n", isa)
        with pytest.raises(AsmError):
            assemble_program("nonsense here\n", isa)
    after = {p for p in os.listdir(tmp) if p.startswith("bca-prog-")}
    assert after == before


# -- faults and escape attempts ----------------------------------------------------------------

def fault_of(r) -> ProcFault:
    assert r.fault is not None, (r.stdout, r.status)
    assert r.status is None
    return r.fault


@both
def test_write_to_rodata_faults(isa):
    x86 = "lea rax, [rip + ro]\nmov byte ptr [rax], 1"
    a64 = "adrp x0, ro\nadd x0, x0, :lo12:ro\nstrb w1, [x0]"
    im = build(prog(isa, x86, a64, ".section .rodata\nro: .byte 0\n"), isa)
    f = fault_of(run_program(im, b""))
    assert f.kind == "oob" and "read-only" in f.message and f.address == im.symbols["ro"]
    assert TEXT_BASE <= f.pc < TEXT_BASE + 0x100


@both
def test_write_to_text_faults(isa):
    x86 = "lea rax, [rip + _start]\nmov dword ptr [rax], 0x90909090"
    a64 = "adr x0, _start\nstr wzr, [x0]"
    f = fault_of(run(prog(isa, x86, a64), isa))
    assert f.kind == "write_to_text" and f.address == TEXT_BASE


@both
def test_read_syscall_into_text_faults(isa):
    x86 = "xor eax, eax\nxor edi, edi\nlea rsi, [rip + _start]\nmov edx, 4\nsyscall"
    a64 = "mov x0, #0\nadr x1, _start\nmov x2, #4\nmov x8, #63\nsvc #0"
    f = fault_of(run(prog(isa, x86, a64), isa, b"abcd"))
    assert f.kind == "write_to_text" and f.pc == TEXT_BASE + 16


@both
@pytest.mark.parametrize("ptr", ["0", "0x10", "0x7fffffff0000", "0x401000"])
def test_bad_pointer_in_write(isa, ptr):
    x86 = f"mov eax, 1\nmov edi, 1\nmovabs rsi, {ptr}\nmov edx, 16\nsyscall"
    a64 = f"mov x0, #1\nldr x1, ={ptr}\nmov x2, #16\nmov x8, #64\nsvc #0"
    f = fault_of(run(prog(isa, x86, a64), isa))
    assert f.kind == "oob" and "not mapped" in f.message


@both
def test_write_huge_length_faults(isa):
    x86 = "mov eax, 1\nmov edi, 1\nlea rsi, [rip + _start]\nmov rdx, -1\nsyscall"
    a64 = "mov x0, #1\nadr x1, _start\nmov x2, #-1\nmov x8, #64\nsvc #0"
    assert fault_of(run(prog(isa, x86, a64), isa)).kind == "oob"


@both
def test_bad_pointer_in_read_writes_nothing_at_eof(isa):
    # Linux copies only what is available: with no input left the buffer is never touched.
    x86 = "xor eax, eax\nxor edi, edi\nxor esi, esi\nmov edx, 16\nsyscall\nmov edi, eax\nmov eax, 60\nsyscall"
    a64 = "mov x0, #0\nmov x1, #0\nmov x2, #16\nmov x8, #63\nsvc #0\nmov x8, #93\nsvc #0"
    r = run(prog(isa, x86, a64, exit_code=None), isa, b"")
    assert (r.status, r.fault) == (0, None)
    r = run(prog(isa, x86, a64, exit_code=None), isa, b"x")
    assert fault_of(r).kind == "oob"


@both
@pytest.mark.parametrize("what", ["unknown", "bad_fd_write", "bad_fd_read"])
def test_bad_syscalls(isa, what):
    nr = {"x86_64": {"unknown": 39, "bad_fd_write": 1, "bad_fd_read": 0},
          "aarch64": {"unknown": 172, "bad_fd_write": 64, "bad_fd_read": 63}}[isa][what]
    fd = {"unknown": 0, "bad_fd_write": 3, "bad_fd_read": 1}[what]
    x86 = f"mov eax, {nr}\nmov edi, {fd}\nlea rsi, [rip + _start]\nmov edx, 1\nsyscall"
    a64 = f"mov x8, #{nr}\nmov x0, #{fd}\nadr x1, _start\nmov x2, #1\nsvc #0"
    f = fault_of(run(prog(isa, x86, a64), isa, b"in"))
    assert f.kind == "bad_syscall"
    assert f.pc == TEXT_BASE + (16 if isa == "aarch64" else 22)


def test_int80_is_bad_syscall():
    if "x86_64" not in ISAS:
        pytest.skip("no x86-64 assembler")
    f = fault_of(run(prog("x86_64", "mov eax, 1\nint 0x80", ""), "x86_64"))
    assert f.kind == "bad_syscall" and "int 0x80" in f.message


@both
def test_infinite_loop_is_no_exit(isa):
    x86 = "1: inc rax\njmp 1b"
    a64 = "1: add x0, x0, #1\nb 1b"
    r = run(prog(isa, x86, a64), isa, max_insns=1_000_000)
    assert fault_of(r).kind == "no_exit" and r.insns == 1_000_000


@both
def test_running_off_the_end_is_no_exit(isa):
    f = fault_of(run(prog(isa, "nop\nnop", "nop\nnop", exit_code=None), isa))
    assert f.kind == "no_exit" and "ran off the end" in f.message


@both
def test_deep_recursion_is_stack_overflow(isa):
    r = run_program(fixture("recurse", isa), b"deep\n")
    f = fault_of(r)
    assert f.kind == "stack_overflow" and STACK_LO - 4096 <= f.address < STACK_LO


@both
def test_huge_stack_frame_is_stack_overflow(isa):
    x86 = "sub rsp, 0x200000\nmov qword ptr [rsp], 1"
    a64 = "sub sp, sp, #0x200, lsl #12\nstr xzr, [sp]"
    assert fault_of(run(prog(isa, x86, a64), isa)).kind == "stack_overflow"


@both
def test_huge_output_is_output_limit(isa):
    x86 = "1: mov eax, 1\nmov edi, 1\nlea rsi, [rip + _start]\nmov edx, 1000\nsyscall\njmp 1b"
    a64 = "1: mov x0, #1\nadr x1, _start\nmov x2, #1000\nmov x8, #64\nsvc #0\nb 1b"
    r = run(prog(isa, x86, a64), isa, stdout_cap=50_000)
    assert fault_of(r).kind == "output_limit" and len(r.stdout) == 50_000
    im = build(prog(isa, x86.replace("edi, 1", "edi, 2"), a64.replace("x0, #1\n", "x0, #2\n")), isa)
    r = run_program(im, b"", stdout_cap=50_000)
    assert fault_of(r).kind == "output_limit" and len(r.stderr) == 50_000 and r.stdout == b""


@both
@pytest.mark.parametrize("target", ["data", "rodata", "stack", "unmapped"])
def test_jump_into_non_code_is_crash(isa, target):
    x86 = {"data": "lea rax, [rip + d]\njmp rax", "rodata": "lea rax, [rip + ro]\njmp rax",
           "stack": "jmp rsp", "unmapped": "mov eax, 0x12345678\njmp rax"}[target]
    a64 = {"data": "adrp x0, d\nadd x0, x0, :lo12:d\nbr x0",
           "rodata": "adrp x0, ro\nadd x0, x0, :lo12:ro\nbr x0",
           "stack": "mov x0, sp\nbr x0", "unmapped": "ldr x0, =0x12345678\nbr x0"}[target]
    tail = ".data\nd: .byte 0xc3, 0xc3, 0xc3, 0xc3\n.section .rodata\nro: .byte 0xc3, 0xc3, 0xc3, 0xc3\n"
    f = fault_of(run(prog(isa, x86, a64, tail), isa))
    assert f.kind == "crash" and "jumped to" in f.message


@both
def test_jump_to_address_one_is_crash(isa):
    x86 = "mov eax, 1\njmp rax"
    a64 = "mov x0, #1\nbr x0"
    assert fault_of(run(prog(isa, x86, a64), isa)).kind == "crash"


@both
def test_invalid_instruction_and_traps_are_crash(isa):
    for body in (("ud2", "udf #1"), ("int3", "brk #0"), ("hlt", "hvc #0")):
        f = fault_of(run(prog(isa, *body), isa))
        assert f.kind == "crash", (body, f)


@both
def test_load_from_unmapped_is_oob(isa):
    x86 = "mov eax, 0x10\nmov rax, [rax]"
    a64 = "mov x0, #0x10\nldr x0, [x0]"
    f = fault_of(run(prog(isa, x86, a64), isa))
    assert f.kind == "oob" and f.address == 0x10 and f.pc == TEXT_BASE + (5 if isa == "x86_64" else 4)


# -- throughput ------------------------------------------------------------------------------

@both
def test_throughput(isa):
    x86 = "mov ecx, 5000000\n1: add rax, rcx\nxor rax, 3\ndec rcx\njnz 1b"
    a64 = "ldr x1, =5000000\n1: add x0, x0, x1\neor x0, x0, #3\nsubs x1, x1, #1\nb.ne 1b"
    im = build(prog(isa, x86, a64), isa)
    t = time.perf_counter()
    r = run_program(im, b"")
    rate = r.insns / (time.perf_counter() - t)
    assert r.status == 0 and r.insns == 20_000_004
    assert rate > 5e6, f"{rate / 1e6:.1f} M instructions/s"


def test_python_counter_fallback_matches_native():
    """The portable counter (no executable host page) counts exactly like the native one."""
    import subprocess
    import sys

    code = ("import sys; from tests.test_process import fixture; from bca.process import run_program; "
            "print(*[run_program(fixture('recurse', i), b'10\\n').insns for i in sys.argv[1:]])")
    root = Path(__file__).parent.parent
    out = [subprocess.run([sys.executable, "-c", code, *ISAS], cwd=root, capture_output=True, text=True,
                          env={**os.environ, **env}, check=True).stdout.split()
           for env in ({}, {"BCA_PROCESS_PY_COUNTER": "1"})]
    assert out[0] == out[1] and all(int(n) > 1000 for n in out[0])
