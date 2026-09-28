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
* `guard=(start, end)` (offsets into the data region; or a list of such windows) makes any
  write to the data region outside the window(s) raise `OutOfBoundsWrite`; `read_guard`
  does the same for reads (`OutOfBoundsRead`).
* Arguments beyond the register arguments are passed on the stack per ABI (x86-64: the 7th
  at [rsp+8] on entry; AArch64: the 9th at [sp]); the callee may write its own argument
  slots but nothing else of the caller's frame.
* `aligned_entries=[addr, ...]` checks the call-site stack alignment whenever control enters
  one of those addresses (x86-64: rsp == 8 mod 16 and DF clear; AArch64: sp == 0 mod 16)
  and raises `MisalignedCall` otherwise. `hostile_callback(isa, f)` is a ready-made callee for
  function-pointer arguments: f(x) = 3x + 1 by default (see CALLBACK_FUNCTIONS for the others)
  that clobbers every caller-saved register, the flags and the stack below its sp (map all of
  them with `callback_segments(isa)` at CALLBACK_BASE; pass `callback_address(f)`).
* `strict_sse_alignment=True` (x86-64) faults legacy-SSE 16-byte memory operands that are
  not 16-byte aligned (hardware raises #GP; Unicorn does not) with `MisalignedAccess`.

Every EmulationError carries `category` (crash, abi, oob_write, oob_read,
misaligned_call, misaligned_access, hang) and, when known, `pc`: an address within the
offending instruction (for MisalignedCall: within the call instruction).

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
    """Emulation failed. `category` classifies the failure without matching message text;
    `pc` is an address within the offending instruction when known."""

    category = "crash"

    def __init__(self, message: str, *, pc: int | None = None):
        super().__init__(message)
        self.pc = pc


class ABIViolation(EmulationError):
    """The code returned (or tried to) in a way the calling convention forbids."""

    category = "abi"

    def __init__(self, message: str, register: str | None = None, address: int | None = None,
                 *, pc: int | None = None):
        super().__init__(message, pc=pc)
        self.register = register
        self.address = address


class MisalignedCall(ABIViolation):
    """Control entered a function (see `aligned_entries`) with a misaligned stack pointer."""

    category = "misaligned_call"

    def __init__(self, message: str, target: int, sp: int, return_address: int | None,
                 *, pc: int | None = None):
        super().__init__(message, register="sp", pc=pc)
        self.target = target
        self.sp = sp
        self.return_address = return_address


class OutOfBoundsWrite(EmulationError):
    """The code wrote to the data region outside the window it was allowed to modify."""

    category = "oob_write"

    def __init__(self, message: str, address: int, *, pc: int | None = None):
        super().__init__(message, pc=pc)
        self.address = address


class OutOfBoundsRead(EmulationError):
    """The code read the data region outside the window(s) it was allowed to read."""

    category = "oob_read"

    def __init__(self, message: str, address: int, *, pc: int | None = None):
        super().__init__(message, pc=pc)
        self.address = address


class MisalignedAccess(EmulationError):
    """A memory access that faults on hardware for misalignment (strict_sse_alignment)."""

    category = "misaligned_access"

    def __init__(self, message: str, address: int, *, pc: int | None = None):
        super().__init__(message, pc=pc)
        self.address = address


class InstructionLimit(EmulationError):
    """The code did not return within the instruction budget (probably an endless loop)."""

    category = "hang"


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


def _pooled(isa: Isa, segments=()) -> _Machine:
    """A per-thread machine for `isa` with `segments` mapped (keyed by their layout+content)."""
    pool = getattr(_POOL, "machines", None)
    if pool is None:
        pool = _POOL.machines = {}
    key = (isa.name, tuple(segments))
    m = pool.get(key)
    if m is None:
        if len(pool) > 16:
            pool.clear()
        m = pool[key] = _Machine(isa, CODE_BASE, False, segments)
    return m


def _discard_pooled(isa: Isa) -> None:
    pool = getattr(_POOL, "machines", {})
    for key in [k for k in pool if k[0] == isa.name]:
        pool.pop(key, None)


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


def _windows(spec) -> list[tuple[int, int]]:
    """Normalise a guard: (lo, hi) or an iterable of (lo, hi) windows."""
    if spec is None:
        return []
    spec = list(spec)
    if len(spec) == 2 and all(isinstance(x, int) for x in spec):
        return [tuple(spec)]
    return [tuple(w) for w in spec]


def _complement(windows: list[tuple[int, int]], size: int) -> list[tuple[int, int]]:
    """The parts of [0, size) outside every window."""
    out, pos = [], 0
    for lo, hi in sorted((max(0, lo), min(size, hi)) for lo, hi in windows if hi > lo):
        if lo > pos:
            out.append((pos, lo))
        pos = max(pos, hi)
    if pos < size:
        out.append((pos, size))
    return out


# Legacy-SSE mnemonics whose 16-byte memory operand may be unaligned (everything else with a
# 16-byte memory operand and no VEX prefix raises #GP on hardware when misaligned).
_SSE_UNALIGNED_OK = frozenset(
    "movdqu movups movupd lddqu maskmovdqu movq movd movsd movss movlps movlpd movhps movhpd "
    "pmovsxbw pmovsxbd pmovsxbq pmovsxwd pmovsxwq pmovsxdq pmovzxbw pmovzxbd pmovzxbq "
    "pmovzxwd pmovzxwq pmovzxdq fxsave fxrstor".split()
)


_SSE_ALIGNED_VEX = frozenset("vmovdqa vmovaps vmovapd vmovntdq vmovntps vmovntpd vmovntdqa".split())
_CS_X86 = None


def _sse_aligned_operand(code: bytes, pc: int):
    """For an instruction whose 16-byte memory operand must be 16-byte aligned: (text, base
    reg, index reg, scale, disp) with unicorn register ids (rip resolved into disp); else None."""
    global _CS_X86
    if _CS_X86 is None:
        _CS_X86 = X86_64.disassembler()
        _CS_X86.detail = True
    insn = next(_CS_X86.disasm(code, pc, 1), None)
    if insn is None:
        return None
    name = insn.mnemonic
    if name.startswith("v") and name not in _SSE_ALIGNED_VEX or name in _SSE_UNALIGNED_OK:
        return None
    from capstone import x86_const as cx

    for op in insn.operands:
        if op.type == cx.X86_OP_MEM and op.size == 16:
            def uc_reg(r):
                if not r:
                    return 0
                n = insn.reg_name(r)
                return getattr(x86, f"UC_X86_REG_{n.upper()}", 0)

            disp = op.mem.disp
            base = uc_reg(op.mem.base)
            if op.mem.base and insn.reg_name(op.mem.base) == "rip":
                base, disp = 0, pc + insn.size + disp
            return (f"{name} {insn.op_str}", base, uc_reg(op.mem.index), op.mem.scale, disp, 16)
    return None


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
    guard=None,
    read_guard=None,
    aligned_entries: Iterable[int] = (),
    strict_sse_alignment: bool = False,
    count_insns: bool = False,
    code_base: int = CODE_BASE,
    writable_code: bool = False,
    segments: Iterable[tuple[int, bytes, int | str]] = (),
) -> RunResult:
    """Call `code` at `code_base` with integer `args`; `data` is loaded at DATA_BASE + data_offset.

    Pointer arguments should point into the data region. Returns the return register
    (unsigned 64-bit) and the final contents of `data`'s bytes (trimmed to len(data)).
    Arguments beyond the ISA's register arguments are passed on the stack per ABI.

    abi:        check callee-saved registers, the final stack pointer, x86 DF and writes to
                the caller's frame on return; raise ABIViolation on a breach.
    garbage:    fill non-argument registers, flags, SIMD registers, the stack and the rest
                of the data region with pseudo-random junk (default: on unless tracing).
    seed:       seed for the register garbage (default: derived from `args`).
    guard:      (start, end) offsets into the data region the code may write, or a list of
                such windows; any other data-region write raises OutOfBoundsWrite.
    read_guard: the same for reads (OutOfBoundsRead); None (default) allows every read.
    aligned_entries: addresses whose entry requires ABI call alignment (MisalignedCall).
    strict_sse_alignment: x86-64 only; fault misaligned legacy-SSE 16-byte memory operands.
    count_insns: fill RunResult.insn_count (installs a per-instruction hook: slower).
    code_base / writable_code / segments: alternative load address, RWX code mapping, and
                extra mappings [(page-aligned address, initial bytes, prot "rwx" or int)].
    """
    nreg = len(isa.args)
    reg_args, stack_args = list(args)[:nreg], list(args)[nreg:]
    code, data = bytes(code), bytes(data)
    segments = _parse_segments(segments)
    _check_layout(code_base, code, data, data_offset, segments)
    aligned_entries = _with_callback_entries(aligned_entries)
    tracing = bool(trace or on_step)
    if garbage is None:
        garbage = not tracing
    spec = _abi(isa)

    pooled = not (tracing or count_insns or writable_code or code_base != CODE_BASE)
    m = _pooled(isa, segments) if pooled else _Machine(isa, code_base, writable_code, segments)
    uc = m.uc
    uc.context_restore(m.context)
    m.load_code(code)
    if pooled:  # writable segments may have been modified by a previous run
        for address, content, prot in segments:
            if prot & unicorn.UC_PROT_WRITE and content:
                uc.mem_write(address, content)

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
    for reg, value in zip(isa.args, reg_args, strict=False):
        uc.reg_write(reg, value & MASK64)

    sp = STACK_TOP
    # Stack-passed arguments: 8-byte slots at the call-time sp, area padded to 16 bytes.
    sp -= (8 * len(stack_args) + 15) & ~15
    arg_lo, arg_hi = sp, sp + 8 * len(stack_args)
    if stack_args:
        uc.mem_write(sp, b"".join((v & MASK64).to_bytes(8, "little") for v in stack_args))
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

    def stop(uc_, err: EmulationError) -> None:
        if not violations:
            violations.append(err)
        uc_.emu_stop()

    def forbid(lo: int, hi: int, make_error: Callable[[int, int], EmulationError],
               access: int = unicorn.UC_HOOK_MEM_WRITE) -> None:
        """Stop with make_error(address, pc) on any access overlapping [lo, hi)."""
        if lo >= hi:
            return

        def hook(uc_, _access, address, size, _value, _user):
            if address + size > lo and address < hi:
                stop(uc_, make_error(max(address, lo), uc_.reg_read(isa.pc)))

        # Widen the watched range downwards so a wide access straddling `lo` is seen too.
        hooks.append(uc.hook_add(access, hook, begin=max(0, lo - 63), end=hi - 1))

    if abi:
        # The return-address slot (x86) and everything above the entry sp is the caller's,
        # except the stack-argument slots, which belong to the callee.
        def frame_error(a, pc):
            return ABIViolation(f"{isa.name}: wrote the caller's stack frame at {a:#x} "
                                f"(entry sp={sp:#x})", address=a, pc=pc)

        forbid(sp, arg_lo, frame_error)
        forbid(max(sp, arg_hi), STACK_BASE + STACK_SIZE, frame_error)

    def data_guard(windows, what, cls, access):
        allowed = ", ".join(f"[{DATA_BASE + lo:#x}, {DATA_BASE + hi:#x})" for lo, hi in windows
                            if hi > lo) or "nothing"
        for lo, hi in _complement(windows, DATA_SIZE):
            forbid(DATA_BASE + lo, DATA_BASE + hi, lambda a, pc: cls(
                f"{isa.name}: out-of-bounds {what} at {a:#x} (allowed {allowed})", a, pc=pc),
                access)  # fmt: skip

    if guard is not None:
        data_guard(_windows(guard), "write", OutOfBoundsWrite, unicorn.UC_HOOK_MEM_WRITE)
    if read_guard is not None:
        data_guard(_windows(read_guard), "read", OutOfBoundsRead, unicorn.UC_HOOK_MEM_READ)

    for target in dict.fromkeys(aligned_entries):

        def entry_hook(uc_, address, size, _user, target=target):
            if address != target:
                return
            now = uc_.reg_read(isa.sp)
            if isa is X86_64:
                ra = int.from_bytes(uc_.mem_read(now, 8), "little") if STACK_BASE <= now < STACK_BASE + STACK_SIZE - 8 else None
                bad = now % 16 != 8
                df = bool(uc_.reg_read(spec.flags) & _X86_DF)
                if not bad and not df:
                    return
                site = f"; the call returns to {ra:#x}" if ra is not None else ""
                what = (f"rsp={now:#x} (rsp % 16 = {now % 16}, must be 8 at entry, i.e. rsp must "
                        "be 16-byte aligned at the call instruction)") if bad else "eflags.DF set at the call"
                pc = ra - 1 if ra is not None else None
            else:
                ra = uc_.reg_read(spec.link)
                if now % 16 == 0:
                    return
                site = f"; the call returns to {ra:#x}"
                what = f"sp={now:#x} (sp % 16 = {now % 16}, must be 0 at every call)"
                pc = ra - 4
            stop(uc_, MisalignedCall(f"{isa.name}: misaligned call to {target:#x}: {what}{site}",
                                     target, now, ra, pc=pc))

        hooks.append(uc.hook_add(unicorn.UC_HOOK_CODE, entry_hook, begin=target, end=target))
        # A pooled machine may hold a translated block for `target` made without this hook.
        uc.ctl_remove_cache(target, target + 1)

    if strict_sse_alignment and isa is X86_64:
        # Unicorn splits a 16-byte access into two 8-byte ones, so the operand's alignment is
        # computed from the decoded instruction (cached per pc), not from the access address.
        memo: dict[int, tuple | None] = {}

        def sse_hook(uc_, _access, address, size, _value, _user):
            pc = uc_.reg_read(isa.pc)
            if pc not in memo:
                memo[pc] = _sse_aligned_operand(bytes(uc_.mem_read(pc, 15)), pc)
            info = memo[pc]
            if info is None:
                return
            text, base, index, scale, disp, size_ = info
            ea = disp + (uc_.reg_read(base) if base else 0) + (uc_.reg_read(index) * scale if index else 0)
            ea &= MASK64
            if ea % 16:
                stop(uc_, MisalignedAccess(
                    f"{isa.name}: misaligned 16-byte SSE memory operand at {ea:#x} (address % 16 = "
                    f"{ea % 16}) in `{text}` at pc={pc:#x}: it needs 16-byte alignment (#GP on "
                    "hardware; use movdqu/movups for unaligned data)", ea, pc=pc))

        for begin, size in ((DATA_BASE, DATA_SIZE), (STACK_BASE, STACK_SIZE)):
            hooks.append(uc.hook_add(unicorn.UC_HOOK_MEM_READ | unicorn.UC_HOOK_MEM_WRITE, sse_hook,
                                     begin=begin, end=begin + size - 1))

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
            if violations:  # a hook stopped the run and the stop surfaced as a fault
                raise violations[0] from exc
            raise EmulationError(f"{isa.name}: {exc} at pc={pc:#x}", pc=pc) from exc
        if violations:
            raise violations[0]
        pc = uc.reg_read(isa.pc)
        if pc != RETURN_SENTINEL:
            raise InstructionLimit(f"{isa.name}: did not return within {max_insns} instructions "
                                   f"(stopped at pc={pc:#x})", pc=pc)
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
            for target in dict.fromkeys(aligned_entries):
                uc.ctl_remove_cache(target, target + 1)
    return result


