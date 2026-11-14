"""The `harness_v2` prompt variant: four harness improvements for a new round, opt-in.

The recorded runs (40+ models) were all made with the baseline harness, so these changes are not
patches to it: they exist only under this variant, and with any other variant (baseline included)
every prompt, every failure text and what every pipeline accepts is byte-identical to before
(tests/test_liveeval_harness_v2.py). The four changes, each from a report's follow-up list:

1. QBE compile errors echo the offending source line of the model's own code
   (`qbe_error_lines`; reports/live-model-eval-haiku-sonnet.md).
2. The structured schema's shared register definitions are renamed in what the model sees:
   `$defs/reg` -> `$defs/register_name` (and x86's `reg64` -> `register64_name`), so a bare
   register string reads as the obvious form (same report; `input_schemas(..., harness_v2=True)`).
3. The structured AArch64 encoder accepts, and the schema lists, the extend ops `sxtb sxth sxtw
   uxtb uxth` and add/subtract with carry `adc adcs sbc sbcs` (x86 already has movsx / adc / sbb;
   reports/live-model-eval-adversarial.md). Under baseline they are rejected exactly as before.
4. When a checked output buffer (or memory window) differs, the failure names the first differing
   index with both values, not only the first 12 elements of each side
   (reports/live-model-eval-long.md).

Selection: `--variant harness_v2`. The variant function rewrites the prompt (item 2 and the op list
of item 3: the structured schema is replaced by the v2 one; other paths' prompts are unchanged).
Items 1, 3 (acceptance) and 4 live in the pipelines, which consult `HARNESS_V2`: the runner sets it
for every cell (True only when the cell's variant is "harness_v2"), so code outside the runner
(re-scoring scripts, tests) sees the baseline unless it opts in with `enabled()`.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re

from bca.liveeval.tasks import Task

NAME = "harness_v2"

HARNESS_V2: contextvars.ContextVar[bool] = contextvars.ContextVar("bca_harness_v2", default=False)


def active() -> bool:
    return HARNESS_V2.get()


@contextlib.contextmanager
def enabled(on: bool = True):
    """Run a block with harness v2 on (or off): `with enabled(): attempt(...)`."""
    token = HARNESS_V2.set(on)
    try:
        yield
    finally:
        HARNESS_V2.reset(token)


# -- 1. QBE errors with the source line ---------------------------------------------------------

_QBE_LINE = re.compile(r"^qbe:-:(\d+):", re.M)


def qbe_error_lines(stage: str, detail: str, source: str) -> str:
    """Lines to append to a QBE compile failure: the model's source line(s) the error names.
    Only QBE's own `qbe:-:<line>:` diagnostics refer to the model's source (an assembler error
    refers to QBE's output), so anything else gives ""."""
    if stage != "qbe":
        return ""
    lines = source.splitlines()
    out = []
    for n in dict.fromkeys(int(m) for m in _QBE_LINE.findall(detail)):
        if 1 <= n <= len(lines):
            out.append(f"Line {n} of your code: {lines[n - 1].strip()}")
    return "".join("\n" + s for s in out[:3])


# -- 4. first differing element ------------------------------------------------------------------

def first_difference(expected: list[int], actual: list[int]) -> int | None:
    return next((k for k, (e, a) in enumerate(zip(expected, actual)) if e != a), None)


_WINDOW = re.compile(r"^(\w+)\[(\d+)\.\.\d+\)$")


def difference_note(expected: list[int], actual: list[int], name: str = "a", start: int = 0) -> str:
    """'; first difference at a[17]: expected 5, got 4' ('' if the elements agree)."""
    k = first_difference(expected, actual)
    if k is None:
        return ""
    return f"; first difference at {name}[{start + k}]: expected {expected[k]}, got {actual[k]}"


def window_difference_note(window: str | None, expected: list[int], actual: list[int]) -> str:
    """The same for a memory window named like `dst[4..20)` (index in the argument) or otherwise
    (index within the window)."""
    m = _WINDOW.match(window or "")
    if m:
        return difference_note(expected, actual, m.group(1), int(m.group(2)))
    k = first_difference(expected, actual)
    if k is None:
        return ""
    return f"; first difference at element {k} of that range: expected {expected[k]}, got {actual[k]}"


# -- 2 + 3. the structured schema in the prompt ----------------------------------------------------

def _schema_json(isa: str, v2: bool) -> str:
    from bca.structured import input_schemas

    return json.dumps(input_schemas(isa, harness_v2=v2)["program"], separators=(",", ":"))


def harness_v2(prompt: str, path: str, isa: str, task: Task) -> str:
    """The prompt half of harness v2: on the structured path, the v2 schema (renamed register
    definitions; AArch64 extend / carry ops) replaces the baseline one. Other paths' prompts are
    unchanged; their v2 differences are in the failure texts (see the module docstring)."""
    if path != "structured":
        return prompt
    old, new = _schema_json(isa, False), _schema_json(isa, True)
    if prompt.count(old) != 1:
        raise ValueError(f"harness_v2: the structured {isa} schema is not in the prompt exactly once")
    return prompt.replace(old, new)


__all__ = ["HARNESS_V2", "NAME", "active", "difference_note", "enabled", "first_difference", "harness_v2",
           "qbe_error_lines", "window_difference_note"]
