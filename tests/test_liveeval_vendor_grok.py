"""The Grok Build (`grok`) subject (bca.liveeval.vendor_grok) against a fake CLI: parsing, isolation, the
request check against grok's session record, credentials, model / effort reporting and errors."""

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from bca.liveeval import runner, vendor_base, vendor_grok
from bca.liveeval.pipelines import attempt
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.tasks import TASKS
from bca.liveeval.vendor_grok import GROK_AGENT, GROK_CONFIG, GrokSubject, _rfc3339

SUM = next(t for t in TASKS if t.name == "sum_to")
SID = "01a0eb53-9859-77f1-92c7-a63e8bb68ccd"

# The fake writes grok 1.0.41's session record (sessions/<encoded cwd>/<id>/...) into GROK_HOME, the way
# the real CLI does before exiting, then prints the streaming-json lines of the spec.
FAKE = r'''#!{python}
import json, os, sys, time, urllib.parse
from pathlib import Path
spec = json.loads({spec!r})
if sys.argv[1:] == ["--version"]:
    print("grok " + spec["version"] + " (4220f3b224a6)"); sys.exit(0)
gh = Path(os.environ["GROK_HOME"])
prompt = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--single="))
files = {{str(p.relative_to(gh)): p.read_text() for p in gh.rglob("*") if p.is_file()}}
json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), "cwd_files": os.listdir("."),
           "home_files": files}}, open({seen!r}, "w"))
rec = spec["record"]
if rec is not None:
    d = gh / "sessions" / urllib.parse.quote(os.getcwd(), safe="") / "{sid}"
    d.mkdir(parents=True)
    (d / "tool_definitions.json").write_text(json.dumps(rec.get("tools", [])))
    (d / "system_prompt.txt").write_text(rec.get("system", spec["system_prompt"]))
    (d / "prompt_context.json").write_text(json.dumps(rec.get("context", {{"prompt_mode": "full",
        "prompt_body": spec["system_prompt"], "agents_md_files": [], "persona_summaries": [], "memory_enabled": False}})))
    (d / "signals.json").write_text(json.dumps(rec.get("signals", {{"toolCallCount": 0, "toolsUsed": []}})))
    (d / "summary.json").write_text(json.dumps({{"agent_name": rec.get("agent", "bca-subject")}}))
    (d / "updates.jsonl").write_text("".join(json.dumps({{"method": "session/update", "params": {{"update":
        {{"sessionUpdate": u}}}}}}) + "\n" for u in rec.get("updates", ["user_message_chunk", "agent_message_chunk"])))
    user = rec.get("user", prompt.strip())
    items = rec.get("items") or [
        {{"type": "system", "content": spec["system_prompt"]}},
        {{"type": "user", "content": [{{"type": "text", "text": ""}}]}},
        {{"type": "user", "content": [{{"type": "text", "text": user}}], "prompt_index": 0}},
        *rec.get("extra_items", []),
        {{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "x", "status": "completed"}},
        *([{{"type": "assistant", "content": rec.get("answer", "mov x0, #1\nret\n"), "model_id": rec.get("served", "grok-4.7-build"),
             "model_fingerprint": "fp_1", "reasoning_effort": rec.get("effort", "high"), **rec.get("assistant_extra", {{}})}}]
          if rec.get("assistant", True) else []),
    ]
    (d / "chat_history.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items))
    if rec.get("garbage"):
        (d / "chat_history.jsonl").write_text("{{not json\n")
if spec.get("catalog"):
    (gh / "models_cache.json").write_text(json.dumps({{"models": {{"grok-4.7": {{"info": {{"reasoning_effort": "high",
        "reasoning_efforts": [{{"id": e}} for e in ("xhigh", "high", "medium", "low")]}}}}}}}}))
if spec.get("tmp_sessions"):
    Path(spec["tmp_sessions"], "{sid}").mkdir(parents=True, exist_ok=True)
if spec.get("auth"):
    (gh / "auth.json").write_text(spec["auth"])
time.sleep(spec["sleep"])
for ev in spec["events"]:
    print(json.dumps(ev), flush=True)
sys.stderr.write(spec["stderr"])
sys.exit(spec["exit"])
'''

