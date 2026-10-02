"""CodexSubject (bca.liveeval.vendor_codex) against a fake `codex`: the tool-less catalog, the switches,
fail-closed parsing, model identity, effort, error classification and credentials."""

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from bca.liveeval import runner
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_codex import (
    CATALOG_TOOL_LESS,
    CODEX_REFRESH_WINDOW_S,
    CODEX_RUST_LOG,
    CodexSubject,
    codex_input_bound,
    codex_safety,
)

# A catalog entry shaped like codex 0.157.1's live one for gpt-6-luna (the tool-bearing fields as served).
ENTRY = {
    "slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "description": "d", "default_reasoning_level": "medium",
    "supported_reasoning_levels": [{"effort": e, "description": e} for e in ("low", "medium", "high", "xhigh", "max")],
    "shell_type": "unified_exec", "visibility": "list", "supported_in_api": True, "priority": 3,
    "availability_nux": None, "upgrade": None, "base_instructions": "You are Codex, ... (18 KB)",
    "model_messages": {"persistent_instructions": "## Overview ...", "instructions_template": "You are Codex"},
    "include_skills_usage_instructions": False, "include_plugin_usage_instructions": False,
    "include_apps_usage_instructions": False, "default_reasoning_summary": "none", "support_verbosity": True,
    "default_verbosity": "low", "apply_patch_tool_type": "freeform", "web_search_tool_type": "text_and_image",
    "truncation_policy": {"mode": "tokens", "limit": 10000}, "context_window": 272000,
    "experimental_supported_tools": ["send_user_message_async", "clock"], "supports_search_tool": True,
    "use_responses_lite": True, "tool_mode": "code_mode_only", "multi_agent_version": "v2",
}  # fmt: skip
BUNDLED = {**ENTRY, "default_service_tier": "priority"}

CODEX_OK = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "thinking..."}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "mov x0, #1\nret"}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
]


def fake_codex(tmp_path: Path, events: list, *, exit_code: int = 0, stderr: str = "", rewrite: dict | None = None,
               sleep: float = 0.0, catalog: list | None = None, catalog_rewrite: dict | None = None,
               catalog_fail: str = "", version: str = "0.157.1") -> Path:
    """A fake `codex`: `--version`, `features list`, `debug models [--bundled]` (appending to
    tmp_path/catalog_calls.jsonl), and `exec`, which records argv / env / its config home's files into
    tmp_path/seen.json, optionally rewrites credential files, then prints `events` as JSONL."""
    script = tmp_path / "fakecodex"
    live = {"models": catalog if catalog is not None else [ENTRY]}
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys, time
        from pathlib import Path
        tmp = Path({str(tmp_path)!r})
        home = os.environ.get("CODEX_HOME") or ""
        def files():
            return {{str(p.relative_to(home)): p.read_text() for p in Path(home).rglob("*")
                    if p.is_file() and not p.name.startswith(".bca")}}
        a = sys.argv[1:]
        if a == ["--version"]:
            print("codex-cli {version}"); sys.exit(0)
        if a == ["features", "list"]:
            print("shell_tool       stable             true")
            print("goals            stable             true")
            print("js_repl          removed            true")
            print("enable_request_compression stable   true")
            print("memories         stable             false"); sys.exit(0)
        if a[:2] == ["debug", "models"]:
            with open(tmp / "catalog_calls.jsonl", "a") as f:
                f.write(json.dumps({{"argv": a, "home": home, "files": files()}}) + "\\n")
            if "--bundled" not in a and {catalog_fail!r}:
                sys.stderr.write({catalog_fail!r}); sys.exit(1)
            if "--bundled" not in a:
                for rel, data in {catalog_rewrite or {}!r}.items():
                    Path(home, rel).write_text(data)
            Path(home, "models_cache.json").write_text("{{}}")
            print(json.dumps({{"models": [{BUNDLED!r}]}} if "--bundled" in a else {live!r})); sys.exit(0)
        json.dump({{"argv": a, "env": dict(os.environ), "cwd": os.getcwd(), "cwd_files": os.listdir("."),
                   "home_files": files()}}, open(tmp / "seen.json", "w"))
        for rel, data in {rewrite or {}!r}.items():
            Path(home, rel).write_text(data)
        time.sleep({sleep})
        for ev in {events!r}:
            print(json.dumps(ev), flush=True)
        sys.stderr.write({stderr!r})
        sys.exit({exit_code})
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seen.json").read_text())


