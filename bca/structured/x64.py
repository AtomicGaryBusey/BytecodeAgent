"""x86-64: structured instructions -> machine code via iced-x86's Instruction/Encoder API.

No assembly text is produced or parsed. Each instruction object is mapped onto an iced
`Code` (the exact opcode form, e.g. ADD_RM64_IMM8) by enumerating the operand-form names that
could apply, encoding every candidate that exists, and keeping the shortest. Branches go through
`BlockEncoder`, which resolves label targets and picks rel8/rel32 forms.

Conventions (Intel operand order: dst first, then src):
  {"op":"add","dst":"rax","src":["rbx"]}                  add rax, rbx
  {"op":"mov","dst":{"mem":{"base":"rdi","index":"rcx","scale":8}},"src":["rax"]}
  {"op":"add","dst":{"mem":{"base":"rdi","size":8}},"src":[{"imm":1}]}   add qword [rdi], 1
  {"op":"imul","dst":"rax","src":["rcx",{"imm":10}]}      imul rax, rcx, 10
  {"op":"shl","dst":"rax","src":["cl"]}   {"op":"jne","target":"loop"}   {"op":"push","src":["rbx"]}
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import iced_x86 as ix
from iced_x86 import Code, Instruction, MemoryOperand, Register

from bca.structured.common import (
    AssemblyError,
    EncodeError,
    Entry,
    Family,
    Field,
    Insn,
    InsnError,
    Kind,
    Label,
    Program,
    Spec,
    check_int,
    check_object,
    label_kind,
    normalize_program,
    parse_program,
    suggest,
    undefined_label,
)

ISA_NAME = "x86_64"
M64 = (1 << 64) - 1

# --------------------------------------------------------------------------- registers


@dataclass(frozen=True)
class Reg:
    name: str
    reg: int  # iced Register
    bits: int


REGISTERS: dict[str, Reg] = {}


def _add(name, iced_name, bits):
    REGISTERS[name] = Reg(name, getattr(Register, iced_name), bits)


for _q, _d, _w, _b in [
    ("rax", "eax", "ax", "al"), ("rbx", "ebx", "bx", "bl"), ("rcx", "ecx", "cx", "cl"),
    ("rdx", "edx", "dx", "dl"), ("rsi", "esi", "si", "sil"), ("rdi", "edi", "di", "dil"),
    ("rbp", "ebp", "bp", "bpl"), ("rsp", "esp", "sp", "spl"),
]:  # fmt: skip
    for _n, _bits in ((_q, 64), (_d, 32), (_w, 16), (_b, 8)):
        _add(_n, _n.upper(), _bits)
for _i in range(8, 16):
    _add(f"r{_i}", f"R{_i}", 64)
    _add(f"r{_i}d", f"R{_i}D", 32)
    _add(f"r{_i}w", f"R{_i}W", 16)
    _add(f"r{_i}b", f"R{_i}L", 8)
for _h in ("ah", "bh", "ch", "dh"):
    _add(_h, _h.upper(), 8)
REG_NAMES = list(REGISTERS)
REG64_NAMES = [n for n, r in REGISTERS.items() if r.bits == 64]

# canonical condition -> accepted spellings
CONDS = {
    "e": ("e", "z"), "ne": ("ne", "nz"), "l": ("l", "nge"), "ge": ("ge", "nl"), "le": ("le", "ng"),
    "g": ("g", "nle"), "b": ("b", "c", "nae"), "ae": ("ae", "nb", "nc"), "be": ("be", "na"),
    "a": ("a", "nbe"), "s": ("s",), "ns": ("ns",), "o": ("o",), "no": ("no",), "p": ("p", "pe"),
    "np": ("np", "po"),
}  # fmt: skip
COND_ALIAS = {alias: canon for canon, aliases in CONDS.items() for alias in aliases}


def parse_reg(v, path) -> Reg:
    r = REGISTERS.get(v) if isinstance(v, str) else None
    if r is None and isinstance(v, str):
        r = REGISTERS.get(v.lower())
    if r is None:
        raise EncodeError(f"unknown register {v!r}{suggest(v, REG_NAMES)}", path)
    return r


@dataclass(frozen=True)
class Imm:
    value: int


@dataclass(frozen=True)
class Mem:
    base: Reg | None
    index: Reg | None
    scale: int
    disp: int
    size: int | None  # bytes

    def operand(self) -> MemoryOperand:
        return MemoryOperand(
            self.base.reg if self.base else Register.NONE,
            self.index.reg if self.index else Register.NONE,
            self.scale,
            self.disp,
            1 if self.disp else 0,
        )


# --------------------------------------------------------------------------- kinds

REG = Kind("reg", 'a register name like "rax"', lambda b: {"type": "string", "enum": REG_NAMES},
           lambda v: isinstance(v, str), parse_reg)  # fmt: skip


def _parse_imm(v, path):
    check_object(v, path, {"imm": True}, "immediate")
    return Imm(check_int(v["imm"], f"{path}.imm", -(1 << 63), M64, "immediate"))


IMM = Kind(
    "imm",
    '{"imm": <int>}',
    lambda b: {"type": "object", "properties": {"imm": {"type": "integer"}}, "required": ["imm"], "additionalProperties": False},
    lambda v: isinstance(v, dict) and "imm" in v,
    _parse_imm,
)


def _mem_schema(bounds):
    return {
        "type": "object",
        "properties": {
            "mem": {
                "type": "object",
                "description": "[base + index*scale + disp]; size (bytes) is required when no register operand implies it",
                "properties": {
                    "base": {"$ref": "#/$defs/reg64"},
                    "index": {"$ref": "#/$defs/reg64"},
                    "scale": {"type": "integer", "enum": [1, 2, 4, 8]},
                    "disp": {"type": "integer"},
                    "size": {"type": "integer", "enum": [1, 2, 4, 8]},
                },
                "additionalProperties": False,
            }
        },
        "required": ["mem"],
        "additionalProperties": False,
    }


def _parse_mem(v, path):
    check_object(v, path, {"mem": True}, "memory operand")
    p = f"{path}.mem"
    m = check_object(v["mem"], p, {"base": False, "index": False, "scale": False, "disp": False, "size": False}, "mem")
    regs = {}
    for k in ("base", "index"):
        if m.get(k) is not None:
            r = parse_reg(m[k], f"{p}.{k}")
            if r.bits != 64:
                raise EncodeError(f"{k} must be a 64-bit register, got {r.name}", f"{p}.{k}")
            regs[k] = r
    if "index" in regs and regs["index"].name == "rsp":
        raise EncodeError("rsp cannot be an index register", f"{p}.index")
    scale = check_int(m.get("scale", 1), f"{p}.scale", what="scale")
    if scale not in (1, 2, 4, 8):
        raise EncodeError(f"scale must be 1, 2, 4 or 8, got {scale}", f"{p}.scale")
    if scale != 1 and "index" not in regs:
        raise EncodeError("scale given without an index register", f"{p}.scale")
    disp = check_int(m.get("disp", 0), f"{p}.disp", -(1 << 31), (1 << 31) - 1, "disp")
    size = m.get("size")
    if size is not None and size not in (1, 2, 4, 8):
        raise EncodeError(f"size must be 1, 2, 4 or 8 bytes, got {size!r}", f"{p}.size")
    if not regs:
        raise EncodeError("memory operand needs a base and/or index register (absolute addresses unsupported)", p)
    return Mem(regs.get("base"), regs.get("index"), scale, disp, size)


MEM = Kind("mem", '{"mem": {"base": .., ...}}', _mem_schema, lambda v: isinstance(v, dict) and "mem" in v, _parse_mem)
REG64 = Kind("reg64", "a 64-bit register", lambda b: {"type": "string", "enum": REG64_NAMES}, lambda v: False, parse_reg)
LABEL = label_kind()


def F(*kinds, many=False, min=1, max=1, description=""):  # noqa: A002
    return Field(tuple(kinds), many, min, max, description)


def _cc_ops(prefix: str) -> tuple[str, ...]:
    return tuple(prefix + a for aliases in CONDS.values() for a in aliases)


JCC_OPS, CMOV_OPS, SET_OPS = _cc_ops("j"), _cc_ops("cmov"), _cc_ops("set")

FAMILIES = [
    Family("alu", ("mov", "add", "sub", "and", "or", "xor", "cmp", "test", "adc", "sbb", "xchg"),
           {"dst": F(REG, MEM), "src": F(REG, IMM, MEM, many=True)}, ("dst", "src"),
           "dst = dst op src[0] (cmp/test only set flags). One of dst/src may be memory. Immediates are "
           "sign-extended 32-bit, except mov r64 which accepts any 64-bit value.",
           ({"op": "add", "dst": "rax", "src": [{"imm": 1}]},)),
    Family("lea", ("lea",), {"dst": F(REG), "src": F(MEM, many=True)}, ("dst", "src"),
           "dst = effective address of src[0] (no memory access).",
           ({"op": "lea", "dst": "rax", "src": [{"mem": {"base": "rdi", "index": "rsi", "scale": 4, "disp": 1}}]},)),
    Family("imul", ("imul",), {"dst": F(REG), "src": F(REG, MEM, IMM, many=True, min=1, max=2)}, ("dst", "src"),
           "dst = dst * src[0], or dst = src[0] * src[1] when src[1] is an immediate (truncating signed multiply).",
           ({"op": "imul", "dst": "rax", "src": ["rcx"]},)),
    Family("muldiv", ("mul", "div", "idiv"), {"src": F(REG, MEM, many=True)}, ("src",),
           "One-operand forms on rdx:rax. mul: rdx:rax = rax * src; div/idiv: rax = rdx:rax / src, rdx = remainder "
           "(set rdx first: cqo before idiv, xor edx,edx before div).",
           ({"op": "idiv", "src": ["rcx"]},)),
    Family("ext", ("movzx", "movsx", "movsxd"), {"dst": F(REG), "src": F(REG, MEM, many=True)}, ("dst", "src"),
           "Zero/sign-extend a narrower src into dst (memory src needs mem.size).",
           ({"op": "movzx", "dst": "eax", "src": [{"mem": {"base": "rdi", "size": 1}}]},)),
    Family("shift", ("shl", "sal", "shr", "sar", "rol", "ror"), {"dst": F(REG, MEM), "src": F(IMM, REG, many=True)},
           ("dst", "src"), "Shift/rotate dst by an immediate or by register cl.",
           ({"op": "sar", "dst": "rax", "src": [{"imm": 63}]},)),
    Family("unary", ("inc", "dec", "neg", "not"), {"dst": F(REG, MEM)}, ("dst",), "In-place unary operation.",
           ({"op": "neg", "dst": "rax"},)),
    Family("cmov", CMOV_OPS, {"dst": F(REG), "src": F(REG, MEM, many=True)}, ("dst", "src"),
           "dst = src[0] if condition holds (16/32/64-bit registers).",
           ({"op": "cmovl", "dst": "rax", "src": ["rcx"]},)),
    Family("setcc", SET_OPS, {"dst": F(REG, MEM)}, ("dst",), "dst (8-bit) = condition ? 1 : 0.",
           ({"op": "setl", "dst": "al"},)),
    Family("jump", ("jmp", "call") + JCC_OPS, {"target": F(LABEL)}, ("target",),
           "Jump / conditional jump / call to a label.", ({"op": "jne", "target": "loop"},)),
    Family("push", ("push",), {"src": F(REG, IMM, MEM, many=True)}, ("src",), "Push a 64-bit value.",
           ({"op": "push", "src": ["rbx"]},)),
    Family("pop", ("pop",), {"dst": F(REG, MEM)}, ("dst",), "Pop a 64-bit value.", ({"op": "pop", "dst": "rbx"},)),
    Family("nullary", ("ret", "nop", "cqo", "cdq", "cdqe", "leave"), {}, (),
           "ret; nop; cqo (rdx:rax = sext rax); cdq (edx:eax = sext eax); cdqe (rax = sext eax); leave.",
           ({"op": "ret"},)),
]  # fmt: skip

SPEC = Spec(ISA_NAME, FAMILIES, {"reg": REG, "reg64": REG64})

# --------------------------------------------------------------------------- operand-form search


def _form(v, bits: int | None) -> str:
    if isinstance(v, Reg):
        return f"r{v.bits}"
    if isinstance(v, Mem):
        return f"m{bits}" if bits else "m"
    return "imm"


def _imm_fits(value: int, tok_bits: int, op_bits: int) -> int | None:
    """The signed value to hand iced for a `tok_bits` immediate field of an `op_bits` operation,
    or None if the CPU's sign extension of that field would not reproduce `value`."""
    if tok_bits > op_bits:
        return None
    v = value & ((1 << op_bits) - 1)
    sv = v - (1 << op_bits) if v >> (op_bits - 1) else v
    if tok_bits == op_bits or -(1 << (tok_bits - 1)) <= sv < (1 << (tok_bits - 1)):
        return sv
    return None


