"""The GitHub Copilot subject (bca.liveeval.vendor_copilot) against a fake driver: the recorded event streams and
wire requests of real tool-less calls (tests/fixtures/copilot, CLI 1.0.89 / SDK 1.0.15, 2026-09-30), and the
checks that fail closed when a tool, another system prompt, another model or an unexpected event appears."""

import copy
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from bca.liveeval import runner, vendor_copilot
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_copilot import CopilotSubject, copilot_input_bound, wire_requests

FIX = Path(__file__).parent / "fixtures" / "copilot"
PROMPT = "Reply with the single word OK."  # the prompt of the recorded calls

# Emulates copilot_driver.mjs: reads the call config, records what it was given, writes the runtime's log (the
# `turn tool surface resolved` and `Wire request` lines) into <home>/logs, then prints the event stream.
FAKE = r'''#!{python}
import json, os, sys
spec = json.load(open({spec!r}))
cfg = json.load(open(sys.argv[2]))
json.dump({{"argv": sys.argv, "cfg": cfg, "env": dict(os.environ), "cwd": os.getcwd()}}, open({seen!r}, "w"))
logs = os.path.join(cfg["home"], "logs")
os.makedirs(logs, exist_ok=True)
with open(os.path.join(logs, "process-1-1.log"), "w") as f:
    f.write("2026-09-30T06:14:34.990Z [DEBUG] [rust:auth] Authentication method resolution completed\n")
    for n in spec["tool_counts"]:
        f.write('2026-09-30T06:14:35.000Z [DEBUG] [rust:copilot_runtime::session::native_message_turn] turn tool '
                'surface resolved {{"model":"m","catalog_tool_count":%d,"tool_count":%d}}\n' % (n, n))
    for w in spec["wires"]:
        body = w if isinstance(w, str) else json.dumps(w, indent=2)
        f.write("2026-09-30T06:14:35.100Z [DEBUG] [rust:model_wire] Wire request: " + body + "\n")
    f.write("2026-09-30T06:14:36.000Z [DEBUG] runHeadlessMode: exiting with code 0\n")
for ev in spec["events"]:
    print(json.dumps(ev), flush=True)
sys.stderr.write(spec["stderr"])
sys.exit(spec["exit"])
'''

NATIVE = "#!/bin/sh\necho 'GitHub Copilot CLI {version}.'\n"


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake native CLI (version) and SDK (package + pinned CLI version) for the adapter's version checks."""
    native = tmp_path / "copilot-native"
    native.write_text(NATIVE.format(version="1.0.89"))
    native.chmod(native.stat().st_mode | stat.S_IEXEC)
    sdk = tmp_path / "sdk"
    (sdk / "dist").mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({"version": "1.0.15"}))
    (sdk / "dist" / "cliVersion.js").write_text('const COPILOT_CLI_VERSION = "1.0.89";\n')
    (sdk / "dist" / "index.js").write_text("")
    monkeypatch.setenv("BCA_COPILOT_CLI", str(native))
    monkeypatch.setenv("BCA_COPILOT_SDK", str(sdk))
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))
    return tmp_path


def subject(tmp_path: Path, rec: dict, model: str, *, wires=None, tool_counts=(0,), exit_code=0, stderr="", **kw):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"events": rec["events"], "wires": [rec["wire"]] if wires is None else wires,
                                "tool_counts": list(tool_counts), "exit": exit_code, "stderr": stderr}))
    node = tmp_path / "node"
    node.write_text(FAKE.format(python=sys.executable, spec=str(spec), seen=str(tmp_path / "seen.json")))
    node.chmod(node.stat().st_mode | stat.S_IEXEC)
    s = CopilotSubject(model, **kw)
    s.node = str(node)
    return s


def seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seen.json").read_text())


def events(rec: dict, typ: str) -> list[dict]:
    return [e for e in rec["events"] if e["type"] == typ]


