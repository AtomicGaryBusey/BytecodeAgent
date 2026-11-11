"""bca.liveeval with a scripted FakeSubject: no model calls, no quota."""

import json

import pytest

from bca.liveeval import BY_NAME, Budget, FakeSubject, attempt, build_prompt, check, run_cell
from bca.liveeval.pipelines import classify_error, parse_json_object, strip_fences
from bca.liveeval.subject import CLAUDE_FLAGS, ClaudeSubject

SUM = BY_NAME["sum_to"]
REV = BY_NAME["reverse"]

X86_SUM = """sum_to:
    xor eax, eax
    test rdi, rdi
    jle .Ldone
.Lloop:
    add rax, rdi
    dec rdi
    jnz .Lloop
.Ldone:
    ret
"""

A64_SUM = """sum_to:
    mov x1, x0
    mov x0, #0
.Lloop:
    cmp x1, #0
    b.le .Ldone
    add x0, x0, x1
    sub x1, x1, #1
    b .Lloop
.Ldone:
    ret
"""

A64_SUM_JSON = {"instructions": [
    {"op": "mov", "dst": "x1", "src": ["x0"]},
    {"op": "mov", "dst": "x0", "src": [{"imm": 0}]},
    {"label": "loop"},
    {"op": "cmp", "src": ["x1", {"imm": 0}]},
    {"op": "b.le", "target": "done"},
    {"op": "add", "dst": "x0", "src": ["x0", "x1"]},
    {"op": "sub", "dst": "x1", "src": ["x1", {"imm": 1}]},
    {"op": "b", "target": "loop"},
    {"label": "done"},
    {"op": "ret"},
]}

QBE_SUM = """export function l $sum_to(l %n) {
@start
    %s =l copy 0
    %i =l copy 1
@loop
    %c =w cslel %i, %n
    jnz %c, @body, @end
@body
    %s =l add %s, %i
    %i =l add %i, 1
    jmp @loop
@end
    ret %s
}
"""


def chunks(text, n=7):
    return [text[i : i + n] for i in range(0, len(text), n)]


def _structured_ok(isa="aarch64"):
    from bca.structured import Session

    return Session(isa).emit_program(A64_SUM_JSON["instructions"])


# -- prompts --------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["stream", "structured", "ir"])
@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_prompt_structure(path, isa):
    p = build_prompt(path, isa, SUM)
    assert "int64_t sum_to(int64_t n)" in p and SUM.spec in p
    assert ("rdi" in p) if isa == "x86_64" else ("x0-x7" in p)
    assert "<previous>" not in p
    r = build_prompt(path, isa, SUM, ("old code", "it broke"))
    assert r.startswith(p.rstrip()) and "old code" in r and "it broke" in r


