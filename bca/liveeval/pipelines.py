"""The three pipelines (stream / structured / ir) and the differential checker.

`attempt(subject, path, isa, task, prompt)` asks the subject once and returns an `Attempt`:
the code the pipeline produced (or the failure it caught) and, if code was produced, the
differential verdict. Failure categories:

  format        model output is not in the requested format (prose, unparsable JSON)
  syntax        stream assembler / structured encoder rejected an instruction
  crosscheck    GNU as disagreed with (or rejected) a statement keystone accepted
  compile       QBE / assembler / extraction failed on the IR
  requirement   a static requirement of the spec is not met (recursion / SIMD; round 2)
  wrong_result  ran to completion with a wrong return value or buffer
  abi           ABIViolation (callee-saved register, sp, caller frame, DF)
  misaligned_call  a call (to the hostile callback or a recursive call) with a misaligned stack
  oob           OutOfBoundsWrite (write outside the buffer / any data write for scalars)
  oob_read      OutOfBoundsRead (read outside the readable windows; round-2 memory tasks)
  hang          did not return within the instruction budget
  crash         emulation fault (bad memory access, invalid instruction, misaligned SSE, ...)
  subject_timeout / subject_error   the model process timed out / failed

Categories come from `Mismatch.kind` (the EmulationError's `category`), not from message
text; `classify_error` remains as the text fallback for mismatches without a kind.
Failure messages quote the source line of the faulting pc when it is known (stream: the
assembly line; structured: the JSON instruction; ir: the disassembled instruction).
"""

from __future__ import annotations

import ctypes
import json
import re
import time
from dataclasses import asdict, dataclass, field

from collections.abc import Callable

from bca.difftest import (
    BUFFER_OFFSET,
    Mismatch,
    buffer_differential,
    compile_c_reference,
    differential,
    memory_differential,
)
from bca.emu import CODE_BASE, DATA_BASE, MASK64, EmulationError, callback_segments, run_function
from bca.ir import IRCompileError, lower
from bca.isa import ISAS
from bca.liveeval.subject import Response, Subject
from bca.liveeval.tasks import Task
from bca.stream import StreamAssembler
from bca.structured import Session
from bca.structured import assemble as structured_assemble

# Locator: pc -> a description of the source statement containing it (or None).
Locator = Callable[[int], "str | None"]

MAX_INSNS = 2_000_000
_FENCE_RE = re.compile(r"^\s*```[\w+-]*\s*\n(.*?)\n\s*```\s*$", re.S)
_QBE_START_RE = re.compile(r"^(?:export\b|function\b|type\b|data\b|thread\b|section\b|#|\$|@|%|\})")
_PROSE_RE = re.compile(r"^(?:[A-Z][a-z']+|I|Here|This|The|Sure)\b(?: [A-Za-z',()-]+){3,}")


@dataclass
class Verdict:
    ok: bool
    category: str | None  # None when ok
    cases: int = 0
    failed: int = 0
    message: str = ""  # human/model-readable description of the first failures


@dataclass
class Attempt:
    path: str
    isa: str
    task: str
    ok: bool
    category: str | None
    failure: str  # text fed back to the model on repair ("" when ok)
    code_text: str  # what the model wrote (for the stream path: up to the abort point)
    code: bytes | None = None
    verdict: Verdict | None = None
    response: Response | None = None
    wall_s: float = 0.0
    check_s: float = 0.0
    stream: dict = field(default_factory=dict)  # stream-path statistics
    locate: Locator | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("locate", None)
        d["code"] = self.code.hex() if self.code is not None else None
        return d


# -- output clean-up -----------------------------------------------------------------------


def strip_fences(text: str) -> str:
    t = text.strip()
    m = _FENCE_RE.match(t)
    if m:
        return m.group(1)
    if t.startswith("```"):  # unterminated or with trailing prose
        body = t.split("\n", 1)[1] if "\n" in t else ""
        return body.split("```", 1)[0]
    return t


def parse_json_object(text: str):
    t = strip_fences(text)
    try:
        return json.loads(t)
    except ValueError:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no JSON object found in the output")
    return json.loads(t[i : j + 1])


# -- checking -------------------------------------------------------------------------------


