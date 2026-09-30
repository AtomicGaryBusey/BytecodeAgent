"""The app-battery prompt: one text per (task, ISA), identical for the `asm` and `stream` paths.

The two paths differ only in how the harness processes the answer (GNU as on the whole file vs.
the stream assembler line by line with an early abort), so the model gets the same facts and the
same format rules on both; `path` is accepted and validated but never changes the text. The
system prompt is the harness's usual SYSTEM_PROMPT (bca.liveeval.subject), unchanged.

Structure: target and program model, entry state, system calls, memory, assembly format, the
task specification (bca.apps, ISA-neutral), and on repair rounds the previous attempt plus the
exact failure (the same wording as the function batteries' repair section).
"""

from __future__ import annotations

from bca.liveeval.prompts import ISA_LEVEL

APP_PATHS = ("asm", "stream")
STACK_BYTES = 1 << 20  # bca.process.STACK_SIZE

TARGET = {
    "x86_64": "x86-64 (AMD64)",
    "aarch64": "AArch64 (ARMv8-A, little-endian)",
}

ENTRY = {
    "x86_64": (
        "Entry state (as the Linux kernel sets it up): execution starts at the label `_start`. rsp is "
        "16-byte aligned and points at argc = 0, followed by the NULL terminators of an empty argv, an empty "
        "envp and an empty auxiliary vector: the program gets no arguments. Every other general-purpose "
        "register is zero, and the direction flag is clear. There is no return address: `_start` must "
        "never return with `ret`; the program ends only through the exit system call."
    ),
    "aarch64": (
        "Entry state (as the Linux kernel sets it up): execution starts at the label `_start`. sp is "
        "16-byte aligned and points at argc = 0, followed by the NULL terminators of an empty argv, an empty "
        "envp and an empty auxiliary vector: the program gets no arguments. Every other general-purpose "
        "register (x0-x30) is zero; x30 is not a return address, so `_start` must never return with "
        "`ret`; the program ends only through the exit system call."
    ),
}

SYSCALLS = {
    "x86_64": (
        "System calls: the `syscall` instruction, with the system call number in rax and the arguments in "
        "rdi, rsi, rdx; the result comes back in rax. The kernel overwrites rcx and r11; every other "
        "register is preserved. Exactly these four are available:\n"
        "- read = 0: read(fd = 0, buf, count) reads up to count bytes of standard input into buf; returns the "
        "number of bytes read, 0 at end of input.\n"
        "- write = 1: write(fd, buf, count) with fd 1 (standard output) or 2 (standard error); returns count.\n"
        "- exit = 60: exit(status) ends the program; the exit status is the low 8 bits of status.\n"
        "- exit_group = 231: the same as exit.\n"
        "Any other system call number, another file descriptor, `int 0x80` or `sysenter` stops the program "
        "with an error, and so does a buffer that is not entirely inside the program's memory."
    ),
    "aarch64": (
        "System calls: the `svc #0` instruction, with the system call number in x8 and the arguments in x0, "
        "x1, x2; the result comes back in x0. Every other register is preserved. Exactly these four are "
        "available:\n"
        "- read = 63: read(fd = 0, buf, count) reads up to count bytes of standard input into buf; returns the "
        "number of bytes read, 0 at end of input.\n"
        "- write = 64: write(fd, buf, count) with fd 1 (standard output) or 2 (standard error); returns count.\n"
        "- exit = 93: exit(status) ends the program; the exit status is the low 8 bits of status.\n"
        "- exit_group = 94: the same as exit.\n"
        "Any other system call number or file descriptor stops the program with an error, and so does a "
        "buffer that is not entirely inside the program's memory."
    ),
}

SYNTAX = {
    "x86_64": (
        "GNU assembler (GNU as) syntax, Intel flavour without register prefixes: the harness puts "
        "`.intel_syntax noprefix` in front of your text, so do not write it yourself. Examples: "
        "`mov rax, qword ptr [rbx + rcx*8]`, `movzx eax, byte ptr [rsi]`, `lea rsi, [rip + buffer]`. "
        "Comments start with `#`; do not use `;`."
    ),
    "aarch64": (
        "GNU assembler (GNU as) syntax for AArch64. Examples: `ldr x2, [x0, x1, lsl #3]`, "
        "`ldrb w3, [x1], #1`, `stp x29, x30, [sp, #-16]!`. Comments start with `//`; do not use `;`."
    ),
}

