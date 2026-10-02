"""The opt-in hex path: the model writes the machine code itself (bca.liveeval.hexpath)."""

import pytest

from bca.emu import CODE_BASE
from bca.isa import ISAS
from bca.liveeval import BY_NAME, FakeSubject, attempt, build_prompt, run_cell
from bca.liveeval import hexpath
from bca.liveeval.prompts import EXTRA_PATHS, PATHS
from bca.liveeval.runner import plan_cells
from bca.stream import StreamAssembler

SUM = BY_NAME["sum_to"]

X86_SUM = """sum_to:
    xor eax, eax
    test rdi, rdi
    jle .Ldone
.Lloop:
    add rax, rdi
    dec rdi
    jnz .Lloop
.Ldone:
    ret
"""

A64_SUM = """sum_to:
    mov x1, x0
    mov x0, #0
.Lloop:
    cmp x1, #0
    b.le .Ldone
    add x0, x0, x1
    sub x1, x1, #1
    b .Lloop
.Ldone:
    ret
"""
SRC = {"x86_64": X86_SUM, "aarch64": A64_SUM}


def _hex(isa: str, src: str, comments: bool = True) -> str:
    """A correct hex answer: the canned assembly, assembled, one instruction per line."""
    sa = StreamAssembler(ISAS[isa])
    sa.feed(src)
    prog = sa.finish()
    assert prog.ok, prog.errors
    out = []
    for i in prog.instructions:
        word = i.data[::-1].hex() if isa == "aarch64" else i.data.hex(" ")
        out.append(f"{word}  // {i.text.strip()}" if comments else word)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("isa", ["x86_64", "aarch64"])
def test_correct_hex_passes_and_runs_the_bytes_as_written(isa, monkeypatch):
    text = _hex(isa, SRC[isa])
    seen = {}
    import bca.liveeval.pipelines as pl

    real = pl.check

    def spy(task, isa_name, code, **kw):
        seen["code"] = code
        return real(task, isa_name, code, **kw)

    monkeypatch.setattr(pl, "check", spy)
    a = attempt(FakeSubject([text]), "hex", isa, SUM, "p")
    assert a.ok, a.failure
    want, _ = hexpath.parse(isa, text)
    assert seen["code"] == want == a.code  # nothing between the model's hex and the executed bytes


def test_aarch64_words_are_stored_little_endian_and_0x_prefix_allowed():
    code, lines = hexpath.parse("aarch64", "0x8b010000\nd65f03c0 ; ret\n\n// a comment line\n")
    assert code == bytes.fromhex("0000018bc0035fd6")
    assert [(hl.offset, hl.line) for hl in lines] == [(0, 1), (4, 2)]


@pytest.mark.parametrize("isa,text,fragment,category", [
    ("aarch64", "8b01000\n", "exactly 8 hex digits", "encoding"),
    ("aarch64", "8b010000 d65f03c0\n", "not one 32-bit instruction word", "encoding"),
    ("aarch64", "1ac24020\n", "outside the prompt's instruction set", "encoding"),       # crc32b
    ("aarch64", "f8210002\n", "outside the prompt's instruction set", "encoding"),       # ldadd (LSE)
    ("x86_64", "48 01\n", "does not decode as one instruction", "encoding"),
    ("x86_64", "48 01 f0 c3\n", "does not decode as one instruction", "encoding"),
    ("x86_64", "c5 f9 fe c0\n", "AVX", "encoding"),                                       # vpaddd
    ("x86_64", "4801f0\n", "is not a byte", "encoding"),
    ("x86_64", "Here is the machine code for the function you asked for\n48 01 f0\n", "", "format"),
    ("aarch64", "// only a comment\n", "no instruction", "format"),
])
def test_malformed_or_forbidden_machine_code_is_rejected(isa, text, fragment, category):
    a = attempt(FakeSubject([text]), "hex", isa, SUM, "p")
    assert not a.ok and a.category == category and a.code is None
    assert fragment in a.failure


def test_undecodable_aarch64_word_is_an_encoding_error():
    # 0x00000000 is udf #0 (decodes); an unallocated encoding does not
    a = attempt(FakeSubject(["d4a00000\n"]), "hex", "aarch64", SUM, "p")
    assert a.category == "encoding" and "does not decode" in a.failure


def test_valid_armv8_0_word_capstone_misses_is_not_refused():
    # seen live (claude-sonnet-4-5, sort_lines hex app): 384568e7 is `ldtrb w7, [x7, #86]`, a valid ARMv8.0
    # unprivileged load GNU as accepts; capstone does not decode it, so GNU objdump's text is used instead
    import struct

    word = struct.pack("<I", 0x384568E7)
    assert hexpath.describe("aarch64", word, CODE_BASE) == "ldtrb w7, [x7, #86]"
    _, lines = hexpath.parse("aarch64", "384568e7\nd65f03c0\n")
    hexpath.check("aarch64", lines, CODE_BASE)  # no HexError
    # undefined words and a byte-swapped word (b4000460 written as 600400b4) are still refused,
    # and an ARMv8.1 atomic is still outside the prompt's instruction set
    for text, frag in (("d71d001d\n", "does not decode"), ("600400b4\n", "does not decode"),
                       ("386e600f\n", "outside the prompt's instruction set")):
        with pytest.raises(hexpath.HexError, match=frag):
            hexpath.check("aarch64", hexpath.parse("aarch64", text)[1], CODE_BASE)


def test_runtime_failure_feedback_lists_what_the_bytes_mean(tmp_path):
    good = _hex("aarch64", A64_SUM)
    wrong = good.replace("8b010000", "cb010000", 1)  # add x0, x0, x1 -> sub x0, x0, x1
    assert wrong != good
    subj = FakeSubject([wrong, good])
    rec = run_cell(subj, SUM, "hex", "aarch64", tmp_path)
    assert rec["pass_at_1"] is False and rec["pass_final"] is True
    repair = subj.prompts[1]
    assert "<previous>" in repair and "cb010000" in repair
    assert "decodes as" in repair and "sub x0, x0, x1" in repair  # capstone's view of the model's bytes


def test_prompt_states_the_format_and_isa_level():
    for isa in ("x86_64", "aarch64"):
        p = build_prompt("hex", isa, SUM)
        assert "there is no assembler" in p and "exactly as you write them" in p
        assert ("8b010000" in p) == (isa == "aarch64") and ("48 01 f0" in p) == (isa == "x86_64")
        assert "Instruction set:" in p and SUM.spec in p


def test_hex_is_opt_in_only():
    assert "hex" in EXTRA_PATHS and "hex" not in PATHS
    default = plan_cells([SUM], None, ["x86_64"])
    assert [p for _t, _b, p, _i in default] == list(PATHS)
    explicit = plan_cells([SUM], ["hex"], ["x86_64", "aarch64"])
    assert [(p, i) for _t, _b, p, i in explicit] == [("hex", "x86_64"), ("hex", "aarch64")]


def test_locator_points_at_the_models_line():
    text = "d2800000 // mov x0, #0\n" "f9400000 // ldr x0, [x0]  (faults)\nd65f03c0\n"
    a = attempt(FakeSubject([text]), "hex", "aarch64", SUM, "p")
    assert not a.ok and "line 2" in a.failure
