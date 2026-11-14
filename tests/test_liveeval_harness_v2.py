"""The opt-in `harness_v2` variant (bca/liveeval/harness_v2.py): each of its four changes appears
with it, and without it every prompt, schema, failure text and acceptance decision is byte-identical
to the baseline harness the recorded runs used. No model calls: FakeSubjects only.

The baseline pin is tests/fixtures/harness_v2/baseline_digest.json: sha256 digests, per group, of
`baseline_corpus()` (every first-attempt and repair prompt of the four function batteries on every
path and ISA under every pre-existing variant, both ISAs' schemas and tool definitions, and a corpus
of failure texts: the canned wrong answers, do-nothing bodies for every buffer/memory task, the new
AArch64 ops, QBE errors). It was computed BEFORE harness_v2 existed; regenerate it only for a
deliberate baseline change: `python tests/test_liveeval_harness_v2.py --write-digest`.
"""

import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from bca.difftest import MemCase, Mismatch, Ptr
from bca.liveeval import adversarial as adv
from bca.liveeval import harness_v2 as v2
from bca.liveeval import registry
from bca.liveeval.pipelines import _buffer_detail, _mem_detail, attempt
from bca.liveeval.prompts import build_prompt
from bca.liveeval.runner import run_cell
from bca.liveeval.subject import FakeSubject
from bca.liveeval.tasks import BY_NAME
from bca.structured import AssemblyError, Session, assemble, input_schemas, tool_definitions

ROOT = Path(__file__).resolve().parent.parent
DIGEST = ROOT / "tests" / "fixtures" / "harness_v2" / "baseline_digest.json"
BATTERIES = ("round1", "round2", "long", "adversarial")
PATHS = ("stream", "structured", "ir", "hex")
ISAS = ("x86_64", "aarch64")
NEW_A64_OPS = ("sxtb", "sxth", "sxtw", "uxtb", "uxth", "adc", "adcs", "sbc", "sbcs")
QBE_BAD = (
    "export function l $sum_to(l %n) {\n@start\n\t%x =l add %n, 0xff\n\tret %x\n}\n",
    "export function l $sum_to(l %n) {\n@start\n\t%x =l frob %n\n\tret %x\n}\n",
    "export function l $sum_to(l %n) {\n@start\n\t%x =w add %n, 1\n\tret %x\n}\n",
    "export function l $sum_to(l %n) {\n@start\n\tjmp @nowhere\n}\n",
)


# -- the baseline corpus and its digest -------------------------------------------------------------

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def baseline_corpus() -> dict[str, str]:
    """key -> text, for everything a model sees or a pipeline decides under the baseline harness."""
    sys.path.insert(0, str(ROOT))
    import tests.liveeval_solutions as s2
    import tests.liveeval_solutions_adversarial as sa

    out: dict[str, str] = {}
    variants = {k: f for k, f in adv.VARIANTS.items() if k != v2.NAME}
    tasks = {}
    for b in BATTERIES:
        for t in registry.load_battery(b):
            tasks[t.name] = t
            for p in PATHS:
                if p in t.na_paths:
                    continue
                for i in ISAS:
                    for prev in (None, ("prev code\n", "3 of 10 test cases failed. First failures:\n  x")):
                        base = build_prompt(p, i, t, prev)
                        for vn, vf in variants.items():
                            k = f"prompt/{b}/{t.name}/{p}/{i}/{'rep' if prev else 'first'}/{vn}"
                            try:
                                out[k] = vf(base, p, i, t)
                            except Exception as e:  # noqa: BLE001 - e.g. legacy_style has no hex style
                                out[k] = f"ERR {type(e).__name__}"
    for i in ISAS:
        for strict in (False, True):
            out[f"schemas/{i}/{strict}"] = json.dumps(input_schemas(i, strict=strict), sort_keys=True)
            for fl in ("anthropic", "openai"):
                out[f"tools/{i}/{strict}/{fl}"] = json.dumps(tool_definitions(i, flavor=fl, strict=strict),
                                                             sort_keys=True)

    def run(key, tname, path, isa, text):
        a = attempt(FakeSubject([text]), path, isa, tasks[tname], "p")
        out[f"fail/{key}"] = json.dumps({"ok": a.ok, "cat": a.category, "failure": a.failure})

    n = 0
    for corpus in (s2.WRONG, sa.BAITED, getattr(sa, "CONTROLS", [])):
        for item in corpus:
            tname, path, isa, text = item[:4]
            if path == "structured" and not text.lstrip().startswith("{"):
                text = json.dumps(s2.to_structured(isa, text))
            run(f"corpus/{n}", tname, path, isa, text)
            n += 1
    for t in tasks.values():
        if not (t.buffer or t.mem):
            continue
        for isa in ISAS:
            run(f"noop/{t.name}/{isa}", t.name, "stream", isa, f"{t.name}:\n    ret\n")
            z = "xor eax, eax" if isa == "x86_64" else "mov x0, #0"
            run(f"zero/{t.name}/{isa}", t.name, "stream", isa, f"{t.name}:\n    {z}\n    ret\n")
    for op in (*NEW_A64_OPS, "frob"):
        for isa in ISAS:
            ins = {"instructions": [{"op": op, "dst": "x0" if isa == "aarch64" else "rax",
                                     "src": ["w1", "x2"] if op.startswith(("adc", "sbc")) else ["w1"]},
                                    {"op": "ret"}]}
            run(f"newop/{op}/{isa}", "sum_to", "structured", isa, json.dumps(ins))
    for isa in ISAS:
        for src in QBE_BAD:
            run(f"ir/{_sha(src)[:8]}/{isa}", "sum_to", "ir", isa, src)
    return out


