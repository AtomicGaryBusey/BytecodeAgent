"""The prompt for the raw-machine-code editor: the same specification, system calls and real argv as the
assembly editor (demo.editor.prompt), with the app battery's hex format and fixed memory layout
(bca.liveeval.prompts_apps.hex_format_rules) instead of assembly."""

from __future__ import annotations

from bca.liveeval.prompts_apps import hex_format_rules
from demo.editor.prompt import SPEC, SYSCALLS

WORK_AREA = 16 << 20  # the zero-initialised area at 0x600000

ENTRY = (
    "Entry state (as the Linux kernel sets it up): execution starts at the first instruction of your code, "
    "address 0x400000. sp is 16-byte aligned and points at argc (8 bytes), followed by argc pointers to the "
    "NUL-terminated argument strings (argv[0] is the program name, argv[1] the file name), a NULL pointer, the "
    "environment pointers, a NULL pointer and the auxiliary vector. x30 is not a return address; the program "
    "ends only through the exit system call."
)

MEMORY = (
    "Memory: the code, data and zero-initialised areas below, plus the stack (8 MiB on Linux by default). There "
    "is no heap: no brk, no mmap, no malloc. Code is not writable."
)


def build_prompt(previous: tuple[str, str] | None = None) -> str:
    spec = (SPEC.strip().replace("Keep the buffer in `.bss`", "Keep the buffer in the zero-initialised work area")
            .replace("the Linux stack at `_start`", "the Linux stack at the entry point"))
    parts = [
        "Write the complete program `edit`: a static Linux executable for AArch64 (ARMv8-A, little-endian), "
        "written directly as machine code. There is no assembler, no linker, no C library, no C runtime and no "
        "helper code: you write every instruction, every table and string, and make the system calls yourself. "
        "The program runs in a real terminal (a pseudo-terminal) and is tested by sending keystrokes and checking "
        "the files it saves and that it restores the terminal.",
        ENTRY,
        SYSCALLS,
        MEMORY,
        hex_format_rules("aarch64", WORK_AREA),
        "Specification:\n" + spec,
    ]
    if previous is not None:
        code, failure = previous
        parts.append(
            "Your previous attempt was:\n<previous>\n" + code.rstrip() + "\n</previous>\n"
            "It failed:\n<failure>\n" + failure.rstrip() + "\n</failure>\n"
            "Write a corrected, complete version. Same output format; output only the program text."
        )
    return "\n\n".join(parts) + "\n"