# -- clean calls: the three request formats ---------------------------------------------------------


@pytest.mark.parametrize("name,model,effort,fmt", [
    ("luna", "gpt-6-luna", "low", "instructions"),          # Responses API
    ("sonnet", "claude-sonnet-5.5", "high", "system"),      # Anthropic /v1/messages
    ("kimi", "kimi-k3", "high", "messages"),                # chat/completions
])
def test_answer_model_usage_and_isolation(env, monkeypatch, name, model, effort, fmt):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("GH_TOKEN", "gho_secret")  # never reaches the child (child_env allowlist)
    rec = load(name)
    assert fmt in rec["wire"]
    got = []
    s = subject(env, rec, model, effort=effort)
    r = s.complete(PROMPT, got.append)
    assert r.ok, r.error
    assert r.text == "OK" and got == ["OK"] and r.deltas == 1
    assert r.model == model and r.model_source == "reported" and r.refusal == ""
    assert r.usage["reasoning_effort"] == effort and r.usage["ai_credits"] > 0 and r.usage["input_tokens"] > 0
    assert r.usage["plan_remaining_pct"] == 99.9
    sw = seen(env)
    cfg, cenv = sw["cfg"], sw["env"]
    assert sw["argv"][1] == str(vendor_copilot.DRIVER) and cfg["model"] == model and cfg["effort"] == effort
    assert cfg["system"] == SYSTEM_PROMPT and cfg["prompt"] == PROMPT
    assert cfg["cli"] == os.environ["BCA_COPILOT_CLI"] and cfg["sdk"].endswith("dist/index.js")
    home = Path(cfg["home"]).parent
    assert str(home).startswith("/tmp/subj-home-") and cfg["cliArgs"][0] == "--disable-builtin-mcps"
    assert cfg["cliArgs"][cfg["cliArgs"].index("--log-dir") + 1] == str(home / "copilot" / "logs")
    # private homes; the gh config dir for auth; no token or Claude session variable reaches the child
    assert cenv["HOME"] == str(home / "home") and cenv["COPILOT_HOME"] == str(home / "copilot")
    assert cenv["TMPDIR"] == str(home / "tmp") and cenv["XDG_CONFIG_HOME"].startswith(str(home))
    assert cenv["GH_CONFIG_DIR"] == str(env / "gh-config") and cenv["COPILOT_AUTO_UPDATE"] == "false"
    assert "GH_TOKEN" not in cenv and "CLAUDECODE" not in cenv and "CLAUDE_CONFIG_DIR" not in cenv
    assert sw["cwd"].startswith("/tmp/subj-")
    assert not home.exists() and not Path(sw["cwd"]).exists()  # both deleted after the call
    assert "the runtime's prompt prefix was removed (userPromptTransformed)" in r.notes


def test_cli_default_effort_is_noted_not_enforced(env):
    r = subject(env, load("luna"), "gpt-6-luna").complete(PROMPT)
    assert r.ok, r.error
    assert "served at effort low (CLI default)" in r.notes


def test_early_abort_still_verifies_the_request(env):
    rec = load("luna")
    rec["wire"]["tools"] = [{"type": "function", "name": "bash"}]
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT, lambda t: False)
    assert not r.ok and r.error.startswith("isolation:") and "tools in the request" in r.error


def test_anthropic_trailing_whitespace_trim_is_allowed_and_noted(env):
    # seen live (sum_to, claude-sonnet-5.5): the runtime echoes the prompt as given, the /v1/messages body has it
    # without its final newline
    rec = _mutate(load("sonnet"), "user.message", content=PROMPT + "\n", transformedContent=PROMPT + "\n")
    r = subject(env, rec, "claude-sonnet-5.5", effort="high").complete(PROMPT + "\n")
    assert r.ok, r.error
    assert "the runtime trimmed the prompt's trailing whitespace" in r.notes


