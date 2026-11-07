"""The Antigravity (`agy`) subject (bca.liveeval.vendor_agy) against a fake CLI: parsing, isolation,
the request check against agy's recorded model request, errors."""

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from bca.liveeval import runner
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_agy import (
    AGY_AGENT,
    AGY_SYSTEM,
    AgySubject,
    agy_user_re,
    pb_fields,
    read_requests,
)

# The fake writes agy's conversation database: gen_metadata rows whose field 1 is the request record
# (1 system prompt, repeated 2 {2 role, 3 text}, repeated 8 {1 tool name}, 19 served model), the
# layout agy 1.2.12 writes (see vendor_agy).
FAKE = r'''#!{python}
import json, os, sqlite3, sys, time
from pathlib import Path
spec = json.loads({spec!r})
if sys.argv[1:] == ["--version"]:
    print("1.2.12"); sys.exit(0)

def varint(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        if n: out += bytes([b | 0x80])
        else: return out + bytes([b])

def ld(f, payload):
    if isinstance(payload, str): payload = payload.encode()
    return varint(f << 3 | 2) + varint(len(payload)) + payload

def vi(f, n):
    return varint(f << 3) + varint(n)

home = Path(os.environ["HOME"])
prompt = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--print="))
files = {{str(p.relative_to(home)): p.read_text() for p in home.rglob("*") if p.is_file()}}
json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), "cwd_files": os.listdir("."),
           "home_files": files}}, open({seen!r}, "w"))
req = spec["request"]
if req is not None:
    user = req.get("user") or ("<USER_REQUEST>\n" + prompt.strip() + "\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n"
            "The current local time is: 2026-09-28T19:00:54-07:00.\n</ADDITIONAL_METADATA>\n<USER_SETTINGS_CHANGE>\n"
            "The user changed setting `Model Selection` from None to Gemini 3.8 Flash (High). No need to comment.\n"
            "</USER_SETTINGS_CHANGE>")
    body = ld(1, req.get("system", "")) + ld(2, vi(2, 1) + ld(3, user)) + ld(2, vi(2, 2) + ld(3, "answer"))
    for m in req.get("extra_messages", []):
        body += ld(2, vi(2, m[0]) + ld(3, m[1]))
    for t in req.get("tools", []):
        body += ld(8, ld(1, t) + ld(2, "desc") + ld(3, "{{}}"))
    if req.get("model"):
        body += ld(19, req["model"])
    row = ld(3, ld(28, "requested")) + ld(1, body)
    if req.get("garbage"):
        row = b"\xff\xff\xff"
    d = home / ".gemini" / "antigravity-cli" / "conversations"
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "c1.db")
    con.execute("CREATE TABLE gen_metadata (idx integer, data blob, size integer)")
    con.execute("INSERT INTO gen_metadata VALUES (0, ?, ?)", (row, len(row)))
    con.commit(); con.close()
time.sleep(spec["sleep"])
for ev in spec["events"]:
    print(json.dumps(ev), flush=True)
sys.stderr.write(spec["stderr"])
sys.exit(spec["exit"])
'''

OK_REQ = {"system": AGY_SYSTEM, "tools": ["manage_task"], "model": "gemini-3.8-flash"}


def init(agent="bca-subject"):
    return {"event": "init", "conversation_id": "c1",
            "init": {"cwd": "/tmp/x", "agent": agent, "tools": ["run_command", "view_file", "manage_task"],
                     "permission_mode": "request-review"}}


def step(st, text=None, state="DONE", usage=None):
    su = {"conversation_id": "c1", "step_index": 1, "state": state, "step_type": st}
    if text is not None:
        su["text_delta"] = text
    if usage:
        su["usage"] = usage
    return {"event": "step_update", "step_update": su}


USAGE = {"input_tokens": 585, "output_tokens": 96, "thinking_tokens": 95, "cache_read_tokens": 0, "total_tokens": 681}


