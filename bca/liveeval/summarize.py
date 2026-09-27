"""Aggregate a run directory into Markdown tables (used by reports/live-model-eval.md)."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from bca.liveeval.prompts import ISAS, PATHS
from bca.liveeval.tasks import TASKS


def load(run_dir: Path) -> tuple[list[dict], list[dict]]:
    cells = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    atts_path = run_dir / "attempts.jsonl"
    atts = [json.loads(l) for l in atts_path.read_text().splitlines() if l.strip()] if atts_path.exists() else []
    # A cell may be recorded twice if a run was interrupted between writes; keep the last.
    cells = list({c["cell"]: c for c in cells}.values())
    done = {c["cell"] for c in cells}
    atts = [a for a in atts if a["cell"] in done]
    return cells, atts


def _rate(xs: list[bool]) -> str:
    return f"{sum(xs)}/{len(xs)} ({100 * sum(xs) / len(xs):.0f}%)" if xs else "-"


def summarize(run_dir: Path) -> str:
    cells, atts = load(run_dir)
    by = {(c["task"], c["path"], c["isa"]): c for c in cells}
    out = []
    # Matrix
    hdr = "| task | " + " | ".join(f"{p} {i}" for p in PATHS for i in ISAS) + " |"
    out += ["### Per cell (pass@1 / pass within 4 / attempts used)", "", hdr,
            "|" + "---|" * (1 + len(PATHS) * len(ISAS))]
    for t in TASKS:
        row = [t.name]
        for p in PATHS:
            for i in ISAS:
                c = by.get((t.name, p, i))
                if not c:
                    row.append("n/a")
                    continue
                mark = "✓" if c["pass_final"] else "✗"
                row.append(f"{int(c['pass_at_1'])} / {mark} / {c['attempts']}")
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    # Aggregates
    def agg(key, values, label):
        rows = [f"### By {label}", "", f"| {label} | cells | pass@1 | pass@≤4 | mean attempts |", "|---|---|---|---|---|"]
        for v in values:
            cs = [c for c in cells if key(c) == v]
            if not cs:
                continue
            rows.append(f"| {v} | {len(cs)} | {_rate([c['pass_at_1'] for c in cs])} | "
                        f"{_rate([c['pass_final'] for c in cs])} | "
                        f"{statistics.mean(c['attempts'] for c in cs):.2f} |")
        return rows + [""]

    out += agg(lambda c: c["path"], PATHS, "path")
    out += agg(lambda c: c["isa"], ISAS, "ISA")
    out += agg(lambda c: f"{c['path']} {c['isa']}", [f"{p} {i}" for p in PATHS for i in ISAS], "path × ISA")
    out += agg(lambda c: c["task"], [t.name for t in TASKS], "task")
    out += ["### Overall", "", f"cells: {len(cells)}; pass@1 {_rate([c['pass_at_1'] for c in cells])}; "
            f"pass@≤4 {_rate([c['pass_final'] for c in cells])}; attempts (calls) {len(atts)}", ""]
    # Failure categories per path
    cats = defaultdict(Counter)
    for a in atts:
        if not a["ok"]:
            cats[a["path"]][a["category"]] += 1
    allc = sorted({k for c in cats.values() for k in c})
    out += ["### Failed attempts by category and path", "", "| category | " + " | ".join(PATHS) + " | total |",
            "|---" * (len(PATHS) + 2) + "|"]
    for k in allc:
        out.append(f"| {k} | " + " | ".join(str(cats[p][k]) for p in PATHS) + f" | {sum(cats[p][k] for p in PATHS)} |")
    first = Counter(a["category"] for a in atts if a["attempt"] == 1 and not a["ok"])
    out += ["", "First-attempt failure categories: " + ", ".join(f"{k} {v}" for k, v in first.most_common()), ""]
    # Latency and sizes
    out += ["### Latency and size (per attempt)", "", "| path | ISA | median wall s | median first-text s | "
            "median first-insn s (stream) | median check s | median final code bytes |", "|---|---|---|---|---|---|---|"]
    med = lambda xs: f"{statistics.median(xs):.1f}" if xs else "-"  # noqa: E731
    for p in PATHS:
        for i in ISAS:
            aa = [a for a in atts if a["path"] == p and a["isa"] == i]
            if not aa:
                continue
            fi = [a["stream"]["first_insn_s"] for a in aa if a.get("stream") and a["stream"].get("first_insn_s")]
            sizes = [c["code_size"] for c in cells if c["path"] == p and c["isa"] == i and c["code_size"]]
            out.append(f"| {p} | {i} | {med([a['wall_s'] for a in aa])} | "
                       f"{med([a['first_text_s'] for a in aa if a['first_text_s'] is not None])} | {med(fi)} | "
                       f"{statistics.median([a['check_s'] for a in aa if a['check_s']] or [0]):.2f} | "
                       f"{med(sizes).replace('.0', '')} |")
    out.append("")
    # Stream early aborts
    aborts = [a for a in atts if a.get("stream") and a["stream"].get("abort")]
    if aborts:
        out += ["### Stream early aborts", "", "| cell | attempt | category | line | chars received | "
                "insns committed | abort at s | offending text |", "|---|---|---|---|---|---|---|---|"]
        for a in aborts:
            ab = a["stream"]["abort"]
            txt = ab["text"].strip().replace("|", "\\|")[:60]
            out.append(f"| {a['cell']} | {a['attempt']} | {a['category']} | {ab['line']} | {ab['chars_received']} | "
                       f"{ab['insns_committed']} | {ab['s']:.1f} | `{txt}` |")
        out.append("")
    models = Counter(a.get("model") for a in atts)
    cost = sum(a.get("cost_usd") or 0 for a in atts)
    out += [f"Models reported by the stream: {dict(models)}; reported cost (list price) ${cost:.2f}", ""]
    return "\n".join(out)
