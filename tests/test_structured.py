"""Structured emission: encoders cross-checked against keystone/capstone, programs run under
Unicorn against C references, schemas validated, errors and streaming exercised."""

import json
import random

import fastjsonschema
import pytest

from bca.difftest import buffer_differential, compile_c_reference, differential, int_inputs
from bca.emu import run_function
from bca.isa import AARCH64, X86_64
from bca.structured import AssemblyError, Session, assemble, input_schemas, tool_definitions
from bca.structured import a64, x64
from bca.structured.a64 import encode_bitmask

BASE = 0x100000


def ks_bytes(isa, text):
    enc, _ = isa.assembler().asm(text, BASE)
    return bytes(enc)


def disasm(isa, code):
    return [f"{i.mnemonic} {i.op_str}".strip() for i in isa.disassembler().disasm(code, BASE)]


def errors_of(isa, program):
    with pytest.raises(AssemblyError) as info:
        assemble(isa, program)
    return info.value.errors


# =========================================================================== AArch64 vs keystone


def _rand_a64(rng: random.Random):
    """One random (structured instruction, keystone text) pair; all encodings unambiguous."""
    w = rng.choice([64, 64, 32])
    p = "x" if w == 64 else "w"

    def r():
        return f"{p}{rng.randrange(31)}"

    kind = rng.choice(
        "arith_reg arith_shift arith_imm arith_imm12 cmp logic_reg logic_imm shift_imm shift_reg mul madd "
        "csel cset cinc movwide mov_reg neg mvn ldst_off ldst_reg ldst_wb ldur pair sp tst".split()
    )
    d, n, m, a = r(), r(), r(), r()
    if kind == "arith_reg":
        op = rng.choice(["add", "sub", "adds", "subs"])
        return {"op": op, "dst": d, "src": [n, m]}, f"{op} {d}, {n}, {m}"
    if kind == "arith_shift":
        op = rng.choice(["add", "sub", "adds", "subs"])
        sh, amt = rng.choice(["lsl", "lsr", "asr"]), rng.randrange(w)
        return ({"op": op, "dst": d, "src": [n, {"reg": m, "shift": sh, "amount": amt}]}, f"{op} {d}, {n}, {m}, {sh} #{amt}")
    if kind == "arith_imm":
        op, imm = rng.choice(["add", "sub", "adds", "subs"]), rng.randrange(4096)
        return {"op": op, "dst": d, "src": [n, {"imm": imm}]}, f"{op} {d}, {n}, #{imm}"
    if kind == "arith_imm12":
        op, imm = rng.choice(["add", "sub"]), rng.randrange(1, 4096)
        return {"op": op, "dst": d, "src": [n, {"imm": imm, "lsl": 12}]}, f"{op} {d}, {n}, #{imm}, lsl #12"
    if kind == "cmp":
        op = rng.choice(["cmp", "cmn"])
        if rng.random() < 0.5:
            imm = rng.randrange(4096)
            return {"op": op, "src": [n, {"imm": imm}]}, f"{op} {n}, #{imm}"
        return {"op": op, "src": [n, m]}, f"{op} {n}, {m}"
    if kind == "tst":
        return {"op": "tst", "src": [n, m]}, f"tst {n}, {m}"
    if kind == "logic_reg":
        op = rng.choice(["and", "orr", "eor", "ands", "bic", "orn", "eon", "bics"])
        sh, amt = rng.choice(["lsl", "lsr", "asr", "ror"]), rng.randrange(w)
        return ({"op": op, "dst": d, "src": [n, {"reg": m, "shift": sh, "amount": amt}]}, f"{op} {d}, {n}, {m}, {sh} #{amt}")
    if kind == "logic_imm":
        op = rng.choice(["and", "orr", "eor", "ands"])
        esize = rng.choice([s for s in (2, 4, 8, 16, 32, 64) if s <= w])
        ones = rng.randrange(1, esize)
        rot = rng.randrange(esize)
        elem = ((1 << ones) - 1)
        elem = ((elem >> rot) | (elem << (esize - rot))) & ((1 << esize) - 1)
        val = 0
        for i in range(w // esize):
            val |= elem << (i * esize)
        return {"op": op, "dst": d, "src": [n, {"imm": val}]}, f"{op} {d}, {n}, #{val:#x}"
    if kind == "shift_imm":
        op, s = rng.choice(["lsl", "lsr", "asr", "ror"]), rng.randrange(w)
        return {"op": op, "dst": d, "src": [n, {"imm": s}]}, f"{op} {d}, {n}, #{s}"
    if kind == "shift_reg":
        op = rng.choice(["lsl", "lsr", "asr", "ror"])
        return {"op": op, "dst": d, "src": [n, m]}, f"{op} {d}, {n}, {m}"
    if kind == "mul":
        op = rng.choice(["mul", "mneg", "sdiv", "udiv"] + (["smulh", "umulh"] if w == 64 else []))
        return {"op": op, "dst": d, "src": [n, m]}, f"{op} {d}, {n}, {m}"
    if kind == "madd":
        op = rng.choice(["madd", "msub"])
        return {"op": op, "dst": d, "src": [n, m, a]}, f"{op} {d}, {n}, {m}, {a}"
    conds = [c for c in a64.CONDS if c not in ("al", "nv")]
    if kind == "csel":
        op, c = rng.choice(["csel", "csinc", "csinv", "csneg"]), rng.choice(conds)
        return {"op": op, "dst": d, "src": [n, m], "cond": c}, f"{op} {d}, {n}, {m}, {c}"
    if kind == "cset":
        op, c = rng.choice(["cset", "csetm"]), rng.choice(conds)
        return {"op": op, "dst": d, "cond": c}, f"{op} {d}, {c}"
    if kind == "cinc":
        op, c = rng.choice(["cinc", "cinv", "cneg"]), rng.choice(conds)
        return {"op": op, "dst": d, "src": [n], "cond": c}, f"{op} {d}, {n}, {c}"
    if kind == "movwide":
        op, imm, sh = rng.choice(["movz", "movk", "movn"]), rng.randrange(65536), rng.randrange(w // 16) * 16
        return {"op": op, "dst": d, "src": [{"imm": imm, "lsl": sh}]}, f"{op} {d}, #{imm}, lsl #{sh}"
    if kind == "mov_reg":
        return {"op": "mov", "dst": d, "src": [n]}, f"mov {d}, {n}"
    if kind == "neg":
        op = rng.choice(["neg", "negs"])
        return {"op": op, "dst": d, "src": [n]}, f"{op} {d}, {n}"
    if kind == "mvn":
        return {"op": "mvn", "dst": d, "src": [n]}, f"mvn {d}, {n}"
    base = f"x{rng.randrange(31)}"
    if kind in ("ldst_off", "ldst_reg", "ldst_wb", "ldur"):
        op, reg_w = rng.choice(
            [("ldr", 64), ("ldr", 32), ("str", 64), ("str", 32), ("ldrb", 32), ("strb", 32), ("ldrh", 32),
             ("strh", 32), ("ldrsb", 64), ("ldrsb", 32), ("ldrsh", 64), ("ldrsh", 32), ("ldrsw", 64)]
        )  # fmt: skip
        t = f"{'x' if reg_w == 64 else 'w'}{rng.randrange(31)}"
        size = {"b": 1, "h": 2}.get(op[-1], 4 if op == "ldrsw" or reg_w == 32 else 8)
        if kind in ("ldst_wb",):
            while t[1:] == base[1:]:
                base = f"x{rng.randrange(31)}"
        field = [t] if op.startswith("st") else None
        if kind == "ldst_off":
            disp = rng.randrange(4096) * size
            mem, text = {"base": base, "disp": disp}, f"[{base}, #{disp}]"
        elif kind == "ldur":
            disp = rng.randrange(-256, 256)
            if disp >= 0 and disp % size == 0:
                disp = -1
            mem, text = {"base": base, "disp": disp}, f"[{base}, #{disp}]"
        elif kind == "ldst_reg":
            idx = f"x{rng.randrange(31)}"
            scale = rng.choice([1, size])
            if scale == 1:
                mem, text = {"base": base, "index": idx}, f"[{base}, {idx}]"
            else:
                sh = scale.bit_length() - 1
                mem, text = {"base": base, "index": idx, "scale": scale}, f"[{base}, {idx}, lsl #{sh}]"
        else:
            disp, wb = rng.randrange(-256, 256), rng.choice(["pre", "post"])
            mem = {"base": base, "disp": disp, "writeback": wb}
            text = f"[{base}, #{disp}]!" if wb == "pre" else f"[{base}], #{disp}"
        if field:
            return {"op": op, "src": [t, {"mem": mem}]}, f"{op} {t}, {text}"
        return {"op": op, "dst": t, "src": [{"mem": mem}]}, f"{op} {t}, {text}"
    if kind == "pair":
        size = w // 8
        t1, t2 = rng.sample(range(31), 2)
        t1, t2 = f"{p}{t1}", f"{p}{t2}"
        base = rng.choice(["sp", "x" + str(rng.choice([i for i in range(31) if str(i) not in (t1[1:], t2[1:])]))])
        disp = rng.randrange(-64, 64) * size
        wb = rng.choice([None, "pre", "post"])
        mem = {"base": base, "disp": disp} | ({"writeback": wb} if wb else {})
        text = {None: f"[{base}, #{disp}]", "pre": f"[{base}, #{disp}]!", "post": f"[{base}], #{disp}"}[wb]
        if rng.random() < 0.5:
            return {"op": "stp", "src": [t1, t2, {"mem": mem}]}, f"stp {t1}, {t2}, {text}"
        return {"op": "ldp", "dst": [t1, t2], "src": [{"mem": mem}]}, f"ldp {t1}, {t2}, {text}"
    # sp forms
    choice = rng.randrange(4)
    imm = rng.randrange(4096)
    if choice == 0:
        return {"op": "add", "dst": "sp", "src": ["sp", {"imm": imm}]}, f"add sp, sp, #{imm}"
    if choice == 1:
        return {"op": "sub", "dst": "x29", "src": ["sp", {"imm": imm}]}, f"sub x29, sp, #{imm}"
    if choice == 2:
        return {"op": "mov", "dst": "x29", "src": ["sp"]}, "mov x29, sp"
    return {"op": "add", "dst": "x0", "src": ["sp", "x1"]}, "add x0, sp, x1"


def test_a64_fuzz_matches_keystone():
    rng = random.Random(1234)
    mismatches = []
    for _ in range(4000):
        insn, text = _rand_a64(rng)
        ours = assemble("aarch64", [insn]).code
        ref = ks_bytes(AARCH64, text)
        if ours != ref:
            mismatches.append((text, ours.hex(), ref.hex(), disasm(AARCH64, ours)))
    assert not mismatches, mismatches[:10]


A64_TABLE = [
    ({"op": "ret"}, "ret"),
    ({"op": "ret", "src": ["x1"]}, "ret x1"),
    ({"op": "nop"}, "nop"),
    ({"op": "br", "src": ["x16"]}, "br x16"),
    ({"op": "blr", "src": ["x8"]}, "blr x8"),
    ({"op": "add", "dst": "x0", "src": ["x1", {"imm": -4}]}, "sub x0, x1, #4"),
    ({"op": "cmp", "src": ["x0", {"imm": -1}]}, "cmn x0, #1"),
    ({"op": "add", "dst": "x0", "src": ["x1", {"imm": 8192}]}, "add x0, x1, #2, lsl #12"),
    ({"op": "cmp", "src": ["sp", "x1"]}, "cmp sp, x1"),
    ({"op": "mov", "dst": "sp", "src": ["x0"]}, "mov sp, x0"),
    ({"op": "mov", "dst": "x0", "src": ["xzr"]}, "mov x0, xzr"),
    ({"op": "mov", "dst": "w0", "src": [{"imm": 0}]}, "movz w0, #0"),
    ({"op": "mov", "dst": "x0", "src": [{"imm": -1}]}, "movn x0, #0"),
    ({"op": "mov", "dst": "x0", "src": [{"imm": 0x10000}]}, "movz x0, #1, lsl #16"),
    ({"op": "mov", "dst": "x0", "src": [{"imm": 0xFFFF_FFFF_FFFF_1234}]}, "movn x0, #0xedcb"),
    ({"op": "mov", "dst": "x0", "src": [{"imm": 0x5555_5555_5555_5555}]}, "orr x0, xzr, #0x5555555555555555"),
    ({"op": "and", "dst": "sp", "src": ["x0", {"imm": -16}]}, "and sp, x0, #0xfffffffffffffff0"),
    ({"op": "bic", "dst": "x0", "src": ["x1", {"imm": 15}]}, "and x0, x1, #0xfffffffffffffff0"),
    ({"op": "tst", "src": ["x0", {"imm": 1}]}, "tst x0, #1"),
    ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "x1", "index": "w2", "extend": "sxtw", "scale": 8}}]},
     "ldr x0, [x1, w2, sxtw #3]"),
    ({"op": "ldr", "dst": "w0", "src": [{"mem": {"base": "x1", "index": "w2", "extend": "uxtw"}}]},
     "ldr w0, [x1, w2, uxtw]"),
    ({"op": "str", "src": ["xzr", {"mem": {"base": "sp", "disp": 8}}]}, "str xzr, [sp, #8]"),
    ({"op": "str", "src": ["x30", {"mem": {"base": "sp", "disp": -16, "writeback": "pre"}}]}, "str x30, [sp, #-16]!"),
    ({"op": "stp", "src": ["fp", "lr", {"mem": {"base": "sp", "disp": -16, "writeback": "pre"}}]},
     "stp x29, x30, [sp, #-16]!"),
    ({"op": "ldp", "dst": ["x29", "x30"], "src": [{"mem": {"base": "sp", "disp": 16, "writeback": "post"}}]},
     "ldp x29, x30, [sp], #16"),
    ({"op": "cset", "dst": "w0", "cond": "hs"}, "cset w0, hs"),
    ({"op": "csel", "dst": "x0", "src": ["x1", "x2"], "cond": "al"}, "csel x0, x1, x2, al"),
]