FIXED = {"al", "ax", "eax", "rax", "cl"}


def _tokens(v, bits: int | None) -> list[tuple[str, object]]:
    if isinstance(v, Reg):
        toks = [(f"RM{v.bits}", None), (f"R{v.bits}", None)]
        if v.name in FIXED:
            toks.append((v.name.upper(), None))
        return toks
    if isinstance(v, Mem):
        return [(f"RM{bits}", None), ("M", None)] if bits else [("M", None)]
    return [("IMM8", 8), ("IMM16", 16), ("IMM32", 32), ("IMM64", 64)]


def _create(code: int, ops: list, imm_vals: list[int]) -> Instruction:
    kinds = []
    args = []
    k = 0
    for v in ops:
        if isinstance(v, Reg):
            kinds.append("reg")
            args.append(v.reg)
        elif isinstance(v, Mem):
            kinds.append("mem")
            args.append(v.operand())
        else:
            kinds.append("imm")
            args.append(imm_vals[k])
            k += 1
    sig = "_".join(kinds)
    if not ops:
        return Instruction.create(code)
    if sig.endswith("imm"):
        val = args[-1]
        name = f"create_{sig[:-3]}i32" if sig != "imm" else "create_i32"
        if not -(1 << 31) <= val < (1 << 31):
            name = f"create_{sig[:-3]}i64"
        return getattr(Instruction, name.replace("__", "_"))(code, *args)
    return getattr(Instruction, f"create_{sig}")(code, *args)


