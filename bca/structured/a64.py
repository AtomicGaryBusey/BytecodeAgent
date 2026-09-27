"""AArch64: structured instructions -> machine words, by direct bit-level encoding.

No assembler is involved at encode time. Every instruction object maps to one (or, for the
`mov` immediate pseudo-op, up to four) 32-bit little-endian words.

Conventions (mirroring assembly operand order):
  {"op":"add","dst":"x0","src":["x1",{"imm":4}]}              add  x0, x1, #4
  {"op":"add","dst":"x0","src":["x1",{"reg":"x2","shift":"lsl","amount":3}]}
  {"op":"ldr","dst":"x0","src":[{"mem":{"base":"x1","disp":8}}]}
  {"op":"str","src":["x0",{"mem":{"base":"sp","disp":-16,"writeback":"pre"}}]}
  {"op":"stp","src":["x29","x30",{"mem":{"base":"sp","disp":-16,"writeback":"pre"}}]}
  {"op":"b.ne","target":"loop"}   {"op":"cbz","src":["x0"],"target":"done"}
  {"op":"csel","dst":"x0","src":["x1","x2"],"cond":"lt"}   {"op":"cset","dst":"x0","cond":"eq"}
"""

from __future__ import annotations

from dataclasses import dataclass

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
    enum_kind,
    label_kind,
    normalize_program,
    parse_program,
    suggest,
    undefined_label,
)

ISA_NAME = "aarch64"
M64 = (1 << 64) - 1

# --------------------------------------------------------------------------- registers


@dataclass(frozen=True)
class Reg:
    name: str
    num: int  # 0..31
    width: int  # 64 or 32
    kind: str  # "gpr" | "sp" | "zr"


REGISTERS: dict[str, Reg] = {}
for _i in range(31):
    REGISTERS[f"x{_i}"] = Reg(f"x{_i}", _i, 64, "gpr")
    REGISTERS[f"w{_i}"] = Reg(f"w{_i}", _i, 32, "gpr")
REGISTERS["xzr"] = Reg("xzr", 31, 64, "zr")
REGISTERS["wzr"] = Reg("wzr", 31, 32, "zr")
REGISTERS["sp"] = Reg("sp", 31, 64, "sp")
REGISTERS["wsp"] = Reg("wsp", 31, 32, "sp")
REGISTERS["fp"] = Reg("x29", 29, 64, "gpr")
REGISTERS["lr"] = Reg("x30", 30, 64, "gpr")
REG_NAMES = list(REGISTERS)

CONDS = {
    "eq": 0, "ne": 1, "cs": 2, "hs": 2, "cc": 3, "lo": 3, "mi": 4, "pl": 5, "vs": 6,
    "vc": 7, "hi": 8, "ls": 9, "ge": 10, "lt": 11, "gt": 12, "le": 13, "al": 14, "nv": 15,
}  # fmt: skip

SHIFTS = {"lsl": 0, "lsr": 1, "asr": 2, "ror": 3}


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
    lsl: int = 0


@dataclass(frozen=True)
class Shifted:
    reg: Reg
    shift: str
    amount: int


@dataclass(frozen=True)
class Mem:
    base: Reg
    index: Reg | None
    scale: int
    disp: int
    writeback: str | None  # None | "pre" | "post"
    extend: str | None  # None | "uxtw" | "sxtw" | "sxtx"


# --------------------------------------------------------------------------- operand kinds


def _reg_schema(bounds):
    return {"type": "string", "enum": REG_NAMES}


REG = Kind("reg", 'a register name like "x1"', _reg_schema, lambda v: isinstance(v, str), parse_reg)


def _imm_schema(bounds):
    return {
        "type": "object",
        "properties": {
            "imm": {"type": "integer"},
            "lsl": {"type": "integer", "enum": [0, 12, 16, 32, 48]},
        },
        "required": ["imm"],
        "additionalProperties": False,
    }


def _parse_imm(v, path):
    check_object(v, path, {"imm": True, "lsl": False}, "immediate")
    val = check_int(v["imm"], f"{path}.imm", -(1 << 63), M64, "immediate")
    lsl = check_int(v.get("lsl", 0), f"{path}.lsl", what="lsl")
    if lsl not in (0, 12, 16, 32, 48):
        raise EncodeError(f"lsl must be 0, 12, 16, 32 or 48, got {lsl}", f"{path}.lsl")
    return Imm(val, lsl)


IMM = Kind("imm", '{"imm": <int>}', _imm_schema, lambda v: isinstance(v, dict) and "imm" in v, _parse_imm)


def _shifted_schema(bounds):
    amount = {"type": "integer"}
    if bounds:
        amount |= {"minimum": 0, "maximum": 63}
    return {
        "type": "object",
        "description": "register with a shift applied, e.g. x2, lsl #3",
        "properties": {
            "reg": {"$ref": "#/$defs/reg"},
            "shift": {"type": "string", "enum": list(SHIFTS)},
            "amount": amount,
        },
        "required": ["reg", "shift", "amount"],
        "additionalProperties": False,
    }


def _parse_shifted(v, path):
    check_object(v, path, {"reg": True, "shift": True, "amount": True}, "shifted register")
    reg = parse_reg(v["reg"], f"{path}.reg")
    if v["shift"] not in SHIFTS:
        raise EncodeError(f"shift must be one of {list(SHIFTS)}, got {v['shift']!r}", f"{path}.shift")
    amt = check_int(v["amount"], f"{path}.amount", 0, 63, "shift amount")
    return Shifted(reg, v["shift"], amt)


SHIFTED = Kind(
    "shifted",
    '{"reg": .., "shift": "lsl|lsr|asr|ror", "amount": n}',
    _shifted_schema,
    lambda v: isinstance(v, dict) and "reg" in v,
    _parse_shifted,
)