def test_a_reasoning_only_message_is_not_a_second_call(env):
    # seen live (gpt-6-luna, sum_to.ir.x86_64, 2026-09-29): the Responses API returns the encrypted reasoning as its
    # own assistant.message (empty content, no tool requests) next to the answer, within the one model call
    rec = copy.deepcopy(load("luna"))
    i = next(k for k, e in enumerate(rec["events"]) if e["type"] == "assistant.message")
    ans = rec["events"][i]["data"]
    rec["events"].insert(i, {"type": "assistant.message", "data": {
        "messageId": "r1", "model": ans.get("model", "gpt-6-luna"), "content": "", "toolRequests": [],
        "reasoningOpaque": "opaque", "turnId": ans.get("turnId", "0")}})
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT)
    assert r.ok, r.error
    assert r.text == "OK"
    # an empty message WITHOUT reasoning, or one with content, is still a second message and fails closed
    for extra in ({"content": ""}, {"content": "more", "reasoningOpaque": "opaque"}):
        bad = copy.deepcopy(rec)
        bad["events"][i]["data"] = {"messageId": "r1", "model": "gpt-6-luna", "toolRequests": [], **extra}
        r = subject(env, bad, "gpt-6-luna", effort="low").complete(PROMPT)
        assert not r.ok and "2 assistant.message events" in r.error


def test_a_runtime_turn_retry_voids_the_attempt_as_transient(env):
    # seen live (mai-code-1.1-flash, reverse.ir.aarch64, 2026-09-30): model.turn_retry {reason: streaming_error}
    # after model.call_finished; the runtime would call the model again inside the same attempt
    rec = copy.deepcopy(load("luna"))
    i = next(k for k, e in enumerate(rec["events"]) if e["type"] == "assistant.message")
    rec["events"].insert(i, {"type": "model.turn_retry", "data": {"kind": "turn_retry", "model": "gpt-6-luna",
                                                                  "turn": 0, "reason": "streaming_error"}})
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT)
    assert not r.ok and r.text == "" and r.refusal == ""
    assert r.error.startswith("copilot turn retry (streaming_error)")
    assert not r.error.startswith("isolation:")
    from bca.liveeval.runner import _FATAL_RE
    assert not _FATAL_RE.search(r.error)  # transient: the runner retries the cell


@pytest.mark.parametrize("typ,data", [
    ("model.model_call_failure", {"kind": "model_call_failure", "turn": 0, "modelCall": {
        "model": "gpt-6-luna", "error": "connection error: Connection timed out (os error 110) [ETIMEDOUT]"}}),
    ("model.call_failure", {"model": "gpt-6-luna", "failureKind": "transport", "errorMessage": "ETIMEDOUT"}),
])
def test_a_failed_model_call_voids_the_attempt_as_transient(env, typ, data):
    # seen live (mai-code-1.1-flash, rev_lines.stream.x86_64.s2, 2026-09-30): a transport timeout, which the runtime
    # follows with a retry of the model call
    rec = copy.deepcopy(load("luna"))
    i = next(k for k, e in enumerate(rec["events"]) if e["type"] == "assistant.message")
    rec["events"].insert(i, {"type": typ, "data": data})
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT)
    assert not r.ok and r.text == "" and "the attempt is void" in r.error
    from bca.liveeval.runner import _FATAL_RE
    assert not r.error.startswith("isolation:") and not _FATAL_RE.search(r.error)


