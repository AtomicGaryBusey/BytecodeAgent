"""The app battery: task definitions, and canned solutions run through the whole-program pipeline.

Every canned CORRECT solution (tests/fixtures/apps/<task>.<isa>.s) must pass every test of its task on both
ISAs through assemble_program -> link_program -> run_program, with stdin delivered whole and in small chunks;
every BROKEN variant (<task>.<isa>.brokenN.s) must fail at least one test, proving the tests discriminate.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from bca.apps import APP_TASKS, AppTask, AppTest, task_by_name

FIXTURES = Path(__file__).parent / "fixtures" / "apps"
NAMES = ("wc", "rev_lines", "hexdump", "sort_lines", "rpn", "base64", "bf", "json_lint")
ISAS = ("aarch64", "x86_64")


# ---------------------------------------------------------------------------------- task definitions

def test_registry():
    assert tuple(t.name for t in APP_TASKS) == NAMES
    for t in APP_TASKS:
        assert isinstance(t, AppTask)
        assert task_by_name(t.name) is t
        assert t.bss_size >= 1 << 16 and t.max_insns >= 10_000_000
    with pytest.raises(KeyError):
        task_by_name("nope")


@pytest.mark.parametrize("name", NAMES)
def test_tests_are_deterministic_and_from_the_reference(name):
    task = task_by_name(name)
    tests = task.tests()
    assert tests == task.tests()  # seeded generators
    assert 15 <= len(tests) <= 40
    labels = [t.label for t in tests]
    assert len(set(labels)) == len(labels)
    assert any(t.stdin == b"" for t in tests), "every task tests empty input"
    assert any(len(t.stdin) == 1 for t in tests), "every task tests a single byte"
    assert any(len(t.stdin) > 4096 for t in tests), "every task crosses a 4 KiB read boundary"
    for t in tests:
        assert isinstance(t, AppTest)
        assert isinstance(t.stdin, bytes) and isinstance(t.stdout, bytes)
        assert (t.stdout, t.status) == task.reference(t.stdin), t.label
        assert 0 <= t.status <= 255
        assert len(t.stdin) <= 65536 and len(t.stdout) <= 1 << 20


@pytest.mark.parametrize("name", NAMES)
def test_spec_is_isa_neutral_and_complete(name):
    spec = task_by_name(name).spec
    low = spec.lower()
    for word in ("x86", "aarch64", "arm64", "rax", "svc", "syscall number"):
        assert word not in low, word
    assert f"`{name}`" in spec
    assert "exit" in low and "status" in low
    assert "may return fewer bytes" in low  # chunked input is part of the contract


def test_reference_spot_checks():
    ref = lambda n, s: task_by_name(n).reference(s)  # noqa: E731
    assert ref("wc", b"hello world\nbye") == (b"1 3 15\n", 0)
    assert ref("wc", b"") == (b"0 0 0\n", 0)
    assert ref("rev_lines", b"abc\n\nxy") == (b"cba\n\nyx\n", 0)
    assert ref("hexdump", b"") == (b"00000000\n", 0)
    assert ref("hexdump", b"0123456789abcdefXYZ\n")[0] == (
        b"00000000  30 31 32 33 34 35 36 37  38 39 61 62 63 64 65 66  |0123456789abcdef|\n"
        b"00000010  58 59 5a 0a                                       |XYZ.|\n"
        b"00000014\n")
    assert ref("sort_lines", b"pear\napple\nPear\n\napple") == (b"\nPear\napple\napple\npear\n", 0)
    assert ref("rpn", b"3 4 +\n-7 2 /\n1 +\n\n5 1 2 + 4 * + 3 -") == (b"7\n-3\nerror\nerror\n14\n", 0)
    assert ref("rpn", b"922337203685477580 10 * 7 + 1 +\n-9223372036854775807\n") == (
        b"-9223372036854775808\nerror\n", 0)
    assert ref("base64", b"Man") == (b"TWFu\n", 0)
    assert ref("base64", b"M") == (b"TQ==\n", 0)
    assert ref("base64", b"") == (b"", 0)
    assert ref("bf", b"+[") == (b"", 1)
    assert ref("bf", b"+,.!") == (b"\x00", 0)
    for doc, want in [(b"[1, 2]", b"ok\n"), (b"", b"error at 0\n"), (b"[1,]", b"error at 3\n"),
                      (b'{"a" 1}', b"error at 5\n"), (b"01", b"error at 1\n"), (b"tru", b"error at 3\n"),
                      (b"trux", b"error at 3\n"), (b"[1] x", b"error at 4\n"), (b'"a\tb"', b"error at 2\n"),
                      (b"-", b"error at 1\n"), (b"1.e5", b"error at 2\n")]:
        assert ref("json_lint", doc) == (want, 0 if want == b"ok\n" else 1), doc


# ---------------------------------------------------------------------------- canned solutions

try:
    from bca.image import assemble_program, link_program
    from bca.process import run_program
    _PIPELINE = None
except ImportError as exc:  # builder A's modules not present yet
    _PIPELINE = f"whole-program pipeline unavailable: {exc}"

needs_pipeline = pytest.mark.skipif(_PIPELINE is not None, reason=_PIPELINE or "")


@functools.lru_cache(maxsize=None)
def _image(path: Path):
    task, isa = path.name.split(".")[:2]
    try:
        obj = assemble_program(path.read_text(), isa)
    except (FileNotFoundError, PermissionError) as exc:
        pytest.skip(f"GNU as for {isa} not available: {exc}")
    return link_program(obj, isa, bss_size_limit=task_by_name(task).bss_size)


def _chunkings(t: AppTest) -> tuple[int | None, ...]:
    # whole stdin, 7-byte reads, and 1-byte reads for the smaller inputs
    return (None, 7, 1) if len(t.stdin) <= 4096 else (None, 7)


def _failures(path: Path, *, first_only: bool = False) -> list[str]:
    task = task_by_name(path.name.split(".")[0])
    image = _image(path)
    out = []
    for t in task.tests():
        for chunk in _chunkings(t):
            r = run_program(image, t.stdin, max_insns=task.max_insns, read_chunk=chunk)
            if r.fault is not None or r.stdout != t.stdout or r.status != t.status:
                what = repr(r.fault) if r.fault else f"status {r.status} (want {t.status})"
                if r.stdout != t.stdout:
                    n = min(len(r.stdout), len(t.stdout))
                    i = next((k for k in range(n) if r.stdout[k] != t.stdout[k]), n)
                    what += f"; stdout differs at byte {i}: {r.stdout[i:i + 16]!r} vs {t.stdout[i:i + 16]!r}"
                out.append(f"{t.label} (read_chunk={chunk}): {what}")
                if first_only:
                    return out
    return out


@needs_pipeline
@pytest.mark.parametrize("isa", ISAS)
@pytest.mark.parametrize("name", NAMES)
def test_canned_correct_solution_passes(name, isa):
    path = FIXTURES / f"{name}.{isa}.s"
    assert path.exists(), path
    fails = _failures(path)
    assert not fails, "\n".join(fails[:10])


@needs_pipeline
@pytest.mark.parametrize("isa", ISAS)
@pytest.mark.parametrize("name", NAMES)
def test_canned_broken_variants_fail(name, isa):
    broken = sorted(FIXTURES.glob(f"{name}.{isa}.broken*.s"))
    assert len(broken) >= 2, f"need at least 2 broken variants for {name}/{isa}"
    for path in broken:
        assert _failures(path, first_only=True), f"{path.name} passes every test: the tests do not discriminate"


def test_fixture_set_is_complete():
    for name in NAMES:
        for isa in ISAS:
            assert (FIXTURES / f"{name}.{isa}.s").exists()
            assert len(list(FIXTURES.glob(f"{name}.{isa}.broken*.s"))) >= 2
    for path in FIXTURES.glob("*.s"):
        src = path.read_text()
        assert ".intel_syntax" not in src  # assemble_program adds it for x86-64
        assert "_start" in src