def classify_error(msg: str) -> str:
    """Text fallback for mismatches that carry no `kind` (older producers)."""
    if "out-of-bounds write" in msg:
        return "oob"
    if "out-of-bounds read" in msg:
        return "oob_read"
    if "misaligned call" in msg:
        return "misaligned_call"
    if "did not return within" in msg:
        return "hang"
    if any(k in msg for k in ("not restored on return", "callee-saved register", "caller's stack frame",
                              "direction flag")):
        return "abi"
    return "crash"


# Mismatch.kind (EmulationError.category) -> attempt category.
KIND_CATEGORY = {
    "wrong_result": "wrong_result", "abi": "abi", "misaligned_call": "misaligned_call",
    "oob_write": "oob", "oob_read": "oob_read", "hang": "hang", "crash": "crash",
    "misaligned_access": "crash",
}  # fmt: skip


def classify_mismatch(m: Mismatch) -> str:
    if m.kind:
        return KIND_CATEGORY.get(m.kind, "crash")
    if m.error and m.error != "buffer differs":
        return classify_error(m.error)
    return "wrong_result"


def _fmt_args(task: Task, args) -> str:
    if task.buffer:
        vals = list(args)
        shown = ", ".join(str(v) for v in vals[:12]) + (", ..." if len(vals) > 12 else "")
        return f"{task.name}(a=[{shown}], n={len(vals)})"
    return f"{task.name}({', '.join(str(a) for a in args)})"


def _s64(v):
    if v is None:
        return None
    v &= MASK64
    return v - (1 << 64) if v >> 63 else v


def _cut(xs) -> str:
    xs = list(xs)
    return "[" + ", ".join(map(str, xs[:12])) + (", ..." if len(xs) > 12 else "") + "]"


def _words(data: bytes, size: int, signed: bool) -> list[int]:
    return [int.from_bytes(data[i : i + size], "little", signed=signed) for i in range(0, len(data) - size + 1, size)]


def _buffer_detail(task: Task, m: Mismatch) -> str:
    """Expected vs actual buffer contents for one failing buffer case."""
    if m.expected_data is None or m.actual_data is None or m.expected_data == m.actual_data:
        return ""
    return (f"; array after the call: expected {_cut(_words(m.expected_data, 8, True))}, "
            f"got {_cut(_words(m.actual_data, 8, True))}")


def _mem_detail(task: Task, m: Mismatch) -> str:
    """For memory tasks: the writable window(s) after the call, expected vs actual."""
    if m.expected_data is None or m.actual_data is None or m.expected_data == m.actual_data:
        return ""
    case = m.case
    parts = []
    for lo, hi in (case.writable if case is not None and case.writable else [(0, len(m.expected_data))]):
        e, a = m.expected_data[lo:hi], m.actual_data[lo:hi]
        if e != a:
            parts.append(f"memory [{lo}, {hi}) of the block after the call: expected "
                         f"{_cut(_words(e, task.mem_elem, task.mem_signed))}, got "
                         f"{_cut(_words(a, task.mem_elem, task.mem_signed))}")
    if not parts:
        parts.append("memory outside the writable range changed")
    return "; " + "; ".join(parts)


def run_kwargs(task: Task, isa_name: str) -> dict:
    """Extra run_function options a task needs (hostile callback, call-alignment checks)."""
    kw: dict = {}
    if task.calls == "callback":
        kw["segments"] = callback_segments(ISAS[isa_name])
        kw["aligned_entries"] = [callback_segments(ISAS[isa_name])[0][0]]
    elif task.calls == "self":
        kw["aligned_entries"] = [CODE_BASE]
    if task.round >= 2 and isa_name == "x86_64":
        kw["strict_sse_alignment"] = True
    return kw


def _where(m: Mismatch, locate: Locator | None) -> str:
    if m.pc is None or locate is None:
        return ""
    try:
        text = locate(m.pc)
    except Exception:  # noqa: BLE001 - location is best effort
        text = None
    if not text:
        return ""
    prefix = "the call instruction" if m.kind == "misaligned_call" else "the faulting instruction"
    return f" [{prefix} is {text}]"


def _fmt_case(task: Task, m: Mismatch) -> str:
    if task.mem:
        return m.case.label if m.case is not None and m.case.label else f"{task.name}{tuple(m.args)}"
    return _fmt_args(task, m.args)


