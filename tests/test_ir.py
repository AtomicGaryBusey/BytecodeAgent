"""Portable IR (QBE IL, C via clang/tcc, WAT via Cranelift) -> raw bytes -> Unicorn differential."""

import statistics
import time

import pytest

from bca.difftest import buffer_differential, compile_c_reference, differential, int_inputs
from bca.emu import EmulationError, run_function
from bca.ir import (
    IRCompileError,
    compile_ir,
    cranelift_disassembly,
    find_tool,
    lower,
    run_wat,
    wat_differential,
)
from bca.ir.kernels import BUFFER_KERNELS, SCALAR_KERNELS, buffer_inputs, kernel_inputs
from bca.ir.wasm import host_isa
from bca.isa import AARCH64, X86_64

ISAS = [X86_64, AARCH64]
ISA_IDS = [i.name for i in ISAS]


def _need(tool):
    if find_tool(tool) is None:
        pytest.skip(f"{tool} not installed (see bca/ir/tools.py for lookup paths)")


def _need_for(lang, compiler, isa):
    if lang == "qbe":
        _need("qbe")
        _need("llvm-mc")
    elif lang == "c":
        _need("clang" if compiler == "clang" else f"tcc-{isa.name}")
    elif lang == "wat" and isa.name != host_isa():
        _need("wasmtime")


# (lang, compiler) pairs that yield raw standalone function bytes.
BACKENDS = [("qbe", None), ("c", "clang"), ("c", "tcc")]
SCALAR_BACKENDS = BACKENDS + [("wat", None)]


def _source(kernel, lang):
    return getattr(kernel, lang)


def _fmt(report):
    return [(m.args, m.expected, m.actual, m.error) for m in report.mismatches[:5]]


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("lang,compiler", SCALAR_BACKENDS,
                         ids=[f"{lang}-{c}" if c else lang for lang, c in SCALAR_BACKENDS])
@pytest.mark.parametrize("kernel", SCALAR_KERNELS, ids=[k.name for k in SCALAR_KERNELS])
def test_scalar_kernel(kernel, lang, compiler, isa):
    _need_for(lang, compiler, isa)
    code = lower(_source(kernel, lang), lang, isa, kernel.name, compiler=compiler)
    ref = compile_c_reference(kernel.c_ref, kernel.name, kernel.nargs)
    inputs = kernel_inputs(kernel)
    assert len(inputs) >= 200
    report = differential(isa, code, ref, inputs)
    assert report.ok, _fmt(report)


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("lang,compiler", BACKENDS,
                         ids=[f"{lang}-{c}" if c else lang for lang, c in BACKENDS])
@pytest.mark.parametrize("kernel", BUFFER_KERNELS, ids=[k.name for k in BUFFER_KERNELS])
def test_buffer_kernel(kernel, lang, compiler, isa):
    _need_for(lang, compiler, isa)
    code = lower(_source(kernel, lang), lang, isa, kernel.name, compiler=compiler)
    bufs = buffer_inputs()
    assert len(bufs) >= 200
    report = buffer_differential(isa, code, kernel.c_ref, kernel.name, bufs)
    assert report.ok, _fmt(report)


@pytest.mark.parametrize("kernel", SCALAR_KERNELS, ids=[k.name for k in SCALAR_KERNELS])
def test_wat_native_wasmtime(kernel):
    """Reference-equivalence of the WAT itself, executed natively by wasmtime on the host."""
    ref = compile_c_reference(kernel.c_ref, kernel.name, kernel.nargs)
    report = wat_differential(kernel.wat, kernel.name, ref, kernel_inputs(kernel))
    assert report.cases >= 200
    assert report.ok, _fmt(report)


