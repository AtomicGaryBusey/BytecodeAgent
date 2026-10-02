"""The `assisted` prompt variant: more information for the model, nothing else.

Baseline is the headline comparison. This variant measures how much of a model's failure rate is
missing knowledge and unclear feedback: it adds, to the unchanged baseline prompt,

1. path/ISA notes (`NOTES`): the facts behind the most common failures in the recorded runs --
   AArch64 constants and logical immediates, x86-64 addressing and zeroing, a QBE cheat sheet
   (every opcode in it compiles with the installed qbe: tests/test_liveeval_assist.py), structured
   examples checked against the encoder, and output discipline;
2. on repair rounds only, notes about the failure shown above (`failure_notes`): explanations
   matched from the failure text (`HINTS`) and advisory lints of the model's own previous code
   (callee-saved registers written but never saved, x86 stack alignment before a call, `xor r, 0`,
   rip-relative addressing with an index, raw-hex branch targets outside the code).

Everything is computed from the prompt itself (the task text, the model's previous code and the
failure the harness already reported): no test input, expected value or correct answer is added.
Acceptance rules, tests, limits, attempts, timeouts and scoring are those of baseline, so a score
change measures information, not leniency. The notes are advisory: a lint never fails a program.
"""

from __future__ import annotations

import difflib
import json
import re

from bca.liveeval.tasks import Task

# -- 1. notes added to every prompt ---------------------------------------------------------------

OUTPUT = ("Output discipline: answer with the code only, exactly in the output format above: no plan, no "
          "explanation, no Markdown fences, no tool-call or XML markup before, inside or after it.")

A64_CONSTANTS = (
    "AArch64 constants: `mov xN, #imm` only encodes a 16-bit value shifted by 0, 16, 32 or 48 (or its "
    "bitwise inverse, or a logical bitmask). Build any other constant 16 bits at a time: `movz x9, #0x5678` "
    "then `movk x9, #0x1234, lsl #16` gives 0x12345678 (add `movk ..., lsl #32` / `lsl #48` for wider "
    "values). Immediates of and/orr/eor/tst must be logical bitmasks (a repeating pattern of a rotated "
    "run of ones); for any other mask, build it in a register first. add/sub/cmp immediates are 12 bits "
    "(0-4095), optionally shifted left by 12; beyond that, use a register.")
A64_CMP = ("AArch64 compares: `cmp` takes a register first and a register or 12-bit immediate second "
           "(`cmp x0, #5`, never `cmp #5, x0`); conditions after cmp a, b: b.lt/b.ge/b.le/b.gt are signed, "
           "b.lo/b.hs/b.ls/b.hi unsigned.")
X86_NOTES = (
    "x86-64 reminders: rip-relative addressing takes no index register (`[rip + sym + rcx]` is invalid): "
    "load the address with `lea` first, then index from that register. To zero a register use "
    "`xor eax, eax` (writing a 32-bit register clears the upper half); `xor rax, 0` changes nothing. "
    "`imul` has the forms `imul r, r/m` and `imul r, r/m, imm` (no three-register form). Shifts by a "
    "variable count take the count in `cl`. `div`/`idiv` divide rdx:rax: clear rdx (`xor edx, edx`) before "
    "an unsigned `div`, sign-extend with `cqo` before `idiv`.")

