"""Prompts: one fixed structure per path, filled in with the ISA, ABI and task spec.

The structure is identical for every task: target + ABI, output format rules, the task, and
(on repair rounds) the previous attempt plus the exact failure.
"""

from __future__ import annotations

import json

from bca.liveeval.tasks import Task
from bca.structured import input_schemas, tool_definitions

PATHS = ("stream", "structured", "ir")
ISAS = ("x86_64", "aarch64")

ABI = {
    "x86_64": (
        "Target: x86-64, Linux System V ABI. Integer/pointer arguments in rdi, rsi, rdx, rcx, "
        "r8, r9; 64-bit result in rax. Callee-saved: rbx, rbp, r12, r13, r14, r15 (restore "
        "them if you use them); all other registers may be clobbered. rsp is 16-byte aligned "
        "before the call, so it is 8 mod 16 at entry; it must be back to its entry value when "
        "you return with ret. Never write to the caller's frame (at or above the return address)."
    ),
    "aarch64": (
        "Target: AArch64 (ARMv8-A), Linux AAPCS64. Integer/pointer arguments in x0-x7; 64-bit "
        "result in x0. Callee-saved: x19-x28, x29 (frame pointer) and d8-d15 (restore them if "
        "you use them); x9-x15 are free scratch registers. sp must stay 16-byte aligned and be "
        "back to its entry value when you return with ret (return address in x30)."
    ),
}

STREAM_RULES = {
    "x86_64": (
        "Output format: GNU assembler syntax, Intel flavour without register prefixes (as with "
        "`.intel_syntax noprefix`), e.g. `mov rax, qword ptr [rdi + rcx*8]`. Output ONLY the "
        "function's instructions and labels, one per line, starting with the label `{name}:` "
        "(the entry point). Labels are `name:` (letters, digits, _ and ., e.g. `.Lloop:`); "
        "every branch target must be a label, never a number. Comments start with `#`; do not "
        "use `;`. No directives of any kind (no .section/.align/.byte/.quad/.p2align), no data, "
        "no calls to other functions. Each line under 120 characters. No Markdown fences, no "
        "prose before or after the code."
    ),
    "aarch64": (
        "Output format: GNU assembler syntax for AArch64, e.g. `ldr x2, [x0, x1, lsl #3]`. "
        "Output ONLY the function's instructions and labels, one per line, starting with the "
        "label `{name}:` (the entry point). Labels are `name:` (letters, digits, _ and ., e.g. "
        "`.Lloop:`); every branch target (b, b.cond, cbz, cbnz, tbz, tbnz, bl, adr) must be a "
        "label, never a number. Comments start with `//`; do not use `;`. No directives of any "
        "kind (no .section/.align/.word/.quad), no literal pools (`ldr x0, =...`), no data, no "
        "calls to other functions. Each line under 120 characters. No Markdown fences, no "
        "prose before or after the code."
    ),
}

# The instruction-set level the emulator executes correctly (probed against Unicorn: see
# tests/test_stream.py::test_x86_isa_level_matches_the_emulator). Appended to the stream rules
# for round >= 3 tasks only, so round-1/round-2 prompts (and their committed results) are unchanged.
ISA_LEVEL = {
    "x86_64": (
        "Instruction set: x86-64 with SSE up to SSE4.2 (including SSSE3, SSE4.1 and crc32), POPCNT, "
        "LZCNT, BMI1/BMI2 (andn, bextr, blsi, blsr, tzcnt, bzhi, mulx, pdep, pext, rorx, sarx, shlx, "
        "shrx), MOVBE, AES-NI and PCLMULQDQ. Not supported (rejected): AVX, AVX2, AVX-512, FMA, F16C "
        "and every other VEX/EVEX vector instruction, ADX (adcx/adox; use adc) and SHA."
    ),
    "aarch64": (
        "Instruction set: ARMv8.0-A with floating point and Advanced SIMD (NEON). Not supported "
        "(rejected): CRC32, LSE atomics, dot product, half-precision, crypto (AES/SHA/PMULL) and SVE."
    ),
}

IR_RULES = (
    "Output format: QBE intermediate language (the IL of the QBE compiler backend, "
    "c9x.me/compile). Write exactly one function, `export function l ${name}(...)`, taking "
    "every argument as `l` (64-bit; pointers are `l` too) and returning `l`. QBE will compile "
    "it for the target above, so you do not handle registers or the ABI yourself. Remember: "
    "every block starts with an `@label`; temporaries are `%name`; comparisons such as "
    "`csltl`/`ceql` produce a `w` value; `jnz` tests a `w`; loads/stores are `loadl`/`storel`. "
    "No data definitions, no calls, no other functions. Output only the QBE code: no "
    "Markdown fences, no prose."
)


