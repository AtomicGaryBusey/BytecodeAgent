"""Vendor CLI subjects (bca.liveeval.vendors) against fake CLIs: parsing, isolation, credentials."""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from bca.liveeval import runner
from bca.liveeval.registry import expected_model
from bca.liveeval.subject import SYSTEM_PROMPT, ClaudeSubject
from bca.liveeval.vendors import (
    CodexSubject,
    CredHome,
    KimiSubject,
    all_efforts,
    make_subject,
    split_spec,
)


def fake_cli(tmp_path: Path, events: list, *, exit_code: int = 0, stderr: str = "", rewrite: dict | None = None,
             sleep: float = 0.0) -> Path:
    """A fake vendor CLI: records argv / env / the files of its config home into tmp_path/seen.json,
    optionally rewrites credential files (a token refresh), then prints `events` as JSONL."""
    seen = tmp_path / "seen.json"
    script = tmp_path / "fakecli"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys, time
        from pathlib import Path
        if sys.argv[1:] == ["--version"]:
            print("fakecli 9.8.7"); sys.exit(0)
        home = os.environ.get("CODEX_HOME") or os.environ.get("KIMI_CODE_HOME") or ""
        files = {{}}
        if home:
            for p in Path(home).rglob("*"):
                if p.is_file() and not p.name.startswith(".bca"):
                    files[str(p.relative_to(home))] = p.read_text()
        json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(),
                   "cwd_files": os.listdir("."), "home_files": files}}, open({str(seen)!r}, "w"))
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


@pytest.fixture
def codex_home(tmp_path):
    h = tmp_path / "codex-home"
    h.mkdir()
    (h / "auth.json").write_text('{"tokens": "old"}')
    (h / "config.toml").write_text('model = "leaky"\n')
    (h / "AGENTS.md").write_text("SECRET USER INSTRUCTIONS")
    return h


@pytest.fixture
def kimi_home(tmp_path):
    h = tmp_path / "kimi-home"
    (h / "credentials").mkdir(parents=True)
    (h / "credentials" / "kimi-code.json").write_text('{"access": "old"}')
    (h / "config.toml").write_text('default_model = "kimi-code/k3"\n')
    (h / "sessions").mkdir()
    (h / "sessions" / "old.json").write_text("PRIVATE SESSION")
    return h


CODEX_OK = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "thinking..."}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "mov x0, #1\nret"}},
    {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
]


# -- spec routing ------------------------------------------------------------------------------


def test_split_spec_and_make_subject():
    assert split_spec("codex:gpt-6-luna") == ("codex", "gpt-6-luna")
    assert split_spec("kimi:kimi-code/k3") == ("kimi", "kimi-code/k3")
    assert split_spec("opus") == (None, "opus")
    assert split_spec("nosuch:model") == (None, "nosuch:model")
    assert isinstance(make_subject("sonnet", timeout=5), ClaudeSubject)
    assert isinstance(make_subject("codex:gpt-6-luna", timeout=5), CodexSubject)
    assert isinstance(make_subject("kimi:kimi-code/k3", timeout=5), KimiSubject)
    assert expected_model("codex:gpt-6-luna") == "gpt-6-luna"
    assert expected_model("opus") == "claude-opus-5-5"


def test_effort_is_validated_per_vendor():
    make_subject("codex:m", timeout=5, effort="ultra")
    with pytest.raises(ValueError, match="kimi: unknown effort"):
        make_subject("kimi:m", timeout=5, effort="medium")
    with pytest.raises(ValueError, match="claude: unknown effort"):
        make_subject("opus", timeout=5, effort="ultra")
    with pytest.raises(ValueError, match="model is required"):
        make_subject("codex:", timeout=5)
    assert {"ultra", "max", "low"} <= set(all_efforts())


# -- codex -------------------------------------------------------------------------------------


