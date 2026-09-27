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
        return STREAM_RULES[isa].replace("{name}", task.name)
    if path == "structured":
        return _structured_rules(isa)
    if path == "ir":
        return IR_RULES.replace("{name}", task.name)
    raise ValueError(path)


def build_prompt(path: str, isa: str, task: Task, previous: tuple[str, str] | None = None) -> str:
    """The attempt prompt; `previous` = (code, failure description) for a repair round."""
    parts = [
        f"Implement the function `{task.signature}`.",
        ABI[isa],
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
