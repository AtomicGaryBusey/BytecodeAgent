"""Aggregate run directories into Markdown tables (used by reports/live-model-eval*.md).

A run directory is either flat (round 1 / round 2 layout: results.jsonl at the top) or has a
subject and variant dimension (`<run>/<subject>/<variant>/results.jsonl`, see runner.run_plan).
`summarize` prints the original tables for a flat directory, unchanged, and cross tables plus
the original tables per (subject, variant) otherwise. `compare` sets several run directories
side by side (model x battery, model x variant, model x path).

Fairness rules of the cross tables: tables across subjects/models by battery, path or ISA use the
baseline variant only (variants are their own dimension, in the "× variant" tables); every table
that compares subjects (or variants) uses only the cells completed by all of them (a call cap or an
aborted run can leave groups with different coverage), and says how many cells that excluded.
Variants flagged `conflicts_with_format` in adversarial.VARIANT_INFO are labelled as
format-compliance probes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from bca.liveeval.prompts import ISAS, PATHS
from bca.liveeval.tasks2 import ALL_TASKS


def load(run_dir: Path) -> tuple[list[dict], list[dict]]:
    cells = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    atts_path = run_dir / "attempts.jsonl"
    atts = [json.loads(l) for l in atts_path.read_text().splitlines() if l.strip()] if atts_path.exists() else []
    # A cell may be recorded twice if a run was interrupted between writes; keep the last.
    cells = list({c["cell"]: c for c in cells}.values())
    n_att = {c["cell"]: c["attempts"] for c in cells}
    # Attempts of unfinished cells are not counted; an attempt recorded twice (a run interrupted
    # between writes, or a pre-fix resume that restarted a cell at attempt 1) counts once, as the
    # last record, and attempts beyond the cell's final count are orphans of such a restart.
    last = {(a["cell"], a["attempt"]): a for a in atts if a["cell"] in n_att}
    atts = [a for a in last.values() if a["attempt"] <= n_att[a["cell"]]]
    return cells, atts


def load_infra(run_dir: Path) -> list[dict]:
    """Infrastructure failures (retried calls, not attempts) of one flat dir or group."""
    p = run_dir / "infra.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _rate(xs: list[bool]) -> str:
    return f"{sum(xs)}/{len(xs)} ({100 * sum(xs) / len(xs):.0f}%)" if xs else "-"


def _ordered_tasks(cells: list[dict]) -> list:
    """Tasks present in `cells`, in registry order (round 1, round 2, then the other batteries,
    then unknown names in order of appearance as N/A-free placeholders)."""
    present = list(dict.fromkeys(c["task"] for c in cells))
    tasks = [t for t in ALL_TASKS if t.name in present]
    missing = [n for n in present if n not in {t.name for t in tasks}]
    if missing:
        from bca.liveeval.registry import known_tasks

        known = known_tasks()
        tasks += [known[n][0] if n in known else _Unknown(n) for n in missing]
    return tasks


class _Unknown:
    def __init__(self, name: str):
        self.name = name
        self.na_paths: dict = {}


def summarize(run_dir: Path) -> str:
    """Markdown tables for a run directory: flat (round 1, round 2 or mixed task sets) or with a
    subject/variant dimension."""
    groups = find_groups(run_dir)
    if len(groups) == 1 and groups[0][0] == run_dir:
        return summarize_group(run_dir)
    return summarize_matrix(run_dir, groups)


def summarize_group(run_dir: Path) -> str:
    """The original tables for one flat run directory (or one subject/variant group)."""
    cells, atts = load(run_dir)
    by = {(c["task"], c["path"], c["isa"]): c for c in cells}
    TASKS = _ordered_tasks(cells)  # noqa: N806 - registry order
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
                    row.append("N/A" if p in t.na_paths else "not run")
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
    infra = load_infra(run_dir)
    out += ["### Overall", "", f"cells: {len(cells)}; pass@1 {_rate([c['pass_at_1'] for c in cells])}; "
            f"pass@≤4 {_rate([c['pass_final'] for c in cells])}; attempts (calls) {len(atts)}"
            + (f"; infrastructure errors (retried calls, not attempts) {len(infra)}" if infra else ""), ""]
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
    models = Counter(_model_label(a) for a in atts)
    out += [f"Models reported by the stream: {dict(models)}; reported cost (list price) {_cost_text(_cost(atts))}", ""]
    return "\n".join(out)



def _model_label(a: dict) -> str | None:
    """A model id as recorded; marked when the CLI never reported it (the id is the requested one)."""
    m = a.get("model")
    return f"{m} (requested; not reported)" if m and a.get("model_source") == "requested" else m


def _cost(atts: list[dict]) -> float | None:
    """Summed list-price cost, or None when no attempt reported one (vendor CLIs report none)."""
    costs = [a["cost_usd"] for a in atts if a.get("cost_usd") is not None]
    return sum(costs) if costs else None


def _cost_text(cost: float | None) -> str:
    return "n/a" if cost is None else f"${cost:.2f}"


# -- subject / variant / battery dimensions -------------------------------------------------------


def find_groups(run_dir: Path) -> list[tuple[Path, str | None, str]]:
    """(dir, subject or None for a flat dir, variant) for every results.jsonl in the run dir."""
    run_dir = Path(run_dir)
    out = []
    if (run_dir / "results.jsonl").exists():
        out.append((run_dir, None, "baseline"))
    for res in sorted(run_dir.glob("*/*/results.jsonl")):
        g = res.parent
        meta = {}
        if (g / "group.json").exists():
            meta = json.loads((g / "group.json").read_text())
        out.append((g, meta.get("subject", g.parent.name), meta.get("variant", g.name)))
    if not out:
        raise FileNotFoundError(f"no results.jsonl in {run_dir} or {run_dir}/<subject>/<variant>/")
    return out


def _dominant_model(recs: list[dict]) -> str | None:
    models = Counter(r.get("model") for r in recs if r.get("model"))
    return models.most_common(1)[0][0] if models else None


def load_all(run_dir: Path) -> tuple[list[dict], list[dict]]:
    """Cells and attempts of every group, each with subject, variant, battery and model filled in
    (a flat directory's subject is the model its stream reported)."""
    from bca.liveeval.registry import battery_of

    cells_all, atts_all = [], []
    bat_cache: dict[str, str] = {}
    for g, subject, variant in find_groups(run_dir):
        cells, atts = load(g)
        subj = subject or _dominant_model(cells) or "default"
        model = _dominant_model(cells) or subj
        for r in cells + atts:
            r.setdefault("subject", subj)
            r.setdefault("variant", variant)
            if not r.get("battery"):
                if r["task"] not in bat_cache:
                    bat_cache[r["task"]] = battery_of(r["task"])
                r["battery"] = bat_cache[r["task"]]
            r["subject_model"] = model
            r["run_dir"] = str(run_dir)
        cells_all += cells
        atts_all += atts
    return cells_all, atts_all


def _stats(cs: list[dict]) -> list[str]:
    return [str(len(cs)), _rate([c["pass_at_1"] for c in cs]), _rate([c["pass_final"] for c in cs]),
            f"{statistics.mean(c['attempts'] for c in cs):.2f}" if cs else "-"]


def _table(title: str, keys: list[str], cells: list[dict], keyfn, order=None, label=str, note: str = "") -> list[str]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in cells:
        groups[keyfn(c)].append(c)
    ks = sorted(groups, key=order) if order else sorted(groups)
    rows = [f"### {title}", ""] + ([note, ""] if note else [])
    rows += ["| " + " | ".join(keys) + " | cells | pass@1 | pass final | mean attempts |",
             "|" + "---|" * (len(keys) + 4)]
    rows += ["| " + " | ".join([*map(label, k), *_stats(groups[k])]) + " |" for k in ks]
    return rows + [""]


def _order(key_order):
    """Sort key for tuples of dimension values: known values in `key_order` order, then by name."""
    idx = {v: n for n, v in reversed(list(enumerate(key_order)))}
    return lambda k: tuple((idx.get(x, len(idx)), str(x)) for x in k)


def _battery_order():
    from bca.liveeval.registry import battery_names

    return battery_names()


def _vlabel(v: str) -> str:
    from bca.liveeval.registry import variant_label

    return variant_label(v)


def cell_key(c: dict) -> tuple:
    return (c["task"], c["path"], c["isa"])


def common(cells: list[dict], who, key=cell_key) -> tuple[list[dict], int, int]:
    """(the cells whose key(c) was completed by every who(c) group, keys kept, keys in total)."""
    have: dict = defaultdict(set)
    for c in cells:
        have[who(c)].add(key(c))
    if len(have) < 2:
        n = len({key(c) for c in cells})
        return cells, n, n
    shared = set.intersection(*have.values())
    total = len(set.union(*have.values()))
    return [c for c in cells if key(c) in shared], len(shared), total


def _common_note(kept: int, total: int, what: str) -> str:
    if kept == total:
        return ""
    if kept == 0:
        return f"_WARNING: no cell was completed by every {what}; this table is empty (see Coverage)._"
    return (f"_Restricted to the {kept} cells completed by every {what}; {total - kept} of {total} cells "
            "excluded (see Coverage)._")


def load_plan(run_dir: Path) -> dict[str, list[str]]:
    """plan.json's planned cells per group ("." flat, "<subject>/<variant>"), or {}."""
    p = Path(run_dir) / "plan.json"
    if not p.exists():
        return {}
    try:
        return dict(json.loads(p.read_text()).get("cells", {}))
    except (ValueError, AttributeError):
        return {}


def summarize_matrix(run_dir: Path, groups=None) -> str:
    """Coverage, then cross tables over subjects, variants, batteries, paths and ISAs (see the
    module docstring for the fairness rules), then the original tables for each (subject, variant)
    group."""
    groups = groups or find_groups(run_dir)
    cells, atts = load_all(run_dir)
    plan = load_plan(run_dir)
    subjects = list(dict.fromkeys(c["subject"] for c in cells))
    variants = list(dict.fromkeys(["baseline", *(c["variant"] for c in cells)]))
    vset = set(variants)
    order = _order([*subjects, *variants, *_battery_order(), *PATHS, *ISAS])
    lab = lambda x: _vlabel(x) if x in vset else str(x)  # noqa: E731
    out = [f"## Run {run_dir}", ""]
    # Subjects, variants and coverage
    out += ["### Subjects and variants", "", "| subject | model reported | variant | planned | cells | pass@1 | "
            "pass final | mean attempts | calls | infra errors | effort | CLI | cost (list) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s_ in subjects:
        for v in variants:
            cs = [c for c in cells if c["subject"] == s_ and c["variant"] == v]
            if not cs:
                continue
            aa = [a for a in atts if a["subject"] == s_ and a["variant"] == v]
            models = ", ".join(sorted({_model_label(a) for a in aa if a.get("model")})) or "-"
            cost = _cost(aa)
            g = next((g for g, gs, gv in groups if gs == s_ and gv == v), None)
            infra = len(load_infra(g)) if g is not None else 0
            planned = plan.get(f"{s_}/{v}")
            efforts = ", ".join(sorted({str(c.get("effort", "?")) for c in cs}))
            clis = ", ".join(sorted({str(c.get("cli_version", "?")) for c in cs}))
            out.append(f"| {s_} | {models} | {lab(v)} | {len(planned) if planned is not None else '?'} | "
                       + " | ".join(_stats(cs)) + f" | {len(aa) + infra} | {infra} | {efforts} | {clis} | {_cost_text(cost)} |")
    out += ["", "calls = scored attempts + infrastructure errors (retried calls that are not attempts); "
            "planned = cells the run was asked for (plan.json), cells = cells completed.", ""]
    probes = [v for v in variants if _vlabel(v) != v]
    if probes:
        out += [f"Format-compliance probes (the variant asks for an output format the pipelines reject; they "
                f"measure instruction-following, not code quality): {', '.join(probes)}.", ""]
    # Across subjects: baseline only, common cells
    base = [c for c in cells if c["variant"] == "baseline"]
    if base:
        bc, kept, total = common(base, lambda c: c["subject"])
        note = _common_note(kept, total, "subject")
        out += _table("By subject × path × ISA (baseline variant)", ["subject", "path", "ISA"], bc,
                      lambda c: (c["subject"], c["path"], c["isa"]), order, note=note)
        out += _table("By subject × battery (baseline variant)", ["subject", "battery"], bc,
                      lambda c: (c["subject"], c["battery"]), order, note=note)
    else:
        out += ["_No baseline cells: the subject × path × ISA and subject × battery tables are omitted "
                "(see the per-variant tables)._", ""]
    vc, kept, total = common(cells, lambda c: c["subject"], lambda c: (c["variant"], *cell_key(c)))
    out += _table("By subject × variant", ["subject", "variant"], vc, lambda c: (c["subject"], c["variant"]), order,
                  label=lab, note=_common_note(kept, total, "subject"))
    if len(variants) > 1:
        wc, kept, total = common(cells, lambda c: c["variant"], lambda c: (c["subject"], *cell_key(c)))
        note = _common_note(kept, total, "variant")
        out += _table("By variant × battery", ["variant", "battery"], wc,
                      lambda c: (c["variant"], c["battery"]), order, label=lab, note=note)
        out += _table("By variant × path", ["variant", "path"], wc, lambda c: (c["variant"], c["path"]), order,
                      label=lab, note=note)
    # Failure categories per subject (baseline) and per variant
    def cat_table(title, rows_of, cols, key):
        cats: dict[str, Counter] = defaultdict(Counter)
        for a in rows_of:
            if not a["ok"]:
                cats[key(a)][a["category"]] += 1
        allc = sorted({k for c in cats.values() for k in c})
        res = [f"### {title}", "", "| category | " + " | ".join(map(lab, cols)) + " | total |",
               "|---" * (len(cols) + 2) + "|"]
        for k in allc:
            res.append(f"| {k} | " + " | ".join(str(cats[x][k]) for x in cols) + f" | {sum(cats[x][k] for x in cols)} |")
        return res

    batts = [a for a in atts if a["variant"] == "baseline"]
    out += cat_table("Failed attempts by category and subject (baseline variant)", batts, subjects,
                     lambda a: a["subject"])
    first = {s_: Counter(a["category"] for a in batts if a["subject"] == s_ and a["attempt"] == 1 and not a["ok"])
             for s_ in subjects}
    out += [""] + [f"First-attempt failures, {s_} (baseline): "
                   + (", ".join(f"{k} {v}" for k, v in first[s_].most_common()) or "none") for s_ in subjects] + [""]
    if len(variants) > 1:
        out += cat_table("Failed attempts by category and variant", atts, variants, lambda a: a["variant"]) + [""]
    out += [f"cells: {len(cells)}; pass@1 {_rate([c['pass_at_1'] for c in cells])}; pass final "
            f"{_rate([c['pass_final'] for c in cells])}; attempts {len(atts)} (all subjects and variants pooled)", ""]
    for g, subject, variant in groups:
        name = f"{subject}/{lab(variant)}" if subject else f"(flat) {g}"
        body = summarize_group(g).replace("\n### ", "\n#### ")
        body = ("#" + body) if body.startswith("### ") else body
        out += [f"## {name}", "", body]
    return "\n".join(out)


def compare(run_dirs: list[Path]) -> str:
    """Several run directories side by side. Rows are models (the id the stream reported; the
    subject alias when nothing was reported); every completed cell counts, so the same cell in two
    run directories counts twice (two samples). The battery / path / ISA tables use the baseline
    variant and, with several models, only the cells every model completed (see Coverage)."""
    cells, atts = [], []
    src = ["### Sources", "", "| run dir | subject | model | batteries | variants | planned | cells | calls |",
           "|---|---|---|---|---|---|---|---|"]
    for d in run_dirs:
        cs, aa = load_all(Path(d))
        plan = load_plan(Path(d))
        groups = find_groups(Path(d))
        for s_ in dict.fromkeys(c["subject"] for c in cs):
            mine = [c for c in cs if c["subject"] == s_]
            gnames = [(f"{gs}/{gv}" if gs else ".") for _g, gs, gv in groups if (gs or s_) == s_]
            planned = sum(len(plan[g]) for g in gnames if g in plan) if any(g in plan for g in gnames) else "?"
            infra = sum(len(load_infra(g)) for g, gs, _v in groups if (gs or s_) == s_)
            src.append(f"| {d} | {s_} | {mine[0]['subject_model']} | "
                       f"{', '.join(dict.fromkeys(c['battery'] for c in mine))} | "
                       f"{', '.join(dict.fromkeys(_vlabel(c['variant']) for c in mine))} | {planned} | {len(mine)} | "
                       f"{sum(1 for a in aa if a['subject'] == s_) + infra} |")
        cells += cs
        atts += aa
    models = list(dict.fromkeys(c["subject_model"] for c in cells))
    base = [c for c in cells if c["variant"] == "baseline"]
    bcells, kept, total = common(base, lambda c: c["subject_model"])
    variants = list(dict.fromkeys(["baseline", *(c["variant"] for c in cells)]))
    variants = [v for v in variants if any(c["variant"] == v for c in cells)]
    vcells, vkept, vtotal = common(cells, lambda c: c["subject_model"], lambda c: (c["variant"], *cell_key(c)))

    def cols_of(cs, key, known):
        present = [b for b in known if any(key(c) == b for c in cs)]
        return present + list(dict.fromkeys(key(c) for c in cs if key(c) not in present))

    def pivot(title, cs, cols, colkey, metric, note="", label=str):
        rows = [f"### {title}", ""] + ([note, ""] if note else [])
        rows += ["| model | " + " | ".join(map(label, cols)) + " | all |", "|---" * (len(cols) + 2) + "|"]
        for m in models:
            mine = [c for c in cs if c["subject_model"] == m]
            vals = [metric([c for c in mine if colkey(c) == col]) for col in cols]
            rows.append(f"| {m} | " + " | ".join(vals) + f" | {metric(mine)} |")
        return rows + [""]

    p1 = lambda cs: _rate([c["pass_at_1"] for c in cs])  # noqa: E731
    fin = lambda cs: _rate([c["pass_final"] for c in cs])  # noqa: E731
    att = lambda cs: f"{statistics.mean(c['attempts'] for c in cs):.2f}" if cs else "-"  # noqa: E731
    out = ["## Comparison", ""] + src + [""]
    # Coverage
    out += ["### Coverage", "", "| model | cells (all variants) | baseline cells | baseline cells used below | "
            "pass@1 on all its baseline cells |", "|---|---|---|---|---|"]
    for m in models:
        mb = [c for c in base if c["subject_model"] == m]
        out.append(f"| {m} | {sum(1 for c in cells if c['subject_model'] == m)} | {len(mb)} | "
                   f"{sum(1 for c in bcells if c['subject_model'] == m)} | {p1(mb)} |")
    note = _common_note(kept, total, "model")
    out += ["", "The battery / path / ISA tables use the baseline variant"
            + (" and only the cells every model completed." if len(models) > 1 else "."), ""]
    batteries = cols_of(bcells, lambda c: c["battery"], _battery_order())
    out += pivot("pass@1 by model × battery", bcells, batteries, lambda c: c["battery"], p1, note)
    out += pivot("pass final by model × battery", bcells, batteries, lambda c: c["battery"], fin, note)
    out += pivot("mean attempts by model × battery", bcells, batteries, lambda c: c["battery"], att, note)
    if len(variants) > 1 or variants != ["baseline"]:
        out += pivot("pass@1 by model × variant", vcells, variants, lambda c: c["variant"], p1,
                     _common_note(vkept, vtotal, "model"), label=_vlabel)
    out += pivot("pass@1 by model × path", bcells, list(PATHS), lambda c: c["path"], p1, note)
    out += pivot("pass@1 by model × ISA", bcells, list(ISAS), lambda c: c["isa"], p1, note)
    cats: dict[str, Counter] = defaultdict(Counter)
    for a in atts:
        if not a["ok"] and a["variant"] == "baseline":
            cats[a["subject_model"]][a["category"]] += 1
    allc = sorted({k for c in cats.values() for k in c})
    if allc:
        out += ["### Failed attempts by category and model (baseline variant, all cells)", "",
                "| category | " + " | ".join(models) + " |", "|---" * (len(models) + 1) + "|"]
        out += [f"| {k} | " + " | ".join(str(cats[m][k]) for m in models) + " |" for k in allc] + [""]
    return "\n".join(out)
