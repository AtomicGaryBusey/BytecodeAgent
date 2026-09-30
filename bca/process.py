"""Run a linked program image as a Linux process under Unicorn (no kernel, no libc, no host).

Memory: the image's segments (text r-x, rodata r--, data rw-, bss rw- zeroed) and a 1 MiB stack
ending at STACK_TOP; nothing else. The 64 MiB below the stack are never mapped: an access there
is reported as `stack_overflow` (the guard region).

Entry state (as the kernel leaves it for a static binary): every register zero except the
stack pointer, which is 16-byte aligned and points at argc = 0, followed by an empty argv
(NULL), an empty envp (NULL) and an empty auxv (AT_NULL).

System calls (x86-64 `syscall`: number in rax, args rdi rsi rdx, result in rax, rcx/r11
clobbered as by the kernel; AArch64 `svc #0`: number in x8, args x0-x2, result in x0):

    read(0, buf, n)   x86-64 0 / AArch64 63   copies min(n, bytes left[, read_chunk]) of stdin;
                                             0 at end of input
    write(1|2, buf, n) x86-64 1 / AArch64 64  appends to stdout / stderr; returns n
    exit, exit_group  x86-64 60, 231 / AArch64 93, 94   status = low 8 bits of the argument

Any other system call or file descriptor is a `bad_syscall` fault. Buffers are checked against
the mapped regions: a read into memory that is not writable, or a write from memory that is not
mapped, is an `oob` fault (`write_to_text` when read() targets the code), where Linux would have
returned -EFAULT; a correct program never does either.

Faults (ProcFault.kind): crash (invalid instruction, trap, jump to non-executable memory),
bad_syscall, no_exit (instruction budget exhausted, or ran off the end of the code into the
trap fill), oob (load/store to unmapped or read-only memory), write_to_text, stack_overflow,
output_limit (stdout or stderr beyond stdout_cap). Each carries the pc of the offending
instruction.

Runs are deterministic: a fresh Unicorn per run, no clocks, no randomness. Instructions are
counted exactly by a native (machine-code) Unicorn code hook, and the budget is enforced by
Unicorn's own instruction count, so neither costs a Python call per instruction.
"""

import ctypes
import mmap
import os
import platform
import threading
from dataclasses import dataclass

import unicorn
from unicorn import arm64_const as a64
from unicorn import x86_const as x86

from bca.image import PAGE, Image, _page_up
from bca.isa import ISAS

STACK_SIZE = 1 << 20
STACK_TOP = 0x7FFFFFFF0000
STACK_LO = STACK_TOP - STACK_SIZE
GUARD_SPAN = 64 << 20  # unmapped region below the stack whose accesses mean stack overflow
ENTRY_SP = STACK_TOP - 0x100  # argc at [sp]; argv, envp, auxv terminators above it (zeros)
_UNTIL = 1  # emu_start's stop address: odd, never a valid target on either ISA

SYSCALLS = {
    "x86_64": {0: "read", 1: "write", 60: "exit", 231: "exit_group"},
    "aarch64": {63: "read", 64: "write", 93: "exit", 94: "exit_group"},
}
FAULT_KINDS = ("crash", "bad_syscall", "no_exit", "oob", "write_to_text", "stack_overflow",
               "output_limit")
_PROT = {"rx": unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC, "r": unicorn.UC_PROT_READ,
         "rw": unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE}
_MASK64 = (1 << 64) - 1


class ProcFault(Exception):
    def __init__(self, kind: str, message: str, pc: int | None = None, address: int | None = None):
        assert kind in FAULT_KINDS, kind
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.pc = pc
        self.address = address

    def __repr__(self) -> str:
        pc = f", pc={self.pc:#x}" if self.pc is not None else ""
        return f"ProcFault({self.kind!r}, {self.message!r}{pc})"


@dataclass
class ProcResult:
    stdout: bytes
    stderr: bytes
    status: int | None  # exit status (0-255); None when the program did not exit
    insns: int
    fault: ProcFault | None
    syscalls: int


# -- exact instruction counting without a Python call per instruction ------------------------
# A UC_HOOK_CODE callback `void cb(uc_engine *, uint64_t, uint32_t, void *user)` that does
# `++*(uint64_t *)user`, as host machine code in an executable page.
_COUNTER_CODE = {
    "aarch64": bytes.fromhex("680040f9" "08050091" "680000f9" "c0035fd6"),  # ldr/add/str/ret via x3
    "x86_64": bytes.fromhex("48ff01c3"),  # inc qword ptr [rcx]; ret
}
_native: list = []  # [mmap, address] once built; [None] when unavailable
_native_lock = threading.Lock()


