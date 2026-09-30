"""The app battery in the live eval: whole static programs (bca.apps) through the same runner,
subjects, budgets, guards, resume and summaries as the function batteries.

Paths (design decision 1): `asm` and `stream` only.

  asm     strip Markdown fences -> bca.image.assemble_program (GNU as) -> link_program -> tests
  stream  the answer is fed to StreamAssembler(isa, program=True) as it arrives, with the same early
          abort and repair cut as the function stream path (pipelines.stream_collect) -> finish()
          -> StreamObject.link() -> tests

Both paths get the same prompt (prompts_apps.build_app_prompt) and link with the task's .bss limit.
Tests: every AppTest runs with stdin delivered whole, then every AppTest again with reads of at
most 7 bytes (a program must handle short reads); the first failing run decides the verdict.

Categories (besides the harness's format / syntax / crosscheck / refusal / subject_timeout /
subject_error):
  link            the text was accepted statement by statement but is not a linkable program
                  (undefined symbol, no `_start`, .bss over the task's limit, relocation overflow);
                  the stream path's "undefined label" / never-defined `.globl` errors map here too,
                  so one mistake gets one category on both paths
  crash, bad_syscall, no_exit, oob, write_to_text, stack_overflow, output_limit
                  the process runner's ProcFault kinds (bca.process)
  wrong_output    exited, stdout differs
  wrong_status    exited with the expected stdout but the wrong exit status

Cells are sampled: `wc.asm.x86_64.s2` is sample 2 of (wc, asm, x86_64); every sample is its own
cell with its own attempts (up to 4, repairs included) and resumes independently.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass

from bca.apps import APP_TASKS, AppTask, AppTest
from bca.isa import ISAS
from bca.liveeval.pipelines import (
    _PROSE_RE,
    Attempt,
    Verdict,
    _call,
    _range_locator,
    _stream_category,
    stream_collect,
    stream_locator,
    strip_fences,
)
from bca.liveeval.subject import Subject

APP_PATHS = ("asm", "stream")
APP_NAMES = frozenset(t.name for t in APP_TASKS)
READ_CHUNK = 7  # the second delivery of every test: reads of at most this many bytes
STDIN_SHOWN = 200  # bytes of stdin quoted in a failure message
WINDOW = 160  # bytes of expected / actual stdout quoted from the first difference on
_NA = "the app battery runs on the asm and stream paths only (design decision 1)"


@dataclass(frozen=True)
class AppEvalTask:
    """An AppTask as the live-eval runner sees it: a name, the paths it cannot run on, and the
    sample number of the cell (0 = not sampled; app cells are always sampled, 1..N)."""

    app: AppTask
    sample: int = 0
    is_app = True  # class attribute: pipelines / prompts / summaries dispatch on it

    @property
    def name(self) -> str:
        return self.app.name

    @property
    def spec(self) -> str:
        return self.app.spec

    @property
    def na_paths(self) -> dict[str, str]:
        return {"structured": _NA, "ir": _NA}

    @property
    def na(self) -> tuple[tuple[str, str], ...]:
        return tuple(self.na_paths.items())

    @property
    def max_insns(self) -> int:
        return self.app.max_insns


TASKS: tuple[AppEvalTask, ...] = tuple(AppEvalTask(t) for t in APP_TASKS)
BY_NAME = {t.name: t for t in TASKS}


def is_app(task) -> bool:
    return bool(getattr(task, "is_app", False))


@functools.lru_cache(maxsize=None)
def app_tests(name: str) -> tuple[AppTest, ...]:
    return BY_NAME[name].app.tests()


# -- failure messages --------------------------------------------------------------------------


def escape(data: bytes) -> str:
    """Bytes as a double-quoted string: printable ASCII as is, \\n \\t \\r \\\\ \\" and \\xHH."""
    out = []
    for b in data:
        c = chr(b)
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif 0x20 <= b < 0x7F:
            out.append(c)
        else:
            out.append(f"\\x{b:02x}")
    return '"' + "".join(out) + '"'


def _excerpt(data: bytes, n: int) -> str:
    if len(data) <= n:
        return escape(data)
    return escape(data[:n]) + f" ... ({len(data) - n} more bytes)"


def first_difference(expected: bytes, actual: bytes) -> int | None:
    """Index of the first differing byte (a length difference counts), or None if equal."""
    if expected == actual:
        return None
    n = min(len(expected), len(actual))
    return next((i for i in range(n) if expected[i] != actual[i]), n)


