"""Shared machinery for structured emission.

An ISA backend describes its instruction language as a set of *families*: groups of ops that
share one JSON shape (e.g. `add/sub/adds/subs` all take `dst` + `src[2]`). Each field of a
family accepts one or more operand *kinds* (register name, `{"imm": ..}`, `{"mem": ..}`, ...).
From that single description we derive:

* the JSON Schema (one `anyOf` variant per family, `op` enumerated per variant), and
* a structural parser that turns raw JSON into typed operands, reporting precise paths
  (`src[1].imm`, `src[0].mem.scale`, ...) for anything malformed.

The ISA-specific encoder then only has to deal with well-typed operands.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

LABEL_PATTERN = r"^[A-Za-z_.][A-Za-z0-9_.]*$"
_LABEL_RE = re.compile(LABEL_PATTERN)


# --------------------------------------------------------------------------- errors


class EncodeError(Exception):
    """Raised by parsers/encoders; `path` locates the offending field inside the instruction."""

    def __init__(self, message: str, path: str = ""):
        super().__init__(message)
        self.message = message
        self.path = path


@dataclass
class InsnError:
    index: int
    path: str
    message: str
    insn: Any = None

    def __str__(self) -> str:
        what = ""
        if isinstance(self.insn, dict):
            if "op" in self.insn:
                what = f" ({self.insn['op']})"
            elif "label" in self.insn:
                what = f" (label {self.insn['label']!r})"
        where = f" {self.path}" if self.path else ""
        return f"instruction {self.index}{what}{where}: {self.message}"

    def to_dict(self) -> dict:
        return {"index": self.index, "path": self.path, "message": self.message, "text": str(self)}


class AssemblyError(ValueError):
    """One or more instructions were rejected; `errors` holds every per-instruction error."""

    def __init__(self, errors: list[InsnError]):
        self.errors = errors
        super().__init__("\n".join(str(e) for e in errors))


def suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(str(word), list(choices), n=3, cutoff=0.6)
    return f"; did you mean {' or '.join(repr(c) for c in close)}?" if close else ""


def join_path(base: str, sub: str) -> str:
    if not base:
        return sub
    if not sub:
        return base
    return f"{base}{sub}" if sub.startswith("[") else f"{base}.{sub}"


# --------------------------------------------------------------------------- spec


@dataclass(frozen=True)
class Kind:
    """One operand shape. `match` decides (cheaply, by JSON shape) whether a value is meant
    to be this kind; `parse` then validates it fully and returns a typed operand."""

    name: str
    describe: str
    schema: Callable[[bool], dict]  # arg: include numeric bounds
    match: Callable[[Any], bool]
    parse: Callable[[Any, str], Any]


@dataclass(frozen=True)
class Field:
    kinds: tuple[Kind, ...]
    many: bool = False  # a JSON array of operands
    min: int = 1
    max: int = 1
    description: str = ""


@dataclass(frozen=True)
class Family:
    name: str
    ops: tuple[str, ...]
    fields: dict[str, Field]
    required: tuple[str, ...]
    doc: str
    examples: tuple[dict, ...] = ()


@dataclass
class Insn:
    """A structurally valid instruction: typed operands keyed by field name."""

    index: int
    op: str
    family: Family
    f: dict[str, Any]
    raw: dict


@dataclass
class Label:
    index: int
    name: str
    raw: dict


def label_kind() -> Kind:
    def parse(v, path):
        if not isinstance(v, str) or not _LABEL_RE.match(v):
            raise EncodeError(f"invalid label name {v!r} (must match {LABEL_PATTERN})", path)
        return v

    return Kind(
        "label",
        "a label name",
        lambda bounds: {"type": "string", "pattern": LABEL_PATTERN},
        lambda v: isinstance(v, str),
        parse,
    )


def enum_kind(name: str, values: Iterable[str], describe: str) -> Kind:
    values = tuple(values)

    def parse(v, path):
        if v not in values:
            raise EncodeError(f"unknown {name} {v!r}{suggest(v, values)}", path)
        return v

    return Kind(
        name,
        describe,
        lambda bounds: {"type": "string", "enum": list(values)},
        lambda v: isinstance(v, str),
        parse,
    )


def check_int(v, path: str, lo: int | None = None, hi: int | None = None, what: str = "value"):
    if isinstance(v, bool) or not isinstance(v, int):
        # Tolerate integral floats and numeric strings (common LLM slips) but nothing else.
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        elif isinstance(v, str):
            try:
                v = int(v, 0)
            except ValueError:
                raise EncodeError(f"{what} must be an integer, got {v!r}", path) from None
        else:
            raise EncodeError(f"{what} must be an integer, got {v!r}", path)
    if lo is not None and v < lo or hi is not None and v > hi:
        raise EncodeError(f"{what} {v} out of range [{lo}, {hi}]", path)
    return v


def check_object(v, path: str, allowed: dict[str, bool], what: str) -> dict:
    """`allowed` maps key -> required. Rejects unknown keys and reports missing ones."""
    if not isinstance(v, dict):
        raise EncodeError(f"{what} must be an object, got {v!r}", path)
    for k in v:
        if k not in allowed:
            raise EncodeError(
                f"unexpected key {k!r} in {what}; allowed: {', '.join(allowed)}{suggest(k, allowed)}",
                path,
            )
    for k, req in allowed.items():
        if req and k not in v:
            raise EncodeError(f"{what} requires key {k!r}", path)
    return v


# --------------------------------------------------------------------------- spec → schema


class Spec:
    """An ISA's instruction language: families plus the schema/parse logic derived from them."""

    def __init__(self, isa_name: str, families: list[Family], shared: dict[str, Kind]):
        self.isa_name = isa_name
        self.families = families
        self.shared = shared  # kinds emitted once under $defs and referenced
        self.by_op: dict[str, Family] = {}
        for fam in families:
            for op in fam.ops:
                assert op not in self.by_op, op
                self.by_op[op] = fam
        self.label = label_kind()

    @property
    def ops(self) -> list[str]:
        return list(self.by_op)

    # ---- schema

    def _kind_schema(self, kind: Kind, bounds: bool) -> dict:
        if kind.name in self.shared:
            return {"$ref": f"#/$defs/{kind.name}"}
        return kind.schema(bounds)

    def _field_schema(self, fld: Field, bounds: bool) -> dict:
        alts = [self._kind_schema(k, bounds) for k in fld.kinds]
        item = alts[0] if len(alts) == 1 else {"anyOf": alts}
        if fld.many:
            s = {"type": "array", "items": item, "minItems": fld.min, "maxItems": fld.max}
        else:
            s = dict(item)
        if fld.description:
            s = {**s, "description": fld.description} if "$ref" not in s else {"allOf": [s], "description": fld.description}
        return s

    def family_schema(self, fam: Family, bounds: bool = True) -> dict:
        props: dict[str, Any] = {"op": {"type": "string", "enum": list(fam.ops)}}
        for name, fld in fam.fields.items():
            props[name] = self._field_schema(fld, bounds)
        props["comment"] = {"type": "string", "description": "optional free-text note; ignored"}
        return {
            "type": "object",
            "title": fam.name,
            "description": fam.doc,
            "properties": props,
            "required": ["op", *fam.required],
            "additionalProperties": False,
        }

    def label_schema(self) -> dict:
        return {
            "type": "object",
            "title": "label",
            "description": "Defines a branch target at the position of the next instruction.",
            "properties": {
                "label": {"type": "string", "pattern": LABEL_PATTERN},
                "comment": {"type": "string"},
            },
            "required": ["label"],
            "additionalProperties": False,
        }

    def defs(self, bounds: bool = True) -> dict:
        return {name: k.schema(bounds) for name, k in self.shared.items()}

    def instruction_schema(self, bounds: bool = True) -> dict:
        """Schema for one instruction (uses `#/$defs/...`; embed via `defs()` at the root)."""
        return {"anyOf": [self.label_schema()] + [self.family_schema(f, bounds) for f in self.families]}

    # ---- parse

    def parse_field(self, fld: Field, v, path: str):
        if fld.many:
            if not isinstance(v, list):
                if fld.max == 1 or fld.min <= 1:
                    v = [v]  # tolerate a bare operand where a 1-element list is expected
                else:
                    raise EncodeError(f"expected a list of {fld.min}..{fld.max} operands, got {v!r}", path)
            if not fld.min <= len(v) <= fld.max:
                n = f"{fld.min}" if fld.min == fld.max else f"{fld.min}..{fld.max}"
                raise EncodeError(f"expected {n} operands, got {len(v)}", path)
            return [self.parse_operand(fld.kinds, item, f"{path}[{i}]") for i, item in enumerate(v)]
        return self.parse_operand(fld.kinds, v, path)

    def parse_operand(self, kinds: tuple[Kind, ...], v, path: str):
        for k in kinds:
            if k.match(v):
                return k.parse(v, path)
        allowed = " or ".join(k.describe for k in kinds)
        raise EncodeError(f"expected {allowed}, got {v!r}", path)

    def parse(self, raw, index: int) -> Insn | Label:
        if not isinstance(raw, dict):
            raise EncodeError(f"instruction must be a JSON object, got {raw!r}")
        if "label" in raw:
            extra = [k for k in raw if k not in ("label", "comment")]
            if extra:
                raise EncodeError(
                    f"a label entry takes only 'label' (got also {extra}); put the instruction "
                    "in its own entry after the label"
                )
            return Label(index, self.label.parse(raw["label"], "label"), raw)
        if "op" not in raw:
            raise EncodeError("missing 'op' (or 'label')")
        op = raw["op"]
        if not isinstance(op, str):
            raise EncodeError(f"'op' must be a string, got {op!r}", "op")
        fam = self.by_op.get(op) or self.by_op.get(op.lower())
        if fam is None:
            raise EncodeError(f"unknown {self.isa_name} op {op!r}{suggest(op.lower(), self.by_op)}", "op")
        op = op.lower() if op not in self.by_op else op
        for k in raw:
            if k not in fam.fields and k not in ("op", "comment"):
                takes = ", ".join(fam.fields) or "no operands"
                raise EncodeError(f"unexpected field {k!r}; {op} takes: {takes}", k)
        for k in fam.required:
            if k not in raw:
                raise EncodeError(f"{op} requires field {k!r}", k)
        parsed = {name: self.parse_field(fld, raw[name], name) for name, fld in fam.fields.items() if name in raw}
        return Insn(index, op, fam, parsed, raw)