@pytest.mark.parametrize("insn,text", A64_TABLE, ids=[t for _, t in A64_TABLE])
def test_a64_table_matches_keystone(insn, text):
    assert assemble("aarch64", [insn]).code.hex() == ks_bytes(AARCH64, text).hex()


def test_a64_branches_match_keystone():
    body = [{"label": "top"}]
    text = ["top:"]
    for c in a64.BCOND_OPS:
        body.append({"op": c, "target": "end"})
        text.append(f"{c} end")
        body.append({"op": c, "target": "top"})
        text.append(f"{c} top")
    for op in ("cbz", "cbnz"):
        for r in ("x3", "w4"):
            body.append({"op": op, "src": [r], "target": "end"})
            text.append(f"{op} {r}, end")
    body += [
        {"op": "tbz", "src": ["x5", {"imm": 40}], "target": "top"},
        {"op": "tbnz", "src": ["w6", {"imm": 3}], "target": "end"},
        {"op": "b", "target": "top"},
        {"op": "bl", "target": "end"},
        {"op": "adr", "dst": "x7", "target": "top"},
        {"label": "end"},
        {"op": "ret"},
    ]
    text += ["tbz x5, #40, top", "tbnz w6, #3, end", "b top", "bl end", "adr x7, top", "end:", "ret"]
    assert assemble("aarch64", body).code.hex() == ks_bytes(AARCH64, "\n".join(text)).hex()