def _encode_one(ins: Instruction, ip: int = 0x100000) -> bytes:
    enc = ix.Encoder(64)
    enc.encode(ins, ip)
    return bytes(enc.take_buffer())


def best_encoding(prefixes: list[str], ops: list, op_bits: int, *, shift_count: bool = False,
                  mem_bits: int | None = None) -> Instruction | None:  # fmt: skip
    """Find the shortest iced instruction whose Code name is PREFIX_TOK1_TOK2... for `ops`."""
    token_lists = [_tokens(v, mem_bits) for v in ops]
    best = None
    best_encoding.last_error = None
    for prefix in prefixes:
        for combo in itertools.product(*token_lists):
            name = "_".join([prefix, *(t for t, _ in combo)]) if combo else prefix
            code = getattr(Code, name, None)
            if code is None:
                continue
            imm_vals = []
            ok = True
            for v, (_tok, tbits) in zip(ops, combo, strict=True):
                if isinstance(v, Imm):
                    if shift_count:
                        fitted = v.value if tbits == 8 else None
                    else:
                        fitted = _imm_fits(v.value, tbits, op_bits)
                    if fitted is None:
                        ok = False
                        break
                    imm_vals.append(fitted)
            if not ok:
                continue
            try:
                ins = _create(code, ops, imm_vals)
                n = len(_encode_one(ins))
            except (ValueError, TypeError) as exc:
                best_encoding.last_error = str(exc)
                continue
            if best is None or n < best[0]:
                best = (n, ins)
    return best[1] if best else None


