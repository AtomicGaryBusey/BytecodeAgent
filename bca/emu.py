"""Run raw machine code as a function call under Unicorn, optionally tracing each step.

The harness is deliberately hostile to code that only works by accident:

* Every register that is not an argument starts with deterministic pseudo-random garbage
  (seedable, different per run), as do the flags, the SIMD registers, the stack and the part
  of the data region not covered by `data`. Code that reads an uninitialised register or
  memory gets junk instead of a convenient zero.
* On return the calling convention is checked (`abi=True`, the default): callee-saved
  registers must hold their entry values (x86-64 SysV: rbx, rbp, r12-r15; AArch64 AAPCS64:
  x19-x29 and d8-d15), the stack pointer must be back at its entry value (+8 on x86-64 once
  `ret` popped the return address), the x86 direction flag must be clear, and the caller's
  frame (the return-address slot and everything above it) must not be written. A breach
  raises `ABIViolation`, which names the register or address. Pass `abi=False` for
  deliberately non-ABI snippets.
* `guard=(start, end)` (offsets into the data region) makes any write to the data region
  outside that window raise `OutOfBoundsWrite`.

Tracing (`trace=True` / `on_step`) keeps the old all-zero initial state by default so step
deltas are reproducible and match `bca.stream.LiveRunner`; pass `garbage=True` to override.

Untraced runs reuse one pooled Unicorn per ISA (state is fully reset on every call) and
count instructions in C via `emu_start(count=...)` rather than a Python hook, so
`RunResult.insn_count` is only filled in when `trace`, `on_step` or `count_insns` is set.
"""

import random
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import unicorn
from unicorn import arm64_const as a64
from unicorn import x86_const as x86

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
# Stack pointer before the (emulated) call instruction; x86 then pushes the return address.
STACK_TOP = STACK_BASE + STACK_SIZE - 0x100

_PROT = {"r": unicorn.UC_PROT_READ, "w": unicorn.UC_PROT_WRITE, "x": unicorn.UC_PROT_EXEC}


@dataclass
class Step:
    pc: int
    changed: dict[str, int]


@dataclass
class RunResult:
    ret: int
    data: bytes
    steps: list[Step] = field(default_factory=list)
    insn_count: int = 0  # only counted with trace / on_step / count_insns


class EmulationError(RuntimeError):
    pass


class ABIViolation(EmulationError):
    """The code returned (or tried to) in a way the calling convention forbids."""

    def __init__(self, message: str, register: str | None = None, address: int | None = None):
        super().__init__(message)
        self.register = register
        self.address = address


class OutOfBoundsWrite(EmulationError):
    """The code wrote to the data region outside the window it was allowed to modify."""

    def __init__(self, message: str, address: int):
        super().__init__(message)
        self.address = address


# -- per-ISA register sets -------------------------------------------------------------------


@dataclass(frozen=True)
class _Abi:
    gprs: tuple[tuple[str, int], ...]  # general registers to fill with garbage (not sp/pc)
    vecs: tuple[int, ...]  # SIMD registers (128-bit) to fill with garbage
    callee_saved: tuple[tuple[str, int], ...]
    callee_saved_mask: int  # bits of each callee-saved register that must be preserved
    sp_delta: int  # final sp - entry sp after a proper return
    flags: int
    link: int | None  # register that receives the return address, if any


_X86_ABI = _Abi(
    gprs=tuple(
        (n, getattr(x86, f"UC_X86_REG_{n.upper()}"))
        for n in "rax rbx rcx rdx rsi rdi rbp r8 r9 r10 r11 r12 r13 r14 r15".split()
    ),
    vecs=tuple(getattr(x86, f"UC_X86_REG_XMM{i}") for i in range(16)),
    callee_saved=tuple(
        (n, getattr(x86, f"UC_X86_REG_{n.upper()}")) for n in "rbx rbp r12 r13 r14 r15".split()
    ),
    callee_saved_mask=MASK64,
    sp_delta=8,
    flags=x86.UC_X86_REG_EFLAGS,
    link=None,
)
_A64_ABI = _Abi(
    gprs=tuple((f"x{i}", getattr(a64, f"UC_ARM64_REG_X{i}")) for i in range(30)),
    vecs=tuple(getattr(a64, f"UC_ARM64_REG_Q{i}") for i in range(32)),
    # AAPCS64: x19-x28, the frame pointer x29, and the low 64 bits of v8-v15 (d8-d15).
    callee_saved=tuple((f"x{i}", getattr(a64, f"UC_ARM64_REG_X{i}")) for i in range(19, 30))
    + tuple((f"d{i}", getattr(a64, f"UC_ARM64_REG_Q{i}")) for i in range(8, 16)),
    callee_saved_mask=MASK64,
    sp_delta=0,
    flags=a64.UC_ARM64_REG_NZCV,
    link=a64.UC_ARM64_REG_X30,
)