def test_claude_flags_isolate_subject():
    argv = ClaudeSubject(binary="claude").argv("hi")
    for flag in ("--tools", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                 "--setting-sources", "--include-partial-messages"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[-2:] == ["--", "hi"]
    assert CLAUDE_FLAGS[0] == "-p"


# -- stream path ----------------------------------------------------------------------------


@pytest.mark.parametrize("isa,src", [("x86_64", X86_SUM), ("aarch64", A64_SUM)])
def test_stream_chunked_pass(isa, src):
    subj = FakeSubject([chunks(src, 5)])
    a = attempt(subj, "stream", isa, SUM, build_prompt("stream", isa, SUM))
    assert a.ok and a.category is None, a.failure
    assert a.stream["first_insn_s"] is not None and a.stream["abort"] is None
    assert subj.stopped_at == [None]


def test_stream_fenced_output_passes():
    subj = FakeSubject([chunks("```asm\n" + X86_SUM + "```\n", 9)])
    assert attempt(subj, "stream", "x86_64", SUM, "p").ok


def test_stream_error_kills_subject_early():
    bad = X86_SUM.replace("    dec rdi\n", "    decq rdi, 1, 2\n")
    parts = chunks(bad, 6)
    subj = FakeSubject([parts])
    a = attempt(subj, "stream", "x86_64", SUM, "p")
    assert not a.ok and a.category == "syntax"
    stop = subj.stopped_at[0]
    assert stop is not None and stop < len(parts) - 1  # stopped before the end of the output
    assert a.response.killed
    ab = a.stream["abort"]
    assert ab["line"] == 7 and "decq" in ab["text"] and ab["insns_committed"] == 4
    assert "line 7" in a.failure and "decq" in a.failure
    assert a.code_text == "".join(parts[: stop + 1])


def test_stream_prose_is_format_error():
    subj = FakeSubject([["Here is the implementation you asked for:\n", X86_SUM]])
    a = attempt(subj, "stream", "x86_64", SUM, "p")
    assert a.category == "format" and subj.stopped_at == [0]


def test_stream_undefined_label_at_finish():
    subj = FakeSubject([X86_SUM.replace("jle .Ldone", "jle .Lnowhere")])
    a = attempt(subj, "stream", "x86_64", SUM, "p")
    assert a.category == "syntax" and "nowhere" in a.failure


def test_stream_wrong_result_then_repair(tmp_path):
    wrong = X86_SUM.replace("jnz .Lloop", "jg .Lloop").replace("add rax, rdi", "add rax, 1")
    subj = FakeSubject([chunks(wrong), chunks(X86_SUM)])
    rec = run_cell(subj, SUM, "stream", "x86_64", tmp_path)
    assert rec["pass_at_1"] is False and rec["pass_final"] is True
    assert rec["attempts"] == 2 and rec["categories"] == ["wrong_result", None]
    repair = subj.prompts[1]
    assert "<previous>" in repair and "add rax, 1" in repair and "expected" in repair and "got" in repair


# -- structured path ------------------------------------------------------------------------


def test_structured_json_with_fences():
    text = "```json\n" + json.dumps(A64_SUM_JSON, indent=1) + "\n```"
    a = attempt(FakeSubject([text]), "structured", "aarch64", SUM, "p")
    assert a.ok, a.failure
    assert a.code == bytes.fromhex(_structured_ok()["code"])


def test_structured_encoder_error_and_format_error():
    bad = json.loads(json.dumps(A64_SUM_JSON))
    bad["instructions"][5]["dst"] = "x99"
    a = attempt(FakeSubject([json.dumps(bad)]), "structured", "aarch64", SUM, "p")
    assert a.category == "syntax" and "instruction 5" in a.failure
    a = attempt(FakeSubject(["I cannot do that."]), "structured", "aarch64", SUM, "p")
    assert a.category == "format"
    a = attempt(FakeSubject(['{"code": []}']), "structured", "aarch64", SUM, "p")
    assert a.category == "format"


# -- IR path --------------------------------------------------------------------------------


def test_ir_compile_error_then_fixed(tmp_path):
    broken = QBE_SUM.replace("%c =w cslel %i, %n", "%c =w cslel %i")
    subj = FakeSubject([broken, "```\n" + QBE_SUM + "```"])
    rec = run_cell(subj, SUM, "ir", "x86_64", tmp_path)
    assert rec["categories"] == ["compile", None] and rec["pass_final"]
    assert "qbe" in subj.prompts[1] and "<failure>" in subj.prompts[1]


def test_ir_prose_before_code_is_format_error():
    text = "QBE temporaries can be assigned multiple times.\n\n" + QBE_SUM
    a = attempt(FakeSubject([text]), "ir", "x86_64", SUM, "p")
    assert a.category == "format" and "unknown keyword" in a.failure


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_ir_pass(isa):
    assert attempt(FakeSubject([QBE_SUM]), "ir", isa, SUM, "p").ok


# -- checker categories ---------------------------------------------------------------------


def _asm(isa, src):
    from bca.isa import ISAS
    from bca.stream import StreamAssembler

    sa = StreamAssembler(ISAS[isa])
    sa.feed(src)
    prog = sa.finish()
    assert prog.ok, prog.errors
    return prog.code


def test_check_abi_violation():
    code = _asm("x86_64", "xor ebx, ebx\n" + X86_SUM.replace("sum_to:\n", ""))
    v = check(SUM, "x86_64", code)
    assert v.category == "abi" and "rbx" in v.message


def test_check_oob_write():
    # reverse that also writes a[n]
    src = """reverse:
    mov qword ptr [rdi + rsi*8], 0
    xor eax, eax
    ret
"""
    v = check(REV, "x86_64", _asm("x86_64", src))
    assert v.category == "oob" and "out-of-bounds" in v.message


def test_check_hang_and_buffer_detail():
    v = check(SUM, "aarch64", _asm("aarch64", "l:\n b l\n"))
    assert v.category == "hang"
    src = "reverse:\n    mov x0, #0\n    ret\n"  # returns 0, reverses nothing
    v = check(REV, "aarch64", _asm("aarch64", src))
    assert v.category == "wrong_result" and "array after the call: expected" in v.message


def test_classify_and_helpers():
    assert classify_error("x86_64: callee-saved register rbx clobbered: 1 -> 2") == "abi"
    assert classify_error("aarch64: sp not restored on return: ...") == "abi"
    assert classify_error("x: out-of-bounds write at 0x1") == "oob"
    assert classify_error("x: did not return within 5 instructions") == "hang"
    assert classify_error("x: Invalid memory read (UC_ERR_READ_UNMAPPED) at pc=0x1") == "crash"
    assert strip_fences("```qbe\nabc\n```") == "abc"
    assert parse_json_object('Sure: {"a": 1} done') == {"a": 1}


# -- runner: persistence, resume, budget ----------------------------------------------------

def test_run_cell_persists_and_resumes(tmp_path):
    run = tmp_path
    subj = FakeSubject([chunks(A64_SUM)])
    rec = run_cell(subj, SUM, "stream", "aarch64", run, budget=Budget(run, 10))
    assert rec["pass_at_1"] and rec["attempts"] == 1
    cdir = run / "cells" / "sum_to.stream.aarch64"
    for f in ("attempt1.prompt.txt", "attempt1.response.txt", "attempt1.verdict.json", "cell.json",
              "final.disasm.txt"):
        assert (cdir / f).exists(), f
    assert "ret" in (cdir / "final.disasm.txt").read_text()
    assert len((run / "results.jsonl").read_text().splitlines()) == 1
    assert (run / "calls.log").read_text().count("\n") == 1
    again = run_cell(FakeSubject([]), SUM, "stream", "aarch64", run)  # no calls: resumed
    assert again == json.loads((cdir / "cell.json").read_text())


def test_budget_stops_cell_without_marking_complete(tmp_path):
    run = tmp_path
    bad = X86_SUM.replace("add rax, rdi", "add rax, 2")
    subj = FakeSubject([bad, bad, bad, bad])
    rec = run_cell(subj, SUM, "stream", "x86_64", run, budget=Budget(run, 2))
    assert rec["attempts"] == 2 and rec["incomplete"] and subj.calls == 2
    assert not (run / "cells" / "sum_to.stream.x86_64" / "cell.json").exists()


def test_four_attempts_max(tmp_path):
    bad = X86_SUM.replace("add rax, rdi", "add rax, 2")
    subj = FakeSubject([bad] * 5)
    rec = run_cell(subj, SUM, "stream", "x86_64", tmp_path)
    assert rec["attempts"] == 4 and not rec["pass_final"] and subj.calls == 4


# -- the live subject's stream parsing and kill, against a fake `claude` executable ---------

_FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys, time
lines = sys.argv[-1].split("|")
def out(o):
    print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "model": "fake-opus", "tools": []})