def _group(key: str) -> str:
    parts = key.split("/")
    if parts[0] == "prompt":  # prompt/<battery>/<task>/<path>/<isa>/...
        return f"prompt/{parts[1]}/{parts[3]}/{parts[4]}"
    return "/".join(parts[:2])


def digest(corpus: dict[str, str]) -> dict[str, str]:
    groups: dict[str, list[str]] = {}
    for k in sorted(corpus):
        groups.setdefault(_group(k), []).append(_sha(k) + _sha(corpus[k]))
    return {g: _sha("".join(v)) for g, v in sorted(groups.items())}


@pytest.fixture(scope="module")
def corpus():
    return baseline_corpus()


def test_baseline_is_byte_identical_to_before_harness_v2(corpus):
    want = json.loads(DIGEST.read_text())
    got = digest(corpus)
    assert sorted(got) == sorted(want)
    changed = [g for g in want if got[g] != want[g]]
    assert not changed, f"baseline prompts / schemas / failure texts changed in: {changed}"
    assert sum(k.startswith("prompt/") for k in corpus) == 5400 and sum(k.startswith("fail/") for k in corpus) == 187


def test_baseline_still_rejects_the_new_ops(corpus):
    for op in NEW_A64_OPS:
        r = json.loads(corpus[f"fail/newop/{op}/aarch64"])
        assert r["cat"] == "syntax" and f"unknown aarch64 op '{op}'" in r["failure"]
        with pytest.raises(AssemblyError):
            assemble("aarch64", [{"op": op, "dst": "x0", "src": ["w1"]}])
        assert not Session("aarch64").emit({"op": op, "dst": "x0", "src": ["x1", "x2"]})["ok"]
    assert not set(NEW_A64_OPS) & _ops(input_schemas("aarch64")["program"])
    for i in ISAS:
        s = json.dumps(input_schemas(i))
        assert '"$defs": {"reg":' in s and "register_name" not in s


def _ops(schema) -> set:
    return {o for alt in schema["properties"]["instructions"]["items"]["anyOf"]
            for o in alt["properties"].get("op", {}).get("enum", [])}


def test_variant_is_opt_in_and_registered():
    assert v2.NAME in adv.VARIANTS and adv.VARIANT_INFO[v2.NAME]["opt_in"]
    assert v2.NAME not in adv.ALL_VARIANTS_DEFAULT and not adv.VARIANT_INFO[v2.NAME].get("max_attempts")
    assert not adv.VARIANT_INFO[v2.NAME].get("apps_ok")
    assert registry.load_variants([v2.NAME])[v2.NAME] is v2.harness_v2
    assert v2.active() is False  # nothing outside run_cell / enabled() turns it on


# -- with harness_v2 ------------------------------------------------------------------------------

SUM = BY_NAME["sum_to"]