def test_run_wat_api():
    assert run_wat(SCALAR_KERNELS[1].wat, "gcd", (48, 18)) == 6
    assert run_wat(SCALAR_KERNELS[0].wat, "sum", (-5,)) == 0


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_cranelift_disassembly(isa):
    _need_for("wat", None, isa)
    listing = cranelift_disassembly(SCALAR_KERNELS[0].wat, isa, "sum")
    assert listing and any(("ret" in line) for line in listing)


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_cranelift_vmctx_access_faults_under_unicorn(isa):
    """Cranelift code that touches the instance (here: linear memory) cannot run standalone."""
    _need_for("wat", None, isa)
    wat = """(module (memory 1)
      (func $peek (export "peek") (param $a i64) (result i64)
        (i64.load (i32.wrap_i64 (local.get $a)))))"""
    assert run_wat(wat, "peek", (0,)) == 0  # fine natively
    code = lower(wat, "wat", isa, "peek")
    with pytest.raises(EmulationError):
        run_function(isa, code, [0])


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("assembler", ["gas", "llvm-mc"])
def test_qbe_assemblers_agree(assembler, isa):
    """QBE output assembles identically-behaving code with GNU as and llvm-mc."""
    _need_for("qbe", None, isa)
    _need("llvm-mc" if assembler == "llvm-mc" else f"gas-{isa.name}")
    k = SCALAR_KERNELS[1]
    low = compile_ir(k.qbe, "qbe", isa, k.name, compiler=assembler)
    assert low.compiler == f"qbe+{assembler}"
    ref = compile_c_reference(k.c_ref, k.name, k.nargs)
    report = differential(isa, low.code, ref, kernel_inputs(k))
    assert report.ok, _fmt(report)


SWITCH_C = """int64_t sw(int64_t x) {
    switch (x) {
    case 0: return 11; case 1: return 23; case 2: return -5; case 3: return 99;
    case 4: return 7; case 5: return 1000; case 6: return 64; case 7: return -1;
    default: return x * 3;
    }
}"""


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("compiler", ["clang", "tcc"])
def test_c_switch_jump_table(compiler, isa):
    """Dense switches become jump tables in .rodata: exercises the relocation linker."""
    _need_for("c", compiler, isa)
    low = compile_ir(SWITCH_C, "c", isa, "sw", compiler=compiler)
    ref = compile_c_reference(SWITCH_C, "sw", 1)
    report = differential(isa, low.code, ref, [(i,) for i in range(-3, 12)] + list(int_inputs(1, 50)))
    assert report.ok, _fmt(report)
    if compiler == "clang":
        assert len(low.sections) > 1, "expected a jump table section to be linked"


# --- malformed / unsupported IR produces clear errors -------------------------------------


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_malformed_qbe(isa):
    _need_for("qbe", None, isa)
    bad = "export function l $f(l %a) {\n@start\n    %x =l addd %a, 1\n    ret %x\n}\n"
    with pytest.raises(IRCompileError) as exc:
        lower(bad, "qbe", isa, "f")
    assert exc.value.stage == "qbe"
    assert "addd" in str(exc.value) or "invalid" in str(exc.value).lower()


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_qbe_type_error(isa):
    _need_for("qbe", None, isa)
    bad = "export function l $f(l %a) {\n@start\n    %x =w add %a, 1\n    storel %x, %x\n    ret %x\n}\n"
    with pytest.raises(IRCompileError) as exc:
        lower(bad, "qbe", isa, "f")
    assert exc.value.stage == "qbe"


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("compiler", ["clang", "tcc"])
def test_malformed_c(compiler, isa):
    _need_for("c", compiler, isa)
    with pytest.raises(IRCompileError) as exc:
        lower("int64_t f(int64_t a) { return a + ; }", "c", isa, "f", compiler=compiler)
    assert exc.value.stage == compiler
    assert "input.c:1" in str(exc.value)