def run_differential(task: Task, isa_name: str, code: bytes):
    isa = ISAS[isa_name]
    kw = run_kwargs(task, isa_name)
    if task.mem:
        ref = compile_c_reference(task.c_ref, task.reference_name, task.nargs)
        return memory_differential(isa, code, ref, task.inputs, max_insns=task.max_insns, **kw)
    if task.buffer:
        return buffer_differential(isa, code, task.c_ref, task.name, [list(b) for b in task.inputs],
                                   max_insns=max(MAX_INSNS, task.max_insns), **kw)
    ref = compile_c_reference(task.c_ref, task.reference_name, task.nargs)
    return differential(isa, code, ref, task.inputs, max_insns=max(MAX_INSNS, task.max_insns), **kw)


def check(task: Task, isa_name: str, code: bytes, *, show: int = 3, locate: Locator | None = None) -> Verdict:
    if not code:
        return Verdict(False, "crash", message="no code was produced")
    rep = run_differential(task, isa_name, code)
    if rep.ok:
        return Verdict(True, None, rep.cases, 0, f"all {rep.cases} test cases passed")
    errored = [m for m in rep.mismatches if classify_mismatch(m) != "wrong_result"]
    category = classify_mismatch(errored[0]) if errored else "wrong_result"
    # Prefer showing mismatches of the headline category first.
    ordered = sorted(rep.mismatches, key=lambda m: classify_mismatch(m) != category)
    lines = [f"{len(rep.mismatches)} of {rep.cases} test cases failed. First failures:"]
    for m in ordered[:show]:
        s = f"  {_fmt_case(task, m)}: expected {_s64(m.expected)}"
        if classify_mismatch(m) != "wrong_result":
            s += f", but the run failed: {m.error}{_where(m, locate)}"
        else:
            s += f", got {_s64(m.actual)}"
            if task.mem:
                s += _mem_detail(task, m)
            elif task.buffer:
                s += _buffer_detail(task, m) or ("" if m.expected != m.actual else "; array contents differ")
        lines.append(s)
    return Verdict(False, category, rep.cases, len(rep.mismatches), "\n".join(lines))


# -- static requirements (round 2) ----------------------------------------------------------

_X86_VEC_ARITH = re.compile(r"^v?p(add|sub|mul|madd|hadd|sad)\w*$")
_A64_VEC_OPERAND = re.compile(r"\bv\d+\.(?:\d+)?[bhsd]\b")
_A64_VEC_ARITH = re.compile(r"^[su]?(add|sub|mul|mla|mls|dot|mull2?|mlal2?|mlsl2?|addl2?|addw2?|addlp|adalp|"
                            r"addlv|addv|addp|subl2?|subw2?)$")


def _insns(isa_name: str, code: bytes, base: int = CODE_BASE):
    return list(ISAS[isa_name].disassembler().disasm(code, base))


def _direct_target(isa_name: str, insn) -> int | None:
    op = insn.op_str.strip().lstrip("#")
    try:
        return int(op, 0)
    except ValueError:
        return None


def calls_own_entry(isa_name: str, code: bytes, base: int = CODE_BASE) -> bool:
    """True if the code contains a direct call (call rel32 / bl) to its entry point (or to the
    target of an entry trampoline jump)."""
    insns = _insns(isa_name, code, base)
    entries = {base}
    if insns and insns[0].mnemonic in ("jmp", "b"):
        t = _direct_target(isa_name, insns[0])
        if t is not None:
            entries.add(t)
    call = "call" if isa_name == "x86_64" else "bl"
    return any(i.mnemonic == call and _direct_target(isa_name, i) in entries for i in insns)


def uses_simd(isa_name: str, code: bytes) -> bool:
    """True if the code contains a vector (SSE/NEON) arithmetic instruction."""
    for i in _insns(isa_name, code):
        if isa_name == "x86_64":
            if _X86_VEC_ARITH.match(i.mnemonic) and ("xmm" in i.op_str or "ymm" in i.op_str):
                return True
        elif _A64_VEC_ARITH.match(i.mnemonic) and _A64_VEC_OPERAND.search(i.op_str):
            return True
    return False