def catalog_calls(tmp_path: Path) -> list[dict]:
    p = tmp_path / "catalog_calls.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def codex_home(tmp_path):
    h = tmp_path / "codex-home"
    h.mkdir()
    (h / "auth.json").write_text('{"tokens": "old"}')
    (h / "config.toml").write_text('model = "leaky"\n')
    (h / "AGENTS.md").write_text("SECRET USER INSTRUCTIONS")
    return h


def subject(tmp_path, codex_home, events=CODEX_OK, model="gpt-6-luna", effort=None, **kw) -> CodexSubject:
    return CodexSubject(model, binary=str(fake_codex(tmp_path, events, **kw)), home_real=codex_home, effort=effort)


# -- the request: switches and the tool-less catalog ----------------------------------------------


def test_answer_usage_and_isolation(tmp_path, codex_home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("OPENAI_MODEL", "leak")
    monkeypatch.setenv("RUST_LOG", "trace")
    s = subject(tmp_path, codex_home, effort="low")
    got = []
    r = s.complete("PROMPT -x", on_text=lambda t: got.append(t) or True, raw_path=str(tmp_path / "raw.jsonl"))
    assert r.ok and r.text == "mov x0, #1\nret" and got == [r.text]
    assert r.usage == {"input_tokens": 10, "output_tokens": 5}
    assert r.model == "gpt-6-luna" and r.model_source == "requested"  # codex never names the served model
    sn = seen(tmp_path)
    argv = sn["argv"]
    assert argv[:2] == ["exec", "--json"] and argv[-2:] == ["--", "PROMPT -x"]
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("-m") + 1] == "gpt-6-luna"
    cfg = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    disabled = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
    assert disabled == ["shell_tool", "goals"]  # enabled by default; not removed, not transport plumbing
    for kv in ("features.shell_tool=false", 'web_search="disabled"', 'approval_policy="never"',
               "project_doc_max_bytes=0", 'model_reasoning_effort="low"', "agents.enabled=false",
               "skills.include_instructions=false", "skills.bundled.enabled=false",
               "include_collaboration_mode_instructions=false", "include_environment_context=false",
               "include_permissions_instructions=false", "include_apps_instructions=false"):
        assert kv in cfg
    home = Path(sn["env"]["CODEX_HOME"])
    assert f'model_catalog_json="{home / "bca-models.json"}"' in cfg
    assert f'model_instructions_file="{home / "bca-system.md"}"' in cfg
    # a private home with the credentials, our system prompt and the tool-less catalog only
    files = sn["home_files"]
    assert set(files) == {"auth.json", "bca-system.md", "bca-models.json"}
    assert files["auth.json"] == '{"tokens": "old"}' and files["bca-system.md"] == SYSTEM_PROMPT + "\n"
    assert home != codex_home and not home.exists()
    assert sn["env"]["RUST_LOG"] == CODEX_RUST_LOG
    assert "CLAUDECODE" not in sn["env"] and "OPENAI_MODEL" not in sn["env"]
    assert sn["cwd_files"] == [] and not Path(sn["cwd"]).exists()
    assert "bca_stderr" in (tmp_path / "raw.jsonl").read_text().splitlines()[-1]


