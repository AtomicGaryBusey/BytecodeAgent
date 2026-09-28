"""Batteries, variants and subjects: the dimensions of a live-eval plan.

Batteries are task sets. round1/round2 live in this package's core modules; `long` and
`adversarial` are separate modules (bca.liveeval.tasks_long, bca.liveeval.adversarial) that
are imported lazily, so the CLI and the summaries keep working when one of them is missing
or broken; the error surfaces only when that battery (or one of its variants) is asked for.

A variant rewrites a prompt: `variant(prompt, path, isa, task) -> str`. It is applied to every
prompt of a cell, first attempts and repair rounds alike. "baseline" (identity) always exists.

Subjects are `claude -p --model <id>` processes. Aliases resolve inside the CLI; the ids the
stream reported for one probe call each (2026-09-27, claude 2.1.283) are in MODEL_ALIASES.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable

from bca.liveeval.tasks import TASKS as ROUND1
from bca.liveeval.tasks import Task
from bca.liveeval.tasks2 import ROUND2

# alias -> the model id the stream reported when `claude -p --model <alias>` was probed.
MODEL_ALIASES = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5-1",
}

# Battery name -> module exposing TASKS (lazy), or the task tuple itself (core).
BATTERY_MODULES: dict[str, str] = {
    "long": "bca.liveeval.tasks_long",
    "adversarial": "bca.liveeval.adversarial",
}
CORE_BATTERIES: dict[str, tuple[Task, ...]] = {"round1": ROUND1, "round2": ROUND2}
BATTERY_ORDER = ("round1", "round2", "long", "adversarial")
VARIANTS_MODULE = "bca.liveeval.adversarial"

Variant = Callable[[str, str, str, Task], str]


class RegistryError(ValueError):
    pass


def baseline(prompt: str, path: str, isa: str, task: Task) -> str:
    return prompt


def battery_names() -> list[str]:
    return list(dict.fromkeys([*BATTERY_ORDER, *CORE_BATTERIES, *BATTERY_MODULES]))


def load_battery(name: str) -> tuple[Task, ...]:
    if name in CORE_BATTERIES:
        return tuple(CORE_BATTERIES[name])
    if name not in BATTERY_MODULES:
        raise RegistryError(f"unknown battery {name!r}; have {', '.join(battery_names())}")
    mod = BATTERY_MODULES[name]
    try:
        tasks = importlib.import_module(mod).TASKS
    except Exception as e:  # missing, broken, or no TASKS: say which and why
        raise RegistryError(f"battery {name!r} is unavailable: importing {mod}.TASKS failed: "
                            f"{type(e).__name__}: {e}") from e
    tasks = tuple(tasks)
    bad = [t for t in tasks if not isinstance(t, Task)]
    if bad:
        raise RegistryError(f"battery {name!r}: {mod}.TASKS has non-Task entries: {bad[:3]!r}")
    return tasks


def available_batteries() -> tuple[dict[str, tuple[Task, ...]], dict[str, str]]:
    """(battery -> tasks for every battery that loads, battery -> error for the rest)."""
    ok, errors = {}, {}
    for b in battery_names():
        try:
            ok[b] = load_battery(b)
        except RegistryError as e:
            errors[b] = str(e)
    return ok, errors


def known_tasks() -> dict[str, tuple[Task, str]]:
    """Task name -> (task, battery) over every battery that loads (first battery wins)."""
    out: dict[str, tuple[Task, str]] = {}
    for b, tasks in available_batteries()[0].items():
        for t in tasks:
            out.setdefault(t.name, (t, b))
    return out


def select(spec: str) -> list[tuple[Task, str]]:
    """`round1`, `round2`, `long`, `adversarial`, `all` (every battery) or task names, comma
    separated and mixable. Returns (task, battery) pairs, deduplicated by task name."""
    out: dict[str, tuple[Task, str]] = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if part == "all":
            for b in battery_names():
                for t in load_battery(b):
                    out.setdefault(t.name, (t, b))
        elif part in CORE_BATTERIES or part in BATTERY_MODULES:
            for t in load_battery(part):
                out.setdefault(t.name, (t, part))
        else:
            core = {t.name: (t, b) for b, ts in CORE_BATTERIES.items() for t in ts}
            if part in core:
                out.setdefault(part, core[part])
                continue
            ok, errors = available_batteries()
            hit = next(((t, b) for b, ts in ok.items() for t in ts if t.name == part), None)
            if hit is None:
                names = [t.name for ts in ok.values() for t in ts]
                msg = (f"unknown task or battery {part!r}; batteries: all, {', '.join(battery_names())}; "
                       f"tasks: {', '.join(names)}")
                if errors:
                    msg += "; unavailable: " + "; ".join(errors.values())
                raise RegistryError(msg)
            out.setdefault(part, hit)
    return list(out.values())


def battery_of(task_name: str) -> str:
    for b, ts in CORE_BATTERIES.items():
        if any(t.name == task_name for t in ts):
            return b
    hit = known_tasks().get(task_name)
    return hit[1] if hit else "?"


def load_variants(names: list[str]) -> dict[str, Variant]:
    """name -> variant function. "baseline" never needs the adversarial module."""
    out: dict[str, Variant] = {}
    table = None
    for n in names:
        if n == "baseline":
            out[n] = baseline
            continue
        if table is None:
            try:
                table = dict(importlib.import_module(VARIANTS_MODULE).VARIANTS)
            except Exception as e:
                raise RegistryError(f"variant {n!r} is unavailable: importing {VARIANTS_MODULE}.VARIANTS "
                                    f"failed: {type(e).__name__}: {e}") from e
        if n == "all":
            # Format-compliance probes (e.g. legacy_style) are opt-in: the module lists the rest.
            keep = getattr(importlib.import_module(VARIANTS_MODULE), "ALL_VARIANTS_DEFAULT", tuple(table))
            out.update({k: v for k, v in table.items() if k in keep})
            continue
        if n not in table:
            raise RegistryError(f"unknown variant {n!r}; have {', '.join(dict.fromkeys(['baseline', *table]))}")
        if not callable(table[n]):
            raise RegistryError(f"variant {n!r} in {VARIANTS_MODULE}.VARIANTS is not callable")
        out[n] = table[n]
    return out


def variant_info(name: str) -> dict:
    """VARIANT_INFO[name] from the variants module (lazy), or {} when the module, the table or the
    entry is missing. Known keys: "conflicts_with_format" (the variant asks for a format the
    pipelines reject: a format-compliance probe, not a skill measurement), "max_attempts"."""
    if name == "baseline":
        return {}
    try:
        info = getattr(importlib.import_module(VARIANTS_MODULE), "VARIANT_INFO", {}) or {}
        entry = info.get(name, {})
    except Exception:  # noqa: BLE001 - the summaries must work without the module
        return {}
    return dict(entry) if isinstance(entry, dict) else {}


def is_format_probe(name: str) -> bool:
    return bool(variant_info(name).get("conflicts_with_format"))


def variant_label(name: str) -> str:
    """How summaries show a variant: format-compliance probes are marked as such."""
    return f"{name} (format-compliance probe)" if is_format_probe(name) else name


def variant_max_attempts(name: str, max_attempts: int) -> int:
    """The attempt limit for a variant's cells: VARIANT_INFO[name]["max_attempts"] if it is set and
    lower than the run's limit."""
    cap = variant_info(name).get("max_attempts")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1:
        return min(cap, max_attempts)
    return max_attempts