def test_bitmask_encoder_exhaustive_small_patterns():
    # Every valid 32-bit logical immediate round-trips through keystone's encoding.
    rng = random.Random(7)
    for _ in range(300):
        esize = rng.choice([2, 4, 8, 16, 32])
        ones = rng.randrange(1, esize)
        rot = rng.randrange(esize)
        elem = ((1 << ones) - 1)
        elem = ((elem >> rot) | (elem << (esize - rot))) & ((1 << esize) - 1)
        val = sum(elem << (i * esize) for i in range(32 // esize))
        n, immr, imms = encode_bitmask(val, 32)
        word = 0x12000000 | (1 << 29) | (n << 22) | (immr << 16) | (imms << 10) | 0x20
        assert word.to_bytes(4, "little") == ks_bytes(AARCH64, f"orr w0, w1, #{val:#x}")
    assert encode_bitmask(0, 64) is None and encode_bitmask(2**64 - 1, 64) is None
    assert encode_bitmask(0b101, 64) is None


@pytest.mark.parametrize(
    "value",
    [0, 1, 0xFFFF, 0x10000, 0x12345678, -1, -2, -0x12345, 0x1234_5678_9ABC_DEF0, 2**63, 2**64 - 1,
     0xFFFF_0000_FFFF_0000, 0x0F0F_0F0F_0F0F_0F0F, 0xDEAD_0000_0000_BEEF, -(2**63)],
)  # fmt: skip
def test_a64_mov_imm_materializes_any_value(value):
    prog = assemble("aarch64", [{"op": "mov", "dst": "x0", "src": [{"imm": value}]}, {"op": "ret"}])
    assert len(prog.code) <= 5 * 4
    assert run_function(AARCH64, prog.code).ret == value % 2**64


# =========================================================================== x86-64 vs keystone


def _rand_x64(rng: random.Random):
    bits = rng.choice([64, 64, 32, 16, 8])
    pool = [n for n, r in x64.REGISTERS.items() if r.bits == bits and n not in ("ah", "bh", "ch", "dh")]
    r = lambda: rng.choice(pool)  # noqa: E731
    sizeword = {8: "byte", 16: "word", 32: "dword", 64: "qword"}[bits]
    b64 = [n for n in x64.REG64_NAMES]
    base = rng.choice(b64)
    idx = rng.choice([n for n in b64 if n != "rsp"])
    scale = rng.choice([1, 2, 4, 8])
    disp = rng.choice([0, 8, -8, 1000, -100000])
    mem = {"base": base, "index": idx, "scale": scale, "disp": disp}
    mtext = f"[{base} + {idx}*{scale} + ({disp})]"
    imm = rng.choice([0, 1, -1, 127, -128, 100, 1000, -70000, 2**31 - 1, -(2**31)])
    if bits < 32:
        imm = max(min(imm, 2 ** (bits - 1) - 1), -(2 ** (bits - 1)))
    kind = rng.choice("alu_rr alu_ri alu_rm alu_mr alu_mi lea imul imul3 shift unary cmov setcc ext push pop muldiv".split())
    op = rng.choice(["mov", "add", "sub", "and", "or", "xor", "cmp", "adc", "sbb"])
    if kind == "alu_rr":
        op = rng.choice([op, "test", "xchg"])
        a, b = r(), r()
        return {"op": op, "dst": a, "src": [b]}, f"{op} {a}, {b}"
    if kind == "alu_ri":
        op = rng.choice([op, "test"])
        a = r()
        return {"op": op, "dst": a, "src": [{"imm": imm}]}, f"{op} {a}, {imm}"
    if kind == "alu_rm":
        a = r()
        return {"op": op, "dst": a, "src": [{"mem": mem}]}, f"{op} {a}, {sizeword} ptr {mtext}"
    if kind == "alu_mr":
        a = r()
        return {"op": op, "dst": {"mem": mem}, "src": [a]}, f"{op} {sizeword} ptr {mtext}, {a}"
    if kind == "alu_mi":
        return ({"op": op, "dst": {"mem": mem | {"size": bits // 8}}, "src": [{"imm": imm}]},
                f"{op} {sizeword} ptr {mtext}, {imm}")  # fmt: skip
    if kind == "lea":
        a = rng.choice([n for n, x in x64.REGISTERS.items() if x.bits in (32, 64)])
        return {"op": "lea", "dst": a, "src": [{"mem": mem}]}, f"lea {a}, {mtext}"
    if bits == 8 and kind in ("imul", "imul3", "cmov"):
        kind = "unary"
    if kind == "imul":
        a, b = r(), r()
        return {"op": "imul", "dst": a, "src": [b]}, f"imul {a}, {b}"
    if kind == "imul3":
        a = r()
        return ({"op": "imul", "dst": a, "src": [{"mem": mem}, {"imm": imm}]}, f"imul {a}, {sizeword} ptr {mtext}, {imm}")
    if kind == "shift":
        op = rng.choice(["shl", "shr", "sar", "rol", "ror"])
        a = r()
        if rng.random() < 0.5:
            return {"op": op, "dst": a, "src": ["cl"]}, f"{op} {a}, cl"
        c = rng.randrange(2, bits)
        return {"op": op, "dst": a, "src": [{"imm": c}]}, f"{op} {a}, {c}"
    if kind == "unary":
        op = rng.choice(["inc", "dec", "neg", "not"])
        if rng.random() < 0.5:
            return ({"op": op, "dst": {"mem": mem | {"size": bits // 8}}}, f"{op} {sizeword} ptr {mtext}")
        a = r()
        return {"op": op, "dst": a}, f"{op} {a}"
    if kind == "cmov":
        cc = rng.choice(x64.CMOV_OPS)
        a, b = r(), r()
        return {"op": cc, "dst": a, "src": [b]}, f"{cc} {a}, {b}"
    if kind == "setcc":
        cc = rng.choice(x64.SET_OPS)
        a = rng.choice([n for n, x in x64.REGISTERS.items() if x.bits == 8 and n not in ("ah", "bh", "ch", "dh")])
        return {"op": cc, "dst": a}, f"{cc} {a}"
    if kind == "ext":
        op = rng.choice(["movzx", "movsx"])
        d = rng.choice([n for n, x in x64.REGISTERS.items() if x.bits in (32, 64)])
        if rng.random() < 0.5:
            s = rng.choice(["al", "bl", "sil", "r9b", "cx", "r10w"])
            return {"op": op, "dst": d, "src": [s]}, f"{op} {d}, {s}"
        sz = rng.choice([1, 2])
        return ({"op": op, "dst": d, "src": [{"mem": mem | {"size": sz}}]},
                f"{op} {d}, {'byte' if sz == 1 else 'word'} ptr {mtext}")  # fmt: skip
    if kind == "push":
        if rng.random() < 0.3:
            return {"op": "push", "src": [{"imm": imm if bits >= 32 else 5}]}, f"push {imm if bits >= 32 else 5}"
        a = rng.choice(b64)
        return {"op": "push", "src": [a]}, f"push {a}"
    if kind == "pop":
        a = rng.choice(b64)
        return {"op": "pop", "dst": a}, f"pop {a}"
    op = rng.choice(["mul", "div", "idiv"])
    a = r()
    return {"op": op, "src": [a]}, f"{op} {a}"


def test_x64_fuzz_matches_keystone_semantically():
    # Encodings may legitimately differ (iced picks the shortest form), so compare decoded text.
    rng = random.Random(99)
    mismatches = []
    for _ in range(3000):
        insn, text = _rand_x64(rng)
        try:
            ours = assemble("x86_64", [insn]).code
        except AssemblyError as exc:
            mismatches.append((text, str(exc)))
            continue
        ref = ks_bytes(X86_64, text)
        if disasm(X86_64, ours) != disasm(X86_64, ref) or len(ours) > len(ref):
            mismatches.append((text, ours.hex(), ref.hex(), disasm(X86_64, ours), disasm(X86_64, ref)))
    assert not mismatches, mismatches[:10]


def test_x64_immediate_forms():
    cases = [
        ({"op": "mov", "dst": "rax", "src": [{"imm": 0x1122334455667788}]}, "48b88877665544332211"),
        ({"op": "mov", "dst": "rax", "src": [{"imm": -1}]}, "48c7c0ffffffff"),
        ({"op": "mov", "dst": "rax", "src": [{"imm": 0xFFFFFFFF}]}, "48b8ffffffff00000000"),
        ({"op": "add", "dst": "rax", "src": [{"imm": 1}]}, "4883c001"),
        ({"op": "add", "dst": "rax", "src": [{"imm": 1000}]}, "4805e8030000"),
        ({"op": "and", "dst": "eax", "src": [{"imm": 0xFFFFFFFF}]}, "83e0ff"),
        ({"op": "add", "dst": "al", "src": [{"imm": 200}]}, "04c8"),
        ({"op": "shl", "dst": "rax", "src": [{"imm": 3}]}, "48c1e003"),
        ({"op": "imul", "dst": "rax", "src": [{"imm": 10}]}, "486bc00a"),
        ({"op": "push", "src": [{"imm": 1}]}, "6a01"),
    ]
    for insn, hexcode in cases:
        assert assemble("x86_64", [insn]).code.hex() == hexcode, insn


def test_x64_jumps_all_conditions():
    body, text = [{"label": "top"}], ["top:"]
    for op in x64.JCC_OPS:
        body.append({"op": op, "target": "end"})
        text.append(f"{op} end")
    body += [{"op": "jmp", "target": "top"}, {"op": "call", "target": "end"}, {"label": "end"}, {"op": "ret"}]
    text += ["jmp top", "call end", "end:", "ret"]
    ours = assemble("x86_64", body).code
    assert disasm(X86_64, ours) == disasm(X86_64, ks_bytes(X86_64, "\n".join(text)))


def test_x64_long_branch_uses_rel32():
    body = [{"op": "jne", "target": "far"}] + [{"op": "nop"}] * 300 + [{"label": "far"}, {"op": "ret"}]
    prog = assemble("x86_64", body)
    assert prog.code[:2] == b"\x0f\x85" and prog.labels["far"] == 6 + 300


def test_x64_label_at_end():
    prog = assemble("x86_64", [{"op": "jmp", "target": "end"}, {"op": "ret"}, {"label": "end"}])
    assert prog.labels["end"] == len(prog.code) == 3


# =========================================================================== programs under emulation

A64_PROLOGUE = [
    {"op": "stp", "src": ["fp", "lr", {"mem": {"base": "sp", "disp": -32, "writeback": "pre"}}]},
    {"op": "mov", "dst": "fp", "src": ["sp"]},
    {"op": "str", "src": ["x19", {"mem": {"base": "sp", "disp": 16}}]},
]
A64_EPILOGUE = [
    {"op": "ldr", "dst": "x19", "src": [{"mem": {"base": "sp", "disp": 16}}]},
    {"op": "ldp", "dst": ["fp", "lr"], "src": [{"mem": {"base": "sp", "disp": 32, "writeback": "post"}}]},
    {"op": "ret"},
]

GCD_REF = """int64_t gcd(int64_t a, int64_t b) {
    uint64_t x = a < 0 ? -(uint64_t)a : (uint64_t)a, y = b < 0 ? -(uint64_t)b : (uint64_t)b;
    while (y) { uint64_t t = x % y; x = y; y = t; } return (int64_t)x; }"""

A64_GCD = [
    {"op": "cmp", "src": ["x0", {"imm": 0}]},
    {"op": "cneg", "dst": "x0", "src": ["x0"], "cond": "lt"},
    {"op": "cmp", "src": ["x1", {"imm": 0}]},
    {"op": "cneg", "dst": "x1", "src": ["x1"], "cond": "lt"},
    {"label": "loop"},
    {"op": "cbz", "src": ["x1"], "target": "done"},
    {"op": "udiv", "dst": "x2", "src": ["x0", "x1"]},
    {"op": "msub", "dst": "x2", "src": ["x2", "x1", "x0"]},
    {"op": "mov", "dst": "x0", "src": ["x1"]},
    {"op": "mov", "dst": "x1", "src": ["x2"]},
    {"op": "b", "target": "loop"},
    {"label": "done"},
    {"op": "ret"},
]

X64_GCD = [
    {"op": "mov", "dst": "rax", "src": ["rdi"]},
    {"op": "neg", "dst": "rax"},
    {"op": "cmovl", "dst": "rax", "src": ["rdi"]},
    {"op": "mov", "dst": "rcx", "src": ["rsi"]},
    {"op": "neg", "dst": "rcx"},
    {"op": "cmovl", "dst": "rcx", "src": ["rsi"]},
    {"label": "loop"},
    {"op": "test", "dst": "rcx", "src": ["rcx"]},
    {"op": "je", "target": "done"},
    {"op": "xor", "dst": "edx", "src": ["edx"]},
    {"op": "div", "src": ["rcx"]},
    {"op": "mov", "dst": "rax", "src": ["rcx"]},
    {"op": "mov", "dst": "rcx", "src": ["rdx"]},
    {"op": "jmp", "target": "loop"},
    {"label": "done"},
    {"op": "ret"},
]


@pytest.mark.parametrize("isa,prog", [(AARCH64, A64_GCD), (X86_64, X64_GCD)], ids=["aarch64", "x86_64"])
def test_gcd_differential(isa, prog):
    ref = compile_c_reference(GCD_REF, "gcd", 2)
    code = assemble(isa, prog).code
    inputs = [args for args in int_inputs(2, 200) if -(2**63) not in args]
    report = differential(isa, code, ref, inputs)
    assert report.ok, report.mismatches[:5]


CLAMP_REF = "int64_t clampsum(int64_t a, int64_t b, int64_t c) { int64_t s = a * 3 + (b << 2) - (c >> 1);" \
            " return s < -100 ? -100 : s > 100 ? 100 : s; }"  # fmt: skip

A64_CLAMP = [
    {"op": "add", "dst": "x3", "src": ["x0", {"reg": "x0", "shift": "lsl", "amount": 1}]},
    {"op": "add", "dst": "x3", "src": ["x3", {"reg": "x1", "shift": "lsl", "amount": 2}]},
    {"op": "sub", "dst": "x3", "src": ["x3", {"reg": "x2", "shift": "asr", "amount": 1}]},
    {"op": "mov", "dst": "x4", "src": [{"imm": -100}]},
    {"op": "cmp", "src": ["x3", "x4"]},
    {"op": "csel", "dst": "x3", "src": ["x3", "x4"], "cond": "ge"},
    {"op": "mov", "dst": "x4", "src": [{"imm": 100}]},
    {"op": "cmp", "src": ["x3", {"imm": 100}]},
    {"op": "csel", "dst": "x0", "src": ["x3", "x4"], "cond": "le"},
    {"op": "ret"},
]
X64_CLAMP = [
    {"op": "lea", "dst": "rax", "src": [{"mem": {"base": "rdi", "index": "rdi", "scale": 2}}]},
    {"op": "lea", "dst": "rax", "src": [{"mem": {"base": "rax", "index": "rsi", "scale": 4}}]},
    {"op": "sar", "dst": "rdx", "src": [{"imm": 1}]},
    {"op": "sub", "dst": "rax", "src": ["rdx"]},
    {"op": "mov", "dst": "rcx", "src": [{"imm": -100}]},
    {"op": "cmp", "dst": "rax", "src": ["rcx"]},
    {"op": "cmovl", "dst": "rax", "src": ["rcx"]},
    {"op": "mov", "dst": "ecx", "src": [{"imm": 100}]},
    {"op": "cmp", "dst": "rax", "src": [{"imm": 100}]},
    {"op": "cmovg", "dst": "rax", "src": ["rcx"]},
    {"op": "ret"},
]


@pytest.mark.parametrize("isa,prog", [(AARCH64, A64_CLAMP), (X86_64, X64_CLAMP)], ids=["aarch64", "x86_64"])
def test_clamp_differential(isa, prog):
    ref = compile_c_reference(CLAMP_REF, "clampsum", 3)
    report = differential(isa, assemble(isa, prog).code, ref, int_inputs(3, 300))
    assert report.ok, report.mismatches[:5]


# In-place prefix sums over an int64 buffer, returning the maximum element seen (0 if empty).
PREFIX_REF = """int64_t prefix(int64_t *a, int64_t n) {
    int64_t s = 0, m = 0; for (int64_t i = 0; i < n; i++) { s += a[i]; a[i] = s; if (s > m) m = s; }
    return m; }"""

A64_PREFIX = A64_PROLOGUE + [
    {"op": "mov", "dst": "x19", "src": [{"imm": 0}]},  # m
    {"op": "mov", "dst": "x2", "src": [{"imm": 0}]},  # s
    {"op": "mov", "dst": "x3", "src": [{"imm": 0}]},  # i
    {"op": "b", "target": "cond"},
    {"label": "body"},
    {"op": "ldr", "dst": "x4", "src": [{"mem": {"base": "x0", "index": "x3", "scale": 8}}]},
    {"op": "add", "dst": "x2", "src": ["x2", "x4"]},
    {"op": "str", "src": ["x2", {"mem": {"base": "x0", "index": "x3", "scale": 8}}]},
    {"op": "bl", "target": "maxfn"},  # x19 = max(x19, x2), exercises bl/ret + saved lr
    {"op": "add", "dst": "x3", "src": ["x3", {"imm": 1}]},
    {"label": "cond"},
    {"op": "cmp", "src": ["x3", "x1"]},
    {"op": "b.lt", "target": "body"},
    {"op": "mov", "dst": "x0", "src": ["x19"]},
] + A64_EPILOGUE + [
    {"label": "maxfn"},
    {"op": "cmp", "src": ["x2", "x19"]},
    {"op": "csel", "dst": "x19", "src": ["x2", "x19"], "cond": "gt"},
    {"op": "ret"},
]  # fmt: skip

X64_PREFIX = [
    {"op": "push", "src": ["rbx"]},
    {"op": "xor", "dst": "ebx", "src": ["ebx"]},  # m
    {"op": "xor", "dst": "eax", "src": ["eax"]},  # s
    {"op": "xor", "dst": "ecx", "src": ["ecx"]},  # i
    {"op": "jmp", "target": "cond"},
    {"label": "body"},
    {"op": "add", "dst": "rax", "src": [{"mem": {"base": "rdi", "index": "rcx", "scale": 8}}]},
    {"op": "mov", "dst": {"mem": {"base": "rdi", "index": "rcx", "scale": 8}}, "src": ["rax"]},
    {"op": "call", "target": "maxfn"},
    {"op": "inc", "dst": "rcx"},
    {"label": "cond"},
    {"op": "cmp", "dst": "rcx", "src": ["rsi"]},
    {"op": "jl", "target": "body"},
    {"op": "mov", "dst": "rax", "src": ["rbx"]},
    {"op": "pop", "dst": "rbx"},
    {"op": "ret"},
    {"label": "maxfn"},
    {"op": "cmp", "dst": "rax", "src": ["rbx"]},
    {"op": "cmovg", "dst": "rbx", "src": ["rax"]},
    {"op": "ret"},
]


@pytest.mark.parametrize("isa,prog", [(AARCH64, A64_PREFIX), (X86_64, X64_PREFIX)], ids=["aarch64", "x86_64"])
def test_buffer_differential(isa, prog):
    rng = random.Random(5)
    buffers = [[], [1], [-5, 3, 9], [2**62, 2**62, -(2**63)]] + [
        [rng.randint(-1000, 1000) for _ in range(rng.randint(1, 40))] for _ in range(40)
    ]
    report = buffer_differential(isa, assemble(isa, prog).code, PREFIX_REF, "prefix", buffers)
    assert report.ok, report.mismatches[:5]


def test_byte_ops_and_setcc():
    # Count bytes equal to the byte in arg1 in the 8 bytes of arg0 (tests ldrb/strb, movzx, setcc).
    ref = compile_c_reference(
        "int64_t cnt(int64_t v, int64_t b) { int64_t c = 0; for (int i = 0; i < 8; i++)"
        " c += ((v >> (8*i)) & 0xff) == (b & 0xff); return c; }",
        "cnt",
        2,
    )
    a = [
        {"op": "sub", "dst": "sp", "src": ["sp", {"imm": 16}]},
        {"op": "str", "src": ["x0", {"mem": {"base": "sp"}}]},
        {"op": "strb", "src": ["w1", {"mem": {"base": "sp", "disp": 8}}]},
        {"op": "ldrb", "dst": "w5", "src": [{"mem": {"base": "sp", "disp": 8}}]},
        {"op": "mov", "dst": "x0", "src": [{"imm": 0}]},
        {"op": "mov", "dst": "x2", "src": [{"imm": 0}]},
        {"label": "l"},
        {"op": "ldrb", "dst": "w3", "src": [{"mem": {"base": "sp", "index": "x2"}}]},
        {"op": "cmp", "src": ["w3", "w5"]},
        {"op": "cinc", "dst": "x0", "src": ["x0"], "cond": "eq"},
        {"op": "add", "dst": "x2", "src": ["x2", {"imm": 1}]},
        {"op": "cmp", "src": ["x2", {"imm": 8}]},
        {"op": "b.ne", "target": "l"},
        {"op": "add", "dst": "sp", "src": ["sp", {"imm": 16}]},
        {"op": "ret"},
    ]
    x = [
        {"op": "mov", "dst": {"mem": {"base": "rsp", "disp": -16}}, "src": ["rdi"]},
        {"op": "xor", "dst": "eax", "src": ["eax"]},
        {"op": "xor", "dst": "ecx", "src": ["ecx"]},
        {"label": "l"},
        {"op": "movzx", "dst": "edx", "src": [{"mem": {"base": "rsp", "index": "rcx", "disp": -16, "size": 1}}]},
        {"op": "cmp", "dst": "dl", "src": ["sil"]},
        {"op": "sete", "dst": "dl"},
        {"op": "movzx", "dst": "edx", "src": ["dl"]},
        {"op": "add", "dst": "rax", "src": ["rdx"]},
        {"op": "inc", "dst": "ecx"},
        {"op": "cmp", "dst": "ecx", "src": [{"imm": 8}]},
        {"op": "jne", "target": "l"},
        {"op": "ret"},
    ]
    inputs = [(0x4141_0041_4100_0041, 0x41), (0, 0), (-1, 0xFF), (0x0102030405060708, 3)] + list(int_inputs(2, 100))
    for isa, prog in ((AARCH64, a), (X86_64, x)):
        report = differential(isa, assemble(isa, prog).code, ref, inputs)
        assert report.ok, (isa.name, report.mismatches[:5])


# =========================================================================== errors


@pytest.mark.parametrize(
    "insn,path,fragment",
    [
        ({"op": "add", "dst": "x0", "src": ["x1", {"imm": 5000}]}, "src[1].imm", "does not fit add/sub imm12"),
        ({"op": "add", "dst": "x0", "src": ["x1", {"imm": 1}], "cond": "eq"}, "cond", "unexpected field"),
        ({"op": "and", "dst": "x0", "src": ["x1", {"imm": 5}]}, "src[1].imm", "not a valid 64-bit logical immediate"),
        ({"op": "add", "dst": "x0", "src": ["w1", "x2"]}, "dst", "32-bit"),
        ({"op": "add", "dst": "x0", "src": ["xzr", "sp"]}, "src[1]", "sp is not allowed"),
        ({"op": "orr", "dst": "x0", "src": ["sp", "x1"]}, "src[0]", "sp is not allowed"),
        ({"op": "add", "dst": "x0", "src": ["x1", "x99"]}, "src[1]", "unknown register 'x99'"),
        ({"op": "lsl", "dst": "w0", "src": ["w1", {"imm": 32}]}, "src[1].imm", "0..31"),
        ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "x1", "disp": 5000 * 8}}]}, "src[0].mem.disp", "out of range"),
        ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "x0", "disp": 8, "writeback": "pre"}}]}, "src[0]", "unpredictable"),
        ({"op": "ldrb", "dst": "x0", "src": [{"mem": {"base": "x1"}}]}, "dst", "needs a w register"),
        ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "x1", "index": "x2", "scale": 4}}]}, "src[0].mem.scale", "1 or 8"),
        ({"op": "ldr", "dst": "x0", "src": [{"mem": {"base": "w1"}}]}, "src[0].mem.base", "x register or sp"),
        ({"op": "stp", "src": ["x0", "x1", {"mem": {"base": "sp", "disp": 1000}}]}, "src[2].mem.disp", "multiple of 8"),
        ({"op": "movz", "dst": "x0", "src": [{"imm": 70000}]}, "src[0].imm", "0..65535"),
        ({"op": "movz", "dst": "w0", "src": [{"imm": 1, "lsl": 32}]}, "src[0].lsl", "32-bit register"),
        ({"op": "cset", "dst": "x0", "cond": "al"}, "cond", "cannot be inverted"),
        ({"op": "csel", "dst": "x0", "src": ["x1", "x2"], "cond": "zz"}, "cond", "unknown cond"),
        ({"op": "b.xx", "target": "l"}, "op", "unknown aarch64 op"),
        ({"op": "mul", "dst": "x0", "src": ["x1"]}, "src", "expected 2 operands"),
        ({"op": "add", "dst": "x0", "src": [{"imm": 1}, "x1"]}, "src[0]", "expected a register"),
        ({"op": "mov", "dst": "w0", "src": [{"imm": 2**32}]}, "src[0].imm", "does not fit in a 32-bit register"),
        ({"op": "add", "dst": "x0", "src": ["x1", {"imm": 1.5}]}, "src[1].imm", "must be an integer"),
        ({"label": "a", "op": "nop"}, "", "takes only 'label'"),
        ({"label": "1bad"}, "label", "invalid label name"),
        ("add x0, x1, x2", "", "must be a JSON object"),
    ],
)
def test_a64_errors(insn, path, fragment):
    errs = errors_of("aarch64", [insn])
    assert len(errs) == 1 and errs[0].index == 0
    assert errs[0].path == path, errs[0]
    assert fragment in errs[0].message, errs[0]