def test_catalog_is_the_clis_own_entry_with_every_tool_field_off(tmp_path, codex_home):
    s = subject(tmp_path, codex_home)
    assert s.complete("p").ok
    (m,) = json.loads(seen(tmp_path)["home_files"]["bca-models.json"])["models"]
    for k, v in CATALOG_TOOL_LESS.items():
        assert m[k] == v, k
    assert m["tool_mode"] == "direct" and m["multi_agent_version"] == "disabled"
    assert m["experimental_supported_tools"] == [] and m["apply_patch_tool_type"] is None
    assert m["shell_type"] == "disabled" and m["supports_search_tool"] is False
    assert "model_messages" not in m and m["base_instructions"] == SYSTEM_PROMPT
    # everything that is not about tools stays the CLI's default
    for k in ("default_reasoning_level", "default_verbosity", "use_responses_lite", "supported_reasoning_levels",
              "context_window", "truncation_policy", "default_reasoning_summary"):
        assert m[k] == ENTRY[k], k


def test_catalog_fetched_once_in_its_own_private_home(tmp_path, codex_home):
    s = subject(tmp_path, codex_home, catalog_rewrite={"auth.json": '{"tokens": "refreshed"}'})
    assert s.complete("p").ok and s.complete("q").ok
    (call,) = catalog_calls(tmp_path)  # one live fetch per subject
    assert call["argv"] == ["debug", "models"]
    assert call["files"] == {"auth.json": '{"tokens": "old"}'} and call["home"] != str(codex_home)
    assert not Path(call["home"]).exists()
    assert (codex_home / "auth.json").read_text() == '{"tokens": "refreshed"}'  # refresh copied back
    assert (codex_home / "config.toml").read_text() == 'model = "leaky"\n'


def test_unknown_model_is_fatal_and_never_runs(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, model="gpt-nope").complete("p")
    assert not r.ok and "unknown model" in r.error and runner.infra_kind(r.error) == "fatal"
    assert not (tmp_path / "seen.json").exists()


def test_effort_the_model_does_not_list_is_fatal(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, effort="ultra").complete("p")  # luna lists low..max
    assert not r.ok and "not supported" in r.error and runner.infra_kind(r.error) == "fatal"
    assert not (tmp_path / "seen.json").exists()


def test_default_effort_passes_no_effort_and_is_described(tmp_path, codex_home):
    s = subject(tmp_path, codex_home)
    d = s.describe()  # before any call: the CLI's bundled catalog, offline
    assert d["catalog"]["source"] == "bundled" and d["catalog"]["service_tier"] == "priority"
    assert d["effort"] == "cli-default (medium)" and d["cli_version"] == "codex 0.157.1"
    assert s.complete("p").ok
    assert not any(a.startswith("model_reasoning_effort") for a in seen(tmp_path)["argv"])
    d = s.describe()
    assert d["catalog"] == {"source": "live", "default_effort": "medium",
                            "efforts": ["low", "medium", "high", "xhigh", "max"], "verbosity": "low",
                            "service_tier": None, "reasoning_summary": "none"}
    assert subject(tmp_path, codex_home, effort="high").describe()["effort"] == "high"


# -- fail closed: an allowlist of items and events --------------------------------------------------


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "collab_tool_call",
                                       "web_search", "todo_list", "a_tool_from_a_later_release"])
@pytest.mark.parametrize("phase", ["item.started", "item.completed"])
def test_any_non_answer_item_fails_closed(tmp_path, codex_home, item_type, phase):
    events = [CODEX_OK[0], {"type": phase, "item": {"id": "item_0", "type": item_type}}, CODEX_OK[3]]
    r = subject(tmp_path, codex_home, events).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and item_type in r.error
    assert r.text == ""  # the answer after the tool call was never read
    assert runner.infra_kind(r.error) == "fatal"


def test_unknown_top_level_event_fails_closed(tmp_path, codex_home):
    events = [CODEX_OK[0], {"type": "tool.called", "name": "exec"}, CODEX_OK[3]]
    r = subject(tmp_path, codex_home, events).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and "tool.called" in r.error and r.text == ""