QBE_SHEET = """QBE cheat sheet (every name below is valid QBE; anything else is not):
- Function: `export function l $name(l %a, l %b) {` ... `}`; every parameter and every result has a class:
  `l` (64-bit), `w` (32-bit). Blocks start with `@label` on its own line (no colon); every block ends with
  `jmp @x`, `jnz %c, @yes, @no` or `ret %v`.
- Instructions: `%t =l op args` (the class after `=` is required). Integer literals are decimal only
  (no 0x...): write 255, not 0xff; negative numbers like -1 are fine.
- Arithmetic (class l or w; both operands the same class): add sub mul div rem udiv urem neg and or xor
  sar shr shl. Copy a value or constant: `%x =l copy 0`.
- Comparisons give a w: c + eq/ne/slt/sle/sgt/sge/ult/ule/ugt/uge + operand class, e.g. `%c =w csltl %a, %b`
  (compare two l values), `ceqw` (two w values). There is no select instruction: branch with jnz.
- Memory: loadl / loadw / loadsw / loaduw / loadsh / loaduh / loadsb / loadub (`%v =l loadl %p`,
  `%b =l loadub %p` zero-extends a byte to l); storel / storew / storeh / storeb (`storel %v, %p`).
  Addresses are l values: `%p =l add %base, %off`.
- Widening a w to l: extsw (signed) / extuw (unsigned); also extsh extuh extsb extub. A function returning
  l must `ret` an l.
- You may assign the same temporary again (QBE builds SSA form itself), so loops need no phi:
  `%i =l add %i, 1` is fine. If you do use phi, `%x =l phi @a %v1, @b %v2` must name every predecessor
  block of that block.
Example:
export function l $sum(l %p, l %n) {
@start
	%s =l copy 0
	%i =l copy 0
@loop
	%more =w csltl %i, %n
	jnz %more, @body, @done
@body
	%off =l mul %i, 8
	%a =l add %p, %off
	%v =l loadl %a
	%s =l add %s, %v
	%i =l add %i, 1
	jmp @loop
@done
	ret %s
}"""

# Example instructions per ISA; every one is encoded by the real encoder in tests/test_liveeval_assist.py.
STRUCTURED_EXAMPLE_INSNS = {
    "x86_64": [
        {"op": "mov", "dst": "rax", "src": [{"imm": 0}]},
        {"op": "xor", "dst": "ecx", "src": ["ecx"]},
        {"op": "add", "dst": "rax", "src": [{"mem": {"base": "rdi", "index": "rcx", "scale": 8}}]},
        {"op": "cmp", "dst": "rcx", "src": ["rsi"]},
        {"label": "loop"},
        {"op": "jge", "target": "loop"},
        {"op": "mov", "dst": {"mem": {"base": "rdi", "disp": 8}}, "src": ["rax"]},
    ],
    "aarch64": [
        {"op": "add", "dst": "x2", "src": ["x2", "x4"]},
        {"op": "add", "dst": "x3", "src": ["x3", {"imm": 1}]},
        {"op": "cmp", "src": ["x3", "x1"]},
        {"label": "done"},
        {"op": "b.ge", "target": "done"},
        {"op": "ldr", "dst": "x4", "src": [{"mem": {"base": "x0", "index": "x3", "scale": 8}}]},
        {"op": "str", "src": ["x2", {"mem": {"base": "x0", "disp": 8}}]},
        {"op": "movz", "dst": "x5", "src": [{"imm": 4660, "lsl": 16}]},
    ],
}
_STRUCTURED_FORMS = {
    "x86_64": ('a register is a bare string ("rax", never {"reg": "rax"}); an immediate is {"imm": 5}; memory is '
               '{"mem": {"base": "rdi", "index": "rcx", "scale": 8, "disp": 16}} (index, scale and disp '
               'optional); a label is its own entry {"label": "loop"} and a branch names it in "target"'),
    "aarch64": ('a register is a bare string ("x0", never {"reg": "x0"}); an immediate is {"imm": 5} (with '
                '"lsl" for movz/movk); memory is {"mem": {"base": "x0", "index": "x3", "scale": 8}} or {"mem": '
                '{"base": "x0", "disp": 8}}; cmp takes the register first; a label is its own entry {"label": '
                '"done"} and a branch names it in "target"'),
}
STRUCTURED_EXAMPLES = {
    isa: ("Structured operand forms: " + _STRUCTURED_FORMS[isa] + ". Examples, one per line:\n"
          + "\n".join(json.dumps(e) for e in insns))
    for isa, insns in STRUCTURED_EXAMPLE_INSNS.items()
}