def _mem_schema(bounds):
    disp = {"type": "integer", "description": "byte displacement (default 0)"}
    return {
        "type": "object",
        "properties": {
            "mem": {
                "type": "object",
                "description": "[base, #disp] | [base, index, lsl #log2(scale)] | pre/post-index writeback",
                "properties": {
                    "base": {"$ref": "#/$defs/reg"},
                    "index": {"$ref": "#/$defs/reg"},
                    "scale": {"type": "integer", "enum": [1, 2, 4, 8]},
                    "disp": disp,
                    "writeback": {
                        "type": "string",
                        "enum": ["pre", "post"],
                        "description": "pre: [base, #disp]!  post: [base], #disp",
                    },
                    "extend": {
                        "type": "string",
                        "enum": ["uxtw", "sxtw", "sxtx"],
                        "description": "index extension; required for a w index register",
                    },
                },
                "required": ["base"],
                "additionalProperties": False,
            }
        },
        "required": ["mem"],
        "additionalProperties": False,
    }


def _parse_mem(v, path):
    check_object(v, path, {"mem": True}, "memory operand")
    p = f"{path}.mem"
    m = check_object(
        v["mem"], p, {"base": True, "index": False, "scale": False, "disp": False, "writeback": False, "extend": False}, "mem"
    )
    base = parse_reg(m["base"], f"{p}.base")
    if base.width != 64 or base.kind == "zr":
        raise EncodeError(f"base register must be an x register or sp, got {m['base']!r}", f"{p}.base")
    index = parse_reg(m["index"], f"{p}.index") if m.get("index") is not None else None
    if index is not None and index.kind == "sp":
        raise EncodeError("sp cannot be an index register", f"{p}.index")
    scale = check_int(m.get("scale", 1), f"{p}.scale", what="scale")
    if scale not in (1, 2, 4, 8):
        raise EncodeError(f"scale must be 1, 2, 4 or 8, got {scale}", f"{p}.scale")
    disp = check_int(m.get("disp", 0), f"{p}.disp", -(1 << 32), 1 << 32, "disp")
    wb = m.get("writeback")
    if wb not in (None, "pre", "post"):
        raise EncodeError(f"writeback must be 'pre' or 'post', got {wb!r}", f"{p}.writeback")
    ext = m.get("extend")
    if ext not in (None, "uxtw", "sxtw", "sxtx", "lsl"):
        raise EncodeError(f"extend must be 'uxtw', 'sxtw' or 'sxtx', got {ext!r}", f"{p}.extend")
    if index is None and (ext or scale != 1):
        raise EncodeError("scale/extend given without an index register", p)
    if index is not None and (disp or wb):
        raise EncodeError("AArch64 has no [base + index + disp] or indexed-writeback form; add disp to the base first", p)
    return Mem(base, index, scale, disp, wb, None if ext == "lsl" else ext)


MEM = Kind("mem", '{"mem": {"base": .., ...}}', _mem_schema, lambda v: isinstance(v, dict) and "mem" in v, _parse_mem)
LABEL = label_kind()
COND = enum_kind("cond", CONDS, "a condition code")

# --------------------------------------------------------------------------- families


def F(*kinds, many=False, min=1, max=1, description=""):  # noqa: A002
    return Field(tuple(kinds), many, min, max, description)


def _ops(*s):
    return tuple(s)


BCOND_OPS = tuple(f"b.{c}" for c in CONDS if c != "nv")