def test_malformed_wat():
    with pytest.raises(IRCompileError) as exc:
        run_wat("(module (func $f (result i64) i64.add))", "f")
    assert exc.value.stage == "wasmtime"
    with pytest.raises(IRCompileError):
        lower("(module (func $f (param i64) (result i64) (i64.frobnicate)))", "wat",
              host_isa(), "f")


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("compiler", ["clang", "tcc"])
def test_external_call_rejected_as_relocation(compiler, isa):
    _need_for("c", compiler, isa)
    src = "int64_t g(int64_t);\nint64_t f(int64_t a) { return g(a) + 1; }"
    with pytest.raises(IRCompileError) as exc:
        lower(src, "c", isa, "f", compiler=compiler)
    assert exc.value.stage == "extract"
    assert "relocation" in str(exc.value) and "undefined symbol 'g'" in str(exc.value)


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_qbe_read_only_data_is_linked(isa):
    """Data referenced by the function is laid out after the code and relocated."""
    _need_for("qbe", None, isa)
    src = """data $k = { l 40, l 2 }
export function l $f(l %i) {
@s
    %o =l mul %i, 8
    %a =l add $k, %o
    %v =l loadl %a
    %w =l loadl $k
    %r =l add %v, %w
    ret %r
}
"""
    low = compile_ir(src, "qbe", isa, "f")
    assert low.relocations >= 1 and len(low.sections) == 2
    assert run_function(isa, low.code, [1]).ret == 42


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_missing_function(isa):
    _need_for("qbe", None, isa)
    with pytest.raises(IRCompileError, match="not defined"):
        lower(SCALAR_KERNELS[0].qbe, "qbe", isa, "nope")


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("lang,compiler", BACKENDS,
                         ids=[f"{lang}-{c}" if c else lang for lang, c in BACKENDS])
def test_local_helper_before_entry(lang, compiler, isa):
    """A helper emitted before the requested function stays reachable; a jump to the entry
    is prepended so execution still starts at offset 0."""
    _need_for(lang, compiler, isa)
    qbe = """
function l $sq(l %x) {
@s
    %y =l mul %x, %x
    ret %y
}
export function l $f(l %a) {
@s
    %r =l call $sq(l %a)
    %r =l add %r, 1
    ret %r
}
"""
    c = """static int64_t __attribute__((noinline)) sq(int64_t x) { return x * x; }
    int64_t pad(int64_t x) { return x; }
    int64_t f(int64_t a) { return (int64_t)((uint64_t)sq(a) + 1); }"""
    low = compile_ir(qbe if lang == "qbe" else c, lang, isa, "f", compiler=compiler)
    assert low.entry_offset > 0
    assert run_function(isa, low.code, [7]).ret == 50
    assert run_function(isa, low.code, [-3]).ret == 10


# --- latency ------------------------------------------------------------------------------


def measure_latency(repeats: int = 5) -> dict[str, float]:
    """Median end-to-end lowering latency (ms) per backend/ISA on the gcd kernel."""
    k = SCALAR_KERNELS[1]
    out = {}
    for isa in ISAS:
        for lang, compiler in SCALAR_BACKENDS:
            try:
                _need_for(lang, compiler, isa)
            except pytest.skip.Exception:
                continue
            times = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                lower(_source(k, lang), lang, isa, k.name, compiler=compiler)
                times.append((time.perf_counter() - t0) * 1e3)
            out[f"{lang}{'-' + compiler if compiler else ''}/{isa.name}"] = statistics.median(times)
    return out


def test_compile_latency(capsys):
    lat = measure_latency(repeats=3)
    with capsys.disabled():
        print("\nIR lowering latency (median ms, gcd):")
        for key, ms in lat.items():
            print(f"  {key:22s} {ms:8.1f}")
    assert lat
    assert all(ms < 5000 for ms in lat.values())


# --- regressions: linker entry trampoline -------------------------------------------------

OVERALIGNED_C = """
int64_t g0(int64_t a){return a*7;}
__attribute__((aligned(64))) int64_t g(int64_t a, int64_t b) { return a - b; }
__attribute__((aligned(%d))) int64_t f(int64_t a, int64_t b) { return a + b; }
"""


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("compiler", ["clang", "tcc"])
@pytest.mark.parametrize("align", [128, 256, 4096])
def test_trampoline_with_overaligned_section(align, compiler, isa):
    """.text alignment > 64 with the entry not first: the trampoline must still land on f
    (it used to jump into g, silently computing a-b)."""
    _need_for("c", compiler, isa)
    from bca.ir.elf import trampoline_target

    low = compile_ir(OVERALIGNED_C % align, "c", isa, "f", compiler=compiler)
    assert low.entry_offset > 0 and low.entry_offset % min(align, 128) == 0
    assert trampoline_target(isa.name, low.code) == low.entry_offset
    ref = compile_c_reference("int64_t f(int64_t a, int64_t b){return a+b;}", "f", 2)
    report = differential(isa, low.code, ref, int_inputs(2, 100))
    assert report.ok, _fmt(report)


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_linker_self_check_rejects_bad_trampoline(isa, monkeypatch):
    """A trampoline that misses the resolved symbol raises instead of returning bad bytes."""
    _need_for("c", "clang", isa)
    import bca.ir.elf as elf

    real = elf._entry_trampoline
    monkeypatch.setattr(elf, "_entry_trampoline",
                        lambda name, target, pad: real(name, target - 64, pad))
    with pytest.raises(IRCompileError, match="internal link error") as exc:
        compile_ir(OVERALIGNED_C % 128, "c", isa, "f")
    assert exc.value.stage == "extract"