def test_error_and_reasoning_items_are_not_violations(tmp_path, codex_home):
    events = [CODEX_OK[0], {"type": "item.completed", "item": {"type": "error", "message": "Falling back from "
                                                               "WebSockets to HTTPS transport."}},
              {"type": "error", "message": "Reconnecting... 1/5"}, *CODEX_OK[1:]]
    r = subject(tmp_path, codex_home, events).complete("p")
    assert r.ok and r.text == "mov x0, #1\nret"


def test_several_agent_messages_accumulate(tmp_path, codex_home):
    msg = lambda t: {"type": "item.completed", "item": {"type": "agent_message", "text": t}}  # noqa: E731
    got = []
    r = subject(tmp_path, codex_home, [msg("add:\n"), msg("  ret\n"), CODEX_OK[4]]).complete("p", on_text=got.append)
    assert r.ok and r.text == "add:\n  ret\n" and "".join(got) == r.text and r.deltas == 2


# -- model identity ---------------------------------------------------------------------------------


def test_reroute_is_recorded_as_reported(tmp_path, codex_home):
    events = [{"type": "item.completed", "item": {"type": "error", "message":
                                                  "model rerouted: gpt-6-luna -> gpt-5.2 (HighRiskCyberActivity)"}},
              CODEX_OK[3]]
    r = subject(tmp_path, codex_home, [*events, CODEX_OK[4]]).complete("p")
    # the cyber-safety fallback is Claude's refusal fallback: served model recorded, scored as a refusal
    assert r.ok and r.model == "gpt-5.2" and r.model_source == "reported"
    assert r.refusal.startswith("cyber_reroute: model rerouted: gpt-6-luna -> gpt-5.2")
    assert "answered by fallback model gpt-5.2" in r.refusal


def test_server_reported_model_in_the_log_is_recorded(tmp_path, codex_home):
    log = "2026-09-29T02:08:25Z  INFO codex_core::session: server reported model gpt-6-luna (matches requested model)\n"
    r = subject(tmp_path, codex_home, stderr=log).complete("p")
    assert r.ok and r.model == "gpt-6-luna" and r.model_source == "reported"


# -- failures ---------------------------------------------------------------------------------------

# Verbatim from codex-rs 0.157.1 (protocol/src/error.rs, login/src/auth/manager.rs), as `turn.failed`.
FATAL_NOW = [
    "You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 3:00 PM.",
    "You’ve hit your usage limit for GPT-6-Sol. Switch to another model now, or try again at 3:00 PM.",
    "Quota exceeded. Check your plan and billing details.",
    "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
]
FATAL_NEEDS_RUNNER_REGEX = [
    "Your workspace is out of credits. Add credits to continue.",
    "You hit your spend cap set in your workspace. Increase your spend cap to continue.",
    "To use Codex with your ChatGPT plan, upgrade to Plus: https://chatgpt.com/explore/plus.",
    "Your access token could not be refreshed because your refresh token has expired. Please log out and sign in "
    "again.",
]
TRANSIENT = [
    "We’re currently experiencing high demand, which may cause temporary errors.",
    "Selected model is at capacity. Please try a different model.",
    "stream disconnected before completion: error sending request for url",
    "rate limit exceeded: slow down",
]


def _failed(tmp_path, codex_home, message):
    events = [CODEX_OK[0], {"type": "error", "message": message}, {"type": "turn.failed", "error": {"message": message}}]
    return subject(tmp_path, codex_home, events, exit_code=1).complete("p")


@pytest.mark.parametrize("message", FATAL_NOW)
def test_quota_and_auth_errors_are_fatal(tmp_path, codex_home, message):
    r = _failed(tmp_path, codex_home, message)
    assert not r.ok and r.error == "turn failed: " + message and runner.infra_kind(r.error) == "fatal"