def result(response, status="SUCCESS", error=None):
    r = {"conversation_id": "c1", "status": status, "response": response, "num_turns": 1, "usage": USAGE}
    if error:
        r["error"] = error
    return {"event": "result", "result": r}


OK_EVENTS = [init(), step("user_input"), step("agent_response", "mov x0, ", "ACTIVE"),
             step("agent_response", "#1\nret\n", usage=USAGE), result("mov x0, #1\nret\n")]


def fake_agy(tmp_path: Path, events=OK_EVENTS, *, request=OK_REQ, exit_code=0, stderr="", sleep=0.0) -> Path:
    script = tmp_path / "agy"
    spec = json.dumps({"events": events, "request": request, "exit": exit_code, "stderr": stderr, "sleep": sleep})
    script.write_text(FAKE.format(python=sys.executable, spec=spec, seen=str(tmp_path / "seen.json")))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seen.json").read_text())


@pytest.fixture
def real_home(tmp_path):
    h = tmp_path / "real-gemini"
    h.mkdir()
    (h / "settings.json").write_text('{"trustedWorkspaces": ["/secret"]}')
    (h / "history.jsonl").write_text("PRIVATE HISTORY")
    return h


def subject(tmp_path, real_home, model="gemini-3.8-flash-high", **kw):
    return AgySubject(model, binary=str(kw.pop("binary")), home_real=real_home, **kw)


def test_answer_usage_model_and_isolation(tmp_path, real_home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "leak")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/leak.json")
    s = subject(tmp_path, real_home, binary=fake_agy(tmp_path))
    got = []
    r = s.complete("-- write it\n", on_text=lambda t: got.append(t) or True, raw_path=str(tmp_path / "raw.jsonl"))
    assert r.ok and r.text == "mov x0, #1\nret\n" and "".join(got) == r.text and r.deltas == 2
    # the record's model field is request-side: requested, never reported (vendor_agy docstring)
    assert r.usage == USAGE and r.model == "gemini-3.8-flash-high" and r.model_source == "requested"
    sn = seen(tmp_path)
    argv = sn["argv"]
    assert argv[:2] == ["--output-format", "stream-json"] and argv[-1] == "--print=-- write it\n"
    assert argv[argv.index("--agent") + 1] == "bca-subject" and "--disable-slash-commands" in argv
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high" and "--effort" not in argv
    env = sn["env"]
    home = Path(env["HOME"])
    assert home != real_home and not home.exists() and env["TMPDIR"] == str(home / "tmp")
    assert all(env[k].startswith(str(home)) for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"))
    for k in ("CLAUDECODE", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        assert k not in env
    # the private home: our agent, our settings, the updater stamp -- nothing from the real home
    hf = sn["home_files"]
    assert set(hf) == {".gemini/config/agents/bca-subject.md", ".gemini/antigravity-cli/settings.json",
                       ".gemini/antigravity-cli/last_check.timestamp"}
    agent = hf[".gemini/config/agents/bca-subject.md"]
    assert agent == AGY_AGENT and agent.endswith("---\n" + SYSTEM_PROMPT + "\n")
    for line in ("excludeDefaultComponents: true", "inheritCustomizations: false", "inheritMcp: false", "tools: []"):
        assert line in agent
    assert json.loads(hf[".gemini/antigravity-cli/settings.json"]) == {"useG1Credits": False}
    assert sn["cwd_files"] == [] and not Path(sn["cwd"]).exists()
    assert sorted(p.name for p in real_home.iterdir()) == ["history.jsonl", "settings.json"]  # untouched: not even a lock file
    assert "bca_stderr" in (tmp_path / "raw.jsonl").read_text().splitlines()[-1]


def test_effort_flag_and_describe(tmp_path, real_home):
    s = subject(tmp_path, real_home, model="gemini-3.8-flash", effort="low", binary=fake_agy(tmp_path))
    r = s.complete("p")
    argv = seen(tmp_path)["argv"]
    assert r.ok and argv[argv.index("--effort") + 1] == "low" and r.model == "gemini-3.8-flash"
    with pytest.raises(ValueError, match="agy: unknown effort"):
        AgySubject("m", effort="xhigh")
    d = AgySubject("gemini-3.1-pro-low").describe()
    assert d["vendor"] == "agy" and d["model_effort_variant"] == "low" and d["effort"] == "cli-default"


def test_other_model_in_the_request_is_recorded(tmp_path, real_home):
    s = subject(tmp_path, real_home, binary=fake_agy(tmp_path, request={**OK_REQ, "model": "gemini-3.6-flash"}))
    r = s.complete("p")
    assert r.ok and r.model == "gemini-3.6-flash" and r.model_source == "requested"


def test_no_served_model_is_requested_only(tmp_path, real_home):
    s = subject(tmp_path, real_home, binary=fake_agy(tmp_path, request={**OK_REQ, "model": ""}))
    r = s.complete("p")
    assert r.ok and r.model == "gemini-3.8-flash-high" and r.model_source == "requested"


def test_result_tail_missed_by_deltas_is_delivered(tmp_path, real_home):
    events = [init(), step("agent_response", "mov x0, ", "ACTIVE"), result("mov x0, #1\nret\n")]
    got = []
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events)).complete("p", on_text=lambda t: got.append(t) or True)
    assert r.ok and r.text == "mov x0, #1\nret\n" and got == ["mov x0, ", "#1\nret\n"]