HEX_NOTES = {
    "aarch64": ("Raw-hex reminders: number your instructions from 0; a branch at instruction i to "
                "instruction j has the byte offset (j - i) * 4 (negative for backward branches; the word "
                "holds offset / 4 in two's complement). Recount after inserting or removing an "
                "instruction."),
    "x86_64": ("Raw-hex reminders: a relative branch's displacement is target - (address of the next "
               "instruction); after changing any instruction's length, recompute every displacement that "
               "crosses it."),
}


def notes(path: str, isa: str) -> str:
    """The notes added to every prompt of this path and ISA."""
    parts: list[str] = []
    if path in ("stream", "structured", "hex") and isa == "aarch64":
        parts += [A64_CONSTANTS, A64_CMP]
    if path in ("stream", "structured", "hex") and isa == "x86_64":
        parts.append(X86_NOTES)
    if path == "structured":
        parts.append(STRUCTURED_EXAMPLES[isa])
    if path == "ir":
        parts.append(QBE_SHEET)
    if path == "hex":
        parts.append(HEX_NOTES[isa])
    parts.append(OUTPUT)
    return "Notes (to help you avoid common mistakes):\n" + "\n".join(parts)


# -- 2. notes about a failure (repair rounds) -------------------------------------------------------

QBE_OPS = ("add sub mul div rem udiv urem neg and or xor sar shr shl copy ceql cnel csltl cslel csgtl csgel "
           "cultl culel cugtl cugel ceqw cnew csltw cslew csgtw csgew cultw culew cugtw cugew loadl loadw "
           "loadsw loaduw loadsh loaduh loadsb loadub storel storew storeh storeb extsw extuw extsh extuh "
           "extsb extub alloc4 alloc8 alloc16 call phi jmp jnz ret").split()


def _qbe_unknown(word: str) -> str:
    if re.fullmatch(r"x[0-9a-fA-F]+", word):
        return (f"`{word}` is most likely the tail of a hexadecimal literal (`0{word}`): QBE takes decimal "
                f"literals only (write {int(word[1:], 16)}).")
    near = difflib.get_close_matches(word, QBE_OPS, n=3, cutoff=0.5)
    base = re.sub(r"[lw]$", "", word)
    if word not in QBE_OPS and base in ("add", "sub", "mul", "div", "rem", "and", "or", "xor", "shl", "shr", "sar"):
        return f"`{word}` is not a QBE opcode: the class goes after `=` (`%x =l {base} ...`), not in the opcode."
    if re.fullmatch(r"c(eq|ne|[su](lt|le|gt|ge))", word):
        return f"`{word}` needs the operand class at the end: `{word}l` (two l values) or `{word}w` (two w values)."
    tip = {"const": "use `%x =l copy 5`", "jump": "the jump is `jmp @label`", "select": "QBE has no select: "
           "branch with jnz (or compute it arithmetically)", "mov": "use `%x =l copy %y`"}.get(word, "")
    msg = f"`{word}` is not a QBE keyword or opcode"
    if tip:
        msg += f": {tip}"
    elif near:
        msg += f" (closest valid names: {', '.join(near)})"
    return msg + ". A block label is written `@name` on its own line, without a colon."