def _no_form(op: str, ops: list, mem_bits: int | None, hint: str = "") -> EncodeError:
    shape = ", ".join(_form(v, mem_bits) for v in ops)
    if not hint and getattr(best_encoding, "last_error", None):
        hint = f" (iced-x86: {best_encoding.last_error})"
    return EncodeError(f"no x86-64 encoding for '{op} {shape}'{hint}")


def _check_imm_range(imm: Imm, bits: int, path: str, *, allow64: bool = False):
    lo = -(1 << (bits - 1))
    hi = (1 << bits) - 1
    if bits == 64 and not allow64:
        lo, hi = -(1 << 31), (1 << 31) - 1
        if not lo <= imm.value <= hi and not ((1 << 64) + lo <= imm.value <= M64):
            raise EncodeError(
                f"immediate {imm.value} does not fit a sign-extended 32-bit immediate (only 'mov r64, imm' takes 64-bit "
                "immediates; mov it into a scratch register first)",
                f"{path}.imm",
            )
        return
    if not lo <= imm.value <= hi:
        raise EncodeError(f"immediate {imm.value} does not fit in {bits} bits", f"{path}.imm")


def _mem_size(mem: Mem, path: str, implied: int | None, op: str) -> int:
    bits = mem.size * 8 if mem.size else None
    if implied and bits and bits != implied:
        raise EncodeError(f"mem.size {mem.size} conflicts with the {implied // 8}-byte register operand", f"{path}.mem.size")
    bits = bits or implied
    if bits is None:
        raise EncodeError(f"mem.size is required here: {op} has no register operand to imply the access size", f"{path}.mem")
    return bits


