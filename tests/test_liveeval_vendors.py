"""Vendor CLI subjects (bca.liveeval.vendors) against fake CLIs: parsing, isolation, credentials."""

import os

import pytest

from bca.liveeval import runner
from bca.liveeval.registry import expected_model
from bca.liveeval.subject import ClaudeSubject
from bca.liveeval.vendors import (
    CodexSubject,
    CredHome,
    KimiSubject,
    all_efforts,
    make_subject,
    split_spec,
)


@pytest.fixture
def codex_home(tmp_path):
    h = tmp_path / "codex-home"
    h.mkdir()
    (h / "auth.json").write_text('{"tokens": "old"}')
    (h / "config.toml").write_text('model = "leaky"\n')
    (h / "AGENTS.md").write_text("SECRET USER INSTRUCTIONS")
    return h


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
    with pytest.raises(ValueError, match="codex: unknown effort"):
        make_subject("codex:m", timeout=5, effort="minimal")
    with pytest.raises(ValueError, match="kimi: unknown effort"):
        make_subject("kimi:m", timeout=5, effort="medium")
    with pytest.raises(ValueError, match="claude: unknown effort"):
        make_subject("opus", timeout=5, effort="ultra")
    with pytest.raises(ValueError, match="model is required"):
        make_subject("codex:", timeout=5)
    assert {"ultra", "max", "low"} <= set(all_efforts())


# -- credentials ---------------------------------------------------------------------------------


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
