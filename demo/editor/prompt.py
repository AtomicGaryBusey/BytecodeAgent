"""The prompt for the editor demo: the app battery's output-format rules (bca.liveeval.prompts_apps) with
the editor's system calls, real argv and the specification in SPEC.md."""

from __future__ import annotations

from pathlib import Path

from bca.liveeval.prompts_apps import format_rules

SPEC = (Path(__file__).parent / "SPEC.md").read_text()

ENTRY = (
    "Entry state (as the Linux kernel sets it up): execution starts at the label `_start`. sp is 16-byte "
    "aligned and points at argc (8 bytes), followed by argc pointers to the NUL-terminated argument strings "
    "(argv[0] is the program name, argv[1] the file name), a NULL pointer, the environment pointers, a NULL "
    "pointer and the auxiliary vector. x30 is not a return address: `_start` must never return with `ret`; "
    "the program ends only through the exit system call."
)

SYSCALLS = (
    "System calls: the `svc #0` instruction, with the system call number in x8 and the arguments in x0-x5; "
    "the result comes back in x0 (a negative value -errno on failure). Every other register is preserved. "
    "Use only these:\n"
    "- openat = 56: openat(dirfd, path, flags, mode); use dirfd = AT_FDCWD = -100. Flags: O_RDONLY = 0, "
    "O_WRONLY = 1, O_RDWR = 2, O_CREAT = 0x40, O_TRUNC = 0x200 (AArch64 values). Mode 0644 = 420.\n"
    "- close = 57: close(fd).\n"
    "- lseek = 62: lseek(fd, offset, whence); SEEK_SET = 0, SEEK_END = 2.\n"
    "- read = 63: read(fd, buf, count); returns the number of bytes read, 0 at end of file.\n"
    "- write = 64: write(fd, buf, count); returns the number of bytes written (may be fewer than count: "
    "loop until everything is written).\n"
    "- ioctl = 29: ioctl(fd, request, arg) with TCGETS = 0x5401, TCSETS = 0x5402, TIOCGWINSZ = 0x5413.\n"
    "- ftruncate = 46: ftruncate(fd, length).\n"
    "- exit_group = 94: exit_group(status) ends the program (exit = 93 also works)."
)

MEMORY = (
    "Memory: the program's own sections plus the stack (8 MiB on Linux by default). There is no heap: no "
    "brk, no mmap, no malloc. Put working storage in `.bss` (zero-initialised); up to 64 MiB of `.bss` is "
    "fine. Code is not writable."
)


def build_prompt(previous: tuple[str, str] | None = None) -> str:
    parts = [
        "Write the complete program `edit`: a static Linux executable for AArch64 (ARMv8-A, little-endian), "
        "written in assembly. There is no C library, no C runtime and no helper code: you write the entry "
        "point `_start`, every function, every table and string, and make the system calls yourself. The "
        "program runs in a real terminal (a pseudo-terminal) and is tested by sending keystrokes and checking "
        "the files it saves and that it restores the terminal.",
        ENTRY,
        SYSCALLS,
        MEMORY,
        format_rules("aarch64"),
        "Specification:\n" + SPEC.strip(),
    ]
    if previous is not None:
        code, failure = previous
        parts.append(
            "Your previous attempt was:\n<previous>\n" + code.rstrip() + "\n</previous>\n"
            "It failed:\n<failure>\n" + failure.rstrip() + "\n</failure>\n"
            "Write a corrected, complete version. Same output format; output only the code."
        )
    return "\n\n".join(parts) + "\n"