@pytest.mark.parametrize(
    "insn,path,fragment",
    [
        ({"op": "add", "dst": "rax", "src": [{"imm": 2**40}]}, "src[0].imm", "sign-extended 32-bit"),
        ({"op": "add", "dst": "al", "src": [{"imm": 256}]}, "src[0].imm", "does not fit in 8 bits"),
        ({"op": "add", "dst": {"mem": {"base": "rdi"}}, "src": [{"imm": 1}]}, "dst.mem", "mem.size is required"),
        ({"op": "mov", "dst": {"mem": {"base": "rdi"}}, "src": [{"mem": {"base": "rsi"}}]}, "src[0]", "memory-to-memory"),
        ({"op": "mov", "dst": "rax", "src": ["ebx"]}, "src[0]", "size mismatch"),
        ({"op": "mov", "dst": "rax", "src": [{"mem": {"base": "rdi", "size": 4}}]}, "src[0].mem.size", "conflicts"),
        ({"op": "shl", "dst": "rax", "src": ["rcx"]}, "src[0]", "must be cl"),
        ({"op": "shl", "dst": "eax", "src": [{"imm": 40}]}, "src[0].imm", "0..31"),
        ({"op": "sete", "dst": "eax"}, "dst", "8-bit"),
        ({"op": "push", "src": ["eax"]}, "src[0]", "64-bit register"),
        ({"op": "movzx", "dst": "rax", "src": [{"mem": {"base": "rdi"}}]}, "src[0].mem", "mem.size is required"),
        ({"op": "movzx", "dst": "ax", "src": ["cx"]}, "dst", "wider"),
        ({"op": "lea", "dst": "rax", "src": [{"mem": {"base": "rdi", "index": "rsp"}}]}, "src[0].mem.index", "rsp"),
        ({"op": "mov", "dst": "rax", "src": [{"mem": {"base": "edi"}}]}, "src[0].mem.base", "64-bit"),
        ({"op": "mov", "dst": "ah", "src": ["sil"]}, "", "can't be used if there's a REX prefix"),
        ({"op": "cmovl", "dst": "rax", "src": [{"imm": 1}]}, "src[0]", "expected a register"),
        ({"op": "jnq", "target": "x"}, "op", "unknown x86_64 op"),
        ({"op": "mov", "dst": "rax", "src": ["rbx"], "size": 8}, "size", "unexpected field"),
    ],
)
def test_x64_errors(insn, path, fragment):
    errs = errors_of("x86_64", [insn])
    assert len(errs) == 1 and errs[0].index == 0
    assert errs[0].path == path, errs[0]
    assert fragment in errs[0].message, errs[0]


