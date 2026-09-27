"""The three pipelines (stream / structured / ir) and the differential checker.

`attempt(subject, path, isa, task, prompt)` asks the subject once and returns an `Attempt`:
the code the pipeline produced (or the failure it caught) and, if code was produced, the
differential verdict. Failure categories:

  format        model output is not in the requested format (prose, unparsable JSON)
  syntax        stream assembler / structured encoder rejected an instruction
  crosscheck    GNU as disagreed with (or rejected) a statement keystone accepted
  compile       QBE / assembler / extraction failed on the IR
  wrong_result  ran to completion with a wrong return value or buffer
  abi           ABIViolation (callee-saved register, sp, caller frame, DF)
  oob           OutOfBoundsWrite (write outside the buffer / any data write for scalars)
  hang          did not return within the instruction budget
  crash         emulation fault (bad memory access, invalid instruction, ...)
  subject_timeout / subject_error   the model process timed out / failed
"""

from __future__ import annotations

import ctypes
import json
import re
import time
from dataclasses import asdict, dataclass, field

from bca.difftest import (
    BUFFER_OFFSET,
    Mismatch,
    buffer_differential,
    compile_c_reference,
    differential,
)
from bca.emu import DATA_BASE, MASK64, EmulationError, run_function
from bca.ir import IRCompileError, lower
from bca.isa import ISAS
from bca.liveeval.subject import Response, Subject
from bca.liveeval.tasks import Task
from bca.stream import StreamAssembler
from bca.structured import Session

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

    def to_dict(self) -> dict:
        d = asdict(self)
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
    if "out-of-bounds write" in msg:
        return "oob"
    if "did not return within" in msg:
        return "hang"
    if any(k in msg for k in ("not restored on return", "callee-saved register", "caller's stack frame",
                              "direction flag")):
        return "abi"
    return "crash"


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


def _buffer_detail(isa, code: bytes, task: Task, values: list[int]) -> str:
    """Expected vs actual buffer contents for one failing buffer case (re-run once)."""
    ref = compile_c_reference(task.c_ref, task.name, 2)
    ref.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64]
    arr = (ctypes.c_int64 * max(len(values), 1))(*values)
    ref(arr, len(values))
    expected = list(arr[: len(values)])
    data = b"".join((v & MASK64).to_bytes(8, "little") for v in values)
    try:
        res = run_function(isa, code, [DATA_BASE + BUFFER_OFFSET, len(values)], data=data,
                           data_offset=BUFFER_OFFSET, max_insns=MAX_INSNS, abi=False)
    except EmulationError:
        return ""
    actual = [_s64(int.from_bytes(res.data[i : i + 8], "little")) for i in range(0, len(res.data), 8)]
    if actual == expected:
        return ""
    cut = lambda xs: "[" + ", ".join(map(str, xs[:12])) + (", ..." if len(xs) > 12 else "") + "]"  # noqa: E731
    return f"; array after the call: expected {cut(expected)}, got {cut(actual)}"


def check(task: Task, isa_name: str, code: bytes, *, show: int = 3) -> Verdict:
    isa = ISAS[isa_name]
    if not code:
        return Verdict(False, "crash", message="no code was produced")
    if task.buffer:
        rep = buffer_differential(isa, code, task.c_ref, task.name, [list(b) for b in task.inputs],
                                  max_insns=MAX_INSNS)
    else:
        ref = compile_c_reference(task.c_ref, task.name, task.nargs)
        rep = differential(isa, code, ref, task.inputs, max_insns=MAX_INSNS)
    if rep.ok:
        return Verdict(True, None, rep.cases, 0, f"all {rep.cases} test cases passed")
    errored = [m for m in rep.mismatches if m.error and m.error != "buffer differs"]
    category = classify_error(errored[0].error) if errored else "wrong_result"
    # Prefer showing mismatches of the headline category first.
    ordered = sorted(rep.mismatches, key=lambda m: (classify_error(m.error) if m.error and
                     m.error != "buffer differs" else "wrong_result") != category)
    lines = [f"{len(rep.mismatches)} of {rep.cases} test cases failed. First failures:"]
    for m in ordered[:show]:
        s = f"  {_fmt_args(task, m.args)}: expected {_s64(m.expected)}"
        if m.error and m.error != "buffer differs":
            s += f", but the run failed: {m.error}"
        else:
            s += f", got {_s64(m.actual)}"
            if task.buffer:
                s += _buffer_detail(isa, code, task, list(m.args)) or (
                    "" if m.expected != m.actual else "; array contents differ")
        lines.append(s)
    return Verdict(False, category, rep.cases, len(rep.mismatches), "\n".join(lines))


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
        v = check(task, isa, a.code)
        a.check_s = time.perf_counter() - t0
        a.verdict = v
        a.ok, a.category = v.ok, v.category
        if not v.ok:
            a.failure = v.message
    return a
