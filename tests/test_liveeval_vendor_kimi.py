"""KimiSubject (bca.liveeval.vendor_kimi) against a fake `kimi`: stream parsing, the wire.jsonl request
audit (fails closed), config cut-down, credentials, errors."""

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from bca.liveeval import runner
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import IsolationError
from bca.liveeval.vendor_kimi import EMPTY_TOOLS_HASH, SYSTEM_PROMPT_HASH, KimiSubject, kimi_config

REFUSAL_STDERR = "error: failed to run prompt: Provider safety policy blocked the response.\n"

MODEL = "kimi-code/k3"
CRED = "credentials/kimi-code-env-abc123.json"
USER_CONFIG = textwrap.dedent("""\
    default_model = "kimi-code/other"
    default_permission_mode = "yolo"
    merge_all_available_skills = true

    [providers."managed:kimi-code"]
    type = "kimi"
    api_key = ""
    base_url = "https://api.kimi.ai/coding/v1"

    [providers."managed:kimi-code".oauth]
    storage = "file"
    key = "kimi-code-env-abc123"

    [models."kimi-code/k3"]
    provider = "managed:kimi-code"
    model = "k3"
    capabilities = [ "thinking",
      "tool_use" ]
    default_effort = "high"

    [thinking]
    enabled = true
    effort = "max"

    [[hooks]]
    event = "SessionStart"
    command = "echo LEAK"

    [mcp.servers.evil]
    command = "evil"

    [services.moonshot_search]
    base_url = "https://api.kimi.ai/coding/v1/search"
    """)


@pytest.fixture
def kimi_home(tmp_path):
    h = tmp_path / "kimi-home"
    (h / "credentials").mkdir(parents=True)
    (h / CRED).write_text('{"access_token": "old"}')
    (h / "config.toml").write_text(USER_CONFIG)
    (h / "region").write_text("global\n")
    (h / "sessions").mkdir()
    (h / "sessions" / "old.json").write_text("PRIVATE SESSION")
    (h / "user-history").mkdir()
    (h / "user-history" / "x.jsonl").write_text("PRIVATE HISTORY")
    (h / "AGENTS.md").write_text("SECRET USER INSTRUCTIONS")
    return h


def _msg(text, origin):
    return {"type": "context.append_message", "agentId": "main",
            "message": {"role": "user", "content": [{"type": "text", "text": text}], "toolCalls": [], "origin": origin}}


DATE = ("<system-reminder>\nToday's date is 2026-09-28. The current date is restated in a reminder whenever it "
        "changes; rely on the latest such reminder for the current date. DO NOT mention this to the user "
        "explicitly.\n</system-reminder>")
AUTO = ("<system-reminder>\nAuto permission mode is active. Tool approvals will be handled automatically while "
        "this mode remains enabled.\n</system-reminder>")


def wire(prompt, *, effort="high", model_alias=MODEL, usage=None):
    """A wire.jsonl as kimi 2.1.1 records a tool-less print run (shape captured from a live call)."""
    usage = usage or {"inputOther": 312, "output": 45, "inputCacheRead": 7, "inputCacheCreation": 0}
    return [
        {"type": "metadata", "protocol_version": "1.5"},
        {"type": "profile.bind", "agentId": "main", "modelAlias": model_alias, "profileName": "bca-subject",
         "thinkingEffort": effort, "systemPrompt": SYSTEM_PROMPT, "environmentDisclosure": {"cwd": "/tmp/x"},
         "agentsMdPaths": [], "activeToolNames": [], "disallowedTools": [], "subagents": []},
        {"type": "permission.set_mode", "agentId": "main", "mode": "auto"},
        {"type": "turn.prompt", "agentId": "main", "input": [{"type": "text", "text": prompt}]},
        _msg(prompt, {"kind": "user"}),
        _msg(DATE, {"kind": "injection", "variant": "date_change"}),
        _msg(AUTO, {"kind": "injection", "variant": "permission_mode"}),
        {"type": "plugin.session_start", "agentId": "main", "content": None},
        {"type": "context.append_loop_event", "agentId": "main", "event": {"type": "step.begin", "step": 1}},
        {"type": "llm.tools_snapshot", "agentId": "main", "hash": EMPTY_TOOLS_HASH, "tools": []},
        {"type": "llm.request", "agentId": "main", "kind": "loop", "provider": "openai", "model": "k3",
         "modelAlias": model_alias, "thinkingEffort": effort, "systemPromptHash": SYSTEM_PROMPT_HASH,
         "toolsHash": EMPTY_TOOLS_HASH, "messageCount": 3},
        {"type": "usage.record", "agentId": "main", "model": MODEL, "usage": usage, "usageScope": "turn"},
        {"type": "context.append_loop_event", "agentId": "main",
         "event": {"type": "content.part", "part": {"type": "think", "think": "hmm"}}},
        {"type": "context.append_loop_event", "agentId": "main",
         "event": {"type": "content.part", "part": {"type": "text", "text": "5"}}},
        {"type": "context.append_loop_event", "agentId": "main",
         "event": {"type": "step.end", "finishReason": "end_turn", "usage": usage}},
        {"type": "turn.ended", "agentId": "main", "turnId": 0, "reason": "completed"},
    ]