def requirement_failure(task: Task, isa_name: str, code: bytes) -> str | None:
    """The repair message for an unmet static requirement, or None."""
    if "recursion" in task.requires and not calls_own_entry(isa_name, code):
        call = f"call {task.name}" if isa_name == "x86_64" else f"bl {task.name}"
        return (f"Requirement not met: the specification requires a recursive implementation, but "
                f"the machine code contains no call to the function's own entry point (expected "
                f"a `{call}` instruction for the recursive calls; checked by disassembly). A loop "
                "or a jump back to the entry does not count.")
    if "simd" in task.requires and not uses_simd(isa_name, code):
        what = ("SSE vector arithmetic on xmm registers (e.g. pmuldq, paddq, pmulld)" if isa_name == "x86_64"
                else "NEON vector arithmetic on v registers (e.g. smull/smlal v.2d, add v.2d)")
        return (f"Requirement not met: the specification requires SIMD, but the machine code "
                f"contains no {what} (checked by disassembly). Process several elements per "
                "instruction in the main loop and keep a scalar loop only for the tail.")
    return None


# -- source locators --------------------------------------------------------------------------


def _range_locator(items) -> Locator:
    """items: (address, length, description) triples."""
    items = sorted(items)

    def locate(pc: int) -> str | None:
        for addr, n, text in items:
            if addr <= pc < addr + max(n, 1):
                return text
        return None

    return locate


def stream_locator(prog) -> Locator:
    return _range_locator((i.address, len(i.data), f"line {i.line}: `{i.text.strip()}`")
                          for i in prog.instructions)


def structured_locator(isa_name: str, instructions: list, base: int = CODE_BASE) -> Locator | None:
    try:
        prog = structured_assemble(isa_name, instructions, base=base)
    except Exception:  # noqa: BLE001
        return None
    return _range_locator((base + e.offset, len(e.data), f"instruction {e.index}: "
                           f"{json.dumps(e.raw, separators=(',', ':'))}") for e in prog.entries)


def disasm_locator(isa_name: str, code: bytes, base: int = CODE_BASE) -> Locator:
    return _range_locator((i.address, i.size, f"`{i.mnemonic} {i.op_str}`".replace(" `", "`").rstrip()
                           + f" at {i.address:#x} (disassembly of the compiled code)")
                          for i in _insns(isa_name, code, base))


def disassemble(isa_name: str, code: bytes | None, base: int = 0x100000) -> str:
    if not code:
        return ""
    cs = ISAS[isa_name].disassembler()
    out, end = [], base
    for i in cs.disasm(code, base):
        out.append(f"{i.address:#08x}: {i.bytes.hex():<24} {i.mnemonic} {i.op_str}".rstrip())
        end = i.address + i.size
    if end < base + len(code):
        out.append(f"{end:#08x}: {code[end - base:].hex()}  (undecodable)")
    return "\n".join(out)


# -- pipelines ------------------------------------------------------------------------------


def _stream_attempt(subject: Subject, isa: str, task: Task, prompt: str, raw_path: str) -> Attempt:
    sa = StreamAssembler(ISAS[isa])
    t0 = time.perf_counter()
    st: dict = {"first_insn_s": None, "abort": None, "chars_fed": 0, "insns_at_abort": None}
    halted: list = []

    def on_text(delta: str) -> bool:
        st["chars_fed"] += len(delta)
        try:
            events = sa.feed(delta)
        except Exception as exc:  # noqa: BLE001 - recorded as an internal error
            halted.append(("internal", f"stream assembler raised {type(exc).__name__}: {exc}", 0, ""))
            return False
        for ev in events:
            if ev.kind == "insn" and st["first_insn_s"] is None:
                st["first_insn_s"] = time.perf_counter() - t0
            if ev.kind == "error":
                halted.append(("error", ev.message, ev.line, ev.text))
                st["abort"] = {"line": ev.line, "text": ev.text, "chars_received": st["chars_fed"],
                               "s": time.perf_counter() - t0,
                               "insns_committed": len(sa._insns)}  # noqa: SLF001 - stats only
                return False
        return True

    resp = subject.complete(prompt, on_text, raw_path=raw_path)
    wall = time.perf_counter() - t0
    a = Attempt("stream", isa, task.name, False, None, "", resp.text, response=resp, wall_s=wall, stream=st)
    if halted:
        kind, msg, line, text = halted[0]
        st["killed"] = resp.killed
        if kind == "internal":
            a.category, a.failure = "crash", msg
            return a
        a.category = _stream_category(msg, text, st)
        a.failure = (
            f"The assembler rejected line {line} (`{text.strip()}`): {msg}\n"
            f"Generation was stopped at that line; the {st['abort']['insns_committed']} "
            "instructions before it had been accepted."
        )
        return a
    if not resp.ok:
        a.category = "subject_timeout" if resp.timed_out else "subject_error"
        a.failure = f"the model call failed: {resp.error}"
        return a
    prog = sa.finish()
    if prog.errors:
        ev = prog.errors[0]
        a.category = _stream_category(ev.message, ev.text, st)
        a.failure = f"The assembler rejected the program: {ev.message}"
        return a
    a.code = prog.code
    a.locate = stream_locator(prog)
    return a