# --- regressions: position dependence ---------------------------------------------------

PAGE_ASM = {
    "aarch64": """.text
.globl f
f:  adrp x1, k
    add x1, x1, :lo12:k
    ldr x0, [x1, x0, lsl #3]
    ret
.section .rodata
.p2align 3
k:  .quad 11, 22, 33
""",
    "aarch64-ldst": """.text
.globl f
f:  adrp x1, k
    ldr x0, [x1, :lo12:k]
    ret
.section .rodata
.p2align 3
k:  .quad 77
""",
    "x86_64": """.intel_syntax noprefix
.text
.globl f
f:  lea rax, [rip + k]
    mov rax, [rax + rdi*8]
    ret
.section .rodata
.p2align 3
k:  .quad 11, 22, 33
""",
}


@pytest.mark.parametrize("variant", sorted(PAGE_ASM))
def test_position_dependent_classification(variant):
    """position_dependent is True exactly when the linked bytes depend on the load address
    (AArch64 ADRP/:lo12: included); pc-relative x86 code is position independent."""
    _need("llvm-mc")
    from bca.emu import CODE_BASE
    from bca.ir import assemble, extract_function

    isa = variant.split("-")[0]
    obj = assemble(PAGE_ASM[variant], isa, assembler="llvm-mc")
    at_base = extract_function(obj, isa, "f", base=CODE_BASE)
    moved = extract_function(obj, isa, "f", base=CODE_BASE + 0x800)
    assert at_base.position_dependent == (at_base.code != moved.code)
    assert at_base.position_dependent == isa.startswith("aarch64")
    want = 77 if variant == "aarch64-ldst" else 22
    arg = 0 if variant == "aarch64-ldst" else 1
    assert run_function({"x86_64": X86_64, "aarch64": AARCH64}[isa], at_base.code,
                        [arg]).ret == want


# --- regressions: WAT lookup, arity, i32 convention, instance isolation -----------------

SWAPPED_WAT = """(module
  (func $add (export "sub") (param i64 i64) (result i64) (i64.sub (local.get 0) (local.get 1)))
  (func $sub (export "add") (param i64 i64) (result i64) (i64.add (local.get 0) (local.get 1)))
  (func (export "mul") (param i64 i64) (result i64) (i64.mul (local.get 0) (local.get 1))))"""


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_wat_lookup_is_by_export_name(isa):
    """run_wat and lower_wat resolve the same function: by export name, not `$name`;
    unnamed functions work; a `$name` that is not an export is not found."""
    _need_for("wat", None, isa)
    for export, a, b in [("add", 5, 3), ("sub", 5, 3), ("mul", 6, 7)]:
        native = run_wat(SWAPPED_WAT, export, [a, b])
        code = lower(SWAPPED_WAT, "wat", isa, export)
        assert run_function(isa, code, [a, b]).ret == native
    assert run_wat(SWAPPED_WAT, "add", [5, 3]) == 8
    with pytest.raises(IRCompileError, match="no exported function"):
        lower("(module (func $hidden (param i64) (result i64) (local.get 0)))", "wat", isa,
              "hidden")


def test_wat_export_of_import_rejected():
    src = """(module (import "m" "h" (func $h (param i64) (result i64)))
      (func (export "f") (param i64) (result i64) (i64.add (local.get 0) (i64.const 1)))
      (export "h" (func $h)))"""
    with pytest.raises(IRCompileError, match="imported"):
        lower(src, "wat", host_isa(), "h")
    # function indices count imports: "f" is function 1 and must still resolve correctly
    assert run_function(AARCH64 if host_isa() == "aarch64" else X86_64,
                        lower(src, "wat", host_isa(), "f"), [41]).ret == 42