@pytest.mark.parametrize("request_, needle", [
    ({**OK_REQ, "tools": ["manage_task", "run_command", "view_file"]}, "request declares tools run_command,view_file"),
    ({**OK_REQ, "system": "<identity>\nYou are Antigravity"}, "system prompt is not SYSTEM_PROMPT"),
    ({**OK_REQ, "user": "<USER_REQUEST>\np\n</USER_REQUEST>\n<user_rules>\nAGENTS.md says hi\n</user_rules>"},
     "user turn is not the harness prompt"),
    ({**OK_REQ, "extra_messages": [[3, "tool output"]]}, "chat message with role 3"),
    ({**OK_REQ, "garbage": True}, "cannot verify the request"),
    (None, "cannot verify the request: 0 conversation databases"),
])
def test_request_check_fails_closed(tmp_path, real_home, request_, needle):
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, request=request_)).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error
    assert runner.infra_kind(r.error) == "fatal"


@pytest.mark.parametrize("bad, needle", [
    (step("run_command", state="ACTIVE"), "step 'run_command'"),
    (step("tool_call"), "step 'tool_call'"),
    ({"event": "tool_call", "tool_call": {"name": "view_file"}}, "unrecognised event 'tool_call'"),
    (init(agent="default"), "agent 'default' instead of bca-subject"),
])
def test_stream_fails_closed(tmp_path, real_home, bad, needle):
    events = [bad, *OK_EVENTS[1:]]
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events)).complete("p")
    assert not r.ok and needle in r.error and runner.infra_kind(r.error) == "fatal"
    assert r.text == ""  # nothing after the violation was read


@pytest.mark.parametrize("short, kind", [
    ("You have exhausted your quota on this model. Your quota will reset after 3h.", "fatal"),
    # a per-minute / capacity 429 is retried (runner._FATAL_RE)
    ("You have exhausted your capacity on this model. Your quota will reset after 7s.", "transient"),
])
def test_agy_error_line_is_the_error(tmp_path, real_home, short, kind):
    # the AGY_ERROR payload below is shaped after the CLI's changelog (status, code, retryability), not captured
    err = f'AGY_ERROR: {{"short_error":"{short}","status":"RESOURCE_EXHAUSTED","code":429,"retryable":false}}\n'
    events = [init(), step("user_input"), result("", status="ERROR", error="model API error")]
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events, request=None, exit_code=3, stderr=err)).complete("p")
    assert not r.ok and r.error.startswith("exit status 3: AGY_ERROR {") and "RESOURCE_EXHAUSTED" in r.error
    assert "(agy ERROR: model API error)" in r.error and runner.infra_kind(r.error) == kind
    assert r.refusal == ""