FAMILIES = [
    Family("mov", _ops("mov"), {"dst": F(REG), "src": F(REG, IMM, many=True)}, ("dst", "src"),
           "mov dst, src — register copy or any 64-bit immediate (expanded to movz/movn/movk or orr as needed).",
           ({"op": "mov", "dst": "x0", "src": [{"imm": 42}]},)),
    Family("movwide", _ops("movz", "movn", "movk"), {"dst": F(REG), "src": F(IMM, many=True)}, ("dst", "src"),
           "16-bit move-wide: src [{imm: 0..65535, lsl: 0|16|32|48}]. movk keeps the other bits.",
           ({"op": "movk", "dst": "x0", "src": [{"imm": 4660, "lsl": 16}]},)),
    Family("arith", _ops("add", "sub", "adds", "subs"),
           {"dst": F(REG), "src": F(REG, IMM, SHIFTED, many=True, min=2, max=2)}, ("dst", "src"),
           "dst = src[0] op src[1]. src[1]: register, shifted register, or imm12 (0..4095, optionally lsl 12). "
           "Negative immediates flip add<->sub. The *s forms set flags.",
           ({"op": "add", "dst": "x0", "src": ["x1", {"imm": 4}]},)),
    Family("compare", _ops("cmp", "cmn", "tst"), {"src": F(REG, IMM, SHIFTED, many=True, min=2, max=2)}, ("src",),
           "Set flags from src[0] vs src[1] (cmp=subs, cmn=adds, tst=ands; result discarded).",
           ({"op": "cmp", "src": ["x0", {"imm": 10}]},)),
    Family("logic", _ops("and", "orr", "eor", "ands", "bic", "orn", "eon", "bics"),
           {"dst": F(REG), "src": F(REG, IMM, SHIFTED, many=True, min=2, max=2)}, ("dst", "src"),
           "Bitwise dst = src[0] op src[1]; immediates must be valid bitmask immediates.",
           ({"op": "and", "dst": "x0", "src": ["x1", {"imm": 255}]},)),
    Family("unary", _ops("neg", "negs", "mvn"), {"dst": F(REG), "src": F(REG, SHIFTED, many=True)}, ("dst", "src"),
           "neg: dst = -src; mvn: dst = ~src.", ({"op": "neg", "dst": "x0", "src": ["x1"]},)),
    Family("shift", _ops("lsl", "lsr", "asr", "ror"), {"dst": F(REG), "src": F(REG, IMM, many=True, min=2, max=2)},
           ("dst", "src"), "dst = src[0] shifted by src[1] (register or immediate 0..width-1).",
           ({"op": "lsl", "dst": "x0", "src": ["x1", {"imm": 3}]},)),
    Family("mul", _ops("mul", "mneg", "sdiv", "udiv", "smulh", "umulh"),
           {"dst": F(REG), "src": F(REG, many=True, min=2, max=2)}, ("dst", "src"),
           "dst = src[0] op src[1]. Division by zero yields 0.", ({"op": "mul", "dst": "x0", "src": ["x1", "x2"]},)),
    Family("muladd", _ops("madd", "msub"), {"dst": F(REG), "src": F(REG, many=True, min=3, max=3)}, ("dst", "src"),
           "madd: dst = src[2] + src[0]*src[1]; msub: dst = src[2] - src[0]*src[1].",
           ({"op": "madd", "dst": "x0", "src": ["x1", "x2", "x3"]},)),
    Family("csel", _ops("csel", "csinc", "csinv", "csneg"),
           {"dst": F(REG), "src": F(REG, many=True, min=2, max=2), "cond": F(COND)}, ("dst", "src", "cond"),
           "dst = cond ? src[0] : f(src[1]) (f = id / +1 / ~ / -).",
           ({"op": "csel", "dst": "x0", "src": ["x1", "x2"], "cond": "lt"},)),
    Family("cset", _ops("cset", "csetm"), {"dst": F(REG), "cond": F(COND)}, ("dst", "cond"),
           "cset: dst = cond ? 1 : 0; csetm: dst = cond ? -1 : 0.", ({"op": "cset", "dst": "x0", "cond": "eq"},)),
    Family("cinc", _ops("cinc", "cinv", "cneg"), {"dst": F(REG), "src": F(REG, many=True), "cond": F(COND)},
           ("dst", "src", "cond"), "dst = cond ? f(src) : src (f = +1 / ~ / -).",
           ({"op": "cneg", "dst": "x0", "src": ["x0"], "cond": "lt"},)),
    Family("load", _ops("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrsw"), {"dst": F(REG), "src": F(MEM, many=True)},
           ("dst", "src"), "Load into dst. ldr x=8 bytes, ldr w=4; ldrb/ldrh zero-extend into w; ldrs* sign-extend.",
           ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "x1", "index": "x2", "scale": 8}}]},)),
    Family("store", _ops("str", "strb", "strh"), {"src": F(REG, MEM, many=True, min=2, max=2)}, ("src",),
           "Store src[0] to memory src[1]. strb/strh take a w register.",
           ({"op": "str", "src": ["x0", {"mem": {"base": "x1", "disp": 8}}]},)),
    Family("ldp", _ops("ldp"), {"dst": F(REG, many=True, min=2, max=2), "src": F(MEM, many=True)}, ("dst", "src"),
           "Load pair. disp must be a multiple of the register size in [-64, 63] * size.",
           ({"op": "ldp", "dst": ["x29", "x30"], "src": [{"mem": {"base": "sp", "disp": 16, "writeback": "post"}}]},)),
    Family("stp", _ops("stp"), {"src": F(REG, MEM, many=True, min=3, max=3)}, ("src",), "Store pair src[0], src[1].",
           ({"op": "stp", "src": ["x29", "x30", {"mem": {"base": "sp", "disp": -16, "writeback": "pre"}}]},)),
    Family("branch", _ops("b", "bl"), {"target": F(LABEL)}, ("target",), "Unconditional branch / call a label.",
           ({"op": "b", "target": "loop"},)),
    Family("bcond", BCOND_OPS, {"target": F(LABEL)}, ("target",), "Conditional branch on flags.",
           ({"op": "b.ne", "target": "loop"},)),
    Family("cbz", _ops("cbz", "cbnz"), {"src": F(REG, many=True), "target": F(LABEL)}, ("src", "target"),
           "Branch if src[0] is zero / non-zero.", ({"op": "cbz", "src": ["x0"], "target": "done"},)),
    Family("tbz", _ops("tbz", "tbnz"), {"src": F(REG, IMM, many=True, min=2, max=2), "target": F(LABEL)},
           ("src", "target"), "Branch if bit src[1] of src[0] is zero / non-zero.",
           ({"op": "tbnz", "src": ["x0", {"imm": 63}], "target": "neg"},)),
    Family("adr", _ops("adr"), {"dst": F(REG), "target": F(LABEL)}, ("dst", "target"), "dst = address of label.",
           ({"op": "adr", "dst": "x0", "target": "table"},)),
    Family("branch_reg", _ops("br", "blr"), {"src": F(REG, many=True)}, ("src",), "Branch / call via register.",
           ({"op": "blr", "src": ["x16"]},)),
    Family("ret", _ops("ret"), {"src": F(REG, many=True, min=0, max=1)}, (), "Return (via x30 unless src given).",
           ({"op": "ret"},)),
    Family("nop", _ops("nop"), {}, (), "No operation.", ({"op": "nop"},)),
]  # fmt: skip

SPEC = Spec(ISA_NAME, FAMILIES, {"reg": REG})

# --------------------------------------------------------------------------- field helpers


def _src(insn: Insn, i: int):
    return insn.f["src"][i], f"src[{i}]"


def num(r: Reg, path: str, *, sp: bool = False, zr: bool = True) -> int:
    """Register number for a slot. `sp`: slot reads 31 as SP; otherwise 31 is XZR."""
    if r.kind == "sp" and not sp:
        raise EncodeError(f"{r.name} is not allowed here (register 31 in this position means {'x' if r.width == 64 else 'w'}zr)", path)
    if r.kind == "zr" and (sp or not zr):
        raise EncodeError(f"{r.name} is not allowed here" + (" (register 31 in this position means sp)" if sp else ""), path)
    return r.num


def want_reg(v, path) -> Reg:
    if not isinstance(v, Reg):
        raise EncodeError(f"expected a register here, got {_show(v)}", path)
    return v


def _show(v) -> str:
    if isinstance(v, Reg):
        return v.name
    if isinstance(v, Imm):
        return f"immediate {v.value}"
    if isinstance(v, Shifted):
        return "a shifted register"
    if isinstance(v, Mem):
        return "a memory operand"
    return repr(v)