# --------------------------------------------------------------------------- per-family builders


def _src(insn: Insn, i: int):
    return insn.f["src"][i], f"src[{i}]"


def b_alu(insn: Insn) -> Instruction:
    op = insn.op
    dst = insn.f["dst"]
    src, sp_ = _src(insn, 0)
    if isinstance(dst, Mem) and isinstance(src, Mem):
        raise EncodeError("x86 has no memory-to-memory form; load one operand into a register first", sp_)
    reg_bits = next((v.bits for v in (dst, src) if isinstance(v, Reg)), None)
    if isinstance(dst, Reg) and isinstance(src, Reg) and dst.bits != src.bits and op != "xchg":
        raise EncodeError(f"operand size mismatch: {dst.name} is {dst.bits}-bit, {src.name} is {src.bits}-bit", sp_)
    mem_bits = None
    for v, p in ((dst, "dst"), (src, sp_)):
        if isinstance(v, Mem):
            mem_bits = _mem_size(v, p, reg_bits, op)
    bits = reg_bits or mem_bits
    if isinstance(src, Imm):
        if op == "xchg":
            raise EncodeError("xchg takes no immediate", sp_)
        _check_imm_range(src, bits, sp_, allow64=(op == "mov" and isinstance(dst, Reg)))
    ops = [dst, src]
    ins = best_encoding([op.upper()], ops, bits, mem_bits=mem_bits)
    if op in ("test", "xchg") and not isinstance(src, Imm):
        # Commutative: the swapped order may be the only form (test m, r) or shorter (xchg r, rax).
        alt = best_encoding([op.upper()], [src, dst], bits, mem_bits=mem_bits)
        if alt is not None and (ins is None or len(_encode_one(alt)) < len(_encode_one(ins))):
            ins = alt
    if ins is None:
        raise _no_form(op, ops, mem_bits)
    return ins