out({"type": "stream_event", "event": {"type": "message_start", "message": {"model": "fake-opus"}}})
for piece in lines:
    out({"type": "stream_event", "event": {"type": "content_block_delta",
         "delta": {"type": "text_delta", "text": piece}}})
    time.sleep(0.05)
time.sleep(5)  # a killed process never gets here
out({"type": "result", "subtype": "success", "is_error": False, "usage": {"output_tokens": 9},
     "total_cost_usd": 0.001, "result": "".join(lines)})
'''


def _fake_claude(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text(_FAKE_CLAUDE)
    exe.chmod(0o755)
    return str(exe)


def test_claude_subject_streams_and_kills_early(tmp_path):
    import time

    subj = ClaudeSubject(binary=_fake_claude(tmp_path), timeout=30)
    prompt = "sum_to:\n|    xor eax, eax\n|    bogus rax\n|    ret\n"
    t0 = time.perf_counter()
    a = attempt(subj, "stream", "x86_64", SUM, prompt, raw_path=str(tmp_path / "raw.jsonl"))
    assert time.perf_counter() - t0 < 4  # killed, did not wait for the sleep(5)
    assert a.category == "syntax" and a.response.killed and a.response.model == "fake-opus"
    assert "bogus" in a.code_text and "ret" not in a.code_text
    assert (tmp_path / "raw.jsonl").read_text().count("text_delta") == 3


def test_claude_subject_timeout(tmp_path):
    subj = ClaudeSubject(binary=_fake_claude(tmp_path), timeout=1)
    r = subj.complete("a|b")
    assert r.timed_out and not r.ok and r.text == "ab"


_ECHO_CLAUDE = r'''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
prompt = args[args.index("--") + 1] if "--" in args else sys.stdin.read()
def out(m): print(json.dumps(m), flush=True)
out({"type": "system", "subtype": "init", "model": "fake-opus"})
out({"type": "stream_event", "event": {"type": "content_block_delta",
     "delta": {"type": "text_delta", "text": "%d %s" % (len(prompt), "--" in args)}}})
out({"type": "result", "subtype": "success", "is_error": False, "usage": {}, "result": ""})
'''


def test_claude_subject_sends_an_oversized_prompt_on_stdin(tmp_path):
    """A repair prompt over Linux's 128 KiB single-argument cap (E2BIG on 2026-11-10) goes on stdin,
    byte for byte; a normal prompt stays in argv."""
    from bca.liveeval.subject import ARGV_PROMPT_MAX

    exe = tmp_path / "claude"
    exe.write_text(_ECHO_CLAUDE)
    exe.chmod(0o755)
    subj = ClaudeSubject(binary=str(exe), timeout=30)
    big = "x" * 135_637
    assert len(big) > 131_072 > ARGV_PROMPT_MAX
    assert "--" not in subj.argv(big) and subj.argv("hi")[-2:] == ["--", "hi"]
    r = subj.complete(big)
    assert r.ok and r.text == f"{len(big)} False"
    assert subj.complete("hi").text == "2 True"
