"""vendor_vibe against a fake `vibe` binary that speaks vibe 3.1.2's headless protocol (one JSON line per finished
entry on stdout, export.json in --output-dir): the private home's config, the answer, denied tool attempts counted
and never fatal, a tool that ran is fatal, and the effective config is checked."""

import json
import stat

import pytest

from bca.liveeval.vendor_vibe import MODELS, VibeSubject

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a == ["--version"]:
    print("vibe 3.1.2"); sys.exit(0)
arg = lambda k: a[a.index(k) + 1]
home = os.environ["VIBE_HOME"]
mode = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode")).read().strip()
prompt = open(arg("--prompt-file")).read()
cfg = open(os.path.join(home, "config.toml")).read()
out = arg("--output-dir"); os.makedirs(out, exist_ok=True)
def emit(e): print(json.dumps(e), flush=True)
emit({"type": "message", "role": "user", "content": [{"type": "text", "text": prompt if mode != "badprompt" else "x"}]})
emit({"type": "reasoning", "text": "thinking"})
status = "completed" if mode == "toolran" else "failed"
err = None if mode == "toolran" else {"code": "tool_denied", "message": "denied"}
if mode in ("denied", "toolran"):
    emit({"type": "effect", "title": "file_system.bash", "detail": {"toolName": "file_system.bash"},
          "state": {"status": status, "error": err}})
if mode == "turnlimit":
    print("Stopped: turn_limit", file=sys.stderr); sys.exit(3)
if mode == "lookup":
    emit({"type": "effect", "title": "search_tool_functions", "detail": {"toolName": "search_tool_functions"},
          "state": {"status": "completed", "output": {"mode": "best_match", "toolCount": 5}}})
emit({"type": "message", "role": "assistant", "content": [{"type": "text", "text": "draft"}]})
emit({"type": "message", "role": "assistant", "content": [{"type": "text", "text": "mov rax, 1\nret"}]})
disabled = ["*", "ask_user_question", "cron"] if 'disabled_tools = ["*"]' in cfg else []
json.dump({"outcome": "finished", "usage": {"input_tokens": 4000, "output_tokens": 50, "cached_input_tokens": 0},
           "cost_usd": 0.0, "steps": 2, "config": {"disabled_tools": disabled, "active_model": arg("--model"),
           "include_project_context": "include_project_context = false" not in cfg, "enable_mcp": False,
           "enable_connectors": False, "enable_subagents": False, "bypass_tool_permissions": False}},
          open(os.path.join(out, "export.json"), "w"))
'''


@pytest.fixture
def fake(tmp_path):
    exe = tmp_path / "vibe"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    mode = tmp_path / "mode"

    def make(m="plain", model="mistral-large-4-0-3", **kw):
        mode.write_text(m)
        return VibeSubject(model, binary=str(exe), timeout=30, **kw)
    return make


def test_answer_is_the_last_assistant_message_and_config_is_private(fake, monkeypatch):
    seen = {}
    orig = VibeSubject.prepare_home

    def spy(self, home):
        orig(self, home)
        seen["cfg"] = (home / "config.toml").read_text()
        seen["sys"] = (home / "system.txt").read_text()
    monkeypatch.setattr(VibeSubject, "prepare_home", spy)
    s = fake()
    r = s.complete("write sum_to")
    assert r.ok and r.text == "mov rax, 1\nret" and r.model == "mistral-large-4-0"
    assert 'disabled_tools = ["*"]' in seen["cfg"] and 'alias = "mistral-large-4-0-3"' in seen["cfg"]
    assert "include_project_context = false" in seen["cfg"] and "[experiments]\nenable = false" in seen["cfg"]
    assert 'thinking = "high"' in seen["cfg"] and "expert low-level programmer" in seen["sys"]
    assert r.usage["input_tokens"] == 4000 and "tool_attempts" not in r.usage


def test_denied_tool_attempts_are_counted_not_fatal(fake):
    r = fake("denied").complete("p")
    assert r.ok and r.usage["tool_attempts"] == 1 and r.usage["tools_attempted"] == ["file_system.bash"]
    assert any("denied" in n for n in r.notes)


def test_a_tool_that_ran_or_a_changed_prompt_is_fatal(fake):
    r = fake("toolran").complete("p")
    assert not r.ok and r.error.startswith("isolation:") and "a tool ran" in r.error
    r = fake("badprompt").complete("p")
    assert not r.ok and "not the task prompt" in r.error


def test_effort_is_the_thinking_level_within_the_catalog(fake):
    assert fake(effort="low").thinking == "low" and fake().thinking == "high"
    with pytest.raises(ValueError, match="thinking levels"):
        fake(effort="max")  # Mistral Large 4: low / medium / high
    with pytest.raises(ValueError, match="unknown model alias"):
        VibeSubject("devstral-2", binary="vibe")
    assert set(MODELS) == {"mistral-large-4-0-3", "glm-5-3", "zai-glm-5-3-flash", "mistral-medium-3.5"}


def test_the_harness_catalog_lookup_is_recorded_not_fatal(fake):
    r = fake("lookup").complete("p")
    assert r.ok and r.usage["harness_lookups"] == 1 and "tool_attempts" not in r.usage


def test_the_turn_limit_is_a_scored_timeout_not_a_retry(fake):
    from bca.liveeval.pipelines import call_failure_category
    r = fake("turnlimit").complete("p")
    assert r.timed_out and not r.ok and r.error == "timeout" and call_failure_category(r) == "subject_timeout"