def same_width(pairs: list[tuple[Reg, str]]) -> int:
    (first, fpath), *rest = pairs
    for r, p in rest:
        if r.width != first.width:
            raise EncodeError(
                f"register {r.name} is {r.width}-bit but {first.name} ({fpath}) is {first.width}-bit; "
                "use all x or all w registers",
                p,
            )
    return first.width


def _signed(v: int, width: int) -> int:
    v &= (1 << width) - 1
    return v - (1 << width) if v >> (width - 1) else v


def encode_bitmask(value: int, width: int) -> tuple[int, int, int] | None:
    """Encode a logical immediate as (N, immr, imms), or None if not representable."""
    mask = (1 << width) - 1
    value &= mask
    if value in (0, mask):
        return None
    size = width
    while size > 2:
        half = size // 2
        if (value & ((1 << half) - 1)) != ((value >> half) & ((1 << half) - 1)):
            break
        size = half
    emask = (1 << size) - 1
    elem = value & emask
    ones = bin(elem).count("1")
    low = (1 << ones) - 1
    for r in range(size):
        if ((low >> r) | (low << (size - r))) & emask == elem:
            immr = r
            break
    else:
        return None
    n = 1 if size == 64 else 0
    imms = ((~(size * 2 - 1)) & 0x3F) | (ones - 1)
    return n, immr, imms


# --------------------------------------------------------------------------- encoders
# Each returns a list of 32-bit words; `ctx.resolve(label, path)` returns the label's address.


class Ctx:
    def __init__(self, pc: int, resolve):
        self.pc = pc
        self.resolve = resolve


def enc_addsub_imm(sf, sub, s, rd, rn, imm12, sh):
    return (sf << 31) | (sub << 30) | (s << 29) | 0x11000000 | (sh << 22) | (imm12 << 10) | (rn << 5) | rd


def enc_addsub_shift(sf, sub, s, rd, rn, rm, shift, amt):
    return (sf << 31) | (sub << 30) | (s << 29) | 0x0B000000 | (shift << 22) | (rm << 16) | (amt << 10) | (rn << 5) | rd


def enc_addsub_ext(sf, sub, s, rd, rn, rm, option, amt):
    return (sf << 31) | (sub << 30) | (s << 29) | 0x0B200000 | (rm << 16) | (option << 13) | (amt << 10) | (rn << 5) | rd


def enc_logic_shift(sf, opc, n, rd, rn, rm, shift, amt):
    return (sf << 31) | (opc << 29) | 0x0A000000 | (shift << 22) | (n << 21) | (rm << 16) | (amt << 10) | (rn << 5) | rd


def enc_logic_imm(sf, opc, n, immr, imms, rd, rn):
    return (sf << 31) | (opc << 29) | 0x12000000 | (n << 22) | (immr << 16) | (imms << 10) | (rn << 5) | rd


def enc_movw(sf, opc, hw, imm16, rd):
    return (sf << 31) | (opc << 29) | 0x12800000 | (hw << 21) | (imm16 << 5) | rd


def _addsub(sub: bool, s: bool, rd: Reg | None, rd_path, rn: Reg, rn_path, op2, op2_path) -> int:
    """add/sub/adds/subs/cmp/cmn with rd=None meaning the zero register (compare)."""
    regs = [(rn, rn_path)] + ([(rd, rd_path)] if rd else [])
    if isinstance(op2, Reg):
        regs.append((op2, op2_path))
    elif isinstance(op2, Shifted):
        regs.append((op2.reg, f"{op2_path}.reg"))
    w = same_width(regs)
    sf = int(w == 64)
    d = 31 if rd is None else None
    if isinstance(op2, Imm):
        imm, lsl = op2.value, op2.lsl
        if lsl not in (0, 12):
            raise EncodeError(f"add/sub immediates take lsl 0 or 12, got {lsl}", f"{op2_path}.lsl")
        if imm < 0:
            sub, imm = not sub, -imm
        sh = 1 if lsl == 12 else 0
        if not sh and imm > 4095:
            if imm % 4096 == 0 and imm >> 12 <= 4095:
                sh, imm = 1, imm >> 12
            else:
                raise EncodeError(
                    f"immediate {op2.value} does not fit add/sub imm12 (0..4095, or a multiple of 4096 up to "
                    f"{4095 << 12}); load it with mov into a scratch register first",
                    f"{op2_path}.imm",
                )
        elif imm > 4095:
            raise EncodeError(f"imm12 must be 0..4095 when lsl 12 is given, got {imm}", f"{op2_path}.imm")
        if d is None:
            d = num(rd, rd_path, sp=not s)
        return enc_addsub_imm(sf, int(sub), int(s), d, num(rn, rn_path, sp=True), imm, sh)
    if isinstance(op2, Reg):
        op2 = Shifted(op2, "lsl", 0)
    rm = num(op2.reg, op2_path, zr=True)
    uses_sp = rn.kind == "sp" or (rd is not None and rd.kind == "sp" and not s)
    if uses_sp:
        # Extended-register form: the only register form that can name sp.
        if op2.shift != "lsl" or op2.amount > 4:
            raise EncodeError("with sp as an operand only 'lsl' by 0..4 is allowed on the second register", op2_path)
        if d is None:
            d = num(rd, rd_path, sp=not s)
        option = 0b011 if sf else 0b010  # UXTX / UXTW (== LSL)
        return enc_addsub_ext(sf, int(sub), int(s), d, num(rn, rn_path, sp=True), rm, option, op2.amount)
    if op2.shift == "ror":
        raise EncodeError("add/sub do not accept ror shifts", f"{op2_path}.shift")
    if op2.amount >= w:
        raise EncodeError(f"shift amount must be < {w}", f"{op2_path}.amount")
    if d is None:
        d = num(rd, rd_path)
    return enc_addsub_shift(sf, int(sub), int(s), d, num(rn, rn_path), rm, SHIFTS[op2.shift], op2.amount)