def test_transient_agy_error_is_retried(tmp_path, real_home):
    err = 'AGY_ERROR: {"short_error":"The service is currently unavailable.","status":"UNAVAILABLE","code":503}\n'
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, [init()], request=None, exit_code=3, stderr=err)).complete("p")
    assert not r.ok and "UNAVAILABLE" in r.error and runner.infra_kind(r.error) == "transient"


def test_invalid_model_result_is_fatal(tmp_path, real_home):
    # captured from agy 1.2.12 (`--model no-such-model-9`): exit 1, a result event with status ERROR
    events = [result("", status="ERROR", error='invalid model selection (--model "no-such-model-9" --effort ""): '
                     "model no-such-model-9 is not recognized as a known model or custom model in settings")]
    r = subject(tmp_path, real_home, model="no-such-model-9",
                binary=fake_agy(tmp_path, events, request=None, exit_code=1, stderr="error: invalid model selection")
                ).complete("p")
    assert not r.ok and "invalid model selection" in r.error and runner.infra_kind(r.error) == "fatal"


def test_nonzero_exit_without_events(tmp_path, real_home):
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, [], request=None, exit_code=2,
                                                     stderr="boom\n")).complete("p")
    assert not r.ok and r.error == "exit status 2: boom"


def test_early_abort_lets_agy_finish_and_verifies(tmp_path, real_home):
    # agy records the request only at the end of the turn: an early abort must not kill it
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path)).complete("p", on_text=lambda t: False)
    assert r.killed and r.ok and not r.error  # killed = the scorer stopped reading; the process finished
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, request=None)).complete("p", on_text=lambda t: False)
    assert not r.ok and r.error.startswith("isolation: cannot verify the request")
    bad = {**OK_REQ, "tools": ["manage_task", "run_command"]}
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, request=bad)).complete("p", on_text=lambda t: False)
    assert not r.ok and "request declares tools run_command" in r.error


@pytest.mark.parametrize("reason", ["print_timeout", "interrupted", "error"])
def test_finished_reason_is_an_unscored_error(tmp_path, real_home, reason):
    # agy >= 1.4.11: a result that names finished_reason ended early and may be partial
    res = result("mov x0, #1\nret\n")
    res["result"]["finished_reason"] = reason
    events = [init(), step("agent_response", "mov x0, #1\nret\n"), res]
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events)).complete("p")
    assert not r.ok and f"finished_reason {reason}" in r.error
    assert runner.infra_kind(r.error) == "transient"


def test_out_of_credits_is_fatal():
    # agy 1.5.3 wording once the plan quota is spent and AI credits cannot cover the call
    assert runner.infra_kind("exit status 1: AGY_ERROR Your AI credits balance is too low to continue.") == "fatal"


def test_audited_version_only(tmp_path, real_home):
    script = fake_agy(tmp_path)
    script.write_text(script.read_text().replace('print("1.2.12")', 'print("1.3.0")'))
    r = subject(tmp_path, real_home, binary=script).complete("p")
    assert not r.ok and r.error.startswith("isolation: agy CLI 1.3.0 is not an audited version")


@pytest.mark.parametrize("bad, needle", [
    ({"event": "step_update", "step_update": "agent_response"}, "malformed step_update event"),
    ({"event": "result", "result": None}, "malformed result event"),
    ({"event": "init", "init": []}, "malformed init event"),
    (step("agent_response", text=["x"]), "malformed text_delta"),
    (step("agent_response", usage="lots"), "malformed step usage"),
    ({"event": "init", "init": {"agent": "bca-subject", "tools": "all"}}, "malformed init tools"),
])
def test_malformed_events_fail_closed(tmp_path, real_home, bad, needle):
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, [bad, *OK_EVENTS[1:]])).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error and r.text == ""