def test_codex_answer_usage_and_isolation(tmp_path, codex_home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("OPENAI_MODEL", "leak")
    s = CodexSubject("gpt-6-luna", binary=str(fake_cli(tmp_path, CODEX_OK)), home_real=codex_home, effort="low")
    got = []
    r = s.complete("PROMPT -x", on_text=lambda t: got.append(t) or True, raw_path=str(tmp_path / "raw.jsonl"))
    assert r.ok and r.text == "mov x0, #1\nret" and got == [r.text]
    assert r.usage == {"input_tokens": 10, "output_tokens": 5} and r.model == "gpt-6-luna"
    sn = seen(tmp_path)
    argv = sn["argv"]
    assert argv[:2] == ["exec", "--json"] and argv[-2:] == ["--", "PROMPT -x"]
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only" and argv[argv.index("-m") + 1] == "gpt-6-luna"
    cfg = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    for kv in ("features.shell_tool=false", 'web_search="disabled"', 'approval_policy="never"',
               "project_doc_max_bytes=0", 'model_reasoning_effort="low"'):
        assert kv in cfg
    # a private home with the credentials and our system prompt only -- no user config or AGENTS.md
    assert sn["home_files"] == {"auth.json": '{"tokens": "old"}', "bca-system.md": SYSTEM_PROMPT + "\n"}
    assert sn["env"]["CODEX_HOME"] != str(codex_home) and not Path(sn["env"]["CODEX_HOME"]).exists()
    assert "CLAUDECODE" not in sn["env"] and "OPENAI_MODEL" not in sn["env"]
    assert sn["cwd_files"] == [] and not Path(sn["cwd"]).exists()
    assert "bca_stderr" in (tmp_path / "raw.jsonl").read_text().splitlines()[-1]


@pytest.mark.parametrize("item_type", ["command_execution", "file_change", "mcp_tool_call", "web_search"])
def test_codex_tool_use_fails_closed(tmp_path, codex_home, item_type):
    events = [CODEX_OK[0], {"type": "item.started", "item": {"id": "item_0", "type": item_type}}, CODEX_OK[3]]
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, events)), home_real=codex_home)
    r = s.complete("p")
    assert not r.ok and r.error.startswith("isolation:") and item_type in r.error
    assert r.text == ""  # the answer after the tool call was never read
    assert runner.infra_kind(r.error) == "fatal"


def test_codex_turn_failed_usage_limit_is_fatal(tmp_path, codex_home):
    events = [CODEX_OK[0], {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, events, exit_code=1)), home_real=codex_home)
    r = s.complete("p")
    assert not r.ok and "usage limit" in r.error and runner.infra_kind(r.error) == "fatal"


def test_codex_reroute_is_recorded(tmp_path, codex_home):
    events = [{"type": "item.completed", "item": {"type": "error", "message": "model rerouted: a -> b-mini (load)"}},
              CODEX_OK[3]]
    r = CodexSubject("a", binary=str(fake_cli(tmp_path, events)), home_real=codex_home).complete("p")
    assert r.ok and r.model == "b-mini"


def test_codex_early_abort_kills(tmp_path, codex_home):
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, CODEX_OK)), home_real=codex_home)
    r = s.complete("p", on_text=lambda t: False)
    assert r.killed and r.ok


def test_codex_timeout(tmp_path, codex_home):
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, CODEX_OK, sleep=5)), home_real=codex_home)
    r = s.complete("p", timeout=0.5)
    assert r.timed_out and not r.ok and r.error == "timeout"


def test_missing_credentials_is_fatal(tmp_path):
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, CODEX_OK)), home_real=tmp_path / "nohome")
    r = s.complete("p")
    assert not r.ok and "log in first" in r.error and runner.infra_kind(r.error) == "fatal"
    assert not (tmp_path / "seen.json").exists()


# -- credentials ---------------------------------------------------------------------------------


def test_refreshed_token_is_copied_back(tmp_path, codex_home):
    s = CodexSubject("m", binary=str(fake_cli(tmp_path, CODEX_OK, rewrite={"auth.json": '{"tokens": "new"}'})),
                     home_real=codex_home)
    assert s.complete("p").ok
    assert (codex_home / "auth.json").read_text() == '{"tokens": "new"}'
    assert oct((codex_home / "auth.json").stat().st_mode & 0o777) == "0o600"
    assert (codex_home / "config.toml").read_text() == 'model = "leaky"\n'  # untouched


def test_concurrent_refresh_keeps_the_first(tmp_path, codex_home):
    h = CredHome(codex_home, ("auth.json",), ("auth.json",), ("auth.json",))
    (h.path / "auth.json").write_text("mine")
    (codex_home / "auth.json").write_text("someone else's newer token")
    assert h.sync_back() == []
    assert (codex_home / "auth.json").read_text() == "someone else's newer token"
    h.cleanup()
    assert not h.path.exists()