def _native_counter() -> int | None:
    with _native_lock:
        if not _native:
            host = platform.machine().lower()
            host = {"amd64": "x86_64", "arm64": "aarch64"}.get(host, host)
            code = _COUNTER_CODE.get(host)
            if code is None or os.environ.get("BCA_PROCESS_PY_COUNTER"):
                _native.append(None)
            else:
                try:
                    page = mmap.mmap(-1, PAGE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
                    page.write(code)
                    _native[:] = [page, ctypes.addressof(ctypes.c_char.from_buffer(page))]
                except (OSError, ValueError, TypeError):
                    _native[:] = [None]
        return _native[1] if _native[0] is not None else None


def _add_counter(uc: unicorn.Uc, cell: ctypes.c_uint64) -> list[int]:
    """Count executed instructions into `cell`; returns the Python hook handles added."""
    fn = _native_counter()
    if fn is not None:
        from unicorn.unicorn_py3 import unicorn as _u

        handle = _u.uc_hook_h()
        status = _u.uclib.uc_hook_add(uc._uch, ctypes.byref(handle), unicorn.UC_HOOK_CODE,
                                      ctypes.c_void_p(fn), ctypes.cast(ctypes.byref(cell), ctypes.c_void_p),
                                      ctypes.c_uint64(1), ctypes.c_uint64(0))
        if status == unicorn.UC_ERR_OK:
            return []

    def count(_uc, _addr, _size, _user):  # portable fallback: ~1-2 M instructions/s
        cell.value += 1

    return [uc.hook_add(unicorn.UC_HOOK_CODE, count)]


# -- the process ---------------------------------------------------------------------------

def run_program(image: Image, stdin: bytes, *, max_insns: int = 200_000_000,
                stdout_cap: int = 1 << 20, read_chunk: int | None = None) -> ProcResult:
    isa = ISAS[image.isa]
    x86_64 = image.isa == "x86_64"
    if read_chunk is not None and read_chunk < 1:
        raise ValueError("read_chunk must be >= 1")
    stdin = bytes(stdin)
    uc = unicorn.Uc(isa.uc_arch, isa.uc_mode)

    # Regions: (start, end, perms), for buffer checks and fault classification.
    regions: list[tuple[int, int, str]] = []
    for addr, data, perms in image.segments:
        size = _page_up(max(len(data), 1))
        uc.mem_map(addr, size, _PROT[perms])
        uc.mem_write(addr, bytes(data))
        regions.append((addr, addr + size, perms))
    if image.bss[1]:
        size = _page_up(image.bss[1])
        uc.mem_map(image.bss[0], size, _PROT["rw"])
        regions.append((image.bss[0], image.bss[0] + size, "rw"))
    uc.mem_map(STACK_LO, STACK_SIZE, _PROT["rw"])
    regions.append((STACK_LO, STACK_TOP, "rw"))
    regions.sort()
    text_lo, text_hi = image.segments[0][0], image.segments[0][0] + _page_up(len(image.segments[0][1]))
    text_end = image.text_end or text_hi
    uc.reg_write(isa.sp, ENTRY_SP)

    hooks: list[int] = []  # Python hooks, deleted after the run (they close over `uc`)
    stdout, stderr = bytearray(), bytearray()
    state = {"pos": 0, "status": None, "fault": None, "syscalls": 0, "mem": None}

    def region_of(addr: int):
        for r in regions:
            if r[0] <= addr < r[1]:
                return r
        return None

    def check_range(addr: int, n: int, need_write: bool) -> str | None:
        """None if [addr, addr+n) is mapped (and writable if need_write), else the reason."""
        cur, end = addr, addr + n
        if end > _MASK64 + 1:
            return f"buffer {addr:#x}+{n} wraps around the address space"
        while cur < end:
            r = region_of(cur)
            if r is None:
                return f"address {cur:#x} is not mapped"
            if need_write and "w" not in r[2]:
                return f"address {cur:#x} is not writable ({_describe(r)})"
            cur = r[1]
        return None

    def _describe(r) -> str:
        if r[0] == text_lo:
            return "program code"
        if r[2] == "r":
            return "read-only data"
        return "data"

    def fault(kind: str, message: str, pc: int, address: int | None = None) -> None:
        state["fault"] = ProcFault(kind, message, pc, address)
        uc.emu_stop()

    def syscall(pc: int) -> None:
        state["syscalls"] += 1
        if x86_64:
            nr = uc.reg_read(x86.UC_X86_REG_RAX)
            a0, a1, a2 = (uc.reg_read(r) for r in isa.args[:3])
        else:
            nr = uc.reg_read(a64.UC_ARM64_REG_X8)
            a0, a1, a2 = (uc.reg_read(r) for r in isa.args[:3])
        name = SYSCALLS[image.isa].get(nr)
        ret = 0
        if name in ("exit", "exit_group"):
            state["status"] = a0 & 0xFF
            uc.emu_stop()
            return
        if name == "read":
            if a0 != 0:
                return fault("bad_syscall", f"read from file descriptor {a0} (only 0, stdin, "
                             "can be read)", pc)
            k = min(a2, len(stdin) - state["pos"], read_chunk or a2)
            if k:
                why = check_range(a1, k, need_write=True)
                if why:
                    r = region_of(a1)
                    kind = "write_to_text" if r and r[0] == text_lo else "oob"
                    return fault(kind, f"read(0, {a1:#x}, {a2}): {why}", pc, a1)
                uc.mem_write(a1, stdin[state["pos"]:state["pos"] + k])
                state["pos"] += k
            ret = k
        elif name == "write":
            if a0 not in (1, 2):
                return fault("bad_syscall", f"write to file descriptor {a0} (only 1, stdout, and "
                             "2, stderr, can be written)", pc)
            if a2:
                why = check_range(a1, a2, need_write=False)
                if why:
                    return fault("oob", f"write({a0}, {a1:#x}, {a2}): {why}", pc, a1)
                out = stdout if a0 == 1 else stderr
                room = stdout_cap - len(out)
                out += uc.mem_read(a1, min(a2, max(room, 0)))
                if a2 > room:
                    return fault("output_limit", f"{'stdout' if a0 == 1 else 'stderr'} exceeds "
                                 f"the limit of {stdout_cap} bytes", pc)
            ret = a2
        else:
            return fault("bad_syscall", f"unsupported system call {nr} (allowed: " + ", ".join(
                f"{v}={k}" for k, v in SYSCALLS[image.isa].items()) + ")", pc)
        if x86_64:  # the kernel returns in rax and clobbers rcx (return rip) and r11 (rflags)
            uc.reg_write(x86.UC_X86_REG_RAX, ret & _MASK64)
            uc.reg_write(x86.UC_X86_REG_RCX, pc + 2)
            uc.reg_write(x86.UC_X86_REG_R11, uc.reg_read(x86.UC_X86_REG_EFLAGS))
        else:
            uc.reg_write(a64.UC_ARM64_REG_X0, ret & _MASK64)

    def in_trap_fill(addr: int) -> bool:
        return text_end <= addr < text_hi

    def ran_off(pc: int) -> None:
        fault("no_exit", f"ran off the end of the code (reached {pc:#x}, past the last "
              f"instruction at {text_end:#x}) without calling exit", pc)

    if x86_64:
        def on_syscall(uc_, _user):
            syscall(uc_.reg_read(x86.UC_X86_REG_RIP))

        def on_other_insn(uc_, _user):
            pc = uc_.reg_read(x86.UC_X86_REG_RIP)
            fault("bad_syscall", "sysenter is not a supported system call instruction", pc)

        hooks.append(uc.hook_add(unicorn.UC_HOOK_INSN, on_syscall, None, 1, 0, x86.UC_X86_INS_SYSCALL))
        hooks.append(uc.hook_add(unicorn.UC_HOOK_INSN, on_other_insn, None, 1, 0,
                                 x86.UC_X86_INS_SYSENTER))

        def on_intr(uc_, intno, _user):
            pc = uc_.reg_read(x86.UC_X86_REG_RIP)
            if intno == 0x80:
                return fault("bad_syscall", "int 0x80 (32-bit system calls) is not supported; "
                             "use syscall", pc - 2)
            if intno == 3 and in_trap_fill(pc - 1):
                return ran_off(pc - 1)
            names = {0: "divide error", 1: "debug trap", 3: "breakpoint (int3)", 4: "overflow",
                     5: "bound range", 6: "invalid opcode", 13: "general protection fault",
                     14: "page fault", 17: "alignment check"}
            fault("crash", f"CPU exception {intno} ({names.get(intno, 'interrupt')}) at {pc:#x}", pc)
    else:
        def on_intr(uc_, intno, _user):
            pc = uc_.reg_read(a64.UC_ARM64_REG_PC)
            if intno == 2:  # EXCP_SWI: svc; pc is past the instruction
                return syscall(pc - 4)
            if in_trap_fill(pc):
                return ran_off(pc)
            names = {1: "undefined instruction", 7: "breakpoint (brk)", 3: "prefetch abort",
                     4: "data abort"}
            fault("crash", f"CPU exception {intno} ({names.get(intno, 'exception')}) at {pc:#x}", pc)

    hooks.append(uc.hook_add(unicorn.UC_HOOK_INTR, on_intr))

    def on_invalid(_uc, access, address, size, _value, _user):
        state["mem"] = (access, address, size)
        return False

    hooks.append(uc.hook_add(unicorn.UC_HOOK_MEM_INVALID, on_invalid))
    counter = ctypes.c_uint64(0)
    hooks += _add_counter(uc, counter)

    error = None
    try:
        uc.emu_start(image.entry, _UNTIL, count=max_insns)
    except unicorn.UcError as exc:
        error = exc
    pc = uc.reg_read(isa.pc)
    for h in hooks:  # break the uc -> callback -> closure -> uc cycle: free the engine now
        uc.hook_del(h)
    insns = min(counter.value, max_insns)
    f = state["fault"]
    if f is None and state["status"] is None:
        if error is not None:
            f = _classify(error, state["mem"], pc, regions, text_lo, text_end, in_trap_fill)
        elif pc == _UNTIL:
            f = ProcFault("crash", f"jumped to unmapped address {pc:#x}", pc)
        elif counter.value >= max_insns:
            f = ProcFault("no_exit", f"did not exit within {max_insns} instructions "
                          f"(stopped at pc={pc:#x})", pc)
        else:
            f = ProcFault("crash", f"execution stopped at {pc:#x} (privileged instruction such "
                          "as hlt)", pc)
    return ProcResult(stdout=bytes(stdout), stderr=bytes(stderr), status=state["status"],
                      insns=insns, fault=f, syscalls=state["syscalls"])


_READS = {unicorn.UC_MEM_READ_UNMAPPED, unicorn.UC_MEM_READ_PROT}
_WRITES = {unicorn.UC_MEM_WRITE_UNMAPPED, unicorn.UC_MEM_WRITE_PROT}
_FETCHES = {unicorn.UC_MEM_FETCH_UNMAPPED, unicorn.UC_MEM_FETCH_PROT}


def _classify(error: unicorn.UcError, mem, pc: int, regions, text_lo: int, text_end: int,
              in_trap_fill) -> ProcFault:
    if mem is not None:
        access, address, size = mem
        region = next((r for r in regions if r[0] <= address < r[1]), None)
        if access in _FETCHES:
            if in_trap_fill(address):
                return ProcFault("no_exit", f"ran off the end of the code (reached {address:#x}) "
                                 "without calling exit", address, address)
            what = ("unmapped memory" if region is None else
                    "read-only data" if region[2] == "r" else "data (not executable)")
            return ProcFault("crash", f"jumped to {address:#x}: {what}", address, address)
        verb = "write" if access in _WRITES else "read"
        if STACK_LO - GUARD_SPAN <= address < STACK_LO:
            return ProcFault("stack_overflow", f"stack overflow: {size}-byte {verb} at {address:#x}, "
                             f"below the {STACK_SIZE >> 20} MiB stack, pc={pc:#x}", pc, address)
        if access in _WRITES and region is not None and region[0] == text_lo:
            return ProcFault("write_to_text", f"write to program code at {address:#x}, pc={pc:#x}",
                             pc, address)
        where = ("unmapped memory" if region is None else
                 "read-only data" if region[2] == "r" else "program code")
        return ProcFault("oob", f"{size}-byte {verb} of {where} at {address:#x}, pc={pc:#x}", pc, address)
    if error.errno == unicorn.UC_ERR_INSN_INVALID:
        if in_trap_fill(pc):
            return ProcFault("no_exit", f"ran off the end of the code (reached {pc:#x}) without "
                             "calling exit", pc)
        return ProcFault("crash", f"invalid instruction at {pc:#x}", pc)
    return ProcFault("crash", f"{error} at pc={pc:#x}", pc)