def test_errors_are_reported_per_instruction_all_at_once():
    prog = [
        {"op": "add", "dst": "x0", "src": ["x0", "x1"]},
        {"op": "bogus"},
        {"op": "b", "target": "nowhere"},
        {"label": "l"},
        {"label": "l"},
        {"op": "ret"},
    ]
    errs = errors_of("aarch64", prog)
    assert [e.index for e in errs] == [1, 2, 4]
    assert "undefined label 'nowhere'" in errs[1].message
    assert "duplicate label" in errs[2].message
    assert str(errs[0]).startswith("instruction 1 (bogus) op:")


def test_branch_out_of_range():
    body = [{"op": "cbz", "src": ["x0"], "target": "far"}] + [{"op": "nop"}] * (1 << 18) + [{"label": "far"}, {"op": "ret"}]
    errs = errors_of("aarch64", body)
    assert len(errs) == 1 and errs[0].index == 0 and "out of range" in errs[0].message
    body[0] = {"op": "b", "target": "far"}
    assert len(assemble("aarch64", body).code) == 4 * ((1 << 18) + 2)


# =========================================================================== schemas and tools


def _examples(spec):
    return [ex for fam in spec.families for ex in fam.examples]


@pytest.mark.parametrize("isa", ["aarch64", "x86_64"])
@pytest.mark.parametrize("strict", [False, True])
def test_schemas_validate_examples_and_reject_garbage(isa, strict):
    from bca.structured import backend

    spec = backend(isa).SPEC
    schemas = input_schemas(isa, strict=strict)
    json.dumps(schemas)  # serializable
    validate_prog = fastjsonschema.compile(schemas["program"])
    validate_one = fastjsonschema.compile(schemas["instruction"])
    examples = _examples(spec)
    assert {f.name for f in spec.families} == {
        f.name for f in spec.families if any(spec.by_op[e["op"]] is f for e in examples)
    }, "every family has an example"
    validate_prog({"instructions": examples + [{"label": "loop"}]})
    for ex in examples:
        validate_one({"instruction": ex})
    bad = [
        {"op": "not_an_op"},
        {"op": examples[0]["op"], "dst": "not_a_reg", "src": [{"imm": 1}]},
        {"op": "ret", "extra": 1},
        {"label": "a b"},
    ]
    for b in bad:
        with pytest.raises(fastjsonschema.JsonSchemaException):
            validate_one({"instruction": b})


