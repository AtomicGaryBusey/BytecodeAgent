"""`python -m bca.liveeval models`: the per-vendor catalog parsers (fed output captured live 2026-11-07), the
benchmarked-subject scan and the report."""

import json

import pytest

from bca.liveeval import models as M

AGY_OUT = """Fetching available models...
gemini-4-argon-medium\tGemini 4 Argon (Medium)
gemini-4-argon-high\tGemini 4 Argon (High)
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
"""
GROK_IN = """You are logged in with grok.com.

Default model: grok-4.7

Available models:
  * grok-4.7 (default)
  - grok-4.7-build-fast
  - grok-4.6
"""
GROK_OUT = """You are not authenticated.

Default model: grok-4.6

Available models:
  * grok-4.6 (default)
  - grok-4.5
"""
KIMI_TOML = """
default_model = "kimi-code/k3"
[providers."managed:kimi-code"]
type = "kimi"
[models."kimi-code/k3"]
provider = "managed:kimi-code"
display_name = "K3"
support_efforts = [ "low", "high", "max" ]
default_effort = "high"
[models."kimi-code/k3.1-pro-highspeed"]
provider = "managed:kimi-code"
display_name = "K3.1 Pro Highspeed 2x Usage Cost"
"""
CODEX_JSON = json.dumps({"models": [
    {"slug": "gpt-6.1-terra", "display_name": "GPT-6.1 Terra", "visibility": "list",
     "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
    {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list"},
    {"slug": "codex-auto-review", "visibility": "hide"},
]})
COPILOT = [{"id": "auto", "name": "Auto"},
           {"id": "claude-haiku-5.5", "name": "Claude Haiku 5.5", "policy": {"state": "enabled"},
            "supportedReasoningEfforts": ["low", "max"]},
           {"id": "grok-4.7", "name": "Grok 4.7"},
           {"id": "mai-code-1.1-flash", "policy": {"state": "enabled"}}]


def test_parse_agy_skips_the_banner():
    assert [m.id for m in M.parse_agy(AGY_OUT)] == ["gemini-4-argon-medium", "gemini-4-argon-high",
                                                    "gemini-3.8-flash-high"]


def test_parse_grok_signed_in_and_out():
    models, signed = M.parse_grok(GROK_IN)
    assert signed == "yes" and [m.id for m in models] == ["grok-4.7", "grok-4.7-build-fast", "grok-4.6"]
    assert models[0].note == "default"
    assert M.parse_grok(GROK_OUT)[1] == "no"


def test_parse_kimi_config():
    ms = M.parse_kimi(KIMI_TOML)
    assert [m.id for m in ms] == ["kimi-code/k3", "kimi-code/k3.1-pro-highspeed"]
    assert ms[0].efforts == ["low", "high", "max"] and ms[0].note == "default effort high"


def test_parse_codex_marks_hidden():
    ms = M.parse_codex(CODEX_JSON)
    assert [(m.id, m.note) for m in ms] == [("gpt-6.1-terra", ""), ("gpt-6-astra", ""), ("codex-auto-review", "hidden")]
    assert ms[0].efforts == ["low", "high"]


def test_parse_copilot_policy_notes():
    ms = {m.id: m.note for m in M.parse_copilot(COPILOT)}
    assert ms == {"auto": "router", "claude-haiku-5.5": "", "grok-4.7": "policy not set", "mai-code-1.1-flash": ""}


def _group(root, sub, model_arg):
    d = root / "run1" / sub / "baseline"
    d.mkdir(parents=True)
    (d / "group.json").write_text(json.dumps({"model_arg": model_arg}))


def test_benchmarked_resolves_aliases_and_vendors(tmp_path):
    _group(tmp_path, "haiku", "haiku")
    _group(tmp_path, "codex_gpt-6-astra", "codex:gpt-6-astra")
    _group(tmp_path, "kimi_kimi-code_k3", "kimi:kimi-code/k3")
    done = M.benchmarked(tmp_path)
    # the `haiku` alias is Haiku 4.5: a run of it does not count as Haiku 5.5
    assert done == {"claude": {M.MODEL_ALIASES["haiku"]}, "codex": {"gpt-6-astra"}, "kimi": {"kimi-code/k3"}}


def test_render_lists_unbenchmarked_but_not_hidden_or_router():
    reports = [M.VendorReport("codex", "0.167.0", ("0.167.0",), models=M.parse_codex(CODEX_JSON), signed_in="yes"),
               M.VendorReport("copilot", "1.0.89", ("1.0.89",), models=M.parse_copilot(COPILOT)),
               M.VendorReport("grok", "1.0.42", ("1.0.41",), models=M.parse_grok(GROK_IN)[0])]
    out = M.render(reports, {"codex": {"gpt-6-astra"}, "copilot": {"mai-code-1.1-flash"}, "grok": set()})
    assert "[ran] gpt-6-astra" in out and "[NEW] gpt-6.1-terra" in out
    assert "not yet benchmarked: gpt-6.1-terra\n" in out  # codex-auto-review is hidden
    assert "not yet benchmarked: claude-haiku-5.5, grok-4.7\n" in out  # auto is the router
    assert "cli 1.0.42  NOT AUDITED (audited: 1.0.41)" in out


def test_unknown_vendor_is_refused():
    with pytest.raises(ValueError, match="unknown vendor"):
        M.main(["openai"])