def test_v2_prompt_changes_only_the_structured_schema():
    for path in PATHS:
        for isa in ISAS:
            base = build_prompt(path, isa, SUM)
            out = v2.harness_v2(base, path, isa, SUM)
            if path != "structured":
                assert out == base
                continue
            assert '"$defs":{"register_name":' in out and "#/$defs/register_name" in out
            assert "#/$defs/reg\"" not in out and '"$defs":{"reg":' not in out
            # everything outside the schema is the baseline prompt
            old = json.dumps(input_schemas(isa)["program"], separators=(",", ":"))
            new = json.dumps(input_schemas(isa, harness_v2=True)["program"], separators=(",", ":"))
            assert out.replace(new, old) == base
            if isa == "x86_64":
                assert "#/$defs/register64_name" in out and "reg64" not in out
            else:
                for op in NEW_A64_OPS:
                    assert f'"{op}"' in out
            # the shifted-register operand keeps its "reg" key (that is the encoder's input format)
            assert ('{"reg":{"$ref":"#/$defs/register_name"}' in out) == (isa == "aarch64")


def test_v2_schema_ops_are_accepted_by_the_v2_encoder():
    new = _ops(input_schemas("aarch64", harness_v2=True)["program"])
    assert new - _ops(input_schemas("aarch64")["program"]) == set(NEW_A64_OPS)
    assert _ops(input_schemas("x86_64", harness_v2=True)["program"]) == _ops(input_schemas("x86_64")["program"])
    assert json.dumps(input_schemas("x86_64", harness_v2=True)).count("register_name") > 0


# Little-endian words checked against GNU as (aarch64-linux-gnu-as + objdump, binutils 2.42).
A64_V2 = [
    ("sxtb", "x0", ["w1"], 0x93401C20), ("sxtb", "w0", ["w1"], 0x13001C20),
    ("sxth", "x3", ["w4"], 0x93403C83), ("sxth", "w3", ["w4"], 0x13003C83),
    ("sxtw", "x5", ["w6"], 0x93407CC5), ("uxtb", "w7", ["w8"], 0x53001D07), ("uxth", "w9", ["w10"], 0x53003D49),
    ("adc", "x0", ["x1", "x2"], 0x9A020020), ("adc", "w0", ["w1", "w2"], 0x1A020020),
    ("adcs", "x3", ["x4", "x5"], 0xBA050083), ("sbc", "x6", ["x7", "x8"], 0xDA0800E6),
    ("sbcs", "w9", ["w10", "w11"], 0x7A0B0149), ("sxtb", "x0", ["wzr"], 0x93401FE0),
    ("adc", "x0", ["xzr", "x30"], 0x9A1E03E0),
]  # fmt: skip


@pytest.mark.parametrize("op,dst,src,word", A64_V2)
def test_v2_a64_encodings(op, dst, src, word):
    prog = assemble("aarch64", [{"op": op, "dst": dst, "src": src}], harness_v2=True)
    assert prog.code == word.to_bytes(4, "little")
    assert Session("aarch64", harness_v2=True).emit({"op": op, "dst": dst, "src": src})["ok"]