@pytest.mark.parametrize("isa", ["aarch64", "x86_64"])
def test_every_schema_example_encodes(isa):
    from bca.structured import backend

    spec = backend(isa).SPEC
    examples = _examples(spec)
    labels = {e["target"] for e in examples if "target" in e}
    prog = examples + [x for name in sorted(labels) for x in ({"label": name}, {"op": "nop"})]
    assert assemble(isa, prog).code


@pytest.mark.parametrize("isa", ["aarch64", "x86_64"])
def test_schema_enumerates_ops_and_registers(isa):
    from bca.structured import backend

    be = backend(isa)
    schema = input_schemas(isa)["instruction"]
    variants = schema["properties"]["instruction"]["anyOf"]
    ops = [op for v in variants if "op" in v["properties"] for op in v["properties"]["op"]["enum"]]
    assert sorted(ops) == sorted(be.SPEC.ops)
    assert schema["$defs"]["reg"]["enum"] == be.REG_NAMES


def test_tool_definitions_shapes():
    for isa in ("aarch64", "x86_64"):
        tools = tool_definitions(isa)
        assert [t["name"] for t in tools] == ["emit_program", "emit_instruction"]
        for t in tools:
            assert t["input_schema"]["type"] == "object"
            assert len(t["description"]) > 100
        strict = tool_definitions(isa, strict=True)
        assert "maximum" not in json.dumps(strict) and all(t["strict"] for t in strict)
        oa = tool_definitions(isa, flavor="openai")
        assert oa[0]["type"] == "function" and "parameters" in oa[0]["function"]