def test_output_cap_continuation_is_stopped_and_the_capped_answer_scored(env):
    # seen live (claude-sonnet-5.5, vm_run.structured.aarch64, 2026-09-30): 32000 output tokens, then
    # model.telemetry {event: model_turn_continuation, source: thinking-exhausted-continuation, outcome: nudged}; the
    # runtime would inject a nudge and call the model again
    rec = copy.deepcopy(load("luna"))
    msgs = [k for k, e in enumerate(rec["events"]) if e["type"] == "assistant.message"]
    rec["events"] = [e for k, e in enumerate(rec["events"])  # no final message yet; our own deltas only
                     if k not in msgs and e["type"] != "assistant.message_delta"]
    i = next(k for k, e in enumerate(rec["events"]) if e["type"] == "assistant.usage")
    rec["events"][i:i] = [{"type": "assistant.message_delta", "data": {"messageId": "m", "deltaContent": "[{\"op\""}},
                          {"type": "assistant.message_delta", "data": {"messageId": "m", "deltaContent": ": \"mov\""}}]
    rec["events"].append({"type": "model.telemetry", "data": {"kind": "telemetry", "telemetry": {
        "event": "model_turn_continuation", "properties": {"source": "thinking-exhausted-continuation",
                                                           "outcome": "nudged", "model": "gpt-6-luna"}}}})
    rec["events"].append({"type": "assistant.message", "data": {"content": "CONTINUED", "toolRequests": []}})
    got = []
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT, got.append)
    assert r.ok, r.error
    assert r.text == '[{"op": "mov"' and got == [r.text]  # the capped answer, never the continuation
    assert any("output cap reached (thinking-exhausted-continuation)" in n for n in r.notes)
    # any other telemetry event still fails closed
    bad = copy.deepcopy(load("luna"))
    bad["events"].insert(3, {"type": "model.telemetry", "data": {"kind": "telemetry", "telemetry": {"event": "x"}}})
    r = subject(env, bad, "gpt-6-luna", effort="low").complete(PROMPT)
    assert not r.ok and r.error.startswith("isolation:") and "model.telemetry" in r.error


# -- fail closed ------------------------------------------------------------------------------------


def _mutate(rec: dict, typ: str, **kw) -> dict:
    rec = copy.deepcopy(rec)
    for e in rec["events"]:
        if e["type"] == typ:
            e["data"].update(kw)
    return rec


@pytest.mark.parametrize("case,expect", [
    ("tools_key", "tools in the request"),
    ("fingerprint", "tool fingerprint"),
    ("system", "system prompt is not SYSTEM_PROMPT"),
    ("prefix", "not the prompt verbatim"),
    ("second_user", "2 user messages"),
    ("extra_key", "unexpected request keys"),
    ("model", "request for model"),
    ("effort", "the request carried"),
])
def test_wire_request_violations_fail_closed(env, case, expect):
    rec = copy.deepcopy(load("luna"))
    w = rec["wire"]
    if case == "tools_key":
        w["tools"] = [{"type": "function", "name": "bash"}]
    elif case == "fingerprint":
        w["copilotToolsFingerprint"] = {"count": 2, "tools": ["skill:ac879b0e", "sql:63f7e41a"]}
    elif case == "system":
        w["instructions"] = "You are the GitHub Copilot CLI, a terminal assistant built by GitHub."
    elif case == "prefix":
        w["input"][0]["content"][0]["text"] = "<current_datetime>2026-09-29</current_datetime>\n\n" + PROMPT
    elif case == "second_user":
        w["input"].append({"role": "user", "content": [{"type": "input_text", "text": "<session_limits_status>"}],
                           "type": "message"})
    elif case == "extra_key":
        w["web_search"] = True
    elif case == "model":
        w["model"] = "gpt-6-sol"
    elif case == "effort":
        w["reasoning"]["effort"] = "high"
    r = subject(env, rec, "gpt-6-luna", effort="low").complete(PROMPT)
    assert not r.ok and r.error.startswith("isolation:") and expect in r.error, r.error
    assert runner.infra_kind(r.error) == "fatal"