def test_emptied_credentials_are_not_copied_back(tmp_path, codex_home):
    h = CredHome(codex_home, ("auth.json",), ("auth.json",), ("auth.json",))
    (h.path / "auth.json").write_text("")
    assert h.sync_back() == [] and (codex_home / "auth.json").read_text() == '{"tokens": "old"}'
    h.cleanup()


# -- kimi ----------------------------------------------------------------------------------------

KIMI_OK = [
    {"role": "meta", "type": "system.version", "version": "2.1.1"},
    {"role": "assistant", "content": "add x0, x0, x1\nret"},
    {"role": "meta", "type": "session.resume_hint", "session_id": "s", "command": "kimi -S s", "content": ""},
]


def test_kimi_answer_and_isolation(tmp_path, kimi_home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "x")
    s = KimiSubject("kimi-code/k3", binary=str(fake_cli(tmp_path, KIMI_OK)), home_real=kimi_home, effort="high")
    r = s.complete("/goal do it")
    assert r.ok and r.text == "add x0, x0, x1\nret" and r.model == "kimi-code/k3"
    sn = seen(tmp_path)
    argv = sn["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json" and argv[argv.index("-m") + 1] == "kimi-code/k3"
    assert argv[-1] == "--prompt= /goal do it"  # never a slash command
    agent = Path(argv[argv.index("--agent-file") + 1]).name
    files = sn["home_files"]
    assert set(files) == {"credentials/kimi-code.json", "config.toml", agent}  # no old sessions
    assert "tools: []" in files[agent] and "${" not in files[agent] and SYSTEM_PROMPT in files[agent]
    env = sn["env"]
    assert env["KIMI_DISABLE_TELEMETRY"] == "1" and env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_MODEL_THINKING_EFFORT"] == "high" and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["KIMI_CODE_HOME"] != str(kimi_home)


@pytest.mark.parametrize("ev", [
    {"role": "assistant", "tool_calls": [{"type": "function", "id": "1", "function": {"name": "Shell"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "ls output"},
])
def test_kimi_tool_use_fails_closed(tmp_path, kimi_home, ev):
    s = KimiSubject("m", binary=str(fake_cli(tmp_path, [KIMI_OK[0], ev, KIMI_OK[1]])), home_real=kimi_home)
    r = s.complete("p")
    assert not r.ok and r.error.startswith("isolation:") and runner.infra_kind(r.error) == "fatal"


def test_kimi_error_exit(tmp_path, kimi_home):
    s = KimiSubject("m", binary=str(fake_cli(tmp_path, [KIMI_OK[0]], exit_code=1,
                                             stderr="provider.api_error: You exceeded your current quota\nSee log: x\n")),
                    home_real=kimi_home)
    r = s.complete("p")
    assert not r.ok and "exceeded your current quota" in r.error and "See log" not in r.error
    assert runner.infra_kind(r.error) == "fatal"


def test_describe_records_vendor_and_version(tmp_path, kimi_home):
    d = KimiSubject("m", binary=str(fake_cli(tmp_path, KIMI_OK)), home_real=kimi_home).describe()
    assert d == {"effort": "cli-default", "cli_version": "kimi 9.8.7", "vendor": "kimi"}


def test_fatal_regex_keeps_transient_errors_transient():
    assert runner.infra_kind("exit status 1: API Error: 529 overloaded") == "transient"
    assert runner.infra_kind("retry provider.rate_limit: slow down") == "transient"
    assert runner.infra_kind("Claude AI usage limit reached|1790000000") == "fatal"
    assert runner.infra_kind("the model said isolation: is a word") == "transient"


def test_cli_run_with_vendor_subjects(tmp_path, codex_home, monkeypatch):
    """`run --subjects codex:...` builds a CodexSubject through make_subject (dry-run: no calls)."""
    from bca.liveeval.__main__ import main

    rc = main(["run", "--run-dir", str(tmp_path / "r"), "--tasks", "sum_to", "--subjects", "codex:gpt-6-luna,opus",
               "--max-calls", "5", "--dry-run", "--effort", "high"])
    assert rc == 0
    assert os.path.basename(str(tmp_path / "r"))
