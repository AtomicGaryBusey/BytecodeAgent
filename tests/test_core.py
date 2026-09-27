from bca.difftest import compile_c_reference, differential, int_inputs
from bca.emu import run_function
from bca.isa import AARCH64, X86_64

ADD_REF = "int64_t add3(int64_t a, int64_t b, int64_t c) { return a + b * c; }"


def assemble(isa, text):
    encoding, _ = isa.assembler().asm(text, 0x100000)
    return bytes(encoding)


def test_x86_function_call_and_trace():
    code = assemble(X86_64, "mov rax, rsi; imul rax, rdx; add rax, rdi; ret")
    res = run_function(X86_64, code, [5, 6, 7], trace=True)
    assert res.ret == 47
    assert res.steps[0].changed["rax"] == 6
    assert len(res.steps) == 4


def test_aarch64_function_call():
    code = assemble(AARCH64, "madd x0, x1, x2, x0; ret")
    assert run_function(AARCH64, code, [5, 6, 7]).ret == 47


def test_differential_catches_bug():
    ref = compile_c_reference(ADD_REF, "add3", 3)
    good = assemble(AARCH64, "madd x0, x1, x2, x0; ret")
    bad = assemble(AARCH64, "add x0, x0, x1; ret")
    assert differential(AARCH64, good, ref, int_inputs(3, 50)).ok
    assert not differential(AARCH64, bad, ref, int_inputs(3, 50)).ok
