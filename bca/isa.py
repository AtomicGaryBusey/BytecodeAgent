"""Per-ISA constants shared by every pipeline: assembler, disassembler, emulator, ABI."""

from dataclasses import dataclass

import capstone
import keystone
import unicorn
from unicorn import arm64_const as a64
from unicorn import x86_const as x86


@dataclass(frozen=True)
class Isa:
    name: str
    ks_arch: int
    ks_mode: int
    cs_arch: int
    cs_mode: int
    uc_arch: int
    uc_mode: int
    pc: int
    sp: int
    ret: int
    args: tuple[int, ...]
    # Registers reported by the step tracer, as (name, unicorn id).
    trace_regs: tuple[tuple[str, int], ...]

    def assembler(self) -> keystone.Ks:
        """The shared, resolver-free keystone handle for this ISA. Do not mutate it.

        keystone 0.9.2 bug: once any handle that had `sym_resolver` set is freed, every
        handle created *afterwards* parses plain decimals as hex (`1000` -> 0x1000).
        Handles created earlier are unaffected, so these are built at import time.
        """
        return _PLAIN_KS[self.name]

    def new_assembler(self) -> keystone.Ks:
        """A fresh keystone handle. If you set `sym_resolver` on it, never free it."""
        return keystone.Ks(self.ks_arch, self.ks_mode)

    def disassembler(self) -> capstone.Cs:
        return capstone.Cs(self.cs_arch, self.cs_mode)


X86_64 = Isa(
    name="x86_64",
    ks_arch=keystone.KS_ARCH_X86,
    ks_mode=keystone.KS_MODE_64,
    cs_arch=capstone.CS_ARCH_X86,
    cs_mode=capstone.CS_MODE_64,
    uc_arch=unicorn.UC_ARCH_X86,
    uc_mode=unicorn.UC_MODE_64,
    pc=x86.UC_X86_REG_RIP,
    sp=x86.UC_X86_REG_RSP,
    ret=x86.UC_X86_REG_RAX,
    # System V AMD64 integer argument registers.
    args=(
        x86.UC_X86_REG_RDI,
        x86.UC_X86_REG_RSI,
        x86.UC_X86_REG_RDX,
        x86.UC_X86_REG_RCX,
        x86.UC_X86_REG_R8,
        x86.UC_X86_REG_R9,
    ),
    trace_regs=tuple(
        (n, getattr(x86, f"UC_X86_REG_{n.upper()}"))
        for n in (
            "rax rbx rcx rdx rsi rdi rbp rsp r8 r9 r10 r11 r12 r13 r14 r15 eflags".split()
        )
    ),
)

AARCH64 = Isa(
    name="aarch64",
    ks_arch=keystone.KS_ARCH_ARM64,
    ks_mode=keystone.KS_MODE_LITTLE_ENDIAN,
    cs_arch=capstone.CS_ARCH_ARM64,
    cs_mode=capstone.CS_MODE_ARM,
    uc_arch=unicorn.UC_ARCH_ARM64,
    uc_mode=unicorn.UC_MODE_ARM,
    pc=a64.UC_ARM64_REG_PC,
    sp=a64.UC_ARM64_REG_SP,
    ret=a64.UC_ARM64_REG_X0,
    # AAPCS64 integer argument registers.
    args=tuple(getattr(a64, f"UC_ARM64_REG_X{i}") for i in range(8)),
    trace_regs=tuple((f"x{i}", getattr(a64, f"UC_ARM64_REG_X{i}")) for i in range(31))
    + (("sp", a64.UC_ARM64_REG_SP), ("nzcv", a64.UC_ARM64_REG_NZCV)),
)

ISAS = {isa.name: isa for isa in (X86_64, AARCH64)}
_PLAIN_KS = {isa.name: isa.new_assembler() for isa in ISAS.values()}