HINTS: list[tuple[str, object]] = [
    (r"immediate cannot be moved by a single instruction|immediate out of range|immediate value out of range",
     "An AArch64 immediate did not fit its instruction. For mov, build the constant 16 bits at a time "
     "(movz, then movk ..., lsl #16/#32/#48); add/sub/cmp take 0-4095 (optionally lsl #12); otherwise put "
     "the value in a register first."),
    (r"logical immediate|invalid immediate for (and|orr|eor|tst|bic)",
     "and/orr/eor/tst only accept logical bitmasks (a repeating pattern of a rotated run of ones). Load "
     "any other mask into a register and use the register form."),
    (r"not a valid base/index expression",
     "That memory operand is not encodable. rip-relative addresses take no index register: `lea` the "
     "address into a register first, then index from it; an index register can be scaled only by 1, 2, "
     "4 or 8."),
    (r"invalid class specifier",
     "QBE needs a class everywhere: on every result (`%x =l add ...`, `%c =w csltl ...`) and every "
     "parameter (`l %a`)."),
    (r"invalid character",
     "QBE block labels are `@name` on their own line with no colon; comments start with `#`."),
    (r"unknown keyword (\S+)", _qbe_unknown),
    (r"invalid type for (first|second) operand",
     "QBE operands must have the class the instruction expects: do not mix w and l (widen with extsw / "
     "extuw), and make sure every temporary is assigned before it is used."),
    (r"invalid type for jump argument",
     "The returned value's class must match the function's return class (l): widen a w with extuw / extsw "
     "before `ret`."),
    (r"predecessors not matched in phi",
     "A phi must list exactly the predecessor blocks of its block. You can avoid phi entirely: QBE lets you "
     "assign the same temporary again (`%i =l add %i, 1`)."),
    (r"misses jump",
     "Every QBE block must end with jmp, jnz or ret."),
    (r"expected a register name like.*\{'reg'",
     "In the structured format a register is a bare string such as \"rax\" or \"x0\", not {\"reg\": ...}."),
    (r"expected a register here, got immediate",
     "Here the encoder needs a register: on AArch64 `cmp` takes the register first ({\"op\": \"cmp\", "
     "\"src\": [\"x0\", {\"imm\": 5}]}); for other operations, load the constant into a register first."),
    (r"missing 'op'",
     "Every entry is either {\"op\": ..., fields} or {\"label\": \"name\"}."),
    (r"unexpected field '(\w+)'",
     "Use only the fields the error lists for that op (see the schema): e.g. dst / src / target."),
    (r"undefined label '([^']+)'",
     "Every branch target must be defined: add a {\"label\": ...} entry (structured) or a `name:` line "
     "(assembly) for it."),
    (r"callee-saved register (\S+) clobbered",
     "That register is callee-saved: save it at entry (push / stp to the stack) and restore it on every "
     "return path, or use a caller-saved scratch register instead."),
    (r"misaligned call",
     "The stack must be 16-byte aligned at the call instruction. On x86-64 rsp is 8 mod 16 at entry: an "
     "odd number of 8-byte pushes (or `sub rsp, 8` plus multiples of 16) before the call fixes it."),
    (r"did not return within",
     "The code looped without terminating on that input: check the loop condition and that the counter "
     "or pointer moves toward the exit on every iteration (including n = 0 and the largest inputs)."),
    (r"out-of-bounds (read|write)",
     "The code touched memory outside the arguments' regions: check the loop bounds (n = 0 included), "
     "the element size used for indexing, and that only the documented ranges are read or written."),
    (r"Invalid memory (read|write|fetch)",
     "The code accessed an unmapped address: a pointer or index register holds a wrong value at that "
     "instruction (an uninitialised register, a wrong scale, or a pointer that walked past the end)."),
    (r"unknown mnemonic|unrecognized instruction|Invalid mnemonic",
     "That mnemonic does not exist on this ISA (or the line is prose): use only real instructions, and "
     "output only code."),
]


def _hint_texts(failure: str) -> list[str]:
    out: list[str] = []
    for pattern, hint in HINTS:
        m = re.search(pattern, failure)
        if not m:
            continue
        text = hint(m.group(1)) if callable(hint) else hint
        if text not in out:
            out.append(text)
    return out


# -- lints of the model's previous code (advisory) ---------------------------------------------------

_X86_CALLEE = {"rbx": ("rbx", "ebx", "bx", "bl", "bh"), "rbp": ("rbp", "ebp", "bp", "bpl"),
               **{f"r{n}": (f"r{n}", f"r{n}d", f"r{n}w", f"r{n}b") for n in (12, 13, 14, 15)}}