def b_lea(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    if dst.bits == 8:
        raise EncodeError("lea needs a 16/32/64-bit destination", "dst")
    mem, _ = _src(insn, 0)
    return Instruction.create_reg_mem(getattr(Code, f"LEA_R{dst.bits}_M"), dst.reg, mem.operand())


def b_imul(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    src = insn.f["src"]
    if dst.bits == 8:
        raise EncodeError("two/three-operand imul needs a 16/32/64-bit destination", "dst")
    s0, p0 = _src(insn, 0)
    if isinstance(s0, Imm):
        # imul r, imm  ==  imul r, r, imm
        src = [dst, s0]
        s0, p0 = dst, "dst"
    if isinstance(s0, Reg) and s0.bits != dst.bits:
        raise EncodeError(f"operand size mismatch: {s0.name} vs {dst.name}", p0)
    mem_bits = _mem_size(s0, p0, dst.bits, "imul") if isinstance(s0, Mem) else None
    ops = [dst, s0]
    if len(src) == 2:
        imm, p1 = src[1], "src[1]"
        if not isinstance(imm, Imm):
            raise EncodeError("the third imul operand must be an immediate", p1)
        _check_imm_range(imm, dst.bits, p1)
        ops.append(imm)
    ins = best_encoding(["IMUL"], ops, dst.bits, mem_bits=mem_bits)
    if ins is None:
        raise _no_form("imul", ops, mem_bits)
    return ins


def b_muldiv(insn: Insn) -> Instruction:
    s, p = _src(insn, 0)
    if isinstance(s, Imm):
        raise EncodeError(f"{insn.op} takes a register or memory operand, not an immediate", p)
    bits = s.bits if isinstance(s, Reg) else _mem_size(s, p, None, insn.op)
    ins = best_encoding([insn.op.upper()], [s], bits, mem_bits=bits)
    if ins is None:
        raise _no_form(insn.op, [s], bits)
    return ins


def b_ext(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    s, p = _src(insn, 0)
    if isinstance(s, Imm):
        raise EncodeError(f"{insn.op} takes a register or memory source", p)
    sbits = s.bits if isinstance(s, Reg) else _mem_size(s, p, None, insn.op)
    ok = {"movzx": (8, 16), "movsx": (8, 16), "movsxd": (32,)}[insn.op]
    if sbits not in ok:
        raise EncodeError(f"{insn.op} source must be {' or '.join(f'{b}-bit' for b in ok)}, got {sbits}-bit", p)
    if dst.bits <= sbits:
        raise EncodeError(f"{insn.op} destination must be wider than the {sbits}-bit source", "dst")
    ins = best_encoding([insn.op.upper()], [dst, s], dst.bits, mem_bits=sbits)
    if ins is None:
        raise _no_form(insn.op, [dst, s], sbits)
    return ins


def b_shift(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    cnt, p = _src(insn, 0)
    bits = dst.bits if isinstance(dst, Reg) else _mem_size(dst, "dst", None, insn.op)
    if isinstance(cnt, Reg):
        if cnt.name != "cl":
            raise EncodeError(f"a register shift count must be cl, got {cnt.name}", p)
    elif isinstance(cnt, Imm):
        if not 0 <= cnt.value < bits:
            raise EncodeError(f"shift count must be 0..{bits - 1} for a {bits}-bit operand", f"{p}.imm")
    else:
        raise EncodeError("shift count must be an immediate or cl", p)
    op = "SHL" if insn.op == "sal" else insn.op.upper()
    ins = best_encoding([op], [dst, cnt], bits, shift_count=True, mem_bits=bits)
    if ins is None:
        raise _no_form(insn.op, [dst, cnt], bits)
    return ins


def b_unary(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    bits = dst.bits if isinstance(dst, Reg) else _mem_size(dst, "dst", None, insn.op)
    # INC_R32/INC_R16 are the 32-bit-mode one-byte forms; best_encoding tries both and iced rejects them in 64-bit.
    ins = best_encoding([insn.op.upper()], [dst], bits, mem_bits=bits)
    if ins is None:
        raise _no_form(insn.op, [dst], bits)
    return ins


def b_cmov(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    s, p = _src(insn, 0)
    if dst.bits == 8:
        raise EncodeError("cmov needs a 16/32/64-bit destination", "dst")
    if isinstance(s, Imm):
        raise EncodeError("cmov cannot take an immediate; mov it into a register first", p)
    if isinstance(s, Reg) and s.bits != dst.bits:
        raise EncodeError(f"operand size mismatch: {s.name} vs {dst.name}", p)
    mem_bits = _mem_size(s, p, dst.bits, insn.op) if isinstance(s, Mem) else None
    cc = COND_ALIAS[insn.op[4:]].upper()
    ins = best_encoding([f"CMOV{cc}"], [dst, s], dst.bits, mem_bits=mem_bits)
    if ins is None:
        raise _no_form(insn.op, [dst, s], mem_bits)
    return ins


def b_setcc(insn: Insn) -> Instruction:
    dst = insn.f["dst"]
    if isinstance(dst, Reg) and dst.bits != 8:
        raise EncodeError(f"set<cc> writes an 8-bit register (e.g. al), got {dst.name}; movzx it afterwards", "dst")
    if isinstance(dst, Mem):
        _mem_size(dst, "dst", 8, insn.op)
    cc = COND_ALIAS[insn.op[3:]].upper()
    code = getattr(Code, f"SET{cc}_RM8")
    return Instruction.create_reg(code, dst.reg) if isinstance(dst, Reg) else Instruction.create_mem(code, dst.operand())


def b_push(insn: Insn) -> Instruction:
    s, p = _src(insn, 0)
    if isinstance(s, Imm):
        _check_imm_range(s, 64, p)
        ins = best_encoding(["PUSHQ"], [s], 64)
    else:
        if isinstance(s, Reg) and s.bits != 64:
            raise EncodeError(f"push takes a 64-bit register, got {s.name}", p)
        bits = 64 if isinstance(s, Reg) else _mem_size(s, p, 64, "push")
        ins = best_encoding(["PUSH"], [s], bits, mem_bits=bits)
    if ins is None:
        raise _no_form("push", [s], 64)
    return ins


def b_pop(insn: Insn) -> Instruction:
    d = insn.f["dst"]
    if isinstance(d, Reg) and d.bits != 64:
        raise EncodeError(f"pop takes a 64-bit register, got {d.name}", "dst")
    bits = 64 if isinstance(d, Reg) else _mem_size(d, "dst", 64, "pop")
    ins = best_encoding(["POP"], [d], bits, mem_bits=bits)
    if ins is None:
        raise _no_form("pop", [d], 64)
    return ins


NULLARY = {"ret": Code.RETNQ, "nop": Code.NOPD, "cqo": Code.CQO, "cdq": Code.CDQ, "cdqe": Code.CDQE, "leave": Code.LEAVEQ}


def jump_code(op: str) -> int:
    if op == "jmp":
        return Code.JMP_REL32_64
    if op == "call":
        return Code.CALL_REL32_64
    return getattr(Code, f"J{COND_ALIAS[op[1:]].upper()}_REL32_64")


BUILDERS = {
    "alu": b_alu, "lea": b_lea, "imul": b_imul, "muldiv": b_muldiv, "ext": b_ext, "shift": b_shift,
    "unary": b_unary, "cmov": b_cmov, "setcc": b_setcc, "push": b_push, "pop": b_pop,
    "nullary": lambda insn: Instruction.create(NULLARY[insn.op]),
}  # fmt: skip


def build(insn: Insn, target_ip) -> Instruction:
    """Build the iced instruction; `target_ip(label, path)` supplies branch targets."""
    if insn.family.name == "jump":
        return Instruction.create_branch(jump_code(insn.op), target_ip(insn.f["target"], "target"))
    ins = BUILDERS[insn.family.name](insn)
    try:
        _encode_one(ins)
    except ValueError as exc:  # e.g. ah together with a REX prefix
        raise EncodeError(f"iced-x86 rejected the instruction: {exc}") from None
    return ins


# --------------------------------------------------------------------------- program

_IP_BASE = 1 << 40  # synthetic ips identify instructions to the BlockEncoder
_IP_STEP = 16


def assemble(instructions, *, base: int = 0x100000, allow_undefined: bool = False) -> Program:
    """Encode a structured program. Raises AssemblyError listing every bad instruction."""
    instructions = normalize_program(instructions)
    items, errors = parse_program(SPEC, instructions)

    # Label -> synthetic ip of the next instruction.
    labels_ip: dict[str, int] = {}
    pending_labels: list[Label] = []
    real = [it for it in items if isinstance(it, Insn)]
    ip_of = {it.index: _IP_BASE + _IP_STEP * (k + 1) for k, it in enumerate(real)}
    end_ip = _IP_BASE + _IP_STEP * (len(real) + 1)
    for it in items:
        if isinstance(it, Label):
            pending_labels.append(it)
        else:
            for lab in pending_labels:
                labels_ip[lab.name] = ip_of[it.index]
            pending_labels = []
    for lab in pending_labels:
        labels_ip[lab.name] = end_ip  # label at the very end: resolved below via a trailing marker
    need_end = False

    pending: list[str] = []
    built: list[tuple[Insn, Instruction]] = []
    for it in real:

        def target_ip(name, path, it=it):
            nonlocal need_end
            if name in labels_ip:
                if labels_ip[name] == end_ip:
                    need_end = True
                return labels_ip[name]
            if allow_undefined:
                pending.append(name)
                return ip_of[it.index]
            raise undefined_label(name, labels_ip, path)

        try:
            ins = build(it, target_ip)
        except EncodeError as exc:
            errors.append(InsnError(it.index, exc.path, exc.message, it.raw))
            continue
        ins.ip = ip_of[it.index]
        built.append((it, ins))
    if errors:
        raise AssemblyError(sorted(errors, key=lambda e: e.index))

    block = [ins for _, ins in built]
    if need_end:
        # A label after the last instruction: give it a real target (an int3 that is never
        # reached through fall-through only if the program ends in ret/jmp), then drop it.
        marker = Instruction.create(Code.INT3)
        marker.ip = end_ip
        block.append(marker)
    enc = ix.BlockEncoder(64)
    enc.add_many(block)
    try:
        code = bytes(enc.encode(base))
    except ValueError as exc:
        raise AssemblyError([InsnError(-1, "", f"iced-x86 block encoding failed: {exc}")]) from None

    # Recover per-instruction offsets by decoding (one decoded instruction per built one).
    decoded = list(ix.Decoder(64, code, ip=base))
    if len(decoded) != len(block):
        raise AssemblyError([InsnError(-1, "", "internal error: block encoder changed the instruction count")])
    if need_end:
        code = code[: decoded[-1].ip - base]
    offsets = {it.index: d.ip - base for (it, _), d in zip(built, decoded, strict=False)}
    entries = [Entry(it.index, offsets[it.index], code[offsets[it.index] : offsets[it.index] + d.len], it.raw)
               for (it, _), d in zip(built, decoded, strict=False)]  # fmt: skip
    ip_to_off = {ip_of[it.index]: offsets[it.index] for it, _ in built}
    ip_to_off[end_ip] = len(code)
    labels = {name: ip_to_off[ip] for name, ip in labels_ip.items()}
    return Program(ISA_NAME, code, base, entries, labels, sorted(set(pending)))