# =========================================================================== streaming session


@pytest.mark.parametrize("isa,prog", [(AARCH64, A64_PREFIX), (X86_64, X64_PREFIX)], ids=["aarch64", "x86_64"])
def test_session_streaming_matches_batch(isa, prog):
    s = Session(isa)
    for insn in prog:
        res = s.handle_tool_call("emit_instruction", {"instruction": insn})
        assert res["ok"], res
    # A bad instruction mid-stream is rejected and not appended.
    bad = s.emit({"op": "add", "dst": "x0", "src": ["nope", "x1"]} if isa is AARCH64 else {"op": "add", "dst": "rax", "src": ["nope"]})
    assert not bad["ok"] and bad["errors"][0]["path"].startswith("src[0]")
    assert s.finish().code == assemble(isa, prog).code


def test_session_reports_bytes_and_pending_labels():
    s = Session("aarch64")
    r = s.emit({"op": "cbz", "src": ["x0"], "target": "done"})
    assert r["ok"] and r["unresolved_labels"] == ["done"] and r["offset"] == 0
    r = s.emit({"op": "add", "dst": "x0", "src": ["x0", {"imm": 1}]})
    assert r == {"ok": True, "index": 1, "offset": 4, "bytes": "00040091", "disasm": "add x0, x0, #1",
                 "unresolved_labels": ["done"]}  # fmt: skip
    with pytest.raises(AssemblyError, match="undefined label 'done'"):
        s.finish()
    assert s.emit({"label": "done"})["ok"]
    assert s.emit({"op": "ret"})["ok"]
    assert run_function(AARCH64, s.finish().code, [0]).ret == 0
    assert run_function(AARCH64, s.finish().code, [5]).ret == 6
    assert not s.emit({"label": "done"})["ok"]  # duplicate label rejected
    assert not s.emit('{"op": ')["ok"]  # malformed JSON rejected


def test_emit_program_tool_returns_listing():
    s = Session("x86_64")
    r = s.handle_tool_call("emit_program", {"instructions": X64_GCD})
    assert r["ok"] and "div rcx" in r["listing"] and bytes.fromhex(r["code"]) == assemble("x86_64", X64_GCD).code
    r = s.handle_tool_call("emit_program", {"instructions": [{"op": "mov", "dst": "rax"}]})
    assert not r["ok"] and "requires field 'src'" in r["errors"][0]["message"]


def test_accepts_json_text_and_wrapped_program():
    text = json.dumps({"instructions": [{"op": "mov", "dst": "x0", "src": [{"imm": 7}]}, {"op": "ret"}]})
    assert run_function(AARCH64, assemble(AARCH64, text).code).ret == 7
