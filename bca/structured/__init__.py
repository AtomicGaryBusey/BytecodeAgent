"""Constrained / structured emission: the model emits instructions as JSON objects (e.g. tool
calls), and a deterministic encoder turns each one directly into machine code.

    from bca.structured import assemble
    prog = assemble("aarch64", [{"op": "add", "dst": "x0", "src": ["x0", "x1"]}, {"op": "ret"}])
    prog.code  # bytes

No assembly text is involved: AArch64 is encoded bit-by-bit (bca.structured.a64); x86-64 goes
through iced-x86's Instruction/BlockEncoder API (bca.structured.x64). Malformed or
unencodable instructions are rejected individually with a path-precise message
(`AssemblyError.errors`). JSON Schemas and ready-made tool definitions live in
bca.structured.tools; `Session` supports one-tool-call-per-instruction streaming.
"""

from bca.structured import a64, x64
from bca.structured.common import AssemblyError, InsnError, Program

BACKENDS = {"aarch64": a64, "x86_64": x64}


def backend(isa):
    """Accept an `Isa` or its name."""
    name = getattr(isa, "name", isa)
    try:
        return BACKENDS[name]
    except KeyError:
        raise ValueError(f"no structured backend for {name!r}; have {list(BACKENDS)}") from None


def assemble(isa, instructions, *, base: int = 0x100000, allow_undefined: bool = False) -> Program:
    """Encode a structured program (list, {"instructions": [...]}, or JSON text) for `isa`."""
    return backend(isa).assemble(instructions, base=base, allow_undefined=allow_undefined)


from bca.structured.tools import Session, input_schemas, tool_definitions  # noqa: E402

__all__ = [
    "AssemblyError", "InsnError", "Program", "Session", "assemble", "backend",
    "input_schemas", "tool_definitions",
]  # fmt: skip