def _wide_wat(n, ty="i64"):
    body = f"({ty}.const 0)"
    for i in range(n):
        body = f"({ty}.add ({ty}.mul {body} ({ty}.const 1000003)) (local.get {i}))"
    params = " ".join([ty] * n)
    return f'(module (func (export "f") (param {params}) (result {ty}) {body}))'


def _wide_ref(n, ty="i64"):
    t = "int64_t" if ty == "i64" else "int32_t"
    u = "uint64_t" if ty == "i64" else "uint32_t"
    params = ", ".join(f"int64_t a{i}" for i in range(n))
    expr = "0"
    for i in range(n):
        expr = f"({u})(({u})({expr}) * 1000003u + ({u})a{i})"
    return f"int64_t f({params}) {{ return ({t})({expr}); }}"


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
@pytest.mark.parametrize("ty", ["i64", "i32"])
def test_wat_many_params(ty, isa):
    """Up to the host's argument-register count (SysV 6, AAPCS64 8): params beyond wasmtime's
    four register params go on the stack; results agree with native wasmtime and C."""
    _need_for("wat", None, isa)
    for n in range(1, len(isa.args) + 1):
        src = _wide_wat(n, ty)
        ref = compile_c_reference(_wide_ref(n, ty), "f", n)
        inputs = int_inputs(n, 40)
        code = lower(src, "wat", isa, "f")
        report = differential(isa, code, ref, inputs)
        assert report.ok, (n, _fmt(report))
        wrep = wat_differential(src, "f", ref, inputs)
        assert wrep.ok, (n, _fmt(wrep))


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_wat_too_many_params_rejected(isa):
    _need_for("wat", None, isa)
    with pytest.raises(IRCompileError, match="at most"):
        lower(_wide_wat(len(isa.args) + 1), "wat", isa, "f")


@pytest.mark.parametrize("isa", ISAS, ids=ISA_IDS)
def test_wat_i32_result_convention(isa):
    """i32 results are sign-extended to 64 bits by both run_wat and the lowered code; i32
    params take the low 32 bits of the argument."""
    _need_for("wat", None, isa)
    src = """(module (func (export "f") (param i32 i32) (result i32) (i32.sub (local.get 0) (local.get 1)))
      (func (export "u") (param i32) (result i32) (local.get 0)))"""
    code = lower(src, "wat", isa, "f")
    ucode = lower(src, "wat", isa, "u")
    for a, b in [(1, 2), (0, 0), (-5, 3), (2**31 - 1, -1), (0x1_0000_0005, 2), (-(2**63), 1)]:
        native = run_wat(src, "f", [a, b])
        assert run_function(isa, code, [a, b]).ret == native
        assert native == (((a - b) & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000 & (2**64 - 1)
    assert run_wat(src, "f", [1, 2]) == 2**64 - 1
    for v in [0x80000000, 0xFFFFFFFF_7FFFFFFF, 3]:
        assert run_wat(src, "u", [v]) == run_function(isa, ucode, [v]).ret


def test_wat_rejects_non_integer_signature():
    src = '(module (func (export "f") (param f64) (result f64) (local.get 0)))'
    with pytest.raises(IRCompileError, match="i32/i64"):
        run_wat(src, "f", [1])
    with pytest.raises(IRCompileError, match="i32/i64"):
        lower(src, "wat", host_isa(), "f")


def test_run_wat_fresh_instance_per_call():
    """A function mutating a global returns the same value for the same input every call."""
    src = """(module (global $g (mut i64) (i64.const 0))
      (func (export "bump") (param i64) (result i64)
        (global.set $g (i64.add (global.get $g) (local.get 0)))
        (global.get $g)))"""
    assert [run_wat(src, "bump", [5]) for _ in range(3)] == [5, 5, 5]
    ref = lambda x: x  # noqa: E731
    assert wat_differential(src, "bump", ref, [(7,), (7,), (1,)]).ok