def _abi(isa: Isa) -> _Abi:
    return _X86_ABI if isa is X86_64 else _A64_ABI


# x86 EFLAGS bits a function may find in any state: CF PF AF ZF SF OF (DF must be clear).
_X86_STATUS_FLAGS = (1 << 0) | (1 << 2) | (1 << 4) | (1 << 6) | (1 << 7) | (1 << 11)
_X86_DF = 1 << 10

# Fixed pseudo-random fill for the stack and data regions (generated once per process).
_FILL_CACHE: dict[str, bytes] = {}


def _fill(kind: str, size: int) -> bytes:
    if kind not in _FILL_CACHE:
        _FILL_CACHE[kind] = random.Random(f"bca-{kind}-fill").randbytes(size)
    return _FILL_CACHE[kind]


def _default_seed(args: Iterable[int]) -> int:
    seed = 0x9E3779B97F4A7C15
    for a in args:
        seed = (seed * 0x100000001B3 ^ (a & MASK64)) & MASK64
    return seed


# -- machines -------------------------------------------------------------------------------


class _Machine:
    """A mapped Unicorn plus its pristine CPU context; reset before every run."""

    def __init__(self, isa: Isa, code_base: int, writable_code: bool, segments=()):
        uc = unicorn.Uc(isa.uc_arch, isa.uc_mode)
        code_prot = unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC
        if writable_code:
            code_prot |= unicorn.UC_PROT_WRITE
        uc.mem_map(code_base, CODE_SIZE, code_prot)
        uc.mem_map(DATA_BASE, DATA_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        uc.mem_map(STACK_BASE, STACK_SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        uc.mem_map(RETURN_SENTINEL, 0x1000, unicorn.UC_PROT_EXEC)
        for address, content, prot in segments:
            size = max(0x1000, (len(content) + 0xFFF) & ~0xFFF)
            uc.mem_map(address, size, prot)
            if content:
                uc.mem_write(address, bytes(content))
        self.uc = uc
        self.isa = isa
        self.code_base = code_base
        self.code = b""
        self.context = uc.context_save()

    def load_code(self, code: bytes) -> None:
        if code == self.code:
            return
        old = len(self.code)
        # Zero-pad over any longer previous code so falling off the end behaves as on a
        # fresh machine, then drop translated blocks that covered the old bytes.
        self.uc.mem_write(self.code_base, code + bytes(max(0, old - len(code))))
        if old:
            self.uc.ctl_remove_cache(self.code_base, self.code_base + max(old, len(code), 1))
        self.code = code


_POOL = threading.local()


def _pooled(isa: Isa) -> _Machine:
    pool = getattr(_POOL, "machines", None)
    if pool is None:
        pool = _POOL.machines = {}
    m = pool.get(isa.name)
    if m is None:
        m = pool[isa.name] = _Machine(isa, CODE_BASE, False)
    return m


def _discard_pooled(isa: Isa) -> None:
    getattr(_POOL, "machines", {}).pop(isa.name, None)


def _parse_segments(segments) -> list[tuple[int, bytes, int]]:
    out = []
    for address, content, prot in segments:
        if isinstance(prot, str):
            prot = sum(_PROT[c] for c in set(prot.lower()) if c in _PROT)
        if address % 0x1000:
            raise ValueError(f"segment address {address:#x} is not page aligned")
        out.append((address, bytes(content), prot))
    return out


def _check_layout(code_base: int, code: bytes, data: bytes, data_offset: int, segments) -> None:
    if code_base % 0x1000:
        raise ValueError(f"code_base {code_base:#x} is not page aligned")
    if len(code) > CODE_SIZE or data_offset < 0 or data_offset + len(data) > DATA_SIZE:
        raise ValueError("code or data too large")
    fixed = [(DATA_BASE, DATA_SIZE), (STACK_BASE, STACK_SIZE), (RETURN_SENTINEL, 0x1000)]
    regions = [(code_base, CODE_SIZE)] + [
        (a, max(0x1000, (len(c) + 0xFFF) & ~0xFFF)) for a, c, _ in segments
    ]
    placed = list(fixed)
    for start, size in regions:
        for s, n in placed:
            if start < s + n and s < start + size:
                raise ValueError(f"region at {start:#x} overlaps the region at {s:#x}")
        placed.append((start, size))


# -- running --------------------------------------------------------------------------------


def run_function(
    isa: Isa,
    code: bytes,
    args: list[int] = (),
    *,
    data: bytes = b"",
    max_insns: int = 1_000_000,
    trace: bool = False,
    on_step: Callable[[Step], None] | None = None,
    abi: bool = True,
    garbage: bool | None = None,
    seed: int | None = None,
    data_offset: int = 0,
    guard: tuple[int, int] | None = None,
    count_insns: bool = False,
    code_base: int = CODE_BASE,
    writable_code: bool = False,
    segments: Iterable[tuple[int, bytes, int | str]] = (),
) -> RunResult:
    """Call `code` at `code_base` with integer `args`; `data` is loaded at DATA_BASE + data_offset.

    Pointer arguments should point into the data region. Returns the return register
    (unsigned 64-bit) and the final contents of `data`'s bytes (trimmed to len(data)).

    abi:        check callee-saved registers, the final stack pointer, x86 DF and writes to
                the caller's frame on return; raise ABIViolation on a breach.
    garbage:    fill non-argument registers, flags, SIMD registers, the stack and the rest
                of the data region with pseudo-random junk (default: on unless tracing).
    seed:       seed for the register garbage (default: derived from `args`).
    guard:      (start, end) offsets into the data region the code may write; any other
                data-region write raises OutOfBoundsWrite.
    count_insns: fill RunResult.insn_count (installs a per-instruction hook: slower).
    code_base / writable_code / segments: alternative load address, RWX code mapping, and
                extra mappings [(page-aligned address, initial bytes, prot "rwx" or int)].
    """
    if len(args) > len(isa.args):
        raise ValueError(f"{isa.name} supports {len(isa.args)} register args")
    code, data = bytes(code), bytes(data)
    segments = _parse_segments(segments)
    _check_layout(code_base, code, data, data_offset, segments)
    tracing = bool(trace or on_step)
    if garbage is None:
        garbage = not tracing
    spec = _abi(isa)

    pooled = not (tracing or count_insns or segments or writable_code or code_base != CODE_BASE)
    m = _pooled(isa) if pooled else _Machine(isa, code_base, writable_code, segments)
    uc = m.uc
    uc.context_restore(m.context)
    m.load_code(code)

    # Memory: stack and data are rewritten in full so no state leaks between pooled runs.
    uc.mem_write(STACK_BASE, _fill("stack", STACK_SIZE) if garbage else bytes(STACK_SIZE))
    uc.mem_write(DATA_BASE, _fill("data", DATA_SIZE) if garbage else bytes(DATA_SIZE))
    if data:
        uc.mem_write(DATA_BASE + data_offset, data)

    # Registers.
    rng = random.Random(_default_seed(args) if seed is None else seed)
    if garbage:
        for _, reg in spec.gprs:
            uc.reg_write(reg, rng.getrandbits(64))
        for reg in spec.vecs:
            uc.reg_write(reg, rng.getrandbits(128))
        if isa is X86_64:
            uc.reg_write(spec.flags, 0x2 | (rng.getrandbits(12) & _X86_STATUS_FLAGS))
        else:
            uc.reg_write(spec.flags, rng.getrandbits(4) << 28)
    for reg, value in zip(isa.args, args, strict=False):
        uc.reg_write(reg, value & MASK64)

    sp = STACK_TOP
    if isa is X86_64:
        # Emulate the caller's `call`: push the return address; rsp ≡ 8 (mod 16) on entry.
        sp -= 8
        uc.mem_write(sp, RETURN_SENTINEL.to_bytes(8, "little"))
    else:
        uc.reg_write(spec.link, RETURN_SENTINEL)
    uc.reg_write(isa.sp, sp)
    saved = {name: uc.reg_read(reg) & spec.callee_saved_mask for name, reg in spec.callee_saved}

    result = RunResult(ret=0, data=b"")
    hooks = []
    violations: list[EmulationError] = []

    def forbid(lo: int, hi: int, make_error: Callable[[int], EmulationError]) -> None:
        """Stop with make_error(address) on any write overlapping [lo, hi)."""
        if lo >= hi:
            return

        def hook(uc_, _access, address, size, _value, _user):
            if address + size > lo and address < hi:
                if not violations:
                    violations.append(make_error(max(address, lo)))
                uc_.emu_stop()

        # Widen the watched range downwards so a wide store straddling `lo` is seen too.
        hooks.append(uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, hook, begin=max(0, lo - 63), end=hi - 1))

    if abi:
        # The return-address slot (x86) and everything above the entry sp is the caller's.
        forbid(sp, STACK_BASE + STACK_SIZE, lambda a: ABIViolation(
            f"{isa.name}: wrote the caller's stack frame at {a:#x} (entry sp={sp:#x})",
            address=a))  # fmt: skip
    if guard is not None:
        lo, hi = guard
        allowed = f"[{DATA_BASE + lo:#x}, {DATA_BASE + hi:#x})"
        for begin, end in ((DATA_BASE, DATA_BASE + lo), (DATA_BASE + hi, DATA_BASE + DATA_SIZE)):
            forbid(begin, end, lambda a: OutOfBoundsWrite(
                f"{isa.name}: out-of-bounds write at {a:#x} (allowed {allowed})", a))  # fmt: skip

    if tracing:
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

        hooks.append(uc.hook_add(unicorn.UC_HOOK_CODE, hook))
    if tracing or count_insns:

        def count(uc_, address, size, _user):
            result.insn_count += 1

        hooks.append(uc.hook_add(unicorn.UC_HOOK_CODE, count))

    try:
        try:
            uc.emu_start(code_base, RETURN_SENTINEL, count=max_insns)
        except unicorn.UcError as exc:
            if pooled:
                _discard_pooled(isa)  # don't trust a machine that faulted mid-instruction
            pc = uc.reg_read(isa.pc)
            raise EmulationError(f"{isa.name}: {exc} at pc={pc:#x}") from exc
        if violations:
            raise violations[0]
        pc = uc.reg_read(isa.pc)
        if pc != RETURN_SENTINEL:
            raise EmulationError(f"{isa.name}: did not return within {max_insns} instructions")
        if tracing and result.steps:
            now = {name: uc.reg_read(reg) for name, reg in isa.trace_regs}
            result.steps[-1].changed = {k: v for k, v in now.items() if prev[k] != v}
            if on_step:
                on_step(result.steps[-1])
        if abi:
            _check_abi(isa, spec, uc, sp, saved)
        result.ret = uc.reg_read(isa.ret) & MASK64
        result.data = bytes(uc.mem_read(DATA_BASE + data_offset, len(data))) if data else b""
    finally:
        if pooled:
            for h in hooks:
                uc.hook_del(h)
    return result


def _check_abi(isa: Isa, spec: _Abi, uc, entry_sp: int, saved: dict[str, int]) -> None:
    final_sp = uc.reg_read(isa.sp)
    want_sp = entry_sp + spec.sp_delta
    if final_sp != want_sp:
        sp_name = "rsp" if isa is X86_64 else "sp"
        raise ABIViolation(
            f"{isa.name}: {sp_name} not restored on return: {final_sp:#x}, expected "
            f"{want_sp:#x} ({final_sp - want_sp:+d})",
            register=sp_name,
        )
    for name, reg in spec.callee_saved:
        now = uc.reg_read(reg) & spec.callee_saved_mask
        if now != saved[name]:
            raise ABIViolation(
                f"{isa.name}: callee-saved register {name} clobbered: "
                f"{saved[name]:#x} -> {now:#x}",
                register=name,
            )
    if isa is X86_64 and uc.reg_read(spec.flags) & _X86_DF:
        raise ABIViolation(f"{isa.name}: direction flag (eflags.DF) set on return", register="eflags")


__all__ = [
    "ABIViolation",
    "CODE_BASE",
    "CODE_SIZE",
    "DATA_BASE",
    "DATA_SIZE",
    "EmulationError",
    "MASK64",
    "OutOfBoundsWrite",
    "RETURN_SENTINEL",
    "RunResult",
    "STACK_BASE",
    "STACK_SIZE",
    "STACK_TOP",
    "Step",
    "run_function",
]
