"""Checks that need no model call and no docker: the scenarios agree with the specification's reference
model, the prompt builds, and (when docker and a built editor are present) the editor passes them."""

import shutil
from pathlib import Path

import pytest

from demo.editor.prompt import build_prompt
from demo.editor.scenarios import SCENARIOS, reference


@pytest.mark.parametrize("s", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenario_expectation_matches_the_reference_model(s):
    (fname, want), = s["expect"].items()
    got, quit_ = reference(s["files"].get(fname), s["keys"])
    assert quit_ and got == want


def test_prompt_builds_with_the_spec_and_repair_section():
    p = build_prompt()
    assert "openat = 56" in p and "TIOCGWINSZ = 0x5413" in p and "Program `edit`" in p
    assert "<previous>" in build_prompt(("code", "it failed"))


EDIT = Path(__file__).parent / "edit"


@pytest.mark.skipif(not EDIT.exists() or shutil.which("docker") is None, reason="needs docker and demo/editor/edit")
def test_the_committed_editor_passes_every_scenario_in_the_sandbox():
    from demo.editor.sandbox import run_scenarios

    results = run_scenarios(EDIT)
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed[0]