def test_anthropic_and_chat_system_prompts_are_checked(env):
    rec = copy.deepcopy(load("sonnet"))
    rec["wire"]["system"][0]["text"] = "something else"
    r = subject(env, rec, "claude-sonnet-5.5", effort="high").complete(PROMPT)
    assert r.error.startswith("isolation:") and "SYSTEM_PROMPT" in r.error
    rec = copy.deepcopy(load("kimi"))
    rec["wire"]["messages"][0]["content"] = "You are the GitHub Copilot CLI"
    r = subject(env, rec, "kimi-k3", effort="high").complete(PROMPT)
    assert r.error.startswith("isolation:") and "SYSTEM_PROMPT" in r.error


def test_tool_surface_and_missing_or_extra_requests_fail_closed(env):
    rec = load("luna")
    r = subject(env, rec, "gpt-6-luna", tool_counts=(0, 18)).complete(PROMPT)
    assert r.error.startswith("isolation:") and "tool surface resolved with 18 tools" in r.error
    r = subject(env, rec, "gpt-6-luna", wires=[]).complete(PROMPT)
    assert r.error.startswith("isolation:") and "no wire request" in r.error
    r = subject(env, rec, "gpt-6-luna", wires=[rec["wire"], rec["wire"]]).complete(PROMPT)
    assert r.error.startswith("isolation:") and "2 wire requests" in r.error
    r = subject(env, rec, "gpt-6-luna", wires=['{"model": "gpt-6-luna", "tools": [ truncated']).complete(PROMPT)
    assert r.error.startswith("isolation:") and "unparseable wire request" in r.error


@pytest.mark.parametrize("typ,data,expect", [
    ("tool.execution_start", {"toolName": "bash"}, "unexpected copilot event 'tool.execution_start'"),
    ("bca.permission_request", {"kind": "shell"}, "permission requested"),
    ("subagent.started", {}, "unexpected copilot event"),
    ("hook.start", {"hookType": "preToolUse"}, "hook 'preToolUse' ran"),
])
def test_event_violations_fail_closed(env, typ, data, expect):
    rec = copy.deepcopy(load("luna"))
    i = next(i for i, e in enumerate(rec["events"]) if e["type"] == "assistant.message")
    rec["events"].insert(i, {"type": typ, "data": data})
    r = subject(env, rec, "gpt-6-luna").complete(PROMPT)
    assert not r.ok and r.error.startswith("isolation:") and expect in r.error, r.error


def test_a_connected_mcp_server_fails_closed(env):
    # recorded before the built-in GitHub MCP server was disabled: it connected (its tools were excluded)
    rec = load("mai_mcp_connected")
    r = subject(env, rec, "mai-code-1.1-flash").complete(PROMPT)
    assert r.error.startswith("isolation:") and "unexpected copilot event 'session.mcp_server_status_changed'" in r.error