# Round 2 adds the ABI facts its tasks depend on (round-1 prompts are unchanged).
ABI_EXTRA = {
    "x86_64": (
        "More ABI facts: arguments beyond the sixth are passed on the stack; at entry the "
        "return address is at [rsp], the 7th argument at [rsp+8], the 8th at [rsp+16]. A call "
        "clobbers the caller-saved registers rax, rcx, rdx, rsi, rdi, r8-r11, all xmm "
        "registers and the flags, so keep values that must survive a call in callee-saved "
        "registers (saved/restored by you) or in your own stack frame. rsp must be 16-byte "
        "aligned at every call instruction (at entry rsp is 8 mod 16, so an odd number of "
        "8-byte pushes, or `sub rsp, 8` plus any multiple of 16, re-aligns it). Anything "
        "below rsp may be overwritten by a callee. The direction flag must be clear at calls "
        "and on return."
    ),
    "aarch64": (
        "More ABI facts: arguments beyond the eighth are passed on the stack (the 9th at [sp] "
        "at entry). A call (bl/blr) overwrites x30, so save x30 (e.g. stp x29, x30, [sp, "
        "#-16]!) before calling and restore it before ret. A call clobbers the caller-saved "
        "registers x0-x18, x30, v0-v7 and v16-v31 (and the upper halves of v8-v15) and the "
        "flags, so keep values that must survive a call in callee-saved registers x19-x28 "
        "(saved/restored by you) or in your own stack frame. sp must be 16-byte aligned at "
        "every call (and whenever it is used as a base register). Anything below sp may be "
        "overwritten by a callee."
    ),
}

# Round-1 wording, replaced for round-2 tasks that call functions.
_NO_CALLS = "no calls to other functions"
_STREAM_CALLS = {
    "self": {"x86_64": "calls only to your own entry label (`call {name}`) for the recursion",
             "aarch64": "calls only to your own entry label (`bl {name}`) for the recursion"},
    "callback": {"x86_64": "the only call is through the function-pointer argument (e.g. `call r12`)",
                 "aarch64": "the only call is through the function-pointer argument (e.g. `blr x19`)"},
}
_IR_NO_CALLS = "No data definitions, no calls, no other functions."
_IR_CALLS = {
    "self": "No data definitions and no other functions; the only calls are to ${name} itself.",
    "callback": "No data definitions and no other functions; the only calls are through the "
                "function-pointer argument.",
}
_STRUCTURED_CALLS = {
    "self": {"x86_64": 'The recursive call is {"op": "call", "target": "<entry label>"}; put a label '
                       'as the first entry so the call can target it.',
             "aarch64": 'The recursive call is {"op": "bl", "target": "<entry label>"}; put a label as '
                        'the first entry so the call can target it.'},
    "callback": {"x86_64": 'Call through a register by giving the 64-bit register name as the target: '
                           '{"op": "call", "target": "r12"}.',
                 "aarch64": 'Call through a register with {"op": "blr", "src": ["x19"]}.'},
}


def _structured_rules(isa: str) -> str:
    tool = tool_definitions(isa)[0]
    schema = json.dumps(input_schemas(isa)["program"], separators=(",", ":"))
    return (
        "Output format: a single JSON object {\"instructions\": [...]} that is valid input for "
        "this tool (the instructions are encoded directly into machine code, no assembler):\n"
        f"Tool name: {tool['name']}\nTool description: {tool['description']}\n"
        f"Input JSON Schema: {schema}\n"
        "The first instruction is the entry point; labels are {\"label\": \"name\"} entries and "
        "branch targets are label names. Output only the JSON object: no Markdown fences, no "
        "prose."
    )


def rules(path: str, isa: str, task: Task) -> str:
    if path == "stream":
        text = STREAM_RULES[isa]
        if task.calls:
            text = text.replace(_NO_CALLS, _STREAM_CALLS[task.calls][isa])
        if task.round >= 3:
            text += " " + ISA_LEVEL[isa]
        return text.replace("{name}", task.name)
    if path == "structured":
        text = _structured_rules(isa)
        if task.calls:
            text += "\n" + _STRUCTURED_CALLS[task.calls][isa]
        return text
    if path == "ir":
        text = IR_RULES
        if task.calls:
            text = text.replace(_IR_NO_CALLS, _IR_CALLS[task.calls])
        if task.ir_hint:
            text += " " + task.ir_hint
        return text.replace("{name}", task.name)
    raise ValueError(path)


def build_prompt(path: str, isa: str, task: Task, previous: tuple[str, str] | None = None) -> str:
    """The attempt prompt; `previous` = (code, failure description) for a repair round."""
    if path in task.na_paths:
        raise ValueError(f"{task.name} is not applicable on the {path} path: {task.na_paths[path]}")
    parts = [
        f"Implement the function `{task.signature}`.",
        ABI[isa] + (" " + ABI_EXTRA[isa] if task.round >= 2 else ""),
        rules(path, isa, task),
        f"Specification: {task.spec}",
    ]
    if previous is not None:
        code, failure = previous
        parts.append(
            "Your previous attempt was:\n<previous>\n" + code.rstrip() + "\n</previous>\n"
            "It failed:\n<failure>\n" + failure.rstrip() + "\n</failure>\n"
            "Write a corrected, complete version. Same output format; output only the code."
        )
    return "\n\n".join(parts) + "\n"