LOGIC = {"and": (0, 0), "orr": (1, 0), "eor": (2, 0), "ands": (3, 0), "bic": (0, 1), "orn": (1, 1), "eon": (2, 1), "bics": (3, 1)}


def _logic(op: str, rd: Reg | None, rd_path, rn: Reg, rn_path, op2, op2_path) -> int:
    opc, n = LOGIC[op]
    regs = [(rn, rn_path)] + ([(rd, rd_path)] if rd else [])
    if isinstance(op2, Reg):
        regs.append((op2, op2_path))
    elif isinstance(op2, Shifted):
        regs.append((op2.reg, f"{op2_path}.reg"))
    w = same_width(regs)
    sf = int(w == 64)
    if isinstance(op2, Imm):
        if op2.lsl:
            raise EncodeError("logical immediates take no lsl", f"{op2_path}.lsl")
        lo = -(1 << (w - 1))
        if not lo <= op2.value < (1 << w):
            raise EncodeError(f"immediate {op2.value} does not fit in {w} bits", f"{op2_path}.imm")
        value = op2.value ^ ((1 << w) - 1) if n else op2.value  # bic/orn/eon: invert
        enc = encode_bitmask(value, w)
        if enc is None:
            raise EncodeError(
                f"{op2.value:#x} is not a valid {w}-bit logical immediate (must be a repeating pattern of a "
                "rotated contiguous run of ones, and not 0 or all-ones); load it with mov first",
                f"{op2_path}.imm",
            )
        nbit, immr, imms = enc
        d = 31 if rd is None else num(rd, rd_path, sp=opc != 3)
        return enc_logic_imm(sf, opc, nbit, immr, imms, d, num(rn, rn_path))
    if isinstance(op2, Reg):
        op2 = Shifted(op2, "lsl", 0)
    if op2.amount >= w:
        raise EncodeError(f"shift amount must be < {w}", f"{op2_path}.amount")
    d = 31 if rd is None else num(rd, rd_path)
    return enc_logic_shift(sf, opc, n, d, num(rn, rn_path), num(op2.reg, op2_path), SHIFTS[op2.shift], op2.amount)