AVAIL = {"type": "available_commands", "tools": [], "commands": ["compact", "context"]}
USAGE = {"input_tokens": 1402, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 128,
         "reasoning_tokens": 127, "total_tokens": 1530}


def end(stop="end_turn"):
    return {"type": "end", "stopReason": stop, "sessionId": SID, "requestId": "r1", "usage": USAGE, "num_turns": 1,
            "total_cost_usd": 0.0012, "modelUsage": {"grok-4.7-build": {"inputTokens": 1402}}}


OK_EVENTS = [AVAIL, {"type": "thought", "data": "The user"}, {"type": "text", "data": "mov x0, "},
             {"type": "text", "data": "#1\nret\n"}, {"type": "usage", "usage": USAGE, "signature": "sig"}, end()]


def fake_grok(tmp_path: Path, events=OK_EVENTS, *, record=None, exit_code=0, stderr="", sleep=0.0, version="1.0.41",
              catalog=True, tmp_sessions=None, auth=None) -> Path:
    script = tmp_path / "grok"
    spec = json.dumps({"events": events, "record": None if record is False else (record or {}), "exit": exit_code,
                       "stderr": stderr, "sleep": sleep, "version": version, "catalog": catalog,
                       "tmp_sessions": str(tmp_sessions) if tmp_sessions else None, "auth": auth,
                       "system_prompt": SYSTEM_PROMPT})
    script.write_text(FAKE.format(python=sys.executable, spec=spec, seen=str(tmp_path / "seen.json"), sid=SID))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seen.json").read_text())


AUTH = {"https://auth.x.ai::client": {"key": "access-1", "refresh_token": "refresh-1", "auth_mode": "oidc",
                                       "user_id": "u1", "expires_at": "2099-09-29T09:56:05.738283037Z"}}


@pytest.fixture
def real_home(tmp_path):
    h = tmp_path / "real-grok"
    h.mkdir()
    (h / "auth.json").write_text(json.dumps(AUTH))
    (h / "config.toml").write_text('[hooks]\nPreToolUse = "evil"\n[[mcp_servers]]\nname = "secret"\n')
    (h / "sessions").mkdir()
    (h / "sessions" / "old.jsonl").write_text("PRIVATE HISTORY")
    (h / "AGENTS.md").write_text("PRIVATE RULES")
    return h


@pytest.fixture(autouse=True)
def tmp_sessions(tmp_path, monkeypatch):
    d = tmp_path / "tmp-sessions"
    monkeypatch.setattr(vendor_grok, "SESSIONS_TMP", d)
    return d


def subject(real_home, binary, model="grok-4.7", **kw):
    return GrokSubject(model, binary=str(binary), home_real=real_home, **kw)


# -- a clean call ---------------------------------------------------------------------------------