def _position(data: bytes, i: int) -> str:
    line = data.count(b"\n", 0, i) + 1
    col = i - (data.rfind(b"\n", 0, i) + 1) + 1
    return f"line {line}, column {col}"


def _stdout_diff(expected: bytes, actual: bytes) -> list[str]:
    i = first_difference(expected, actual)
    if i is None:
        return [f"stdout ({len(actual)} bytes) was as expected"]
    exp_w, act_w = expected[i : i + WINDOW], actual[i : i + WINDOW]
    tail = lambda d: f" ... ({len(d) - i - WINDOW} more bytes)" if len(d) > i + WINDOW else ""  # noqa: E731
    return [
        f"stdout differs from byte {i} on ({_position(expected, i)} of the expected output); "
        f"expected {len(expected)} bytes in total, got {len(actual)}",
        f"  expected from byte {i}: " + (escape(exp_w) + tail(expected) if exp_w else "(end of output)"),
        f"  actual from byte {i}:   " + (escape(act_w) + tail(actual) if act_w else "(end of output)"),
    ]


def failure_message(task: AppEvalTask, test: AppTest, chunk: int | None, k: int, n: int, res,
                    category: str, locate=None) -> str:
    """The repair text for the first failing test run (see the module docstring)."""
    how = ("stdin delivered whole" if chunk is None
           else f"stdin delivered in reads of at most {chunk} bytes each")
    lines = [f"Test `{test.label}` failed ({how}; test run {k} of {n}; the runs before it passed).",
             f"stdin ({len(test.stdin)} bytes): {_excerpt(test.stdin, STDIN_SHOWN)}"]
    if res.fault is not None:
        f = res.fault
        where = ""
        if f.pc is not None:
            where = f" at pc {f.pc:#x}"
            text = None
            if locate is not None:
                try:
                    text = locate(f.pc)
                except Exception:  # noqa: BLE001 - location is best effort
                    text = None
            if text:
                where += f" [the faulting instruction is {text}]"
        lines.append(f"The program stopped with a fault ({category}): {f.message}{where}")
        lines.append(f"exit status: expected {test.status}, got none (the program did not exit)")
        lines += _stdout_diff(test.stdout, res.stdout) if res.stdout or test.stdout else []
        if res.stdout and res.stdout == test.stdout[: len(res.stdout)]:
            lines.append(f"(the {len(res.stdout)} bytes written before the fault were correct so far)")
    else:
        lines.append(f"exit status: expected {test.status}, got {res.status}")
        lines += _stdout_diff(test.stdout, res.stdout)
    return "\n".join(lines)


# -- checking ----------------------------------------------------------------------------------


def run_tests(task: AppEvalTask, image, *, locate=None) -> Verdict:
    """Every test with stdin whole, then every test with READ_CHUNK-byte reads; stops at the first
    failing run (Verdict.failed is then 1: the remaining runs are not executed)."""
    from bca.process import run_program

    tests = app_tests(task.name)
    runs = [(t, c) for c in (None, READ_CHUNK) for t in tests]
    for k, (t, chunk) in enumerate(runs, 1):
        res = run_program(image, t.stdin, max_insns=task.app.max_insns, read_chunk=chunk)
        if res.fault is not None:
            category = res.fault.kind
        elif res.stdout != t.stdout:
            category = "wrong_output"
        elif res.status != t.status:
            category = "wrong_status"
        else:
            continue
        return Verdict(False, category, len(runs), 1,
                       failure_message(task, t, chunk, k, len(runs), res, category, locate))
    return Verdict(True, None, len(runs), 0, f"all {len(runs)} test runs passed")


def text_bytes(image) -> bytes:
    """The program's code: the text segment up to its last instruction."""
    base, data, _perms = image.segments[0]
    end = image.text_end - base if image.text_end else len(data)
    return bytes(data[:end])


def asm_locator(isa: str, image, code: bytes):
    """pc -> the disassembled instruction and the nearest label before it (GNU as keeps no line
    table in the object, so the asm path names the instruction, not the source line)."""
    base = image.segments[0][0]
    syms = sorted((a, n) for n, a in image.symbols.items() if base <= a < base + len(code))

    def label(addr: int) -> str:
        best = None
        for a, n in syms:
            if a <= addr:
                best = (a, n)
        if best is None:
            return ""
        return f"{best[1]}+{addr - best[0]:#x}" if addr != best[0] else best[1]

    items = []
    for i in ISAS[isa].disassembler().disasm(code, base):
        where = label(i.address)
        items.append((i.address, i.size, f"`{i.mnemonic} {i.op_str}`".replace(" `", "`")
                      + f" at {i.address:#x}" + (f" ({where})" if where else "")
                      + " (disassembly of the assembled program)"))
    return _range_locator(items)