# -- a hostile callee for function-pointer arguments -----------------------------------------

CALLBACK_BASE = 0x600000  # where callers map hostile_callback() (free in the default layout)
CALLBACK_SLOT = 0x400  # each callback function f lives at CALLBACK_BASE + k * CALLBACK_SLOT

# The functions a hostile callee can compute, in slot order (slot 0 is the default and sits at
# CALLBACK_BASE itself, so code written before there was a choice is unchanged):
#   "3x+1": f(x) = 3x + 1 (wrapping); never a fixed point (3x + 1 == x has no solution mod 2^64).
#   "fix3": f(x) = x if x % 3 == 0 (C signed remainder, i.e. x divisible by 3) else 3x + 1;
#           has fixed points, so a caller comparing f(x) with x sees equality.
CALLBACK_FUNCTIONS = ("3x+1", "fix3")


def callback_model(f: str = "3x+1") -> Callable[[int], int]:
    """The Python model of callback `f` on signed 64-bit values (returns a signed value)."""
    def s64(v: int) -> int:
        v &= MASK64
        return v - (1 << 64) if v >> 63 else v

    if f == "3x+1":
        return lambda x: s64(3 * x + 1)
    if f == "fix3":
        return lambda x: s64(x) if s64(x) % 3 == 0 else s64(3 * x + 1)
    raise ValueError(f"unknown callback function {f!r}; have {', '.join(CALLBACK_FUNCTIONS)}")