_X86_INDEX_RE = re.compile(r"\b(r(ax|bx|cx|dx|si|di|bp|[89]|1[0-5])|e(ax|bx|cx|dx|si|di))\b")
_X86_NO_DST = re.compile(r"^(cmp|test|push|jmp|j[a-z]+|call|ret|bt|nop|cqo|cdq|leave)$")
_A64_NO_DST = re.compile(r"^(cmp|cmn|tst|b|b\..*|bl|blr|br|ret|cbz|cbnz|tbz|tbnz|str\w*|stp|stur\w*|nop)$")


def _strip_comment(line: str, markers: tuple[str, ...]) -> str:
    for mk in markers:
        i = line.find(mk)
        if i >= 0:
            line = line[:i]
    return line.strip()


def _stream_lines(code: str, isa: str) -> list[tuple[str, list[str]]]:
    out = []
    for raw in code.splitlines():
        line = _strip_comment(raw, ("#", "//") if isa == "x86_64" else ("//",))
        line = re.sub(r"^\s*[\w.$]+:\s*", "", line)
        if not line or line.startswith("."):
            continue
        mnem, _, rest = line.partition(" ")
        ops = [o.strip() for o in re.split(r",(?![^\[]*\])", rest) if o.strip()] if rest else []
        out.append((mnem.lower(), [o.lower() for o in ops]))
    return out


def _lint_stream(code: str, isa: str) -> list[str]:
    lines = _stream_lines(code, isa)
    notes_: list[str] = []
    if isa == "x86_64":
        written, saved = set(), set()
        for mnem, ops in lines:
            if mnem == "push" and ops:
                saved.add(ops[0])
            if ops and not _X86_NO_DST.match(mnem):
                for full, names in _X86_CALLEE.items():
                    if ops[0] in names:
                        written.add(full)
            if mnem == "mov" and len(ops) == 2 and "rsp" in ops[0] and ops[1] in _X86_CALLEE:
                saved.add(ops[1])
            if mnem == "xor" and len(ops) == 2 and ops[1] in ("0", "0x0"):
                notes_.append(f"`xor {ops[0]}, 0` leaves {ops[0]} unchanged; to zero it use `xor {ops[0]}, {ops[0]}`.")
            if any("rip" in o and _X86_INDEX_RE.search(o.replace("rip", "")) for o in ops):
                notes_.append("A rip-relative memory operand with an index register is not encodable: "
                              "lea the address first.")
        for reg in sorted(written - saved):
            notes_.append(f"{reg} is callee-saved and is written, but never pushed: it must be saved and "
                          "restored (or use a caller-saved register).")
        # stack alignment before the first call, counting pushes and sub/add rsp on the way
        depth, seen_call = 8, False
        for mnem, ops in lines:
            if mnem == "push":
                depth += 8
            elif mnem == "pop":
                depth -= 8
            elif mnem in ("sub", "add") and ops and ops[0] == "rsp" and len(ops) == 2 and re.fullmatch(r"-?\d+|0x[0-9a-f]+", ops[1]):
                n = int(ops[1], 0)
                depth += n if mnem == "sub" else -n
            elif mnem == "call" and not seen_call:
                seen_call = True
                if depth % 16:
                    notes_.append("Before the first call, rsp may not be 16-byte aligned (entry is 8 mod 16; "
                                  f"the pushes/sub rsp before it total {depth - 8} bytes, which leaves rsp "
                                  "misaligned).")
    else:
        written, saved = set(), set()
        calls = any(m in ("bl", "blr") for m, _ in lines)
        for mnem, ops in lines:
            if mnem in ("stp", "str", "stur") and ops:
                for o in ops[:2] if mnem == "stp" else ops[:1]:
                    saved.add(re.sub(r"^w", "x", o))
            if ops and not _A64_NO_DST.match(mnem):
                r = re.sub(r"^w", "x", ops[0])
                if re.fullmatch(r"x(19|2[0-9])", r):
                    written.add(r)
        for reg in sorted(written - saved, key=lambda r: int(r[1:])):
            notes_.append(f"{reg} is callee-saved and is written, but never stored to the stack: save it "
                          "(stp/str) and restore it before ret, or use a scratch register (x9-x15).")
        if calls and "x30" not in saved:
            notes_.append("The code calls (bl/blr) but never saves x30 (the return address): save x29/x30 "
                          "first (stp x29, x30, [sp, #-16]!) and restore them before ret.")
    return notes_