OK = [
    {"role": "meta", "type": "system.version", "version": "2.1.1"},
    {"role": "assistant", "content": "add x0, x0, x1\nret"},
    {"role": "meta", "type": "session.resume_hint", "session_id": "s", "command": "kimi -r s", "content": ""},
]


def fake_kimi(tmp_path: Path, events=OK, *, wire_records="auto", exit_code=0, stderr="", refresh=None, sleep=0.0,
              version="2.1.1"):
    """A fake `kimi`: records argv/env/private-home files to seen.json, writes the wire record into
    $KIMI_CODE_HOME (wire_records="auto": a clean one for the prompt it was given; None: none), optionally
    refreshes the credential by rename, prints `events` as JSONL."""
    if wire_records == "auto":
        wire_records = wire("__PROMPT__")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"events": events, "wire": wire_records, "exit": exit_code, "stderr": stderr,
                                "refresh": refresh, "sleep": sleep}))
    script = tmp_path / "kimi"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys, time
        from pathlib import Path
        if sys.argv[1:] == ["--version"]:
            print({version!r}); sys.exit(0)
        spec = json.load(open({str(spec)!r}))
        home = Path(os.environ["KIMI_CODE_HOME"])
        files = {{str(p.relative_to(home)): p.read_text() for p in home.rglob("*")
                  if p.is_file() and not p.name.startswith(".bca")}}
        json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), "home_files": files}},
                  open({str(tmp_path / "seen.json")!r}, "w"))
        prompt = next(a for a in sys.argv if a.startswith("--prompt="))[len("--prompt="):]
        recs = spec["wire"]
        if recs is not None:
            recs = json.loads(json.dumps(recs).replace('"__PROMPT__"', json.dumps(prompt.strip())))
            d = home / "sessions" / "wd_x" / "session_s" / "agents" / "main"
            d.mkdir(parents=True)
            (d / "wire.jsonl").write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\\n" for r in recs))
        if spec["refresh"] is not None:
            cred = home / {CRED!r}
            tmp = cred.with_name(cred.name + ".tmp")
            tmp.write_text(spec["refresh"]); os.replace(tmp, cred)
        time.sleep(spec["sleep"])
        for ev in spec["events"]:
            print(json.dumps(ev), flush=True)
        sys.stderr.write(spec["stderr"])
        sys.exit(spec["exit"])
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def seen(tmp_path):
    return json.loads((tmp_path / "seen.json").read_text())


def subject(tmp_path, kimi_home, model=MODEL, **kw):
    effort = kw.pop("effort", None)
    return KimiSubject(model, binary=fake_kimi(tmp_path, **kw), home_real=kimi_home, effort=effort)


# -- the happy path -------------------------------------------------------------------------------