@pytest.mark.parametrize("op,dst,src,msg", [
    ("sxtw", "w0", ["w1"], "sxtw writes an x register"),
    ("sxtb", "x0", ["x1"], "takes a w source register (e.g. w1)"),
    ("uxtb", "x0", ["w1"], "uxtb writes a w register"),
    ("adc", "x0", ["x1", "w2"], "use all x or all w registers"),
    ("adc", "x0", ["x1", {"imm": 1}], 'expected a register name like "x1"'),
    ("adc", "sp", ["x1", "x2"], "not allowed here"),
])
def test_v2_a64_rejections(op, dst, src, msg):
    with pytest.raises(AssemblyError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        assemble("aarch64", [{"op": op, "dst": dst, "src": src}], harness_v2=True)


def test_v2_ops_run_correctly_on_the_structured_path():
    # sign-extend the low byte of x0 (sxtb) and add with carry: x0 = sxtb(w0) + 0 + C after cmn 0 (C=0).
    prog = {"instructions": [{"op": "sxtb", "dst": "x1", "src": ["w0"]}, {"op": "cmn", "src": ["x0", {"imm": 0}]},
                             {"op": "adc", "dst": "x0", "src": ["x1", "xzr"]}, {"op": "ret"}]}
    task = dataclasses.replace(SUM, name="sx8", ref_name=None, signature="int64_t sx8(int64_t x)",
                               c_ref="int64_t sx8(int64_t x) { return (int8_t)x; }", inputs=((0,), (127,), (128,), (255,), (-1,), (0x1234,)))
    text = json.dumps(prog)
    assert attempt(FakeSubject([text]), "structured", "aarch64", task, "p").category == "syntax"  # baseline
    with v2.enabled():
        a = attempt(FakeSubject([text]), "structured", "aarch64", task, "p")
    assert a.ok, a.failure


def test_v2_qbe_error_echoes_the_source_line():
    src = QBE_BAD[1]
    base = attempt(FakeSubject([src]), "ir", "x86_64", SUM, "p").failure
    with v2.enabled():
        a = attempt(FakeSubject([src]), "ir", "x86_64", SUM, "p")
    assert a.category == "compile"
    assert a.failure == base + "\nLine 3 of your code: %x =l frob %n"
    assert v2.qbe_error_lines("gas", "{standard input}:3: Error: x", src) == ""  # refers to QBE's output
    assert v2.qbe_error_lines("qbe", "qbe:-:99: oops", src) == ""


def _buf_mismatch(exp, act):
    pack = lambda xs: b"".join((x & (2**64 - 1)).to_bytes(8, "little") for x in xs)  # noqa: E731
    return Mismatch((tuple(exp),), 0, 0, None, "wrong_result", expected_data=pack(exp), actual_data=pack(act))


def test_v2_buffer_difference_names_the_first_differing_index():
    exp = list(range(20))
    act = list(range(17)) + [99, 18, 19]
    m = _buf_mismatch(exp, act)
    task = BY_NAME["bubble_sort"]
    base = _buffer_detail(task, m)
    assert "17" not in base.split("got")[1]  # beyond the 12 elements shown: invisible at baseline
    with v2.enabled():
        assert _buffer_detail(task, m) == base + "; first difference at a[17]: expected 17, got 99"
    assert _buffer_detail(task, m) == base


def test_v2_memory_window_difference_is_named_in_the_argument():
    task = dataclasses.replace(SUM, name="f", signature="int64_t f(const int64_t *a, int64_t n, int64_t *out)",
                               mem=True, nargs=3, mem_elem=8)
    exp = bytes(16) + (5).to_bytes(8, "little") + (6).to_bytes(8, "little")
    act = bytes(16) + (5).to_bytes(8, "little") + (7).to_bytes(8, "little")
    case = MemCase(exp, (Ptr(0), 2, Ptr(16)), ((24, 32),), None, "f(...)")
    m = Mismatch((0,), 0, 0, None, "wrong_result", expected_data=exp, actual_data=act, case=case)
    base = _mem_detail(task, m)
    with v2.enabled():
        assert _mem_detail(task, m) == base + "; first difference at out[1]: expected 6, got 7"
        named = dataclasses.replace(m, case=dataclasses.replace(case, writable=((16, 32),), writable_names=("res",)))
        assert _mem_detail(task, named).endswith("; first difference at element 1 of that range: expected 6, got 7")


def test_v2_end_to_end_buffer_failure_from_a_real_run():
    # a do-nothing bubble_sort that returns 0: the buffer is left unsorted
    text = "bubble_sort:\n    xor eax, eax\n    ret\n"
    base = attempt(FakeSubject([text]), "stream", "x86_64", BY_NAME["bubble_sort"], "p").failure
    with v2.enabled():
        new = attempt(FakeSubject([text]), "stream", "x86_64", BY_NAME["bubble_sort"], "p").failure
    assert "first difference at a[0]: expected -9223372036854775808, got 0" in new
    assert new.replace("; first difference at a[", "\0").split("\0")[0] in base


def test_runner_sets_v2_per_cell_and_resets_it(tmp_path):
    """run_cell turns v2 on only for the harness_v2 variant, and off again for the next cell (pool
    threads are reused), so a baseline cell after a v2 cell gets the baseline repair text."""
    def cell(variant, d):
        subj = FakeSubject([QBE_BAD[1], QBE_BAD[1]])
        fn = registry.load_variants([variant])[variant]
        run_cell(subj, SUM, "ir", "x86_64", d, max_attempts=2, variant=fn, meta={"variant": variant})
        return subj.prompts[1]

    rep_v2 = cell("harness_v2", tmp_path / "a")
    rep_base = cell("baseline", tmp_path / "b")
    assert "Line 3 of your code: %x =l frob %n" in rep_v2
    assert "Line 3 of your code" not in rep_base
    assert rep_base == build_prompt("ir", "x86_64", SUM, (QBE_BAD[1], "Compilation failed at stage qbe:\n"
                                                                      "qbe:-:3: unknown keyword frob"))
    assert v2.active() is False


if __name__ == "__main__" and "--write-digest" in sys.argv:
    os.chdir(ROOT)
    DIGEST.parent.mkdir(parents=True, exist_ok=True)
    DIGEST.write_text(json.dumps(digest(baseline_corpus()), indent=1, sort_keys=True) + "\n")
    print(f"wrote {DIGEST}")