def test_answer_model_usage_and_isolation(tmp_path, real_home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    monkeypatch.setenv("GROK_CODE_XAI_API_KEY", "xai-secret-2")
    got = []
    s = subject(real_home, fake_grok(tmp_path))
    r = s.complete("write sum_to\n", got.append)
    assert r.ok, r.error
    assert r.text == "mov x0, #1\nret\n" and got == ["mov x0, ", "#1\nret\n"] and r.deltas == 2
    assert r.model == "grok-4.7-build" and r.model_source == "reported"  # the response's own model id
    assert r.usage["reasoning_effort"] == "high" and r.usage["model_fingerprint"] == "fp_1"
    assert r.usage["input_tokens"] == 1402 and r.usage["output_tokens"] == 128 and r.usage["model_usage"] == ["grok-4.7-build"]
    sw = seen(tmp_path)
    argv, env = sw["argv"], sw["env"]
    home = Path(env["GROK_HOME"])
    assert argv[:2] == ["--agent", str(home / "bca-subject.md")]
    for flag in ("--disable-web-search", "--no-subagents", "--no-memory", "--no-auto-update", "--no-leader", "--verbatim"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "streaming-json"
    assert argv[argv.index("-m") + 1] == "grok-4.7" and "--effort" not in argv and "--tools" not in argv
    assert argv[-1] == "--single=write sum_to\n"
    # private homes: GROK_HOME and HOME, neither the real one; no credential or session env leaks
    assert home != real_home and str(home).startswith("/tmp/subj-home-")
    assert env["HOME"] == str(home / "home") and env["TMPDIR"] == str(home / "tmp")
    assert all(env[k].startswith(str(home)) for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"))
    for k in ("CLAUDECODE", "XAI_API_KEY", "GROK_CODE_XAI_API_KEY"):
        assert k not in env
    assert env["GROK_MAX_RETRIES"] == "1" and env["GROK_TELEMETRY_ENABLED"] == "0" and env["GROK_WORKFLOWS"] == "0"
    assert env["GROK_SESSION_SUMMARY_MODEL"] == "bca-disabled" and env["GROK_CLAUDE_HOOKS_ENABLED"] == "0"
    # only auth.json from the real home, plus the subject's own agent profile and config
    assert set(sw["home_files"]) == {"auth.json", "bca-subject.md", "config.toml"}
    assert sw["home_files"]["bca-subject.md"] == GROK_AGENT and sw["home_files"]["config.toml"] == GROK_CONFIG
    assert json.loads(sw["home_files"]["auth.json"]) == AUTH
    assert sw["cwd_files"] == [] and sw["cwd"].startswith("/tmp/subj-")
    assert not home.exists() and not Path(sw["cwd"]).exists()
    assert (real_home / "sessions" / "old.jsonl").read_text() == "PRIVATE HISTORY"
    assert json.loads((real_home / "auth.json").read_text()) == AUTH  # nothing refreshed: untouched


def test_agent_profile_turns_every_tool_off():
    head, body = GROK_AGENT.split("\n---\n", 1)
    assert body.strip() == SYSTEM_PROMPT
    for line in ("promptMode: full", "injectDefaultTools: false", "disallowedTools: [todo_write, web_search, x_search]",
                 "discoverSkills: false", "agentsMd: false", "mcpInheritance: none", 'userMessageTemplate:\n  custom: ""'):
        assert line in head
    # the single curated tool is the one disallowed again (grok refuses an empty curated toolset)
    assert "- id: GrokBuild:todo_write" in head and GROK_CONFIG == '[skills]\nignore = ["/"]\n'


def test_effort_flag_and_catalog_default(tmp_path, real_home):
    s = subject(real_home, fake_grok(tmp_path, record={"effort": "low"}), effort="low")
    r = s.complete("p")
    assert r.ok, r.error
    argv = seen(tmp_path)["argv"]
    assert argv[argv.index("--effort") + 1] == "low" and r.usage["reasoning_effort"] == "low"
    d = s.describe()
    assert d["effort"] == "low" and d["cli_default_effort"] == "high" and d["model_efforts"] == ["xhigh", "high", "medium", "low"]
    with pytest.raises(ValueError):
        GrokSubject("grok-4.7", effort="max")
    assert GrokSubject("grok-4.7", binary=str(tmp_path / "grok")).describe()["cli_default_effort"] == "high (1.0.41 catalog)"


def test_an_effort_the_model_ignores_is_a_fatal_error(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, record={"effort": "high"}), model="grok-4.7", effort="xhigh").complete("p")
    assert not r.ok and "effort mismatch" in r.error and runner.infra_kind(r.error) == "fatal"


def test_prompt_is_passed_as_one_argument_and_trimmed_whitespace_is_accepted(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path)).complete("--help me\n\n")
    assert r.ok, r.error
    assert seen(tmp_path)["argv"][-1] == "--single=--help me\n\n"
    r = subject(real_home, fake_grok(tmp_path)).complete("/compact now")
    assert seen(tmp_path)["argv"][-1] == "--single= /compact now"  # never a slash command
    assert r.ok, r.error


def test_served_model_other_than_requested_is_noted_and_reported(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, record={"served": "grok-4.6-build"})).complete("p")
    assert r.ok and r.model == "grok-4.6-build" and r.model_source == "reported"