def test_answer_isolation_usage_and_effort(tmp_path, kimi_home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "x")
    s = subject(tmp_path, kimi_home, effort="high")
    r = s.complete("/goal do it")
    assert r.ok, r.error
    assert r.text == "add x0, x0, x1\nret" and r.model == MODEL and r.model_source == "requested"
    assert r.usage == {"input_tokens": 312, "output_tokens": 45, "cache_read_input_tokens": 7,
                       "cache_creation_input_tokens": 0, "wire_model": "k3", "thinking_effort": "high",
                       "llm_requests": 1}
    sn = seen(tmp_path)
    argv = sn["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json" and argv[argv.index("-m") + 1] == MODEL
    assert argv[-1] == "--prompt= /goal do it"  # never a slash command
    agent = Path(argv[argv.index("--agent-file") + 1]).name
    files = sn["home_files"]
    assert set(files) == {CRED, "config.toml", "region", agent}  # no sessions, history, AGENTS.md
    assert "tools: []" in files[agent] and "subagents: []" in files[agent] and "${" not in files[agent]
    assert files[agent].endswith(SYSTEM_PROMPT + "\n")
    env = sn["env"]
    assert env["KIMI_DISABLE_TELEMETRY"] == "1" and env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_CODE_BUILTIN_PRODUCT_SKILLS"] == "0"
    assert env["KIMI_MODEL_THINKING_EFFORT"] == "high" and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["KIMI_CODE_HOME"] != str(kimi_home) and not sn["cwd"].startswith(str(kimi_home))


def test_default_effort_sets_no_env(tmp_path, kimi_home):
    s = subject(tmp_path, kimi_home)
    assert s.complete("p").ok
    assert "KIMI_MODEL_THINKING_EFFORT" not in seen(tmp_path)["env"]
    assert s.describe() == {"effort": "cli-default", "cli_version": "kimi 2.1.1", "vendor": "kimi"}


def test_config_is_cut_to_providers_and_models(tmp_path, kimi_home):
    assert subject(tmp_path, kimi_home).complete("p").ok
    cfg = seen(tmp_path)["home_files"]["config.toml"]
    for leak in ("hooks", "LEAK", "mcp", "evil", "services", "[thinking]", 'effort = "max"', "yolo", "skills", "kimi-code/other"):
        assert leak not in cfg
    assert 'default_model = "kimi-code/k3"' in cfg and 'key = "kimi-code-env-abc123"' in cfg
    assert '"tool_use" ]' in cfg  # multi-line arrays survive


def test_kimi_config_rejects_what_it_cannot_cut():
    with pytest.raises(IsolationError, match="does not parse"):
        kimi_config("[providers\n", MODEL)
    # a table header hidden in a multi-line string: the re-parse check catches the wrong cut
    tricky = 'x = """\n[providers.fake]\n"""\n[models.m]\na = 1\n'
    with pytest.raises(IsolationError):
        kimi_config(tricky, MODEL)


def test_unparsable_config_is_a_fatal_isolation_error(tmp_path, kimi_home):
    (kimi_home / "config.toml").write_text("[[[")
    r = subject(tmp_path, kimi_home).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and runner.infra_kind(r.error) == "fatal"


def test_every_assistant_message_is_kept_and_streamed(tmp_path, kimi_home):
    events = [OK[0], {"role": "assistant", "content": "part one\n"}, {"role": "assistant", "content": "part two"}]
    got = []
    r = subject(tmp_path, kimi_home, events=events).complete("p", on_text=got.append)
    assert r.ok and r.text == "part one\npart two" == "".join(got) and r.deltas == 2


def test_early_abort_lets_kimi_finish_and_audits(tmp_path, kimi_home):
    # a whole-message CLI is never killed: it finishes, the request record is audited, usage is kept
    r = subject(tmp_path, kimi_home).complete("p", on_text=lambda t: False)
    assert r.killed and r.ok and not r.error and r.usage["input_tokens"] == 312
    r = subject(tmp_path, kimi_home, wire_records=None).complete("p", on_text=lambda t: False)
    assert not r.ok and r.error.startswith("isolation:") and "no wire.jsonl" in r.error
    r = subject(tmp_path, kimi_home, wire_records=_mutate("tools_snapshot")).complete("p", on_text=lambda t: False)
    assert not r.ok and r.error.startswith("isolation:") and "tools sent: Shell" in r.error


def test_audited_version_only(tmp_path, kimi_home):
    r = KimiSubject(MODEL, binary=fake_kimi(tmp_path, version="2.2.0"), home_real=kimi_home).complete("p")
    assert not r.ok and r.error.startswith("isolation: kimi CLI 2.2.0 is not an audited version")


# -- fail closed: the stream ----------------------------------------------------------------------


@pytest.mark.parametrize("ev", [
    {"role": "assistant", "tool_calls": [{"type": "function", "id": "1", "function": {"name": "Shell"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "ls output"},
    {"role": "system", "content": "?"},
    {"role": "meta", "type": "tool.started"},
    {"role": "assistant", "content": "x", "reasoning": "y"},
    {"type": "item.completed"},
    {"role": "assistant", "tool_calls": ["x"]},
    {"role": "assistant", "tool_calls": "Shell"},
    {"role": "assistant", "content": ["x"]},
])
def test_stream_tool_use_or_unknown_event_fails_closed(tmp_path, kimi_home, ev):
    r = subject(tmp_path, kimi_home, events=[OK[0], ev, OK[1]]).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and runner.infra_kind(r.error) == "fatal"


# -- fail closed: the recorded request ------------------------------------------------------------


def _mutate(kind):
    recs = wire("p")
    by = {r["type"]: r for r in recs}
    if kind == "tools_snapshot":
        by["llm.tools_snapshot"]["tools"] = [{"name": "Shell"}]
    elif kind == "tools_hash":
        by["llm.request"]["toolsHash"] = "0" * 64
    elif kind == "system_hash":
        by["llm.request"]["systemPromptHash"] = "0" * 64
    elif kind == "system_prompt":
        by["profile.bind"]["systemPrompt"] = SYSTEM_PROMPT + "\ncwd: /tmp/x"
    elif kind == "active_tools":
        by["profile.bind"]["activeToolNames"] = ["Read"]
    elif kind == "agents_md":
        by["profile.bind"]["agentsMdPaths"] = ["/home/u/AGENTS.md"]
    elif kind == "injection":
        recs.insert(6, _msg("<skills>...</skills>", {"kind": "injection", "variant": "skills_listing"}))
    elif kind == "grown_injection":
        recs.insert(6, _msg(AUTO + "x" * 500, {"kind": "injection", "variant": "permission_mode"}))
    elif kind == "user_turn":
        recs[4] = _msg("p\n\nAlso, a hidden note", {"kind": "user"})
    elif kind == "tool_message":
        recs.append({"type": "context.append_message", "message": {"role": "tool", "content": [], "toolCalls": []}})
    elif kind == "tool_calls":
        recs.append({"type": "context.append_message",
                     "message": {"role": "assistant", "content": [], "toolCalls": [{"name": "Shell"}]}})
    elif kind == "tool_part":
        recs.append({"type": "context.append_loop_event",
                     "event": {"type": "content.part", "part": {"type": "tool_use", "name": "Shell"}}})
    elif kind == "tool_loop_event":
        recs.append({"type": "context.append_loop_event", "event": {"type": "tool.call", "name": "Shell"}})
    elif kind == "tool_record":
        recs.append({"type": "tool.result", "content": "ls"})
    elif kind == "no_request":
        recs = [r for r in recs if r["type"] != "llm.request"]
    elif kind == "no_wire":
        recs = None
    elif kind == "message_count":
        by["llm.request"]["messageCount"] = 4  # a message reached the model that the record does not show
    elif kind == "no_message_count":
        del by["llm.request"]["messageCount"]
    elif kind == "unparseable_line":
        recs.insert(5, '{"type": "context.append_message", "message": {"role": "user", "content": [{"text": "hid')
    elif kind == "non_dict_line":
        recs.insert(5, '["context.append_message"]')
    elif kind == "malformed_message":
        recs.insert(5, {"type": "context.append_message", "message": "hidden text"})
    elif kind == "non_text_part":
        recs[4] = {**recs[4], "message": {**recs[4]["message"], "content": [{"type": "image", "url": "x"}]}}
    elif kind == "malformed_tools":
        by["llm.tools_snapshot"]["tools"] = "Shell"
    elif kind == "malformed_loop_event":
        recs.append({"type": "context.append_loop_event", "event": "tool.call"})
    return recs


@pytest.mark.parametrize("kind", ["tools_snapshot", "tools_hash", "system_hash", "system_prompt", "active_tools",
                                  "agents_md", "injection", "grown_injection", "user_turn", "tool_message",
                                  "tool_calls", "tool_part", "tool_loop_event", "tool_record", "no_request",
                                  "no_wire", "message_count", "no_message_count", "unparseable_line",
                                  "non_dict_line", "malformed_message", "non_text_part", "malformed_tools",
                                  "malformed_loop_event"])
def test_request_record_fails_closed(tmp_path, kimi_home, kind):
    r = subject(tmp_path, kimi_home, wire_records=_mutate(kind)).complete("p")
    assert not r.ok and r.error.startswith("isolation:"), (kind, r.error)
    assert runner.infra_kind(r.error) == "fatal"


def test_unknown_benign_wire_record_is_tolerated(tmp_path, kimi_home):
    recs = wire("p") + [{"type": "session.title_set", "title": "x"}]
    assert subject(tmp_path, kimi_home, wire_records=recs).complete("p").ok


def test_change_notifier_snapshot_is_checked(tmp_path, kimi_home):
    # kimi-code 2.3.1 bookkeeping record (shape captured from a live call): fine without an AGENTS.md
    snap = {"type": "changeNotifier.snapshot", "agentId": "main", "agentsMdHash": None, "agentsMdFiles": {},
            "skillsHash": "18df", "subagentNames": ["plan", "agent", "coder", "explore", "bca-subject"],
            "modelPool": None}
    assert subject(tmp_path, kimi_home, wire_records=wire("p") + [snap]).complete("p").ok
    loaded = dict(snap, agentsMdHash="ab12", agentsMdFiles={"AGENTS.md": "ab12"})
    r = subject(tmp_path, kimi_home, wire_records=wire("p") + [loaded]).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and "AGENTS.md loaded" in r.error


def test_model_reroute_is_an_error(tmp_path, kimi_home):
    recs = wire("p")
    for rec in recs:
        if rec["type"] == "llm.request":
            rec["modelAlias"] = "kimi-code/kimi-for-coding"
    r = subject(tmp_path, kimi_home, wire_records=recs).complete("p")
    assert not r.ok and "model rerouted" in r.error


# -- errors -----------------------------------------------------------------------------------------


def test_quota_error_is_fatal(tmp_path, kimi_home):
    r = subject(tmp_path, kimi_home, events=[OK[0]], wire_records=None, exit_code=1,
                stderr="error: failed to run prompt: provider.api_error: You exceeded your current quota\n"
                       "See log: /tmp/x/logs/kimi-code.log\n").complete("p")
    assert not r.ok and "exceeded your current quota" in r.error and "See log" not in r.error
    assert runner.infra_kind(r.error) == "fatal"


def test_retry_note_reaches_the_error(tmp_path, kimi_home):
    retry = {"role": "meta", "type": "turn.step.retrying", "failed_attempt": 10, "next_attempt": 11,
             "max_attempts": 10, "delay_ms": 0, "error_name": "rate_limit", "status_code": 429,
             "error_message": "429 Too Many Requests"}
    r = subject(tmp_path, kimi_home, events=[OK[0], retry], wire_records=None, exit_code=1,
                stderr="error: failed to run prompt: something\n").complete("p")
    assert not r.ok and "rate_limit (429): 429 Too Many Requests" in r.error
    assert runner.infra_kind(r.error) == "transient"  # rate limits are retried by the runner


_NEEDS_RUNNER = ()  # runner._FATAL_RE now knows these kimi auth strings (were xfail)


@pytest.mark.parametrize("stderr", [
    # live, kimi 2.1.1: a rejected token whose refresh fails (OAuth invalid_grant)
    pytest.param("error: failed to run prompt: The provided authorization grant is invalid\n", marks=_NEEDS_RUNNER),
    # live: no usable token at startup
    pytest.param("error: failed to run prompt: provider managed:kimi-code has no credential configured\n",
                 marks=_NEEDS_RUNNER),
    # source (llm-adapter/contract/errors.ts): a 401/403 from the API
    pytest.param("error: failed to run prompt: provider.auth_error: 401 Invalid Authentication\n"),
])
def test_auth_errors_are_fatal(tmp_path, kimi_home, stderr):
    r = subject(tmp_path, kimi_home, events=[OK[0]], wire_records=None, exit_code=1, stderr=stderr).complete("p")
    assert not r.ok and runner.infra_kind(r.error) == "fatal"


# -- credentials ------------------------------------------------------------------------------------


def test_refreshed_token_written_by_rename_is_copied_back(tmp_path, kimi_home):
    before = {p: p.read_bytes() for p in kimi_home.rglob("*") if p.is_file() and p.name != ".bca-cred.lock"}
    r = subject(tmp_path, kimi_home, refresh='{"access_token": "new"}').complete("p")
    assert r.ok
    assert (kimi_home / CRED).read_text() == '{"access_token": "new"}'
    after = {p: p.read_bytes() for p in kimi_home.rglob("*") if p.is_file() and p.name != ".bca-cred.lock"}
    assert set(after) == set(before)  # nothing new in the real home: no sessions, logs, history
    assert all(after[p] == before[p] for p in before if p != kimi_home / CRED)


def test_missing_credentials_is_fatal(tmp_path, kimi_home):
    (kimi_home / CRED).unlink()
    r = subject(tmp_path, kimi_home).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and runner.infra_kind(r.error) == "fatal"


def test_blank_wire_lines_are_ignored(tmp_path, kimi_home):
    recs = wire("p")
    recs.insert(3, "")
    assert subject(tmp_path, kimi_home, wire_records=recs).complete("p").ok


def test_failed_call_is_still_audited(tmp_path, kimi_home):
    # a quota failure whose record shows a request with tools: the isolation error wins
    r = subject(tmp_path, kimi_home, events=[OK[0]], wire_records=_mutate("tools_hash"), exit_code=1,
                stderr="error: failed to run prompt: provider.rate_limit: slow down\n").complete("p")
    assert r.error.startswith("isolation:") and "toolsHash" in r.error


# -- safety blocks are scored refusals ----------------------------------------------------------------


def _filtered_wire(prompt="p"):
    recs = [r for r in wire(prompt) if r["type"] != "turn.ended"]
    for r in recs:
        if r["type"] == "context.append_loop_event" and r["event"]["type"] == "step.end":
            r["event"]["finishReason"] = "filtered"
    return recs + [{"type": "turn.ended", "agentId": "main", "turnId": 0, "reason": "failed",
                    "error": {"code": "provider.filtered", "message": "Provider safety policy blocked the response."}}]


def test_provider_safety_block_is_a_refusal(tmp_path, kimi_home):
    events = [OK[0], {"role": "assistant", "content": "partial"}]
    r = subject(tmp_path, kimi_home, events=events, wire_records=_filtered_wire(), exit_code=1,
                stderr=REFUSAL_STDERR).complete("p")
    assert r.refusal.startswith("provider.filtered: Provider safety policy blocked the response")
    assert not r.ok and not r.error.startswith("isolation:") and runner.infra_kind(r.error) == "transient"


def test_safety_block_seen_only_in_the_wire_record_is_a_refusal(tmp_path, kimi_home):
    r = subject(tmp_path, kimi_home, wire_records=_filtered_wire(), exit_code=1,
                stderr="error: failed to run prompt: something\n").complete("p")
    assert r.refusal.startswith("provider.filtered:")


def test_refusal_needs_a_clean_request(tmp_path, kimi_home):
    r = subject(tmp_path, kimi_home, wire_records=None, exit_code=1, stderr=REFUSAL_STDERR).complete("p")
    assert r.error.startswith("isolation:") and "no wire.jsonl" in r.error and r.refusal == ""
    recs = _filtered_wire()
    for rec in recs:
        if rec["type"] == "llm.tools_snapshot":
            rec["tools"] = [{"name": "Shell"}]
    r = subject(tmp_path, kimi_home, wire_records=recs, exit_code=1, stderr=REFUSAL_STDERR).complete("p")
    assert r.error.startswith("isolation:") and r.refusal == ""


# -- credentials: refresh serialisation ---------------------------------------------------------------


def test_cred_expiry_follows_kimi_refresh_threshold(tmp_path, kimi_home):
    s = subject(tmp_path, kimi_home)
    cred = lambda **kw: json.dumps(kw).encode()  # noqa: E731
    assert s.cred_expiry(CRED, cred(expires_at=2_000_000_000, expires_in=900)) == 2_000_000_000 - 450
    assert s.cred_expiry(CRED, cred(expires_at=2_000_000_000, expires_in=60)) == 2_000_000_000 - 300
    assert s.cred_expiry(CRED, cred(expires_at=2_000_000_000)) == 2_000_000_000 - 300
    assert s.cred_expiry(CRED, cred(expires_at=0, access_token="")) is None  # kimi's blanked file: never refreshes
    assert s.cred_expiry(CRED, b"{") is None and s.cred_expiry("config.toml", b"x") is None


def test_expiring_token_makes_the_call_the_refresh_leader(tmp_path, kimi_home):
    import time

    (kimi_home / CRED).write_text(json.dumps({"access_token": "old", "expires_at": int(time.time()) + 10,
                                              "expires_in": 900}))
    r = subject(tmp_path, kimi_home, refresh='{"access_token": "new", "expires_at": 9999999999}').complete("p")
    assert r.ok and json.loads((kimi_home / CRED).read_text())["access_token"] == "new"