def _lint_structured(code: str, isa: str) -> list[str]:
    try:
        prog = json.loads(code)
    except ValueError:
        return []
    insns = prog.get("instructions") if isinstance(prog, dict) else prog
    if not isinstance(insns, list):
        return []
    as_lines = []
    for e in insns:
        if not isinstance(e, dict) or "op" not in e:
            continue
        ops = []
        if "dst" in e:
            ops.append(e["dst"] if isinstance(e["dst"], str) else "mem")
        for s in e.get("src") or []:
            ops.append(s if isinstance(s, str) else "rsp" if isinstance(s, dict) and "mem" in s and
                       (s["mem"] or {}).get("base") in ("rsp", "sp") else "mem")
        if "target" in e:
            ops.append(str(e["target"]))
        as_lines.append((str(e["op"]).lower(), [str(o).lower() for o in ops]))
    text = "\n".join(f"{m} {', '.join(o)}" for m, o in as_lines)
    return _lint_stream(text, isa)


def _lint_hex(code: str, isa: str) -> list[str]:
    import capstone

    from bca.liveeval.hexpath import parse

    data, _lines = parse(isa, code)
    if isa == "aarch64":
        md, imm_type = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM), capstone.arm64.ARM64_OP_IMM
    else:
        md, imm_type = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64), capstone.x86.X86_OP_IMM
    md.detail = True
    insns = list(md.disasm(bytes(data), 0))
    starts = {i.address for i in insns}
    out = []
    for i in insns:
        if not (i.group(capstone.CS_GRP_JUMP) or i.group(capstone.CS_GRP_CALL)):
            continue
        for o in i.operands:
            if o.type != imm_type:
                continue
            t = o.imm
            if not 0 <= t < len(data):
                where = "outside the code"
            elif t not in starts:
                where = "not the start of an instruction"
            else:
                continue
            out.append(f"The branch at byte offset {i.address:#x} (`{i.mnemonic} {i.op_str}`) targets {t:#x}, "
                       f"which is {where}.")
    return out


def lints(code: str, path: str, isa: str) -> list[str]:
    try:
        if path == "stream":
            return _lint_stream(code, isa)
        if path == "structured":
            return _lint_structured(code, isa)
        if path == "hex":
            return _lint_hex(code, isa)
    except Exception:  # noqa: BLE001 - advisory only: a lint must never break a prompt
        return []
    return []


_PREV_RE = re.compile(r"<previous>\n(.*)\n</previous>\nIt failed:\n<failure>\n(.*)\n</failure>", re.S)


def failure_notes(prompt: str, path: str, isa: str) -> str:
    """Notes about the failure in a repair prompt ('' on a first attempt or when nothing matches)."""
    m = _PREV_RE.search(prompt)
    if not m:
        return ""
    code, failure = m.group(1), m.group(2)
    items = _hint_texts(failure) + lints(code, path, isa)
    if not items:
        return ""
    return ("Notes on the failure above (from the harness; advisory: only the tests decide):\n"
            + "\n".join(f"- {t}" for t in items))


def assisted(prompt: str, path: str, isa: str, task: Task) -> str:
    out = prompt.rstrip("\n") + "\n\n" + notes(path, isa) + "\n"
    extra = failure_notes(prompt, path, isa)
    if extra:
        out += "\n" + extra + "\n"
    return out


__all__ = ["HINTS", "NOTES_DOC", "QBE_OPS", "QBE_SHEET", "STRUCTURED_EXAMPLES", "STRUCTURED_EXAMPLE_INSNS", "assisted", "failure_notes",
           "lints", "notes"]
NOTES_DOC = __doc__