def test_the_tmp_sessions_directory_grok_leaves_is_removed(tmp_path, real_home, tmp_sessions):
    r = subject(real_home, fake_grok(tmp_path, tmp_sessions=tmp_sessions)).complete("p")
    assert r.ok and not tmp_sessions.exists()


def test_tmp_sessions_is_cleaned_even_when_the_stream_names_no_session(tmp_path, real_home, tmp_sessions):
    events = [{"type": "error", "message": "--effort/--reasoning-effort: unknown effort level 'xhigh'"}]
    r = subject(real_home, fake_grok(tmp_path, events=events, exit_code=1, tmp_sessions=tmp_sessions,
                                     record={"assistant": False})).complete("p")
    assert not r.ok and not tmp_sessions.exists()


def test_early_abort_does_not_kill_and_still_verifies(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, record={"tools": [{"name": "run_terminal_cmd"}]})).complete(
        "p", lambda t: False)
    assert r.error.startswith("isolation:") and "run_terminal_cmd" in r.error


def test_unaudited_version_aborts(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, version="1.0.42")).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and "not an audited version" in r.error


# -- the request check (fails closed) ---------------------------------------------------------------


@pytest.mark.parametrize("record,needle", [
    ({"tools": [{"name": "run_terminal_cmd"}]}, "tools sent: run_terminal_cmd"),
    ({"system": "You are Grok, a coding agent."}, "system prompt is not SYSTEM_PROMPT"),
    ({"context": {"prompt_mode": "extend", "prompt_body": SYSTEM_PROMPT}}, "prompt context mode"),
    ({"context": {"prompt_mode": "full", "prompt_body": SYSTEM_PROMPT, "agents_md_files": [{"file_path": "/x/AGENTS.md"}]}},
     "agents_md_files"),
    ({"context": {"prompt_mode": "full", "prompt_body": SYSTEM_PROMPT, "memory_enabled": True}}, "memory enabled"),
    ({"user": "<user_query>\np\n</user_query>"}, "not the prompt verbatim"),
    ({"extra_items": [{"type": "backend_tool_call", "kind": {"web_search": {}}}]}, "conversation item 'backend_tool_call'"),
    ({"extra_items": [{"type": "tool_result", "tool_call_id": "1", "content": "x"}]}, "conversation item 'tool_result'"),
    ({"extra_items": [{"type": "user", "content": [{"type": "text", "text": "<system-reminder>skills</system-reminder>"}],
                       "synthetic_reason": "system_reminder"}]}, "unexpected context injection"),
    ({"assistant_extra": {"tool_calls": [{"id": "1", "name": "bash", "arguments": "{}"}]}}, "assistant item with"),
    ({"signals": {"toolCallCount": 1, "toolsUsed": ["bash"]}}, "grok counted tool calls"),
    ({"updates": ["user_message_chunk", "tool_call"]}, "session update tool_call"),
    ({"agent": "grok-build"}, "ran instead of bca-subject"),
    ({"assistant": False}, "records no assistant response"),
    ({"garbage": True}, "unparseable chat_history.jsonl"),
])
def test_record_violations_fail_closed(tmp_path, real_home, record, needle):
    r = subject(real_home, fake_grok(tmp_path, record=record)).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error, r.error
    assert r.refusal == "" and runner.infra_kind(r.error) == "fatal"


def test_missing_record_on_success_fails_closed(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, record=False)).complete("p")
    assert r.error.startswith("isolation:") and "0 grok session records" in r.error