@pytest.mark.parametrize("message", FATAL_NEEDS_RUNNER_REGEX)
def test_codex_plan_and_token_errors_are_fatal(tmp_path, codex_home, message):
    assert runner.infra_kind(_failed(tmp_path, codex_home, message).error) == "fatal"


@pytest.mark.parametrize("message", TRANSIENT)
def test_overload_errors_stay_transient(tmp_path, codex_home, message):
    r = _failed(tmp_path, codex_home, message)
    assert not r.ok and runner.infra_kind(r.error) == "transient"


def test_exit_status_without_turn_failed(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, [CODEX_OK[0]], exit_code=1, stderr="Error: boom\n").complete("p")
    assert not r.ok and r.error.startswith("exit status 1:") and "boom" in r.error


def test_early_abort_lets_codex_finish_and_checks_the_request(tmp_path, codex_home):
    # a whole-message CLI is never killed: it finishes, its usage is kept and the request size checked
    r = subject(tmp_path, codex_home).complete("p", on_text=lambda t: False)
    assert r.killed and r.ok and r.usage == {"input_tokens": 10, "output_tokens": 5}
    big = [*CODEX_OK[:4], {"type": "turn.completed", "usage": {"input_tokens": 4000, "output_tokens": 5}}]
    r = subject(tmp_path, codex_home, big).complete("p", on_text=lambda t: False)
    assert not r.ok and r.error.startswith("isolation:") and "4000 input tokens" in r.error


def test_timeout(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, sleep=5).complete("p", timeout=0.5)
    assert r.timed_out and not r.ok and r.error == "timeout"


def test_missing_credentials_is_fatal(tmp_path):
    s = CodexSubject("m", binary=str(fake_codex(tmp_path, CODEX_OK)), home_real=tmp_path / "nohome")
    r = s.complete("p")
    assert not r.ok and "log in first" in r.error and runner.infra_kind(r.error) == "fatal"
    assert not (tmp_path / "seen.json").exists() and not catalog_calls(tmp_path)


def test_refreshed_token_is_copied_back(tmp_path, codex_home):
    s = subject(tmp_path, codex_home, rewrite={"auth.json": '{"tokens": "new"}'})
    assert s.complete("p").ok
    assert (codex_home / "auth.json").read_text() == '{"tokens": "new"}'
    assert oct((codex_home / "auth.json").stat().st_mode & 0o777) == "0o600"
    assert (codex_home / "config.toml").read_text() == 'model = "leaky"\n'  # untouched


def test_audited_version_only(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, version="0.158.0").complete("p")
    assert not r.ok and r.error.startswith("isolation: codex CLI 0.158.0 is not an audited version")
    assert runner.infra_kind(r.error) == "fatal" and not (tmp_path / "seen.json").exists()


REC_0160 = json.loads((Path(__file__).parent / "fixtures" / "codex" / "exec-0.160.0.json").read_text())


@pytest.mark.parametrize("version,ok", [("0.157.1", True), ("0.160.0", True), ("0.161.0", False)])
def test_audited_versions(tmp_path, codex_home, version, ok):
    r = subject(tmp_path, codex_home, version=version).complete("p")
    assert r.ok is ok
    if not ok:
        assert r.error.startswith(f"isolation: codex CLI {version} is not an audited version")


def test_recorded_0160_exec_stream_is_a_clean_answer(tmp_path, codex_home):
    # gpt-6-luna on codex 0.160.0, recorded live (websocket transport): the event types are the audited set
    r = subject(tmp_path, codex_home, events=REC_0160["events"], version="0.160.0").complete(REC_0160["prompt"])
    assert r.ok and r.text == "OK" and not r.error
    assert r.usage["input_tokens"] == 48 and r.usage["input_tokens"] <= codex_input_bound(REC_0160["prompt"])
    assert r.model == "gpt-6-luna" and r.model_source == "requested"


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-astra"])
def test_recorded_0160_catalog_fields_are_all_audited(model):
    from bca.liveeval.vendor_codex import CATALOG_AUDITED

    assert set(REC_0160["catalog_fields"][model]) <= CATALOG_AUDITED


