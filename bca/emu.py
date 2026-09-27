"""Run raw machine code as a function call under Unicorn, optionally tracing each step."""

from collections.abc import Callable
from dataclasses import dataclass, field

import unicorn

from bca.isa import X86_64, Isa

CODE_BASE = 0x100000
CODE_SIZE = 0x100000
DATA_BASE = 0x400000
DATA_SIZE = 0x100000
STACK_BASE = 0x800000
STACK_SIZE = 0x100000
# Execution stops when control returns here; the page is mapped but never executed.
RETURN_SENTINEL = 0xDEAD000
MASK64 = (1 << 64) - 1


@dataclass
class Step:
    pc: int
    changed: dict[str, int]


@dataclass
class RunResult:
    ret: int
    data: bytes
    steps: list[Step] = field(default_factory=list)
    insn_count: int = 0


class EmulationError(RuntimeError):
    pass


def run_function(
    isa: Isa,
    code: bytes,
    args: list[int] = (),
    *,
    data: bytes = b"",
    max_insns: int = 1_000_000,
    trace: bool = False,
    on_step: Callable[[Step], None] | None = None,
) -> RunResult:
    """Call `code` at CODE_BASE with integer `args`; `data` is loaded at DATA_BASE.

    Pointer arguments should point into DATA_BASE. Returns the return register (unsigned
    64-bit) and the final contents of the data region (trimmed to len(data)).
    """
    if len(args) > len(isa.args):
        raise ValueError(f"{isa.name} supports {len(isa.args)} register args")
    if len(code) > CODE_SIZE or len(data) > DATA_SIZE:
        raise ValueError("code or data too large")
    uc = unicorn.Uc(isa.uc_arch, isa.uc_mode)
    uc.mem_map(CODE_BASE, CODE_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
    uc.mem_map(DATA_BASE, DATA_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
    uc.mem_map(STACK_BASE, STACK_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
    uc.mem_map(RETURN_SENTINEL, 0x1000, unicorn.UC_PROT_EXEC)
    uc.mem_write(CODE_BASE, bytes(code))
    if data:
        uc.mem_write(DATA_BASE, bytes(data))
    for reg, value in zip(isa.args, args, strict=False):
        uc.reg_write(reg, value & MASK64)

    sp = STACK_BASE + STACK_SIZE - 0x100
    if isa is X86_64:
        # Emulate the caller's `call`: push the return address; rsp ≡ 8 (mod 16) on entry.
        sp -= 8
        uc.mem_write(sp, RETURN_SENTINEL.to_bytes(8, "little"))
    else:
        from unicorn.arm64_const import UC_ARM64_REG_LR

        uc.reg_write(UC_ARM64_REG_LR, RETURN_SENTINEL)
    uc.reg_write(isa.sp, sp)

    result = RunResult(ret=0, data=b"")
    if trace or on_step:
        prev = {name: uc.reg_read(reg) for name, reg in isa.trace_regs}

        def hook(uc_, address, size, _user):
            # Called before each instruction: report the registers the previous one changed.
            nonlocal prev
            now = {name: uc_.reg_read(reg) for name, reg in isa.trace_regs}
            if result.steps:
                result.steps[-1].changed = {k: v for k, v in now.items() if prev[k] != v}
                if on_step:
                    on_step(result.steps[-1])
            prev = now
            result.steps.append(Step(pc=address, changed={}))

        uc.hook_add(unicorn.UC_HOOK_CODE, hook)

    def count(uc_, address, size, _user):
        result.insn_count += 1

    uc.hook_add(unicorn.UC_HOOK_CODE, count)
    try:
        uc.emu_start(CODE_BASE, RETURN_SENTINEL, count=max_insns)
    except unicorn.UcError as exc:
        pc = uc.reg_read(isa.pc)
        raise EmulationError(f"{isa.name}: {exc} at pc={pc:#x}") from exc
    pc = uc.reg_read(isa.pc)
    if pc != RETURN_SENTINEL:
        raise EmulationError(f"{isa.name}: did not return within {max_insns} instructions")
    if result.steps and (trace or on_step):
        now = {name: uc.reg_read(reg) for name, reg in isa.trace_regs}
        result.steps[-1].changed = {k: v for k, v in now.items() if prev[k] != v}
        if on_step:
            on_step(result.steps[-1])
    result.ret = uc.reg_read(isa.ret) & MASK64
    result.data = bytes(uc.mem_read(DATA_BASE, len(data))) if data else b""
    return result