# Default per-call subject timeout (seconds) per battery; --timeout overrides all of them.
DEFAULT_TIMEOUT = 180.0
BATTERY_TIMEOUTS = {"long": 400.0}  # 2.5-8 KB answers (~60-250 instructions)


def default_timeout(battery: str) -> float:
    return BATTERY_TIMEOUTS.get(battery, DEFAULT_TIMEOUT)


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(s: str) -> str:
    """A directory-safe name for a subject or variant."""
    s = _SAFE.sub("_", s).strip("._")
    if not s:
        raise RegistryError("empty subject/variant name")
    return s


def model_for(subject: str) -> str:
    """The value passed to `claude -p --model` (aliases pass through: the CLI resolves them)."""
    return subject


def expected_model(subject: str) -> str:
    """The model id a subject should report: a Claude alias resolved, or the <model> of <vendor>:<model>."""
    from bca.liveeval.vendors import split_spec

    vendor, model = split_spec(subject)
    return model if vendor else MODEL_ALIASES.get(subject, subject)


__all__ = [
    "BATTERY_MODULES", "BATTERY_ORDER", "CORE_BATTERIES", "MODEL_ALIASES", "RegistryError", "VARIANTS_MODULE",
    "available_batteries", "baseline", "battery_names", "battery_of", "expected_model", "known_tasks",
    "load_battery", "load_variants", "model_for", "safe_name", "select", "BATTERY_TIMEOUTS", "DEFAULT_TIMEOUT",
    "default_timeout", "is_format_probe", "variant_info", "variant_label", "variant_max_attempts",
]