# --------------------------------------------------------------------------- results


@dataclass
class Entry:
    index: int  # position in the input list
    offset: int  # byte offset from the start of the code
    data: bytes
    raw: dict


@dataclass
class Program:
    isa_name: str
    code: bytes
    base: int
    entries: list[Entry]
    labels: dict[str, int]  # label -> byte offset
    pending: list[str] = field(default_factory=list)  # undefined labels (streaming preview only)

    def listing(self, disassembler=None) -> str:
        """Human/LLM-readable listing: offset, bytes, disassembly (capstone, if given)."""
        by_off: dict[int, list[str]] = {}
        for name, off in self.labels.items():
            by_off.setdefault(off, []).append(name)
        lines = []
        for e in self.entries:
            for name in by_off.pop(e.offset, []):
                lines.append(f"{name}:")
            text = ""
            if disassembler is not None:
                text = "; ".join(
                    f"{i.mnemonic} {i.op_str}".strip()
                    for i in disassembler.disasm(e.data, self.base + e.offset)
                )
            lines.append(f"  {e.offset:#06x}: {e.data.hex():<24} {text}")
        for off, names in sorted(by_off.items()):
            lines.extend(f"{n}:" for n in names)
        return "\n".join(lines)


def normalize_program(instructions) -> list:
    """Accept a list, {"instructions": [...]}, or a JSON string of either."""
    if isinstance(instructions, (str, bytes)):
        import json

        instructions = json.loads(instructions)
    if isinstance(instructions, dict) and "instructions" in instructions:
        instructions = instructions["instructions"]
    if not isinstance(instructions, list):
        raise AssemblyError([InsnError(-1, "", "program must be a list of instructions")])
    return instructions


def parse_program(spec: Spec, instructions: list) -> tuple[list[Insn | Label], list[InsnError]]:
    items, errors, seen = [], [], {}
    for i, raw in enumerate(instructions):
        try:
            item = spec.parse(raw, i)
        except EncodeError as exc:
            errors.append(InsnError(i, exc.path, exc.message, raw))
            continue
        if isinstance(item, Label):
            if item.name in seen:
                errors.append(
                    InsnError(i, "label", f"duplicate label {item.name!r} (first defined at instruction {seen[item.name]})", raw)
                )
                continue
            seen[item.name] = i
        items.append(item)
    return items, errors


def undefined_label(name: str, labels: Iterable[str], path: str) -> EncodeError:
    return EncodeError(f"undefined label {name!r}{suggest(name, labels)}", path)