def callback_address(f: str = "3x+1") -> int:
    """The function-pointer value to pass for callback `f` (CALLBACK_BASE for the default)."""
    if f not in CALLBACK_FUNCTIONS:
        raise ValueError(f"unknown callback function {f!r}; have {', '.join(CALLBACK_FUNCTIONS)}")
    return CALLBACK_BASE + CALLBACK_SLOT * CALLBACK_FUNCTIONS.index(f)


def callback_entries() -> tuple[int, ...]:
    return tuple(callback_address(f) for f in CALLBACK_FUNCTIONS)


def _with_callback_entries(entries: Iterable[int]) -> list[int]:
    """CALLBACK_BASE in `aligned_entries` stands for every callback function in the segment,
    so a task may pass any callback_address() and keep the same misaligned-call trap."""
    entries = list(entries)
    if CALLBACK_BASE in entries:
        entries += [e for e in callback_entries() if e not in entries]
    return entries


# Every callback computes its f(x) into the return register, then (identically for all f)
# overwrites every caller-saved register with values that change from call to call, sets the
# flags from them, and scribbles over the 128 bytes below its own stack pointer (legal for any
# callee; fatal to a caller that kept data there across the call). It preserves exactly what
# the ABI requires and nothing more.
_CALLBACK_RESULT = {
    ("x86_64", "3x+1"): ["lea rax, [rdi + rdi*2 + 1]"],
    # rcx, rdx and flags are overwritten by the clobber sequence below; rdi still holds x.
    ("x86_64", "fix3"): ["mov rax, rdi", "cqo", "mov ecx, 3", "idiv rcx",
                         "lea rax, [rdi + rdi*2 + 1]", "test rdx, rdx", "cmovz rax, rdi"],
    ("aarch64", "3x+1"): ["add x9, x0, x0, lsl #1", "add x0, x9, #1"],
    # x9-x11 and the flags are overwritten by the clobber sequence below.
    ("aarch64", "fix3"): ["add x9, x0, x0, lsl #1", "add x9, x9, #1", "mov x10, #3", "sdiv x11, x0, x10",
                          "msub x11, x11, x10, x0", "cmp x11, #0", "csel x0, x0, x9, eq"],
}  # fmt: skip
_CALLBACK_CLOBBER = {
    "x86_64": "\n".join(
        ["mov rcx, 0x9e3779b97f4a7c15", "imul rcx, rdi", "xor rcx, 0x5bd1e995"]
        + [f"mov {d}, {s}\nrol {d}, 13\nxor {d}, 0x3c6ef372" for s, d in
           (("rcx", "rdx"), ("rdx", "rsi"), ("rsi", "rdi"), ("rdi", "r8"), ("r8", "r9"),
            ("r9", "r10"), ("r10", "r11"))]
        + [f"movq xmm{i}, {r}\npunpcklqdq xmm{i}, xmm{i}\npxor xmm{i}, xmm{(i + 5) % 16}" for i, r in
           enumerate("rcx rdx rsi rdi r8 r9 r10 r11 rcx rdx rsi rdi r8 r9 r10 r11".split())]
        + [f"mov qword ptr [rsp - {8 * k}], {r}" for k, r in
           zip(range(1, 17), "rcx rdx rsi rdi r8 r9 r10 r11 rcx rdx rsi rdi r8 r9 r10 r11".split())]
        + ["add rcx, rdx", "ret"]),  # fmt: skip
    "aarch64": "\n".join(
        ["movz x9, #0x7c15", "movk x9, #0x7f4a, lsl #16", "movk x9, #0x79b9, lsl #32",
         "movk x9, #0x9e37, lsl #48", "mul x9, x9, x0", "eor x9, x9, #0xff00ff00ff00ff00"]
        + [f"eor x{d}, x{s}, x{s}, ror #13" for s, d in
           ((9, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 10), (10, 11),
            (11, 12), (12, 13), (13, 14), (14, 15), (15, 16), (16, 17), (17, 18))]
        + [f"dup v{v}.2d, x{1 + i % 17}" for i, v in enumerate([*range(8), *range(16, 32)])]
        + [f"stur x{1 + k}, [sp, #-{8 * (k + 1)}]" for k in range(16)]
        + ["cmn x9, x10", "ret"]),  # fmt: skip
}
_CALLBACK_CODE: dict[tuple[str, str], bytes] = {}