DATA_REFS = {
    "x86_64": (
        "Addressing data: rip-relative, `lea rdi, [rip + table]` or `mov al, byte ptr [rip + table + 3]`. "
        "The program is linked at fixed low addresses, so absolute forms (`mov esi, offset buffer`, "
        "`movzx eax, byte ptr [table + rcx]`) work as well."
    ),
    "aarch64": (
        "Addressing data: `adrp x0, label` followed by `add x0, x0, :lo12:label` (or a load/store "
        "`ldr x1, [x0, :lo12:label]`); `adr x0, label` also works within +-1 MiB. Literal pools are NOT "
        "allowed: no `ldr x0, =label` and no `ldr x0, =constant` (build constants with mov/movz/movk)."
    ),
}

WORD = {"x86_64": "2 bytes", "aarch64": "4 bytes"}


def _size(n: int) -> str:
    if n % (1 << 20) == 0:
        return f"{n >> 20} MiB"
    if n % 1024 == 0:
        return f"{n >> 10} KiB"
    return f"{n} bytes"


def format_rules(isa: str) -> str:
    return (
        "Output format: one complete assembly source file in " + SYNTAX[isa] + "\n"
        "Allowed in the file:\n"
        "- Sections: `.text` (code), `.section .rodata` (read-only data), `.data` (initialised writable data), "
        "`.bss` (zero-initialised space: only `.zero`/`.space` and alignment). Instructions go in `.text` only.\n"
        "- Data directives: `.byte`, `.short`/`.2byte`, `.word` (" + WORD[isa] + " on this target), "
        "`.long`/`.int`/`.4byte`, `.quad`/`.8byte`, `.ascii`, `.asciz`/`.string`, `.zero`/`.space`, "
        "alignment with `.align`/`.balign`/`.p2align`, and `.globl`/`.global` (write `.globl _start`).\n"
        "- Labels `name:` in any section, and GNU local labels `1:` referenced as `1f` (next) / `1b` "
        "(previous).\n"
        "- Constants: `.equ name, value` (or `.set name, value`, or `name = value`), where value is a number "
        "or a difference of labels defined earlier in the same section (e.g. `msg_len = . - msg`). Define "
        "every constant before its first use.\n"
        "- " + DATA_REFS[isa] + "\n"
        "Not allowed: macros (`.macro`, `.rept`, `.irp`), conditional assembly (`.if`), `.include`, "
        "`.incbin`, `.fill`, `.org`, `.comm`/`.lcomm`, `.inst`, `.cfi_*` directives, section stacks "
        "(`.pushsection`) and any other section. No libc and no other library: there is nothing to link "
        "against, so every symbol you use must be defined in your file.\n"
        + ISA_LEVEL[isa] + "\n"
        "Output ONLY the assembly source: no Markdown fences, no prose before or after it."
    )


def build_app_prompt(path: str, isa: str, task, previous: tuple[str, str] | None = None) -> str:
    """The prompt for an app cell; the same text for `asm` and `stream` (see the module docstring).
    `previous` = (code, failure description) for a repair round."""
    if path not in APP_PATHS:
        raise ValueError(f"{task.name} is not applicable on the {path} path: {task.na_paths.get(path, '')}")
    app = task.app
    parts = [
        f"Write the complete program `{task.name}`: a static Linux executable for {TARGET[isa]}, written in "
        "assembly. There is no C library, no C runtime and no helper code: you write the entry point "
        "`_start`, every function, every table and string, and make the system calls yourself.",
        ENTRY[isa],
        SYSCALLS[isa],
        f"Memory: the program's own sections plus a stack of {_size(STACK_BYTES)} below the entry sp. There "
        "is no heap (no brk, no mmap, no malloc): put working storage in `.bss` (zero-initialised), at most "
        f"{_size(app.bss_size)} in total, or on the stack. Code is not writable. Any access outside these "
        f"areas stops the program. Each test run may execute at most {app.max_insns:,} instructions.",
        format_rules(isa),
        f"Specification: {task.spec.rstrip()}",
    ]
    if previous is not None:
        code, failure = previous
        parts.append(
            "Your previous attempt was:\n<previous>\n" + code.rstrip() + "\n</previous>\n"
            "It failed:\n<failure>\n" + failure.rstrip() + "\n</failure>\n"
            "Write a corrected, complete version. Same output format; output only the code."
        )
    return "\n\n".join(parts) + "\n"