# -- safety blocks are scored refusals ----------------------------------------------------------------


@pytest.mark.parametrize("events, stderr, needle", [
    ([init(), step("user_input"), result("", status="ERROR", error="model stopped: STOP_REASON_CONTENT_FILTER")],
     "", "result: model stopped: STOP_REASON_CONTENT_FILTER"),
    ([init(), step("agent_response", "mov x0, ", "ACTIVE"),
      {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "ERROR",
                                               "stop_reason": "FINISH_REASON_RECITATION"}},
      result("", status="ERROR", error="model API error")], "", "step agent_response: FINISH_REASON_RECITATION"),
    ([init(), step("user_input"), result("", status="ERROR", error="model API error")],
     'AGY_ERROR: {"short_error":"The response was blocked by the safety filter.","status":"PROHIBITED_CONTENT"}\n',
     "AGY_ERROR: "),
])
def test_safety_block_is_a_refusal(tmp_path, real_home, events, stderr, needle):
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events, exit_code=1, stderr=stderr)).complete("p")
    assert r.refusal.startswith("safety: " + needle) and not r.ok
    assert runner.infra_kind(r.error) == "transient"  # scored by the pipelines, never a run abort


def test_refusal_needs_a_clean_request(tmp_path, real_home):
    events = [init(), step("user_input"), result("", status="ERROR", error="STOP_REASON_CONTENT_FILTER")]
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events, request=None, exit_code=1)).complete("p")
    assert r.error.startswith("isolation: cannot verify the request") and r.refusal == ""


def test_the_answer_text_never_makes_a_refusal(tmp_path, real_home):
    events = [init(), step("agent_response", "; SAFETY: content filter check\nret\n"),
              result("; SAFETY: content filter check\nret\n")]
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, events)).complete("p")
    assert r.ok and r.refusal == ""


def test_timeout(tmp_path, real_home):
    r = subject(tmp_path, real_home, binary=fake_agy(tmp_path, sleep=5)).complete("p", timeout=0.5)
    assert r.timed_out and not r.ok and r.error == "timeout"


def test_home_setup_failure_is_isolation_error(tmp_path, real_home, monkeypatch):
    s = subject(tmp_path, real_home, binary=fake_agy(tmp_path))
    monkeypatch.setattr(Path, "touch", lambda self, *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    r = s.complete("p")
    assert not r.ok and r.error.startswith("isolation: cannot set up the agy home")
    assert not (tmp_path / "seen.json").exists()


def test_user_turn_pattern():
    rx = agy_user_re("  add x0, x0, x1\n")
    wrapped = ("<USER_REQUEST>\nadd x0, x0, x1\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe current local time is: "
               "2026-09-28T19:00:54-07:00.\n</ADDITIONAL_METADATA>")
    assert rx.fullmatch(wrapped)
    assert not rx.fullmatch(wrapped + "\n<memories>\nx\n</memories>")
    assert not agy_user_re("other").fullmatch(wrapped)


def test_pb_fields_rejects_garbage_and_reads_requests(tmp_path):
    assert pb_fields(b"\xff\xff") is None and pb_fields(b"") == []
    db = tmp_path / "c.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE gen_metadata (idx integer, data blob, size integer)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="no model request"):
        read_requests(db)
    assert os.path.exists(db)



def test_error_message_step_is_a_transient_failure_not_isolation(tmp_path, real_home):
    """agy reports a failed model request as a bare `error_message` step (seen live): retried, never scored."""
    from bca.liveeval.vendor_base import Parsed
    from bca.liveeval import runner

    ev = {"event": "step_update", "step_update": {"conversation_id": "c", "step_index": 1, "state": "DONE",
                                                 "step_type": "error_message", "duration_seconds": 0}}
    p = Parsed()
    s = subject(tmp_path, real_home, binary=fake_agy(tmp_path))
    assert s.on_event(ev, p) is None and not p.violations
    assert p.error.startswith("agy error_message step") and runner.infra_kind(p.error) == "transient"