def _stream_category(msg: str, text: str, st: dict) -> str:
    if "GNU as cross-check" in msg or "but GNU as encodes" in msg:
        return "crosscheck"
    if not st.get("first_insn_s") and _PROSE_RE.match(text.strip()):
        return "format"
    return "syntax"


def _structured_attempt(subject: Subject, isa: str, task: Task, prompt: str, raw_path: str) -> Attempt:
    t0 = time.perf_counter()
    resp = subject.complete(prompt, None, raw_path=raw_path)
    a = Attempt("structured", isa, task.name, False, None, "", resp.text, response=resp,
                wall_s=time.perf_counter() - t0)
    if not resp.ok:
        a.category = "subject_timeout" if resp.timed_out else "subject_error"
        a.failure = f"the model call failed: {resp.error}"
        return a
    try:
        obj = parse_json_object(resp.text)
    except ValueError as exc:
        a.category, a.failure = "format", f"The output is not a JSON object: {exc}"
        return a
    if not isinstance(obj, dict) or not isinstance(obj.get("instructions"), list):
        a.category, a.failure = "format", 'The output must be a JSON object {"instructions": [...]}.'
        return a
    res = Session(isa).emit_program(obj["instructions"])
    if not res["ok"]:
        errs = res["errors"]
        a.category = "syntax"
        a.failure = "The encoder rejected the program:\n" + "\n".join(
            "  " + e["text"] for e in errs[:8]) + (f"\n  ... ({len(errs) - 8} more)" if len(errs) > 8 else "")
        return a
    a.code = bytes.fromhex(res["code"])
    a.locate = structured_locator(isa, obj["instructions"])
    return a


def _ir_attempt(subject: Subject, isa: str, task: Task, prompt: str, raw_path: str) -> Attempt:
    t0 = time.perf_counter()
    resp = subject.complete(prompt, None, raw_path=raw_path)
    a = Attempt("ir", isa, task.name, False, None, "", resp.text, response=resp,
                wall_s=time.perf_counter() - t0)
    if not resp.ok:
        a.category = "subject_timeout" if resp.timed_out else "subject_error"
        a.failure = f"the model call failed: {resp.error}"
        return a
    src = strip_fences(resp.text)
    try:
        a.code = lower(src, "qbe", isa, task.name)
        a.locate = disasm_locator(isa, a.code)
    except IRCompileError as exc:
        # Text before the function that is not QBE (a sentence of prose) is a format error.
        head = next((ln.strip() for ln in src.splitlines() if ln.strip()), "")
        a.category = "compile" if _QBE_START_RE.match(head) else "format"
        a.failure = f"Compilation failed at stage {exc.stage}:\n{exc.detail[:1500]}"
    return a


PIPELINES = {"stream": _stream_attempt, "structured": _structured_attempt, "ir": _ir_attempt}


def attempt(subject: Subject, path: str, isa: str, task: Task, prompt: str, *, raw_path: str = "") -> Attempt:
    a = PIPELINES[path](subject, isa, task, prompt, raw_path)
    if a.code is not None and a.category is None:
        t0 = time.perf_counter()
        req = requirement_failure(task, isa, a.code)
        if req is not None:
            a.check_s = time.perf_counter() - t0
            a.verdict = Verdict(False, "requirement", message=req)
            a.ok, a.category, a.failure = False, "requirement", req
            return a
        v = check(task, isa, a.code, locate=a.locate)
        a.check_s = time.perf_counter() - t0
        a.verdict = v
        a.ok, a.category = v.ok, v.category
        if not v.ok:
            a.failure = v.message
    return a
