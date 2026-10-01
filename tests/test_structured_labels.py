"""Label names that are register names (x86-64) and keyword/register-shaped names (AArch64).

x86-64: the jump schema the model sees says register names are not usable as labels, and GNU as agrees
(Intel syntax: `jmp cl` is an operand-size error, not a branch to a label `cl`); the encoder refuses the
label definition itself with that reason. AArch64 branches take labels only, so any name works, as in
GNU as. The fixtures are claude-sonnet-5.5's recorded answers (eval/runs/copilot-r2-20260930-043506)
that defined labels `cl` / `ch`."""

import re
from pathlib import Path

import pytest

from bca.liveeval import FakeSubject, attempt, build_prompt
from bca.liveeval.tasks_long import TASKS as LONG_TASKS
from bca.structured import AssemblyError, assemble

FIX = Path(__file__).parent / "fixtures" / "structured"
BY_NAME = {t.name: t for t in LONG_TASKS}


@pytest.mark.parametrize("label", ["cl", "ch", "rax", "R12", "eax", "r8b"])
def test_x86_register_named_label_is_refused_at_its_definition(label):
    with pytest.raises(AssemblyError, match=rf"label name '{label}' is a register name"):
        assemble("x86_64", [{"label": label}, {"op": "nop"}, {"op": "ret"}])


def test_x86_indirect_jump_through_a_register_still_works():
    p = assemble("x86_64", [{"op": "mov", "dst": "rax", "src": ["rdi"]}, {"op": "jmp", "target": "rax"}])
    assert p.code.hex() == "4889f8ffe0"  # mov rax, rdi; jmp rax


@pytest.mark.parametrize("label", ["x0", "lt", "sp", "lsl", "w3"])
def test_a64_register_or_keyword_named_label_is_a_label(label):
    p = assemble("aarch64", [{"label": label}, {"op": "nop"}, {"op": "b", "target": label}])
    assert p.code.hex()[8:] == "ffffff17"  # b . - 4: back to the label


@pytest.mark.parametrize("name,task,label", [
    ("sonnet_mat_pow_label_cl", "mat_pow", "cl"),
    ("sonnet_base64_encode_label_ch", "base64_encode", "ch"),
])
def test_recorded_answers_fail_only_on_the_label_name(name, task, label):
    text = (FIX / f"{name}.x86_64.json").read_text()
    t = BY_NAME[task]
    a = attempt(FakeSubject([text]), "structured", "x86_64", t, build_prompt("structured", "x86_64", t))
    assert not a.ok and a.category == "syntax" and f"label name '{label}' is a register name" in a.failure
    # the same answer with only the label renamed (definition and jump target; register operands stay) passes
    fixed = re.sub(rf'"(label|target)":\s*"{label}"', rf'"\1":"{label}_lbl"', text)
    assert fixed != text
    a = attempt(FakeSubject([fixed]), "structured", "x86_64", t, build_prompt("structured", "x86_64", t))
    assert a.ok, a.failure