# -- the request size guard (no tool list reached the model) ----------------------------------------


def test_input_bound_covers_real_prompts_and_not_a_tool_list():
    assert codex_input_bound("Reply with the single word OK.") < 1000 < 4000  # 58 live; ~4,000 with tools
    long = "add x0, x0, x1\n" * 2300  # 34 KB of dense assembly, the largest recorded prompt size
    assert codex_input_bound(long) > len(long) / 1.95  # the densest recorded prompts: ~1.95 chars/token


@pytest.mark.parametrize("usage, needle", [
    ({"input_tokens": 4100, "output_tokens": 5}, "4100 input tokens"),
    ({"output_tokens": 5}, "no input token count"),
    (None, "no input token count"),
])
def test_request_larger_than_the_prompt_fails_closed(tmp_path, codex_home, usage, needle):
    events = CODEX_OK[:4] + ([{"type": "turn.completed", "usage": usage}] if usage is not None else [])
    r = subject(tmp_path, codex_home, events).complete("short prompt")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error
    assert runner.infra_kind(r.error) == "fatal"


# -- malformed events are violations, never crashes -------------------------------------------------


@pytest.mark.parametrize("ev, needle", [
    ({"type": "item.completed", "item": "x"}, "malformed item.completed item"),
    ({"type": "item.started", "item": None}, "malformed item.started item"),
    ({"type": "item.completed", "item": {"type": "agent_message", "text": ["a"]}}, "malformed agent_message"),
    ({"type": "item.completed", "item": {"type": "error", "message": 3}}, "malformed error item"),
    ({"type": "turn.completed", "usage": "lots"}, "malformed turn.completed"),
    ({"type": "turn.failed", "error": "boom"}, "malformed turn.failed error"),
    ({"type": "error"}, "malformed error event"),
])
def test_malformed_events_fail_closed(tmp_path, codex_home, ev, needle):
    r = subject(tmp_path, codex_home, [CODEX_OK[0], ev, *CODEX_OK[1:]]).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error and r.text == ""


# -- safety blocks are scored refusals --------------------------------------------------------------

# codex-api/src/api_bridge.rs + sse/responses.rs: the policy codes' fallback messages and a server one.
REFUSALS = [
    ("This request has been flagged for possible cybersecurity risk.", "cyber_policy"),
    ("This request was flagged for cyber policy.", "cyber_policy"),
    ("This content was flagged for possible biological risk.", "bio_policy"),
    ("This request was blocked due to a misalignment policy violation.", "misalignment_policy"),
    ("Invalid prompt: your prompt was flagged as potentially violating our usage policy.", "usage_policy"),
]


@pytest.mark.parametrize("message, kind", REFUSALS)
def test_policy_block_is_a_refusal(tmp_path, codex_home, message, kind):
    r = _failed(tmp_path, codex_home, message)
    assert r.refusal == f"{kind}: {message}" and not r.ok
    assert runner.infra_kind(r.error) == "transient"  # never a run abort: the pipelines score the refusal


def test_content_filter_retried_by_codex_is_still_a_refusal(tmp_path, codex_home):
    # response.incomplete (content_filter) is a stream error codex retries: the later answer is not credited
    events = [CODEX_OK[0], {"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion: "
                                                         "Incomplete response returned, reason: content_filter)"},
              *CODEX_OK[1:]]
    r = subject(tmp_path, codex_home, events).complete("p")
    assert r.refusal.startswith("content_filter: Reconnecting... 1/5") and r.text == "mov x0, #1\nret"