# -- pipelines ---------------------------------------------------------------------------------


def _is_link_error(msg: str) -> bool:
    return msg.startswith("undefined label") or "names a label that is never defined" in msg


def _asm_attempt(subject: Subject, isa: str, task: AppEvalTask, prompt: str, raw_path: str,
                 timeout: float | None = None) -> Attempt:
    from bca.image import AsmError, LinkError, assemble_program, link_program

    t0 = time.perf_counter()
    resp = _call(subject, prompt, None, raw_path, timeout)
    a = Attempt("asm", isa, task.name, False, None, "", resp.text, response=resp, wall_s=time.perf_counter() - t0)
    if resp.refusal:  # before anything else: text streamed before a refusal is not an answer
        a.category, a.failure = "refusal", f"the model refused to answer ({resp.refusal})"
        return a
    if not resp.ok:
        a.category = "subject_timeout" if resp.timed_out else "subject_error"
        a.code_text = ""  # partial text exists only for streaming subjects: never shown, for parity
        a.failure = f"the model call failed: {resp.error}"
        return a
    src = strip_fences(resp.text)
    a.code_text = src
    if not src.strip():
        a.category, a.failure = "format", "The output contains no assembly source."
        return a
    try:
        obj = assemble_program(src, isa)
    except AsmError as exc:
        lines = src.splitlines()
        bad = lines[exc.line - 1].strip() if exc.line and 0 < exc.line <= len(lines) else ""
        first = next((ln.strip() for ln in lines if ln.strip()), "")
        prose = bad and bad == first and _PROSE_RE.match(bad)
        a.category = "format" if prose else "syntax"
        a.failure = f"GNU as rejected the program:\n{str(exc)[:1500]}"
        return a
    try:
        image = link_program(obj, isa, bss_size_limit=task.app.bss_size)
    except LinkError as exc:
        a.category, a.failure = "link", f"The program cannot be linked: {exc}"
        return a
    a.code = text_bytes(image)
    a.locate = asm_locator(isa, image, a.code)
    a.image = image
    return a


def _app_stream_attempt(subject: Subject, isa: str, task: AppEvalTask, prompt: str, raw_path: str,
                        timeout: float | None = None) -> Attempt:
    from bca.image import LinkError
    from bca.stream import StreamAssembler

    sa = StreamAssembler(ISAS[isa], program=True)
    a = stream_collect(subject, sa, "stream", isa, task.name, prompt, raw_path, timeout)
    if a.category is not None:
        return a
    obj = sa.finish()
    if obj.errors:
        ev = obj.errors[0]
        if _is_link_error(ev.message):
            a.category = "link"
            a.failure = f"The program cannot be linked: {ev.message}"
        else:
            a.category = _stream_category(ev.message, ev.text, a.stream)
            a.failure = f"The assembler rejected the program: {ev.message}"
        return a
    try:
        image = obj.link(bss_size_limit=task.app.bss_size)
    except LinkError as exc:
        a.category, a.failure = "link", f"The program cannot be linked: {exc}"
        return a
    a.code = text_bytes(image)
    a.locate = stream_locator(obj)
    a.image = image
    return a


APP_PIPELINES = {"asm": _asm_attempt, "stream": _app_stream_attempt}


def app_attempt(subject: Subject, path: str, isa: str, task: AppEvalTask, prompt: str, *, raw_path: str = "",
                timeout: float | None = None) -> Attempt:
    """One call and its verdict (pipelines.attempt dispatches here for app tasks)."""
    if path not in APP_PIPELINES:
        raise ValueError(f"{task.name}: {_NA} (got {path!r})")
    a = APP_PIPELINES[path](subject, isa, task, prompt, raw_path, timeout)
    image = getattr(a, "image", None)
    if image is not None and a.category is None:
        t0 = time.perf_counter()
        v = run_tests(task, image, locate=a.locate)
        a.check_s = time.perf_counter() - t0
        a.verdict = v
        a.ok, a.category = v.ok, v.category
        if not v.ok:
            a.failure = v.message
    if hasattr(a, "image"):
        del a.image  # not part of the record
    return a