@pytest.mark.parametrize("ev,needle", [
    ({"type": "tool_call", "toolCallId": "1", "title": "bash", "toolName": "run_terminal_cmd"}, "unexpected streaming-json event"),
    ({"type": "tool_call_update", "toolCallId": "1"}, "unexpected streaming-json event"),
    ({"type": "plan", "entries": []}, "unexpected streaming-json event"),
    ({"type": "available_commands", "tools": ["run_terminal_cmd"], "commands": []}, "tools advertised"),
    ({"type": "text", "data": ["x"]}, "malformed text event"),
    ({"type": "end", "stopReason": "end_turn", "usage": "lots"}, "malformed end usage"),
])
def test_stream_violations_fail_closed(tmp_path, real_home, ev, needle):
    r = subject(real_home, fake_grok(tmp_path, events=[AVAIL, ev, *OK_EVENTS[2:]])).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error, r.error


# -- refusals and errors ------------------------------------------------------------------------------


def test_refusal_stop_is_a_scored_refusal(tmp_path, real_home):
    events = [AVAIL, {"type": "text", "data": "I can't"}, end("refusal")]
    s = subject(real_home, fake_grok(tmp_path, events=events, record={"answer": "I can't"}))
    r = s.complete("p")
    assert r.refusal.startswith("refusal: grok stop reason refusal")
    a = attempt(s, "stream", "x86_64", SUM, "p")
    assert a.category == "refusal" and not runner.is_infra_error(a)


def test_content_filter_usage_stop_is_a_refusal(tmp_path, real_home):
    events = [AVAIL, {"type": "usage", "stopReason": "content_filter"}, end("refusal")]
    r = subject(real_home, fake_grok(tmp_path, events=events, record={"assistant": False})).complete("p")
    assert r.refusal.startswith("refusal: grok stop reason content_filter")


def test_refusal_needs_a_clean_request(tmp_path, real_home):
    events = [AVAIL, end("refusal")]
    r = subject(real_home, fake_grok(tmp_path, events=events, record={"tools": [{"name": "x"}]})).complete("p")
    assert r.error.startswith("isolation:") and r.refusal == ""


# Error texts grok 1.0.41 printed against a fault-injecting proxy (see vendor_grok's docstring).
FREE = "You’ve reached your free Grok Build usage limit for now. Get SuperGrok for much higher limits, or try again later: https://grok.com/supergrok?referrer=grok-build"
E401 = ('Internal error: {\n  "message": "Auth recovery succeeded but 4 authenticated inference requests were still rejected (401); '
        'giving up after 3 retries. Turn ran 7s wall-clock.",\n  "http_status": 401\n}')
E403 = ('Internal error: {\n  "message": "API error (status 403 Forbidden): permission_denied: Grok Build requires a Grok '
        'subscription.",\n  "http_status": 403\n}')
E500 = ('Internal error: {\n  "message": "API error (status 500 Internal Server Error): Something went wrong on the server '
        '(HTTP 500).",\n  "http_status": 500\n}')
NOT_SIGNED_IN = ("Not signed in. To authenticate without a browser, run:\n  grok login --device-code\n\n"
                 "Alternatively, set the XAI_API_KEY environment variable or run `grok login` on a machine with a browser.")


@pytest.mark.parametrize("message,kind", [
    (FREE, "fatal"), (E401, "fatal"), (E403, "fatal"), (NOT_SIGNED_IN, "fatal"),
    ("Request failed (HTTP 429).", "transient"), (E500, "transient"),
])
def test_errors_are_classified(tmp_path, real_home, message, kind):
    events = [AVAIL, {"type": "error", "message": message}]
    r = subject(real_home, fake_grok(tmp_path, events=events, exit_code=1, stderr=f"Error: {message}\n",
                                     record={"assistant": False})).complete("p")
    assert not r.ok and r.error == f"grok: {message}" and r.refusal == ""
    assert runner.infra_kind(r.error) == kind


def test_failed_call_without_record_is_an_error_not_an_isolation_failure(tmp_path, real_home):
    events = [{"type": "error", "message": "Request failed (HTTP 429)."}]
    r = subject(real_home, fake_grok(tmp_path, events=events, exit_code=1, record=False)).complete("p")
    assert r.error == "grok: Request failed (HTTP 429)." and runner.infra_kind(r.error) == "transient"