def test_refusal_with_a_tool_item_is_an_isolation_error(tmp_path, codex_home):
    events = [CODEX_OK[0], {"type": "item.completed", "item": {"type": "command_execution"}},
              {"type": "turn.failed", "error": {"message": REFUSALS[0][0]}}]
    r = subject(tmp_path, codex_home, events, exit_code=1).complete("p")
    assert r.error.startswith("isolation:") and r.refusal == ""


def test_transient_messages_are_not_refusals():
    for msg in TRANSIENT + FATAL_NOW + FATAL_NEEDS_RUNNER_REGEX:
        assert codex_safety(msg) == "", msg


# -- catalog fetch failures: transient vs fatal ------------------------------------------------------


def test_catalog_fetch_network_failure_is_transient(tmp_path, codex_home):
    s = subject(tmp_path, codex_home, catalog_fail="error sending request for url (https://chatgpt.com/backend-api/"
                                                   "codex/models): connection reset\n")
    r = s.complete("p")
    assert not r.ok and r.error.startswith("setup: SetupError: `codex debug models` failed")
    assert runner.infra_kind(r.error) == "transient" and not (tmp_path / "seen.json").exists()


def test_catalog_fetch_timeout_is_transient(tmp_path, codex_home, monkeypatch):
    import subprocess

    s = subject(tmp_path, codex_home)
    real = subprocess.run

    def run(argv, *a, **k):
        if argv[1:3] == ["debug", "models"]:
            raise subprocess.TimeoutExpired(argv, 120)
        return real(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    r = s.complete("p")
    assert r.error == "setup: SetupError: `codex debug models` timed out" and runner.infra_kind(r.error) == "transient"


def test_catalog_auth_failure_is_still_fatal(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, catalog_fail="unexpected status 401 Unauthorized\n").complete("p")
    assert r.error.startswith("setup:") and runner.infra_kind(r.error) == "fatal"


def test_unstrippable_catalog_is_fatal(tmp_path, codex_home):
    r = subject(tmp_path, codex_home, catalog=[{**ENTRY, "browser_tool_type": "cdp"}]).complete("p")
    assert r.error.startswith("isolation:") and "browser_tool_type" in r.error
    assert not (tmp_path / "seen.json").exists()


# -- credentials: refresh serialisation --------------------------------------------------------------


def _jwt(claims: dict) -> str:
    import base64

    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def test_cred_expiry_follows_codex_refresh_rule():
    s = CodexSubject("gpt-6-luna", binary="true", home_real="/nonexistent")
    auth = lambda **kw: json.dumps(kw).encode()  # noqa: E731
    assert s.cred_expiry("auth.json", auth(tokens={"access_token": _jwt({"exp": 2_000_000_000})})) == \
        2_000_000_000 - CODEX_REFRESH_WINDOW_S
    # no readable exp: last_refresh + 8 days (chrono writes nanoseconds)
    got = s.cred_expiry("auth.json", auth(tokens={"access_token": "opaque"},
                                          last_refresh="2026-09-20T12:00:00.123456789Z"))
    from datetime import datetime, timezone

    assert got == pytest.approx(datetime(2026, 9, 20, 12, 0, 0, 123456, timezone.utc).timestamp() + 8 * 86400)
    assert s.cred_expiry("auth.json", auth(OPENAI_API_KEY="sk-x", tokens=None)) is None
    assert s.cred_expiry("auth.json", b"not json") is None


def test_expiring_token_leads_without_deadlocking_the_catalog_fetch(tmp_path, codex_home):
    import time

    soon = _jwt({"exp": int(time.time()) + 30})
    (codex_home / "auth.json").write_text(json.dumps({"tokens": {"access_token": soon}}))
    new = json.dumps({"tokens": {"access_token": _jwt({"exp": int(time.time()) + 36000})}})
    s = subject(tmp_path, codex_home, catalog_rewrite={"auth.json": new})
    r = s.complete("p")  # the refresh leader holds the credential lock for the whole call
    assert r.ok, r.error
    assert (codex_home / "auth.json").read_text() == new