def mov_imm_words(rd: Reg, path: str, value: int, vpath: str) -> list[int]:
    w = rd.width
    if not -(1 << (w - 1)) <= value < (1 << w):
        raise EncodeError(f"immediate {value} does not fit in a {w}-bit register", vpath)
    v = value & ((1 << w) - 1)
    sf = int(w == 64)
    if rd.kind == "sp":
        enc = encode_bitmask(v, w)
        if enc is None:
            raise EncodeError("mov to sp needs a bitmask immediate; mov into another register and then to sp", vpath)
        return [enc_logic_imm(sf, 1, *enc, 31, 31)]
    d = num(rd, path)
    halves = [(v >> (16 * i)) & 0xFFFF for i in range(w // 16)]
    zeros = sum(h == 0 for h in halves)
    ones = sum(h == 0xFFFF for h in halves)
    if zeros >= len(halves) - 1:
        hw = next((i for i, h in enumerate(halves) if h), 0)
        return [enc_movw(sf, 0b10, hw, halves[hw], d)]
    if ones >= len(halves) - 1:
        hw = next((i for i, h in enumerate(halves) if h != 0xFFFF), 0)
        return [enc_movw(sf, 0b00, hw, halves[hw] ^ 0xFFFF, d)]
    enc = encode_bitmask(v, w)
    if enc is not None:
        return [enc_logic_imm(sf, 1, *enc, d, 31)]  # orr rd, zr, #imm
    words = []
    if ones > zeros:
        skip, first_opc = 0xFFFF, 0b00
    else:
        skip, first_opc = 0, 0b10
    for i, h in enumerate(halves):
        if h == skip:
            continue
        if not words:
            words.append(enc_movw(sf, first_opc, i, h ^ 0xFFFF if first_opc == 0 else h, d))
        else:
            words.append(enc_movw(sf, 0b11, i, h, d))
    return words


def e_mov(insn: Insn, ctx):
    rd = insn.f["dst"]
    src, sp_ = _src(insn, 0)
    if isinstance(src, Imm):
        if src.lsl:
            raise EncodeError("mov takes a plain immediate; use movz/movk for explicit lsl", f"{sp_}.lsl")
        return mov_imm_words(rd, "dst", src.value, f"{sp_}.imm")
    same_width([(rd, "dst"), (src, sp_)])
    sf = int(rd.width == 64)
    if rd.kind == "sp" or src.kind == "sp":
        return [enc_addsub_imm(sf, 0, 0, num(rd, "dst", sp=True), num(src, sp_, sp=True), 0, 0)]
    return [enc_logic_shift(sf, 1, 0, num(rd, "dst"), 31, num(src, sp_), 0, 0)]


def e_movwide(insn: Insn, ctx):
    rd = insn.f["dst"]
    imm, p = _src(insn, 0)
    hw = imm.lsl // 16
    if imm.lsl % 16 or hw * 16 >= rd.width:
        raise EncodeError(f"lsl must be one of {list(range(0, rd.width, 16))} for a {rd.width}-bit register", f"{p}.lsl")
    if not 0 <= imm.value <= 0xFFFF:
        raise EncodeError(f"{insn.op} immediate must be 0..65535, got {imm.value}", f"{p}.imm")
    opc = {"movn": 0b00, "movz": 0b10, "movk": 0b11}[insn.op]
    return [enc_movw(int(rd.width == 64), opc, hw, imm.value, num(rd, "dst"))]


def e_arith(insn: Insn, ctx):
    rn, p0 = _src(insn, 0)
    rn = want_reg(rn, p0)
    op2, p1 = _src(insn, 1)
    return [_addsub(insn.op.startswith("sub"), insn.op.endswith("s"), insn.f["dst"], "dst", rn, p0, op2, p1)]


def e_compare(insn: Insn, ctx):
    rn, p0 = _src(insn, 0)
    rn = want_reg(rn, p0)
    op2, p1 = _src(insn, 1)
    if insn.op == "tst":
        return [_logic("ands", None, "", rn, p0, op2, p1)]
    return [_addsub(insn.op == "cmp", True, None, "", rn, p0, op2, p1)]


def e_logic(insn: Insn, ctx):
    rn, p0 = _src(insn, 0)
    rn = want_reg(rn, p0)
    op2, p1 = _src(insn, 1)
    return [_logic(insn.op, insn.f["dst"], "dst", rn, p0, op2, p1)]


def e_unary(insn: Insn, ctx):
    rd = insn.f["dst"]
    src, p = _src(insn, 0)
    zr = REGISTERS["xzr" if rd.width == 64 else "wzr"]
    if insn.op == "mvn":
        return [_logic("orn", rd, "dst", zr, "dst", src, p)]
    return [_addsub(True, insn.op == "negs", rd, "dst", zr, "dst", src, p)]


def e_shift(insn: Insn, ctx):
    rd = insn.f["dst"]
    rn, p0 = _src(insn, 0)
    rn = want_reg(rn, p0)
    amt, p1 = _src(insn, 1)
    if isinstance(amt, Imm):
        w = same_width([(rd, "dst"), (rn, p0)])
        sf = int(w == 64)
        s = amt.value
        if amt.lsl or not 0 <= s < w:
            raise EncodeError(f"shift amount must be 0..{w - 1}, got {s}", f"{p1}.imm")
        d, n = num(rd, "dst"), num(rn, p0)
        if insn.op == "ror":  # extr rd, rn, rn, #s
            return [(sf << 31) | 0x13800000 | (sf << 22) | (n << 16) | (s << 10) | (n << 5) | d]
        if insn.op == "lsl":
            immr, imms, base = (-s) % w, w - 1 - s, 0x53000000
        else:
            immr, imms, base = s, w - 1, 0x53000000 if insn.op == "lsr" else 0x13000000
        return [(sf << 31) | base | (sf << 22) | (immr << 16) | (imms << 10) | (n << 5) | d]
    if isinstance(amt, Shifted):
        raise EncodeError("shift amount must be a register or immediate", p1)
    w = same_width([(rd, "dst"), (rn, p0), (amt, p1)])
    op2 = {"lsl": 0, "lsr": 1, "asr": 2, "ror": 3}[insn.op]
    return [(int(w == 64) << 31) | 0x1AC02000 | (num(amt, p1) << 16) | (op2 << 10) | (num(rn, p0) << 5) | num(rd, "dst")]


def e_mul(insn: Insn, ctx):
    rd = insn.f["dst"]
    (rn, p0), (rm, p1) = _src(insn, 0), _src(insn, 1)
    w = same_width([(rd, "dst"), (rn, p0), (rm, p1)])
    sf = int(w == 64)
    d, n, m = num(rd, "dst"), num(rn, p0), num(rm, p1)
    if insn.op in ("smulh", "umulh"):
        if w != 64:
            raise EncodeError(f"{insn.op} only exists for x registers", "dst")
        return [(0x9BC07C00 if insn.op == "umulh" else 0x9B407C00) | (m << 16) | (n << 5) | d]
    if insn.op in ("sdiv", "udiv"):
        return [(sf << 31) | (0x1AC00C00 if insn.op == "sdiv" else 0x1AC00800) | (m << 16) | (n << 5) | d]
    o0 = int(insn.op == "mneg")
    return [(sf << 31) | 0x1B000000 | (m << 16) | (o0 << 15) | (31 << 10) | (n << 5) | d]


def e_muladd(insn: Insn, ctx):
    rd = insn.f["dst"]
    (rn, p0), (rm, p1), (ra, p2) = _src(insn, 0), _src(insn, 1), _src(insn, 2)
    w = same_width([(rd, "dst"), (rn, p0), (rm, p1), (ra, p2)])
    o0 = int(insn.op == "msub")
    return [
        (int(w == 64) << 31) | 0x1B000000 | (num(rm, p1) << 16) | (o0 << 15) | (num(ra, p2) << 10) | (num(rn, p0) << 5) | num(rd, "dst")
    ]


CSEL = {"csel": 0x1A800000, "csinc": 0x1A800400, "csinv": 0x5A800000, "csneg": 0x5A800400}


def _csel(op, rd, rn, rm, cond, paths):
    w = same_width([(rd, paths[0]), (rn, paths[1]), (rm, paths[2])])
    return (int(w == 64) << 31) | CSEL[op] | (num(rm, paths[2]) << 16) | (cond << 12) | (num(rn, paths[1]) << 5) | num(rd, paths[0])


def _inv_cond(name: str, path: str) -> int:
    if name in ("al", "nv"):
        raise EncodeError(f"condition {name!r} cannot be used here (it cannot be inverted)", path)
    return CONDS[name] ^ 1


def e_csel(insn: Insn, ctx):
    (rn, p0), (rm, p1) = _src(insn, 0), _src(insn, 1)
    return [_csel(insn.op, insn.f["dst"], rn, rm, CONDS[insn.f["cond"]], ("dst", p0, p1))]


def e_cset(insn: Insn, ctx):
    rd = insn.f["dst"]
    zr = REGISTERS["xzr" if rd.width == 64 else "wzr"]
    op = "csinc" if insn.op == "cset" else "csinv"
    return [_csel(op, rd, zr, zr, _inv_cond(insn.f["cond"], "cond"), ("dst", "dst", "dst"))]


def e_cinc(insn: Insn, ctx):
    rn, p0 = _src(insn, 0)
    op = {"cinc": "csinc", "cinv": "csinv", "cneg": "csneg"}[insn.op]
    return [_csel(op, insn.f["dst"], rn, rn, _inv_cond(insn.f["cond"], "cond"), ("dst", p0, p0))]


# (size log2, opc) for each load/store op, keyed by (op, register width)
LDST = {
    ("str", 64): (3, 0), ("str", 32): (2, 0), ("ldr", 64): (3, 1), ("ldr", 32): (2, 1),
    ("strb", 32): (0, 0), ("ldrb", 32): (0, 1), ("strh", 32): (1, 0), ("ldrh", 32): (1, 1),
    ("ldrsb", 64): (0, 2), ("ldrsb", 32): (0, 3), ("ldrsh", 64): (1, 2), ("ldrsh", 32): (1, 3),
    ("ldrsw", 64): (2, 2),
}  # fmt: skip


def _ldst(op: str, rt: Reg, rt_path: str, mem: Mem, mpath: str) -> int:
    key = (op, rt.width)
    if key not in LDST:
        other = "w" if rt.width == 64 else "x"
        raise EncodeError(f"{op} needs a{'n' if other == 'x' else ''} {other} register, got {rt.name}", rt_path)
    size, opc = LDST[key]
    nbytes = 1 << size
    t = num(rt, rt_path)
    n = num(mem.base, f"{mpath}.mem.base", sp=True)
    head = (size << 30) | (opc << 22)
    if mem.index is not None:
        idx = mem.index
        if idx.width == 64:
            ext = mem.extend or "lsl"
            if ext not in ("lsl", "sxtx"):
                raise EncodeError(f"extend {ext!r} needs a w index register", f"{mpath}.mem.extend")
            option = 0b011 if ext == "lsl" else 0b111
        else:
            if mem.extend not in ("uxtw", "sxtw"):
                raise EncodeError("a w index register needs extend 'uxtw' or 'sxtw'", f"{mpath}.mem.extend")
            option = 0b010 if mem.extend == "uxtw" else 0b110
        if mem.scale not in (1, nbytes):
            raise EncodeError(f"scale must be 1 or {nbytes} (the access size) for {op}", f"{mpath}.mem.scale")
        s = int(mem.scale == nbytes and nbytes > 1)
        return head | 0x38200800 | (num(idx, f"{mpath}.mem.index") << 16) | (option << 13) | (s << 12) | (n << 5) | t
    if mem.writeback:
        if rt.kind != "zr" and rt.num == mem.base.num and mem.base.kind == "gpr":
            raise EncodeError(f"writeback with {rt.name} as both data and base register is unpredictable", mpath)
        if not -256 <= mem.disp <= 255:
            raise EncodeError(f"{mem.writeback}-index disp must be -256..255, got {mem.disp}", f"{mpath}.mem.disp")
        mode = 0x38000C00 if mem.writeback == "pre" else 0x38000400
        return head | mode | ((mem.disp & 0x1FF) << 12) | (n << 5) | t
    if mem.disp % nbytes == 0 and 0 <= mem.disp // nbytes <= 4095:
        return head | 0x39000000 | ((mem.disp // nbytes) << 10) | (n << 5) | t
    if -256 <= mem.disp <= 255:  # ldur/stur: unscaled 9-bit signed offset
        return head | 0x38000000 | ((mem.disp & 0x1FF) << 12) | (n << 5) | t
    raise EncodeError(
        f"disp {mem.disp} out of range for {op}: use a multiple of {nbytes} in [0, {4095 * nbytes}] or a value "
        "in [-256, 255]; otherwise add the offset to the base register first",
        f"{mpath}.mem.disp",
    )


def e_load(insn: Insn, ctx):
    mem, p = _src(insn, 0)
    return [_ldst(insn.op, insn.f["dst"], "dst", mem, p)]


def e_store(insn: Insn, ctx):
    (rt, p0), (mem, p1) = _src(insn, 0), _src(insn, 1)
    rt = want_reg(rt, p0)
    if not isinstance(mem, Mem):
        raise EncodeError(f"expected a memory operand, got {_show(mem)}", p1)
    return [_ldst(insn.op, rt, p0, mem, p1)]


def _pair(load: bool, r1: Reg, p1: str, r2: Reg, p2: str, mem: Mem, mp: str) -> int:
    w = same_width([(r1, p1), (r2, p2)])
    size = 8 if w == 64 else 4
    if mem.index is not None:
        raise EncodeError("ldp/stp take no index register", f"{mp}.mem.index")
    if mem.disp % size or not -64 <= mem.disp // size <= 63:
        raise EncodeError(f"disp must be a multiple of {size} in [{-64 * size}, {63 * size}], got {mem.disp}", f"{mp}.mem.disp")
    t1, t2 = num(r1, p1), num(r2, p2)
    if load and t1 == t2:
        raise EncodeError("ldp with the same register twice is unpredictable", p2)
    if mem.writeback and mem.base.kind == "gpr" and mem.base.num in (t1, t2):
        raise EncodeError("writeback with the base register also in the pair is unpredictable", mp)
    mode = {"post": 0b01, None: 0b10, "pre": 0b11}[mem.writeback]
    opc = 0b10 if w == 64 else 0b00
    imm7 = (mem.disp // size) & 0x7F
    return (opc << 30) | 0x28000000 | (mode << 23) | (int(load) << 22) | (imm7 << 15) | (t2 << 10) | (num(mem.base, f"{mp}.mem.base", sp=True) << 5) | t1


def e_ldp(insn: Insn, ctx):
    (r1, r2), (mem, mp) = insn.f["dst"], _src(insn, 0)
    return [_pair(True, r1, "dst[0]", r2, "dst[1]", mem, mp)]


def e_stp(insn: Insn, ctx):
    (r1, p1), (r2, p2), (mem, mp) = _src(insn, 0), _src(insn, 1), _src(insn, 2)
    for v, p in ((r1, p1), (r2, p2)):
        want_reg(v, p)
    if not isinstance(mem, Mem):
        raise EncodeError(f"expected a memory operand, got {_show(mem)}", mp)
    return [_pair(False, r1, p1, r2, p2, mem, mp)]


def branch_offset(ctx: Ctx, label: str, bits: int, path: str = "target") -> int:
    """Word offset to `label`, checked against a signed `bits`-bit field."""
    delta = ctx.resolve(label, path) - ctx.pc
    if delta % 4:
        raise EncodeError(f"branch target {label!r} is not 4-byte aligned", path)
    words = delta // 4
    lim = 1 << (bits - 1)
    if not -lim <= words < lim:
        raise EncodeError(
            f"label {label!r} is {delta} bytes away; out of range for this branch (±{lim * 4} bytes)", path
        )
    return words & ((1 << bits) - 1)


def e_branch(insn: Insn, ctx):
    return [(0x94000000 if insn.op == "bl" else 0x14000000) | branch_offset(ctx, insn.f["target"], 26)]


def e_bcond(insn: Insn, ctx):
    return [0x54000000 | (branch_offset(ctx, insn.f["target"], 19) << 5) | CONDS[insn.op[2:]]]


def e_cbz(insn: Insn, ctx):
    rt, p = _src(insn, 0)
    base = 0x35000000 if insn.op == "cbnz" else 0x34000000
    return [(int(rt.width == 64) << 31) | base | (branch_offset(ctx, insn.f["target"], 19) << 5) | num(rt, p)]


def e_tbz(insn: Insn, ctx):
    (rt, p0), (bit, p1) = _src(insn, 0), _src(insn, 1)
    rt = want_reg(rt, p0)
    if not isinstance(bit, Imm) or bit.lsl or not 0 <= bit.value < rt.width:
        raise EncodeError(f"bit number must be an immediate 0..{rt.width - 1}", p1)
    b = bit.value
    op = int(insn.op == "tbnz")
    return [((b >> 5) << 31) | 0x36000000 | (op << 24) | ((b & 31) << 19) | (branch_offset(ctx, insn.f["target"], 14) << 5) | num(rt, p0)]


def e_adr(insn: Insn, ctx):
    rd = insn.f["dst"]
    if rd.width != 64:
        raise EncodeError("adr needs an x register", "dst")
    delta = ctx.resolve(insn.f["target"], "target") - ctx.pc
    if not -(1 << 20) <= delta < (1 << 20):
        raise EncodeError(f"label is {delta} bytes away; adr reaches ±1MB", "target")
    delta &= (1 << 21) - 1
    return [0x10000000 | ((delta & 3) << 29) | ((delta >> 2) << 5) | num(rd, "dst")]


def e_branch_reg(insn: Insn, ctx):
    rn, p = _src(insn, 0)
    if rn.width != 64:
        raise EncodeError("branch register must be an x register", p)
    return [(0xD63F0000 if insn.op == "blr" else 0xD61F0000) | (num(rn, p) << 5)]


def e_ret(insn: Insn, ctx):
    src = insn.f.get("src") or []
    rn = src[0] if src else REGISTERS["x30"]
    if rn.width != 64:
        raise EncodeError("ret needs an x register", "src[0]")
    return [0xD65F0000 | (num(rn, "src[0]") << 5)]


ENCODERS = {
    "mov": e_mov, "movwide": e_movwide, "arith": e_arith, "compare": e_compare, "logic": e_logic,
    "unary": e_unary, "shift": e_shift, "mul": e_mul, "muladd": e_muladd, "csel": e_csel,
    "cset": e_cset, "cinc": e_cinc, "load": e_load, "store": e_store, "ldp": e_ldp, "stp": e_stp,
    "branch": e_branch, "bcond": e_bcond, "cbz": e_cbz, "tbz": e_tbz, "adr": e_adr,
    "branch_reg": e_branch_reg, "ret": e_ret, "nop": lambda insn, ctx: [0xD503201F],
}  # fmt: skip
assert set(ENCODERS) == {f.name for f in FAMILIES}


def encode_insn(insn: Insn, pc: int, resolve) -> list[int]:
    return ENCODERS[insn.family.name](insn, Ctx(pc, resolve))


# --------------------------------------------------------------------------- program


def assemble(instructions, *, base: int = 0x100000, allow_undefined: bool = False) -> Program:
    """Encode a whole structured program. Raises AssemblyError listing every bad instruction.

    With `allow_undefined`, branches to not-yet-defined labels are encoded as branches to
    themselves and the label names are reported in `Program.pending` (streaming preview).
    """
    instructions = normalize_program(instructions)
    items, errors = parse_program(SPEC, instructions)

    # Pass 1: layout. Instruction sizes never depend on label distances.
    labels: dict[str, int] = {}
    sizes: dict[int, int] = {}
    off = 0

    def dummy(name, path):
        return base + off

    for item in items:
        if isinstance(item, Label):
            labels[item.name] = off
            continue
        try:
            sizes[item.index] = 4 * len(encode_insn(item, base + off, dummy))
        except EncodeError as exc:
            errors.append(InsnError(item.index, exc.path, exc.message, item.raw))
            sizes[item.index] = 4
        off += sizes[item.index]

    # Pass 2: resolve labels.
    pending: list[str] = []
    entries, code, off = [], bytearray(), 0
    failed = {e.index for e in errors}
    for item in items:
        if isinstance(item, Label):
            continue
        here = off

        def resolve(name, path, here=here):
            if name in labels:
                return base + labels[name]
            if allow_undefined:
                pending.append(name)
                return base + here
            raise undefined_label(name, labels, path)

        if item.index not in failed:
            try:
                words = encode_insn(item, base + here, resolve)
                data = b"".join(w.to_bytes(4, "little") for w in words)
                entries.append(Entry(item.index, here, data, item.raw))
                code += data
            except EncodeError as exc:
                errors.append(InsnError(item.index, exc.path, exc.message, item.raw))
        off += sizes[item.index]
    if errors:
        raise AssemblyError(sorted(errors, key=lambda e: e.index))
    return Program(ISA_NAME, bytes(code), base, entries, labels, sorted(set(pending)))