@pytest.mark.parametrize("field,value,expect", [
    ("toolCount", 2, "request capture"),
    ("requestCapture", {"tools": ["bash"], "system_segments": [{"segment": "agent_replacement"}]}, "request capture"),
    ("requestCapture", {"tools": [], "system_segments": [{"segment": "identity"}, {"segment": "agent_replacement"}]},
     "system segments"),
])
def test_request_capture_is_checked(env, field, value, expect):
    rec = _mutate(load("luna"), "model.model_call_success", **{field: value})
    r = subject(env, rec, "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and expect in r.error, r.error


def test_echoed_messages_and_tool_requests_are_checked(env):
    r = subject(env, _mutate(load("luna"), "system.message", content="other"), "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and "system message is not SYSTEM_PROMPT" in r.error
    r = subject(env, _mutate(load("luna"), "assistant.message", toolRequests=[{"name": "view"}]), "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and "tool requests" in r.error
    r = subject(env, _mutate(load("luna"), "session.usage_info", toolDefinitionsTokens=900), "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and "tool-definition tokens" in r.error
    r = subject(env, _mutate(load("luna"), "assistant.usage", isAuto=True), "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and "auto / BYOK" in r.error


def test_input_token_bound(env):
    rec = _mutate(load("luna"), "assistant.usage", inputTokens=50_000)
    r = subject(env, rec, "gpt-6-luna").complete(PROMPT)
    assert r.error.startswith("isolation:") and "input tokens billed" in r.error
    assert copilot_input_bound(PROMPT, "grok-4.7") - copilot_input_bound(PROMPT, "gpt-6-luna") == 2400
    assert copilot_input_bound("x" * 1266, "grok-4.7") > 1915  # grok-4.7 sum_to, seen live


# -- model, effort, refusals, errors ----------------------------------------------------------------


def test_another_served_model_is_a_fatal_reroute(env):
    rec = _mutate(load("luna"), "assistant.usage", model="gpt-5.6-luna")
    r = subject(env, rec, "gpt-6-luna").complete(PROMPT)
    assert not r.ok and r.error == "model rerouted: requested gpt-6-luna, copilot served gpt-5.6-luna, gpt-6-luna"
    assert runner.infra_kind(r.error) == "fatal"


def test_a_model_change_event_is_a_fatal_reroute(env):
    rec = copy.deepcopy(load("luna"))
    rec["events"].insert(5, {"type": "session.model_change", "data": {"previousModel": "gpt-6-luna",
                                                                     "newModel": "claude-haiku-4.5", "cause": "refusal_fallback"}})
    r = subject(env, rec, "gpt-6-luna").complete(PROMPT)
    assert not r.ok and r.error.startswith("model rerouted") and "refusal_fallback" in r.error
    assert runner.infra_kind(r.error) == "fatal" and r.refusal == ""


def test_served_effort_must_match_a_requested_one(env):
    rec = _mutate(load("luna"), "model.model_call_success", reasoningEffort="medium")
    rec["wire"]["reasoning"]["effort"] = "high"
    rec = copy.deepcopy(rec)
    r = subject(env, rec, "gpt-6-luna", effort="high").complete(PROMPT)
    assert not r.ok and "copilot effort mismatch: requested high, served medium" in r.error
    assert runner.infra_kind(r.error) == "fatal"


@pytest.mark.parametrize("change", [{"finishReason": "content_filter"}, {"contentFilterTriggered": True}])
def test_content_filter_is_a_scored_refusal(env, change):
    rec = _mutate(load("sonnet"), "assistant.usage", **change)
    r = subject(env, rec, "claude-sonnet-5.5", effort="high").complete(PROMPT)
    assert r.refusal.startswith("refusal:") and "content filter" in r.refusal


def test_openai_style_refusal_is_scored(env):
    rec = copy.deepcopy(load("kimi"))
    for e in events(rec, "model.message"):
        e["data"]["message"]["refusal"] = "I can't help with that."
    r = subject(env, rec, "kimi-k3", effort="high").complete(PROMPT)
    assert r.refusal == "refusal: I can't help with that."


def test_session_errors_are_classified(env):
    base = load("luna")
    ev = [e for e in base["events"] if e["type"] in ("bca.auth", "bca.session", "session.mcp_servers_loaded")]
    for err, kind in (({"errorType": "quota", "errorCode": "quota_exceeded", "statusCode": 402,
                        "message": "You have used all of your AI credits."}, "fatal"),
                      ({"errorType": "rate_limit", "errorCode": "rate_limited", "statusCode": 429,
                        "message": "Please retry shortly."}, "transient"),
                      ({"errorType": "rate_limit", "errorCode": "user_weekly_rate_limited", "statusCode": 429,
                        "message": "Weekly limit."}, "fatal"),
                      ({"errorType": "authorization", "statusCode": 403, "message": "Copilot is not enabled"}, "fatal")):
        rec = {"events": [*ev, {"type": "session.error", "data": err}, {"type": "session.idle", "data": {}}], "wire": None}
        r = subject(env, rec, "gpt-6-luna", wires=[]).complete(PROMPT)
        assert not r.ok and r.error.startswith(f"copilot {err['errorType']}/" if "errorCode" in err else
                                               f"copilot {err['errorType']} "), r.error
        assert runner.infra_kind(r.error) == kind, r.error


def test_unauthenticated_runtime_is_a_fatal_auth_error(env):
    rec = {"events": [{"type": "bca.auth", "data": {"isAuthenticated": False, "statusMessage": "Not authenticated"}},
                      {"type": "session.error", "data": {"errorType": "query",
                       "message": "Execution failed: InvalidArg, No GitHub OAuth token or Copilot HMAC key provided"}},
                      {"type": "session.idle", "data": {}}], "wire": None}
    r = subject(env, rec, "gpt-6-luna", wires=[], exit_code=1).complete(PROMPT)
    assert not r.ok and r.error.startswith("copilot authentication: not authenticated")
    assert runner.infra_kind(r.error) == "fatal"


# -- versions, registry, parsing --------------------------------------------------------------------


def test_unaudited_cli_or_sdk_is_refused(env, monkeypatch):
    s = subject(env, load("luna"), "gpt-6-luna")
    assert s.cli_version == "1.0.89"
    (env / "copilot-native").write_text(NATIVE.format(version="1.0.90"))
    s = subject(env, load("luna"), "gpt-6-luna")
    r = s.complete(PROMPT)
    assert not r.ok and r.error.startswith("isolation: copilot CLI 1.0.90 ") and "not an audited version" in r.error
    (env / "copilot-native").write_text(NATIVE.format(version="1.0.89"))
    (env / "sdk" / "dist" / "cliVersion.js").write_text('const COPILOT_CLI_VERSION = "1.0.91";\n')
    s = subject(env, load("luna"), "gpt-6-luna")
    assert s.cli_version == "1.0.89 (sdk 1.0.15 pinning cli 1.0.91)" and not s.complete(PROMPT).ok
    # two audited pairs (COPILOT_PAIRS): a mixed pair is refused, the newer matched pair is accepted
    (env / "copilot-native").write_text(NATIVE.format(version="1.0.107"))
    (env / "sdk" / "dist" / "cliVersion.js").write_text('const COPILOT_CLI_VERSION = "1.0.89";\n')
    s = subject(env, load("luna"), "gpt-6-luna")
    assert s.cli_version == "1.0.107 (sdk 1.0.15 pinning cli 1.0.89)" and not s.complete(PROMPT).ok
    (env / "sdk" / "package.json").write_text(json.dumps({"version": "1.0.29"}))
    (env / "sdk" / "dist" / "cliVersion.js").write_text('const COPILOT_CLI_VERSION = "1.0.107";\n')
    assert subject(env, load("luna"), "gpt-6-luna").cli_version == "1.0.107"


def test_registry_and_efforts():
    from bca.liveeval.vendors import VENDORS, all_efforts, make_subject, split_spec

    assert VENDORS["copilot"] is CopilotSubject
    assert split_spec("copilot:mai-code-1.1-flash") == ("copilot", "mai-code-1.1-flash")
    s = make_subject("copilot:kimi-k3", timeout=60, effort="max")
    assert isinstance(s, CopilotSubject) and s.effort == "max"
    assert "minimal" in all_efforts() and "none" in all_efforts()
    with pytest.raises(ValueError):
        CopilotSubject("kimi-k3", effort="ultra")


def test_wire_requests_parser():
    log = ("2026-09-30T06:14:35.100Z [DEBUG] [rust:x] something\n"
           '2026-09-30T06:14:35.101Z [DEBUG] [rust:model_wire] Wire request: {\n  "model": "m",\n  "input": []\n}\n'
           "2026-09-30T06:14:35.102Z [DEBUG] [rust:model_wire] Wire request: {not json\n"
           "2026-09-30T06:14:35.103Z [DEBUG] done\n")
    assert wire_requests(log) == [{"model": "m", "input": []}, "{not json"]
