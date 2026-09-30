"""Cost estimates for a plan (subjects x variants x cells) before any call is made.

Worst case: every remaining cell uses all of its attempts (`max_attempts`, or a variant's lower
VARIANT_INFO "max_attempts"). Expected: the mean attempts per cell observed in earlier run
directories (`--estimate-from`) for the same variant, matched as specifically as the data allows:
(model, battery, path), (model, battery), (battery, path), (battery) -- a level is used once it has
MIN_SAMPLES cells -- then, for a non-baseline variant without data of its own, the same levels of
the baseline rates (marked "baseline rate"), else DEFAULT_ATTEMPTS. A variant flagged
`conflicts_with_format` (a format-compliance probe, e.g. legacy_style) is assumed to use every
attempt it has. Rates never cross batteries (their
difficulty differs too much); they may cross models, which the "rate from" column marks. Cells already complete
in the target run directory (cell.json) cost nothing: a run resumes them. A sampled cell (every app
cell: 3 samples by default) counts once per sample.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from pathlib import Path

from bca.liveeval.registry import expected_model, is_format_probe, variant_max_attempts
from bca.liveeval.runner import cell_id, group_dir

DEFAULT_ATTEMPTS = 1.5  # no data: between round 1/2 opus (1.05) and a weak subject
# No data for a battery that is much harder per cell: whole programs need more repairs (a guess until
# the first app runs give real rates through --estimate-from).
DEFAULT_ATTEMPTS_BY_BATTERY = {"apps": 2.5}
MIN_SAMPLES = 3


class Rates:
    def __init__(self, cells: list[dict] | None = None):
        self.table: dict[tuple, list[int]] = defaultdict(list)
        for c in cells or []:
            m, b, p = c.get("subject_model") or c.get("model") or c.get("subject"), c.get("battery"), c["path"]
            v = c.get("variant") or "baseline"
            for key in (("m", m, b, p), ("m", m, b), ("b", b, p), ("b", b)):
                self.table[(v, *key)].append(c["attempts"])

    @classmethod
    def from_dirs(cls, dirs) -> Rates:
        from bca.liveeval.summarize import load_all

        cells = []
        for d in dirs or []:
            cells += load_all(Path(d))[0]
        return cls(cells)

    def rate(self, model: str, battery: str, path: str, variant: str = "baseline") -> tuple[float, str]:
        levels = (("m", model, battery, path), ("m", model, battery), ("b", battery, path), ("b", battery))
        for v in dict.fromkeys((variant, "baseline")):
            for key in levels:
                xs = self.table.get((v, *key), [])
                if len(xs) >= MIN_SAMPLES:
                    label = "/".join(str(k) for k in key[1:]) + (" (any model)" if key[0] == "b" else "")
                    if v != "baseline":
                        label = f"{v}: {label}"
                    elif variant != "baseline":
                        label += " (baseline rate)"
                    return statistics.mean(xs), f"{label} n={len(xs)}"
        return DEFAULT_ATTEMPTS_BY_BATTERY.get(battery, DEFAULT_ATTEMPTS), "default"


def estimate(run_dir: Path | None, subjects: list[str], variants: list[str], cells, *, max_attempts: int,
             rates: Rates, flat: bool = False, variant_names: dict[str, str] | None = None,
             subject_specs: dict[str, str] | None = None) -> list[dict]:
    """One row per (subject, variant, battery): cells, done (resumable), worst, expected calls.
    `variant_names` maps a (directory-safe) variant to its registry name for VARIANT_INFO."""
    rows: dict[tuple, dict] = {}
    for s in subjects:
        model = expected_model((subject_specs or {}).get(s, s))  # the spec, not its directory-safe name
        for v in variants:
            vname = (variant_names or {}).get(v, v)
            vmax = variant_max_attempts(vname, max_attempts)
            probe = is_format_probe(vname)
            for t, b, p, i in cells:
                gdir = None
                if run_dir is not None:
                    gdir = run_dir if flat else group_dir(run_dir, s, v)
                done = gdir is not None and (gdir / "cells" / cell_id(t, p, i) / "cell.json").exists()
                if probe:
                    r, src = vmax, "format probe: every attempt"
                else:
                    r, src = rates.rate(model, b, p, v)
                row = rows.setdefault((s, v, b), {"subject": s, "variant": v, "battery": b, "cells": 0, "done": 0,
                                                  "worst": 0, "expected": 0.0, "sources": set()})
                row["cells"] += 1
                if done:
                    row["done"] += 1
                    continue
                row["worst"] += vmax
                row["expected"] += min(r, vmax)
                row["sources"].add(src)
    return list(rows.values())


def render(rows: list[dict], *, max_calls: int, used: int) -> str:
    out = ["| subject | variant | battery | cells | done | worst-case calls | expected calls | rate from |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['subject']} | {r['variant']} | {r['battery']} | {r['cells']} | {r['done']} | {r['worst']} | "
                   f"{math.ceil(r['expected'])} | {'; '.join(sorted(r['sources'])) or '-'} |")
    worst = sum(r["worst"] for r in rows)
    exp = math.ceil(sum(r["expected"] for r in rows))
    left = max(0, max_calls - used)
    out.append(f"| **total** | | | {sum(r['cells'] for r in rows)} | {sum(r['done'] for r in rows)} | {worst} | "
               f"{exp} | |")
    out += ["", f"calls already used in this run dir: {used}; --max-calls {max_calls} leaves {left}; "
            f"worst case {worst}, expected {exp}"
            + ("" if worst <= left else f"; WARNING: the cap can stop the plan early "
               f"(worst case exceeds the remaining {left} calls)")]
    return "\n".join(out)