def test_failed_call_with_a_tool_in_its_record_is_still_fatal(tmp_path, real_home):
    events = [{"type": "error", "message": "Request failed (HTTP 429)."}]
    r = subject(real_home, fake_grok(tmp_path, events=events, exit_code=1,
                                     record={"tools": [{"name": "bash"}], "assistant": False})).complete("p")
    assert r.error.startswith("isolation:") and "tools sent: bash" in r.error


def test_exit_without_error_line_reports_stderr(tmp_path, real_home):
    r = subject(real_home, fake_grok(tmp_path, events=[], exit_code=2, stderr="Error: boom\n", record=False)).complete("p")
    assert r.error == "exit status 2: Error: boom"


def test_missing_credentials_is_fatal(tmp_path, real_home):
    (real_home / "auth.json").unlink()
    r = subject(real_home, fake_grok(tmp_path)).complete("p")
    assert r.error.startswith("isolation:") and "log in first" in r.error and runner.infra_kind(r.error) == "fatal"


# -- credentials --------------------------------------------------------------------------------------


def test_cred_expiry_is_the_earliest_scope_minus_the_refresh_margin():
    s = GrokSubject("grok-4.7")
    data = json.dumps({"a": {"expires_at": "2026-09-29T10:00:00Z"}, "b": {"expires_at": "2026-09-29T09:56:05.738283037Z"}})
    assert s.cred_expiry("auth.json", data.encode()) == _rfc3339("2026-09-29T09:56:05.738283Z") - 300
    assert _rfc3339("2026-09-29T09:56:05Z") == 1790675765.0
    assert s.cred_expiry("auth.json", b"not json") is None
    assert s.cred_expiry("auth.json", json.dumps({"a": {"expires_at": 5}}).encode()) is None


def test_refreshed_token_is_copied_back(tmp_path, real_home):
    new = json.loads(json.dumps(AUTH))
    new["https://auth.x.ai::client"].update(key="access-2", refresh_token="refresh-2", expires_at="2099-09-29T16:00:00Z")
    r = subject(real_home, fake_grok(tmp_path, auth=json.dumps(new))).complete("p")
    assert r.ok and json.loads((real_home / "auth.json").read_text()) == new
    assert oct((real_home / "auth.json").stat().st_mode & 0o777) == "0o600"


def test_blanked_token_is_not_copied_back(tmp_path, real_home):
    bad = json.loads(json.dumps(AUTH))
    bad["https://auth.x.ai::client"]["refresh_token"] = ""
    subject(real_home, fake_grok(tmp_path, auth=json.dumps(bad))).complete("p")
    assert json.loads((real_home / "auth.json").read_text()) == AUTH


def test_token_near_expiry_makes_the_call_the_refresh_leader(tmp_path, real_home, monkeypatch):
    near = json.loads(json.dumps(AUTH))
    near["https://auth.x.ai::client"]["expires_at"] = "2000-01-01T00:00:00Z"
    (real_home / "auth.json").write_text(json.dumps(near))
    s = subject(real_home, fake_grok(tmp_path))
    exp = s.cred_expiry("auth.json", (real_home / "auth.json").read_bytes())
    assert exp is not None and exp < time.time()
    leaders = []
    orig = vendor_base.CredHome.__init__

    def spy(self, *a, **kw):
        orig(self, *a, **kw)
        leaders.append(self.leader)

    monkeypatch.setattr(vendor_base.CredHome, "__init__", spy)
    assert s.complete("p").ok and leaders == [True]


def test_default_home_follows_grok_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "gh"))
    assert GrokSubject("grok-4.7").home_real == tmp_path / "gh"
    monkeypatch.delenv("GROK_HOME")
    assert GrokSubject("grok-4.7").home_real == Path(os.path.expanduser("~/.grok"))