def hostile_callback(isa: Isa, f: str = "3x+1") -> bytes:
    """Machine code for the hostile callee computing `f` (CALLBACK_FUNCTIONS; default
    f(x) = 3x + 1); position independent.

    Map it with `segments=callback_segments(isa)`, pass callback_address(f) (CALLBACK_BASE for
    the default) as the function-pointer argument, and add CALLBACK_BASE to `aligned_entries`
    (which covers every callback function) so a call made with a misaligned stack raises
    MisalignedCall."""
    key = (isa.name, f)
    if key not in _CALLBACK_CODE:
        if key not in _CALLBACK_RESULT:
            raise ValueError(f"unknown callback function {f!r}; have {', '.join(CALLBACK_FUNCTIONS)}")
        src = "\n".join(_CALLBACK_RESULT[key]) + "\n" + _CALLBACK_CLOBBER[isa.name]
        enc, _ = isa.assembler().asm(src, 0)
        code = bytes(enc)
        assert len(code) <= CALLBACK_SLOT, (key, len(code))
        _CALLBACK_CODE[key] = code
    return _CALLBACK_CODE[key]


def callback_segments(isa: Isa) -> list[tuple[int, bytes, str]]:
    """One segment at CALLBACK_BASE holding every callback function in its slot."""
    pad = b"\xcc" if isa.name == "x86_64" else b"\x00"  # int3 / udf between the functions
    blob = b"".join(hostile_callback(isa, f).ljust(CALLBACK_SLOT, pad) for f in CALLBACK_FUNCTIONS)
    return [(CALLBACK_BASE, blob, "rx")]


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
    "CALLBACK_BASE",
    "CALLBACK_FUNCTIONS",
    "CALLBACK_SLOT",
    "CODE_BASE",
    "CODE_SIZE",
    "DATA_BASE",
    "DATA_SIZE",
    "EmulationError",
    "InstructionLimit",
    "MASK64",
    "MisalignedAccess",
    "MisalignedCall",
    "OutOfBoundsRead",
    "OutOfBoundsWrite",
    "RETURN_SENTINEL",
    "RunResult",
    "STACK_BASE",
    "STACK_SIZE",
    "STACK_TOP",
    "Step",
    "callback_address",
    "callback_entries",
    "callback_model",
    "callback_segments",
    "hostile_callback",
    "run_function",
]
