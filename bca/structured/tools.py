"""JSON Schemas, LLM tool definitions, and a streaming session for structured emission.

Two tools per ISA:
  * emit_program     — input {"instructions": [ ... ]}: a whole function in one call.
  * emit_instruction — input {"instruction": { ... }}: one call per instruction (streaming);
                       each call is validated and encoded immediately, and a rejected
                       instruction is not appended, so the model can simply retry it.

`tool_definitions(isa)` returns Anthropic Messages API tool dicts
({"name", "description", "input_schema"}); `flavor="openai"` wraps them as
{"type": "function", "function": {..., "parameters": ...}}. `strict=True` removes keywords
that strict/structured-output modes commonly reject (numeric bounds, maxItems, minItems > 1);
the encoder enforces those limits anyway.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from bca.structured.common import AssemblyError, InsnError, Program

_STRICT_DROP = ("minimum", "maximum", "maxItems", "title")


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _strip(v) for k, v in node.items() if k not in _STRICT_DROP}
        if out.get("minItems", 0) > 1:
            del out["minItems"]
        return out
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def input_schemas(isa, *, strict: bool = False) -> dict[str, dict]:
    """{"program": schema, "instruction": schema}: object-typed, self-contained (with $defs)."""
    from bca.structured import backend

    spec = backend(isa).SPEC
    insn = spec.instruction_schema(bounds=not strict)
    defs = spec.defs(bounds=not strict)
    program = {
        "type": "object",
        "properties": {
            "instructions": {
                "type": "array",
                "description": "The function body in order. Labels are {\"label\": name} entries.",
                "items": insn,
            }
        },
        "required": ["instructions"],
        "additionalProperties": False,
        "$defs": defs,
    }
    single = {
        "type": "object",
        "properties": {"instruction": copy.deepcopy(insn)},
        "required": ["instruction"],
        "additionalProperties": False,
        "$defs": copy.deepcopy(defs),
    }
    if strict:
        program, single = _strip(program), _strip(single)
    return {"program": program, "instruction": single}


def _conventions(isa_name: str) -> str:
    if isa_name == "aarch64":
        return (
            "AArch64 (AAPCS64: args x0-x7, return x0, x19-x28 callee-saved, return with ret). Operands follow "
            "assembly order: dst is the written register, src the rest. Registers are strings (\"x3\", \"w3\", "
            "\"sp\", \"xzr\"); immediates are {\"imm\": n}; shifted registers {\"reg\":\"x2\",\"shift\":\"lsl\","
            "\"amount\":3}; memory {\"mem\":{\"base\":\"x1\",\"disp\":8}} or {\"mem\":{\"base\":\"x1\",\"index\":"
            "\"x2\",\"scale\":8}} or with \"writeback\":\"pre\"|\"post\". Stores put the value first: "
            "{\"op\":\"str\",\"src\":[\"x0\",{\"mem\":...}]}. Branch targets are label names."
        )
    return (
        "x86-64 (System V: args rdi rsi rdx rcx r8 r9, return rax, rbx rbp r12-r15 callee-saved, end with ret). "
        "Intel operand order: dst is the first operand (also read for add/sub/...), src the rest. Registers are "
        "strings (\"rax\", \"eax\", \"al\", \"r8d\"); immediates {\"imm\": n}; memory {\"mem\":{\"base\":\"rdi\","
        "\"index\":\"rcx\",\"scale\":8,\"disp\":0,\"size\":8}} (size in bytes, needed when no register operand "
        "implies it). Jump/call targets are label names."
    )


def tool_definitions(isa, *, flavor: str = "anthropic", strict: bool = False) -> list[dict]:
    from bca.structured import backend

    be = backend(isa)
    schemas = input_schemas(isa, strict=strict)
    conv = _conventions(be.ISA_NAME)
    tools = [
        {
            "name": "emit_program",
            "description": (
                f"Emit a complete {be.ISA_NAME} function as structured instructions; they are encoded directly "
                f"to machine code (no assembler). {conv} The result reports the encoded bytes and disassembly, or "
                "per-instruction errors (instruction index, field path, reason)."
            ),
            "input_schema": schemas["program"],
        },
        {
            "name": "emit_instruction",
            "description": (
                f"Append ONE {be.ISA_NAME} instruction (or a {{\"label\": name}} entry) to the function being "
                "built. It is validated and encoded immediately; if it is rejected, nothing is appended and the "
                f"error says why — fix and resend. Forward label references are resolved at the end. {conv}"
            ),
            "input_schema": schemas["instruction"],
        },
    ]
    if flavor == "anthropic":
        if strict:
            for t in tools:
                t["strict"] = True
        return tools
    if flavor == "openai":
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                    **({"strict": True} if strict else {}),
                },
            }
            for t in tools
        ]
    raise ValueError(f"unknown flavor {flavor!r}")


def _listing(isa_name: str, prog: Program) -> str:
    from bca.isa import ISAS

    return prog.listing(ISAS[isa_name].disassembler())


class Session:
    """Stateful builder for the one-instruction-per-call protocol (and the batch tool).

        s = Session("aarch64")
        s.emit({"op": "add", "dst": "x0", "src": ["x0", "x1"]})   # -> {"ok": True, ...}
        s.emit({"op": "ret"})
        prog = s.finish()                                          # -> Program (raises on error)

    Each accepted instruction is kept; rejected ones are dropped, so the transcript of accepted
    instructions is always an encodable prefix (modulo forward labels still to be defined).
    """

    def __init__(self, isa, *, base: int = 0x100000):
        from bca.structured import backend

        self.backend = backend(isa)
        self.isa_name = self.backend.ISA_NAME
        self.base = base
        self.accepted: list[dict] = []

    def _preview(self, instructions) -> Program:
        return self.backend.assemble(instructions, base=self.base, allow_undefined=True)

    def emit(self, insn) -> dict:
        if isinstance(insn, str):
            try:
                insn = json.loads(insn)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [{"index": len(self.accepted), "path": "", "message": f"invalid JSON: {exc}"}]}
        # Own a private copy: the caller may reuse or mutate its dict after emitting it.
        insn = copy.deepcopy(insn)
        candidate = self.accepted + [insn]
        try:
            prog = self._preview(candidate)
        except AssemblyError as exc:
            return {"ok": False, "index": len(self.accepted), "errors": [e.to_dict() for e in exc.errors]}
        index = len(self.accepted)
        self.accepted.append(insn)
        entry = next((e for e in prog.entries if e.index == index), None)
        out: dict[str, Any] = {"ok": True, "index": index}
        if entry is not None:
            from bca.isa import ISAS

            dis = ISAS[self.isa_name].disassembler()
            out.update(
                offset=entry.offset,
                bytes=entry.data.hex(),
                disasm="; ".join(f"{i.mnemonic} {i.op_str}".strip() for i in dis.disasm(entry.data, self.base + entry.offset)),
            )
        if prog.pending:
            out["unresolved_labels"] = prog.pending
        return out

    def reset(self) -> None:
        self.accepted = []

    def finish(self) -> Program:
        """Final encode with all labels resolved; raises AssemblyError (e.g. undefined labels)."""
        return self.backend.assemble(self.accepted, base=self.base)

    def emit_program(self, instructions) -> dict:
        """Batch tool: replaces the session contents on success."""
        try:
            prog = self.backend.assemble(instructions, base=self.base)
        except AssemblyError as exc:
            return {"ok": False, "errors": [e.to_dict() for e in exc.errors]}
        from bca.structured.common import normalize_program

        self.accepted = copy.deepcopy(list(normalize_program(instructions)))
        return {"ok": True, "size": len(prog.code), "code": prog.code.hex(), "listing": _listing(self.isa_name, prog)}

    def handle_tool_call(self, name: str, tool_input: dict) -> dict:
        """Dispatch a tool_use block ({name, input}) to the session; returns a JSON-able result."""
        if name == "emit_program":
            if not isinstance(tool_input, dict) or "instructions" not in tool_input:
                return {"ok": False, "errors": [InsnError(-1, "", "input must be {\"instructions\": [...]}").to_dict()]}
            return self.emit_program(tool_input["instructions"])
        if name == "emit_instruction":
            if not isinstance(tool_input, dict) or "instruction" not in tool_input:
                return {"ok": False, "errors": [InsnError(-1, "", "input must be {\"instruction\": {...}}").to_dict()]}
            return self.emit(tool_input["instruction"])
        return {"ok": False, "errors": [InsnError(-1, "", f"unknown tool {name!r}").to_dict()]}
