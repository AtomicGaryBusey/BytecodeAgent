"""Analytics over every recorded baseline run in eval/runs: statistical confidence, repair dynamics, and task
difficulty / discrimination (TODO.md items 1-3). Prints the markdown body of reports/live-model-eval-analytics.md.
Run from the repo root: .venv/bin/python eval/analytics.py

Records are selected exactly as eval/xv_tables.py (function batteries) and eval/app_tables.py (app battery) select
them: baseline variant, no smokes, no hex path, the last record per (model, cell). The reviewed false-reject
corrections are applied cell by cell (CELL_ADJ below, the cells behind xv_tables' ADJ counts, as documented in the
cross-vendor report's false-reject review and the runs' ADJUSTMENTS.md), so every table here matches the
published first-attempt counts."""
import collections
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
_stdout = sys.stdout
sys.stdout = open(os.devnull, "w")  # xv_tables prints its tables on import; reuse its names and ADJ only
import xv_tables  # noqa: E402

sys.stdout.close()
sys.stdout = _stdout

NAMES = dict(xv_tables.names)
NAMES["local:qwen3:30b-a3b-instruct-2507-q8_0"] = "Qwen3-30B-A3B Instruct 2507 Q8 (local)"
ADJ = xv_tables.ADJ
# The cells behind xv_tables.ADJ: (model, battery, cell) -> corrected first-attempt verdict.
CELL_ADJ = {
    ("codex:gpt-daybreak-blue-latest", "adversarial", "max_byte.stream.aarch64"): True,  # uxtw encoding disagreement
    ("grok:grok-4.7-build-fast", "long", "vm_run.structured.aarch64"): True,  # grok CLI doubled a stream chunk
    ("grok:grok-4.7-build-fast", "long", "qsort_pairs.structured.x86_64"): True,
    ("grok:grok-4.7-build-fast", "long", "packbits_decode.structured.x86_64"): True,
    ("copilot:gpt-6-luna", "long", "mat_pow.stream.aarch64"): True,  # keystone refused a GNU-as alias
    ("copilot:claude-sonnet-5.5", "long", "vm_run.structured.aarch64"): False,  # pre-fix output-cap do-over
}
BATS = ["round1", "round2", "long", "adversarial"]
FULL_FN, FULL_APPS = 224, 96


def label_of(r: dict) -> str:
    subj = r.get("subject") or "opus"
    if subj.startswith(("codex_", "kimi_", "agy_", "grok_", "copilot_")):
        return subj.replace("_", ":", 1).replace("kimi-code_", "kimi-code/")
    if subj.startswith("local_"):
        think = (r.get("local") or {}).get("think")
        return "local:" + (r.get("model") or subj[6:]) + ("" if think is None else "@think" if think else "@nothink")
    if subj.startswith("vllm_"):
        return "local:vllm:" + (r.get("model") or subj[5:])
    if subj.startswith("claude-"):
        return subj
    return r.get("model") or subj


def load():
    fn, apps = {}, {}
    files = sorted(glob.glob("eval/runs/*/**/results.jsonl", recursive=True) + glob.glob("eval/runs/*/results.jsonl"))
    for f in files:
        if "smoke" in f or "untuned-trial" in f:
            continue
        rd = f.split("/")[2]
        for line in open(f):
            r = json.loads(line)
            if r.get("variant", "baseline") != "baseline":
                continue
            bat = r.get("battery") or ("round2" if "round2" in rd else "round1")
            path = r.get("path", r["cell"].split(".")[1])
            if path == "hex":
                continue
            r["_bat"] = bat
            r["_dir"] = os.path.dirname(f)
            if bat == "apps":
                apps[(label_of(r), r["cell"])] = r
            else:
                fn[(label_of(r), bat, r["cell"])] = r
    by_fn, by_apps = collections.defaultdict(dict), collections.defaultdict(dict)
    for (m, b, c), r in fn.items():
        if (m, b, c) in CELL_ADJ:
            r = dict(r, pass_at_1=CELL_ADJ[(m, b, c)])
        by_fn[m][(b, c)] = r
    for m, adj in ADJ.items():  # the cell list must account for every reviewed correction
        assert sum(n for _, _, n in adj) == sum(1 if v else -1 for (mm, _, _), v in CELL_ADJ.items() if mm == m), m
    for (m, c), r in apps.items():
        by_apps[m][c] = r
    return by_fn, by_apps


def name(m: str) -> str:
    return NAMES.get(m, m)


def is_local(m: str) -> bool:
    return m.startswith("local:")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on b and c discordant pairs (binomial, p = 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, running = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)"


def ci(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{100 * lo:.0f}-{100 * hi:.0f}%"


def pfmt(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


# ------------------------------------------------------------------------------------------------ 1. confidence
def confidence(by_fn, by_apps):
    print("## 1. Statistical confidence\n")
    print("### Function batteries (224 cells): 95% intervals\n")
    print("Wilson score intervals on the published counts (reviewed corrections included). An interval treats the")
    print("224 cells as independent draws, which they are not quite (cells share tasks across paths and ISAs), so")
    print("read the widths as a floor on the uncertainty.\n")
    rows = []
    for m, cells in by_fn.items():
        n = len(cells)
        p1 = sum(bool(r["pass_at_1"]) for r in cells.values())
        f4 = sum(bool(r["pass_final"]) for r in cells.values())
        rows.append((n == FULL_FN, is_local(m), -p1 / n, m, n, p1, f4))
    rows.sort(key=lambda r: (r[1], not r[0], r[2]))
    print("| Model | First attempt | 95% CI | Within 4 | 95% CI |")
    print("|---|---|---|---|---|")
    for full, _, _, m, n, p1, f4 in rows:
        tag = "" if full else " †"
        print(f"| {name(m)}{tag} | {pct(p1, n)} | {ci(p1, n)} | {pct(f4, n)} | {ci(f4, n)} |")
    print("\n† partial run (fewer than 224 cells); see the cross-vendor report.\n")

    # paired tests between complete vendor rows
    full_vendor = [r for r in rows if r[0] and not r[1]]
    print("### Which gaps are real: paired tests on shared cells\n")
    print("Every complete vendor model ran the same 224 cells, so two models can be compared cell by cell. The")
    print("exact McNemar test looks only at the cells where they disagree: *A only* = A passed on the first")
    print("attempt and B did not, *B only* the reverse. Adjacent models in the ranking, p-values Holm-adjusted")
    print("over the whole table.\n")
    pairs = []
    for i in range(len(full_vendor) - 1):
        a, b = full_vendor[i][3], full_vendor[i + 1][3]
        ca, cb = by_fn[a], by_fn[b]
        shared = set(ca) & set(cb)
        ab = sum(1 for k in shared if ca[k]["pass_at_1"] and not cb[k]["pass_at_1"])
        ba = sum(1 for k in shared if cb[k]["pass_at_1"] and not ca[k]["pass_at_1"])
        pairs.append((a, b, len(shared), ab, ba, mcnemar_exact(ab, ba)))
    adj = holm([p[5] for p in pairs])
    print("| A | B | A only | B only | p | Holm p |")
    print("|---|---|---|---|---|---|")
    for (a, b, n, ab, ba, p), q in zip(pairs, adj):
        mark = " **" if q < 0.05 else ""
        print(f"| {name(a)} | {name(b)} | {ab} | {ba} | {pfmt(p)} | {pfmt(q)}{mark.strip() and ' (significant)'} |")
    sig = [(a, b) for (a, b, *_), q in zip(pairs, adj) if q < 0.05]
    print()
    if sig:
        print("Adjacent gaps that survive correction: " + "; ".join(f"{name(a)} over {name(b)}" for a, b in sig) + ".\n")
    else:
        print("No adjacent gap survives correction.\n")

    # groups of statistically indistinguishable models: compare every complete vendor model with the leader
    lead = full_vendor[0][3]
    print(f"**Against the leader ({name(lead)}).** Each complete vendor model compared with it on the same cells,")
    print("Holm-adjusted over this list:\n")
    comps = []
    for r in full_vendor[1:]:
        m = r[3]
        ca, cb = by_fn[lead], by_fn[m]
        ab = sum(1 for k in ca if k in cb and ca[k]["pass_at_1"] and not cb[k]["pass_at_1"])
        ba = sum(1 for k in ca if k in cb and cb[k]["pass_at_1"] and not ca[k]["pass_at_1"])
        comps.append((m, ab, ba, mcnemar_exact(ab, ba)))
    adj2 = holm([c[3] for c in comps])
    tied = [m for (m, *_), q in zip(comps, adj2) if q >= 0.05]
    print("| Model | Leader only | Model only | Holm p |")
    print("|---|---|---|---|")
    for (m, ab, ba, _), q in zip(comps, adj2):
        print(f"| {name(m)} | {ab} | {ba} | {pfmt(q)} |")
    print()
    print(f"Statistically level with {name(lead)} on first-attempt passes (Holm p ≥ 0.05): "
          + (", ".join(name(m) for m in tied) if tied else "none") + ".\n")

    # apps
    print("### App battery (96 cells): intervals and sample-to-sample variation\n")
    print("Each app cell ran three independent samples, so the battery measures its own noise directly. *Sample")
    print("spread* is the range of the three 32-cell first-attempt scores; *groups agreeing* counts the 32")
    print("(task, path, ISA) groups whose three samples all passed or all failed on the first attempt.\n")
    print("| Model | First attempt | 95% CI | Samples (s1 / s2 / s3) | Spread | Groups agreeing |")
    print("|---|---|---|---|---|---|")
    arows = []
    for m, cells in by_apps.items():
        n = len(cells)
        p1 = sum(bool(r["pass_at_1"]) for r in cells.values())
        arows.append((n == FULL_APPS, -p1 / n, m, cells, n, p1))
    arows.sort(key=lambda r: (not r[0], r[1]))
    for full, _, m, cells, n, p1 in arows:
        per = collections.Counter()
        grp = collections.defaultdict(list)
        for c, r in cells.items():
            t, p, i, s = c.split(".")
            per[s] += bool(r["pass_at_1"])
            grp[(t, p, i)].append(bool(r["pass_at_1"]))
        samples = [per.get(s, 0) if any(c.endswith("." + s) for c in cells) else None for s in ("s1", "s2", "s3")]
        got = [x for x in samples if x is not None]
        spread = (max(got) - min(got)) if len(got) > 1 else "—"
        agree = sum(1 for v in grp.values() if len(v) == 3 and len(set(v)) == 1)
        full3 = sum(1 for v in grp.values() if len(v) == 3)
        tag = "" if full else " †"
        sstr = " / ".join("—" if x is None else str(x) for x in samples)
        print(f"| {name(m)}{tag} | {pct(p1, n)} | {ci(p1, n)} | {sstr} | {spread} | "
              f"{f'{agree}/{full3}' if full3 else '—'} |")
    print()


# ------------------------------------------------------------------------------------------------ 2. repair
def repair(by_fn, by_apps):
    print("## 2. Repair dynamics\n")
    print("A cell that fails its first attempt gets up to three more, each with the harness's exact failure")
    print("message. *Recovered* = cells that failed attempt 1 and passed by attempt 4. *Per-retry success* =")
    print("passes on attempts 2-4 divided by attempts 2-4 made: the chance that one repair attempt works.\n")

    def stats(cells):
        n = len(cells)
        p1 = sum(bool(r["pass_at_1"]) for r in cells)
        # a corrected first-attempt failure with no later attempts (CELL_ADJ's output-cap do-over) has no repair
        # history to measure, so it is left out here
        failed1 = [r for r in cells if not r["pass_at_1"] and r["attempts"] > 1]
        rec = sum(bool(r["pass_final"]) for r in failed1)
        tries = sum(r["attempts"] - 1 for r in failed1)
        by_k = collections.Counter()
        for r in failed1:
            if r["pass_final"]:
                by_k[r["attempts"]] += 1
        return n, p1, len(failed1), rec, tries, by_k

    for title, data, full in (("Function batteries", by_fn, FULL_FN), ("App battery", by_apps, FULL_APPS)):
        print(f"### {title}\n")
        print("| Model | First attempt | Failed attempt 1 | Recovered | Fixed on 2 / 3 / 4 | Per-retry success |")
        print("|---|---|---|---|---|---|")
        rows = []
        for m, cells in data.items():
            n, p1, f1, rec, tries, by_k = stats(list(cells.values()))
            rows.append((is_local(m), n != full, -rec / f1 if f1 else 0, m, n, p1, f1, rec, tries, by_k))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        for loc, partial, _, m, n, p1, f1, rec, tries, by_k in rows:
            if f1 < 5:  # too few failures to say anything about repair
                continue
            tag = " †" if partial else ""
            k = f"{by_k[2]} / {by_k[3]} / {by_k[4]}"
            print(f"| {name(m)}{tag} | {pct(p1, n)} | {f1} | {pct(rec, f1)} | {k} | "
                  f"{100 * sum(by_k.values()) / tries:.0f}% |" if tries else "| — |")
        print("\nModels with fewer than 5 first-attempt failures are left out (nothing to measure).\n")

    # by first-failure category, vendor vs local
    print("### Recovery by first-failure category (function and app batteries together)\n")
    print("What the first attempt failed with, and how often the cell was then recovered within four attempts.")
    print("Vendor and local models are counted separately; categories with fewer than 10 cells are left out.\n")
    agg = {"vendor": collections.defaultdict(lambda: [0, 0]), "local": collections.defaultdict(lambda: [0, 0])}
    for data in (by_fn, by_apps):
        for m, cells in data.items():
            g = "local" if is_local(m) else "vendor"
            for r in cells.values():
                if r["pass_at_1"] or r["attempts"] == 1:
                    continue
                c = r["categories"][0]
                agg[g][c][0] += 1
                agg[g][c][1] += bool(r["pass_final"])
    cats = sorted(set(agg["vendor"]) | set(agg["local"]),
                  key=lambda c: -(agg["vendor"][c][0] + agg["local"][c][0]))
    print("| First failure | Vendor cells | Vendor recovered | Local cells | Local recovered |")
    print("|---|---|---|---|---|")
    for c in cats:
        v, lo = agg["vendor"][c], agg["local"][c]
        if v[0] + lo[0] < 10:
            continue
        vs = pct(v[1], v[0]) if v[0] else "—"
        ls = pct(lo[1], lo[0]) if lo[0] else "—"
        print(f"| `{c}` | {v[0]} | {vs} | {lo[0]} | {ls} |")
    print()


# ------------------------------------------------------------------------------------------------ 3. difficulty
def difficulty(by_fn, by_apps):
    print("## 3. Task difficulty and discrimination\n")
    models = [m for m, cells in by_fn.items() if len(cells) == FULL_FN and not is_local(m)]
    locals_ = [m for m, cells in by_fn.items() if len(cells) == FULL_FN and is_local(m)]
    cells = sorted(next(iter(by_fn[m] for m in models)).keys())
    print(f"Function batteries, the {len(models)} vendor models with all 224 cells. A cell's *pass rate* is the share")
    print("of those models that passed it on the first attempt. *Discrimination* is the item-rest correlation:")
    print("the correlation, across models, between passing this cell and the model's score on the other 223.")
    print("High = the cell separates strong models from weaker ones; near zero or negative = it does not.\n")
    X = {c: [1.0 if by_fn[m][c]["pass_at_1"] else 0.0 for m in models] for c in cells}
    F = {c: [1.0 if by_fn[m][c]["pass_final"] else 0.0 for m in models] for c in cells}
    tot = [sum(X[c][j] for c in cells) for j in range(len(models))]
    rate = {c: sum(X[c]) / len(models) for c in cells}
    disc = {c: pearson(X[c], [tot[j] - X[c][j] for j in range(len(models))]) for c in cells}

    buckets = [(1.0, 1.0, "all models"), (0.75, 0.9999, "75-99%"), (0.5, 0.7499, "50-74%"),
               (0.25, 0.4999, "25-49%"), (0.0001, 0.2499, "1-24%"), (0.0, 0.0, "no model")]
    print("| First-attempt pass rate | Cells |")
    print("|---|---|")
    for lo, hi, lab in buckets:
        print(f"| {lab} | {sum(1 for c in cells if lo <= rate[c] <= hi)} |")
    print()

    print("**By battery and path** (mean first-attempt pass rate over the vendor models):\n")
    print("| Battery | Stream | Structured | IR (QBE) | All |")
    print("|---|---|---|---|---|")
    for b in BATS:
        row = []
        for p in ("stream", "structured", "ir"):
            cs = [c for c in cells if c[0] == b and c[1].split(".")[1] == p]
            row.append(f"{100 * sum(rate[c] for c in cs) / len(cs):.0f}%" if cs else "—")
        cs = [c for c in cells if c[0] == b]
        print(f"| {b} | " + " | ".join(row) + f" | {100 * sum(rate[c] for c in cs) / len(cs):.0f}% |")
    print()

    never = [c for c in cells if rate[c] == 0]
    print(f"### Cells no vendor model passes on the first attempt ({len(never)})\n")
    if never:
        print("| Battery | Cell | Vendor models passing within 4 | Local models passing first try |")
        print("|---|---|---|---|")
        for c in never:
            f4 = int(sum(F[c]))
            lp = sum(1 for m in locals_ if by_fn[m][c]["pass_at_1"])
            print(f"| {c[0]} | `{c[1]}` | {f4}/{len(models)} | {lp}/{len(locals_)} |")
        never4 = [c for c in never if sum(F[c]) == 0]
        print()
        print(f"{len(never4)} of these are never passed by any vendor model even within four attempts"
              + (": " + ", ".join(f"`{c[1]}`" for c in never4) if never4 else "") + ".\n")

    print("### The cells that decide the ranking\n")
    print("The 15 most discriminating cells (highest item-rest correlation) with their pass rate. Cells passed")
    print("by every model or by none carry no ranking information and are excluded.\n")
    ranked = sorted((c for c in cells if disc[c] is not None), key=lambda c: -disc[c])
    print("| Battery | Cell | Pass rate | Discrimination |")
    print("|---|---|---|---|")
    for c in ranked[:15]:
        print(f"| {c[0]} | `{c[1]}` | {100 * rate[c]:.0f}% | {disc[c]:.2f} |")
    neg = [c for c in ranked if disc[c] < 0]
    print()
    print(f"{len(neg)} cells correlate negatively with overall strength (weaker models pass them more often than")
    print("stronger ones); the 10 most negative:\n")
    print("| Battery | Cell | Pass rate | Discrimination |")
    print("|---|---|---|---|")
    for c in ranked[::-1][:10]:
        print(f"| {c[0]} | `{c[1]}` | {100 * rate[c]:.0f}% | {disc[c]:.2f} |")
    print()

    # informative cells: how many cells carry the ranking among the top models
    top = sorted(models, key=lambda m: -sum(X[c][models.index(m)] for c in cells))[:10]
    split = [c for c in cells if 0 < sum(by_fn[m][c]["pass_at_1"] for m in top) < len(top)]
    print(f"**Among the top 10 vendor models** ({', '.join(name(m) for m in top)}), only {len(split)} of the 224")
    print("cells are passed by some and failed by others on the first attempt; every other cell is passed (or")
    print("failed) by all ten, so the top of the ranking rests on those cells:\n")
    per = collections.Counter((c[0], c[1].split(".")[1]) for c in split)
    print("| Battery | Stream | Structured | IR (QBE) |")
    print("|---|---|---|---|")
    for b in BATS:
        print(f"| {b} | " + " | ".join(str(per.get((b, p), 0)) for p in ("stream", "structured", "ir")) + " |")
    print()

    # apps: per-task first-attempt rate over complete app models
    am = [m for m, cs in by_apps.items() if len(cs) == FULL_APPS and not is_local(m)]
    print(f"### App battery tasks (the {len(am)} complete vendor models, 12 cells each)\n")
    print("| Task | First attempt | Within 4 |")
    print("|---|---|---|")
    tasks = collections.OrderedDict()
    for t in ["wc", "rev_lines", "hexdump", "sort_lines", "rpn", "base64", "bf", "json_lint"]:
        k1 = sum(bool(r["pass_at_1"]) for m in am for c, r in by_apps[m].items() if c.split(".")[0] == t)
        k4 = sum(bool(r["pass_final"]) for m in am for c, r in by_apps[m].items() if c.split(".")[0] == t)
        n = sum(1 for m in am for c in by_apps[m] if c.split(".")[0] == t)
        tasks[t] = (k1, k4, n)
        print(f"| `{t}` | {pct(k1, n)} | {pct(k4, n)} |")
    print()


# ------------------------------------------------------------------------------------------------ 4. ISA and path
def isa_path(by_fn, by_apps):
    print("## 4. ISA and path gap\n")
    print("First-attempt pass rates on the function batteries by path and by ISA. The ISA comparison is paired:")
    print("each (battery, task, path) exists once per ISA, so *x86 only* counts tasks a model passed on x86-64")
    print("and failed on AArch64, *A64 only* the reverse. Exact McNemar, Holm-adjusted over the models listed.\n")
    rows = []
    for m, cells in by_fn.items():
        n = len(cells)
        per = collections.defaultdict(lambda: [0, 0])
        pairs = collections.defaultdict(dict)
        for (b, c), r in cells.items():
            t, p, i = c.split(".")
            per[p][0] += bool(r["pass_at_1"]); per[p][1] += 1
            per[i][0] += bool(r["pass_at_1"]); per[i][1] += 1
            pairs[(b, t, p)][i] = bool(r["pass_at_1"])
        xo = sum(1 for v in pairs.values() if len(v) == 2 and v["x86_64"] and not v["aarch64"])
        ao = sum(1 for v in pairs.values() if len(v) == 2 and v["aarch64"] and not v["x86_64"])
        p1 = sum(bool(r["pass_at_1"]) for r in cells.values())
        rows.append((is_local(m), n != FULL_FN, -p1 / n, m, per, xo, ao, mcnemar_exact(xo, ao)))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    adj = holm([r[7] for r in rows])

    def cell(k):
        return f"{100 * k[0] / k[1]:.0f}%" if k[1] else "—"

    print("| Model | Stream | Structured | IR (QBE) | x86-64 | AArch64 | x86 only / A64 only | p | Holm p |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (loc, partial, _, m, per, xo, ao, praw), q in zip(rows, adj):
        tag = " †" if partial else ""
        print(f"| {name(m)}{tag} | {cell(per['stream'])} | {cell(per['structured'])} | {cell(per['ir'])} | "
              f"{cell(per['x86_64'])} | {cell(per['aarch64'])} | {xo} / {ao} | {pfmt(praw)} | {pfmt(q)} |")
    print()
    sig = [(m, xo, ao) for (_, _, _, m, _, xo, ao, _), q in zip(rows, adj) if q < 0.05]
    print("Models with a significant ISA gap: " + ("; ".join(
        f"{name(m)} ({'x86-64' if xo > ao else 'AArch64'} better, {max(xo, ao)} vs {min(xo, ao)})" for m, xo, ao in sig)
        if sig else "none") + ".\n")

    vend = [m for m, c in by_fn.items() if len(c) == FULL_FN and not is_local(m)]
    print(f"**Path × ISA, mean over the {len(vend)} complete vendor models** (first attempt):\n")
    print("| Path | x86-64 | AArch64 |")
    print("|---|---|---|")
    for p in ("stream", "structured", "ir"):
        out = []
        for i in ("x86_64", "aarch64"):
            k = sum(bool(r["pass_at_1"]) for m in vend for (b, c), r in by_fn[m].items() if c.split(".")[1:] == [p, i])
            n = sum(1 for m in vend for (b, c) in by_fn[m] if c.split(".")[1:] == [p, i])
            out.append(f"{100 * k / n:.0f}%")
        print(f"| {'IR (QBE)' if p == 'ir' else p} | " + " | ".join(out) + " |")
    print()
    am = [m for m, c in by_apps.items() if len(c) == FULL_APPS and not is_local(m)]
    print(f"**App battery, the {len(am)} complete vendor models** (first attempt, {len(am) * 24} cells each):\n")
    print("| Path | x86-64 | AArch64 |")
    print("|---|---|---|")
    for p in ("asm", "stream"):
        out = []
        for i in ("x86_64", "aarch64"):
            k = sum(bool(r["pass_at_1"]) for m in am for c, r in by_apps[m].items() if c.split(".")[1:3] == [p, i])
            n = sum(1 for m in am for c in by_apps[m] if c.split(".")[1:3] == [p, i])
            out.append(pct(k, n))
        print(f"| {p} | " + " | ".join(out) + " |")
    print()


# ------------------------------------------------------------------------------------------------ 5. error classes
_ATTEMPT1: dict[str, dict] = {}


def first_failure(r: dict) -> str:
    """The recorded failure message of the cell's first attempt (the last attempt-1 record for the cell)."""
    d = r["_dir"]
    if d not in _ATTEMPT1:
        idx = {}
        path = os.path.join(d, "attempts.jsonl")
        if os.path.exists(path):
            rd = d.split("/")[2]
            for line in open(path):
                a = json.loads(line)
                if a.get("attempt") != 1:
                    continue
                bat = a.get("battery") or ("round2" if "round2" in rd else "round1")
                idx[(a.get("variant", "baseline"), bat, a["cell"])] = a
        _ATTEMPT1[d] = idx
    a = _ATTEMPT1[d].get((r.get("variant", "baseline"), r["_bat"], r["cell"]))
    if a is None:
        return ""
    f = a.get("failure") or a.get("subject_error") or ""
    return f if isinstance(f, str) else json.dumps(f)


QBE_OPS = re.compile(r"^(add|sub|mul|div|rem|udiv|urem|and|or|xor|sar|shr|shl|neg|copy|load|store|ext|trunc|"
                     r"c[a-z]+|cast|call|ret|jmp|jnz|alloc|phi|blit|sel|vaarg|vastart)[a-z0-9]*$")


def classify(cat: str, text: str) -> str:
    t = " ".join(text.split())
    low = t.lower()
    if cat == "refusal":
        return "refusal"
    if cat == "subject_timeout":
        return "timeout (no complete answer)"
    if cat == "output_limit":
        return "prose / markup instead of code"
    if cat in ("abi", "misaligned_call"):
        return "calling-convention violation"
    if cat in ("oob", "oob_read", "crash", "write_to_text"):
        return "memory fault / crash"
    if cat in ("bad_syscall", "no_exit", "wrong_status"):
        return "syscall / exit misuse"
    if cat in ("wrong_result", "wrong_output", "hang"):
        return "wrong result / runs forever"
    if "stage qbe" in low:
        if "invalid class specifier" in low:
            return "QBE: missing or wrong type class"
        m = re.search(r"qbe:-:(\d+): unknown keyword (\S+)", t)
        if m:
            tok = m.group(2)
            if tok[:1].isupper():
                return "prose / markup instead of code"
            if re.fullmatch(r"x[0-9a-fA-F]+", tok):
                return "QBE: hex literal (decimal only)"
            if QBE_OPS.match(tok):
                return "QBE: invented opcode"
            if m.group(1) == "1":
                return "QBE: malformed function signature"
            return "QBE: other syntax"
        if "invalid type for" in low or "phi" in low or "invalid jump to the start block" in low:
            return "QBE: type or SSA error"
        if "invalid character" in low and re.search(r"invalid character [0-9] \(", t):
            return "QBE: hex literal (decimal only)"
        if any(k in low for k in ("invalid function parameter", "function body must start", "top-level definition")):
            return "QBE: malformed function signature"
        return "QBE: other syntax"
    if cat == "compile":
        return "QBE: other syntax"
    if cat == "format" or "not a json object" in low:
        if "json" in low:
            return "malformed JSON"
        return "prose / markup instead of code"
    m = re.search(r"rejected line \d+ \(`([^`]*)`\)", t)
    if m and cat in ("syntax", "crosscheck", "format"):
        line = m.group(1)
        words = [w for w in re.findall(r"[A-Za-z]+", line) if len(w) >= 3]
        if len(words) >= 3 and not re.search(r"[#\[\]%$]", line) and (
                line[:1].isupper() or line.rstrip().endswith((".", ":")) or " the " in f" {line} "):
            return "prose / markup instead of code"  # the model's prose reached the assembler
    if "encoder rejected the program" in low:
        if "size mismatch" in low or re.search(r"is (64|32)-bit but", low) or "is 64-bit," in low:
            return "invalid operand combination"
        return "structured: schema violation"
    if any(k in low for k in ("immediate cannot be moved", "immediate out of range", "out of range", "invalid shift amount",
                              "not a valid base/index", "rip cannot be scaled", "invalid address expression",
                              "invalid immediate", "bitmask", "cannot be encoded")):
        return "operand not encodable (immediate / addressing)"
    if re.search(r"no such instruction: .(section|global|extern|default|bits|db|dq|resb|resq)\b", low) or \
            re.search(r"rejected line \d+ \(.(section|global|extern|default rel|bits)\b", low):
        return "wrong assembler dialect (NASM)"
    if any(k in low for k in ("not allowed in .bss", "not allowed in .text", "instructions belong in .text",
                              "before this definition", "unsupported directive", "is not supported (use",
                              "no `.rodata` directive", "has no `.rodata`", "nothing may be placed")):
        return "program-format rule broken"
    if any(k in low for k in ("invalid mnemonic", "no such instruction", "unknown mnemonic", "produced no bytes")):
        return "invented / malformed instruction"
    if any(k in low for k in ("duplicate label", "undefined label", "undefined symbol", "unknown symbol", "local label",
                              "never defined", "forward reference")):
        return "label / symbol error"
    if any(k in low for k in ("operand type mismatch", "operand size mismatch", "operand mismatch", "invalid operand",
                              "number of operands", "expected a register", "invalid use of", "register")):
        return "invalid operand combination"
    if cat in ("syntax", "crosscheck", "link", "encoding"):
        return "other assembler / encoder reject"
    return "other"


CLASS_ORDER = ["QBE: missing or wrong type class", "QBE: malformed function signature", "QBE: invented opcode",
               "QBE: hex literal (decimal only)", "QBE: type or SSA error", "QBE: other syntax",
               "operand not encodable (immediate / addressing)", "invalid operand combination",
               "invented / malformed instruction", "wrong assembler dialect (NASM)", "program-format rule broken",
               "label / symbol error", "structured: schema violation", "malformed JSON",
               "prose / markup instead of code", "other assembler / encoder reject",
               "calling-convention violation", "memory fault / crash", "syscall / exit misuse",
               "wrong result / runs forever", "timeout (no complete answer)", "refusal", "other"]


def error_classes(by_fn, by_apps):
    print("## 5. One error classification across models\n")
    print("Every first-attempt failure (function and app batteries, baseline) sorted into one shared set of classes")
    print("from its category and the harness's failure message. Static rejects are split by cause; runtime")
    print("failures by kind. Counts are cells.\n")
    prof = collections.defaultdict(collections.Counter)
    for data in (by_fn, by_apps):
        for m, cells in data.items():
            for r in cells.values():
                if r["pass_at_1"]:
                    continue
                prof[m][classify(r["categories"][0] or "", first_failure(r))] += 1
    total = collections.Counter()
    vtotal, ltotal = collections.Counter(), collections.Counter()
    for m, c in prof.items():
        total.update(c)
        (ltotal if is_local(m) else vtotal).update(c)
    print("### All models\n")
    print("| Class | Vendor models | Local models | All |")
    print("|---|---|---|---|")
    for k in CLASS_ORDER:
        if total[k]:
            print(f"| {k} | {vtotal[k]} | {ltotal[k]} | {total[k]} |")
    print(f"| **total** | {sum(vtotal.values())} | {sum(ltotal.values())} | {sum(total.values())} |\n")

    groups = [("QBE", [k for k in CLASS_ORDER if k.startswith("QBE")]),
              ("Encoding", ["operand not encodable (immediate / addressing)", "invalid operand combination",
                            "invented / malformed instruction", "other assembler / encoder reject"]),
              ("Rules / dialect", ["wrong assembler dialect (NASM)", "program-format rule broken", "label / symbol error"]),
              ("Output format", ["structured: schema violation", "malformed JSON", "prose / markup instead of code"]),
              ("ABI / memory", ["calling-convention violation", "memory fault / crash", "syscall / exit misuse"]),
              ("Logic", ["wrong result / runs forever"]),
              ("Timeout", ["timeout (no complete answer)"]),
              ("Refusal", ["refusal"])]
    print("### Per model\n")
    print("First-attempt failures per model, grouped. *QBE* = the QBE classes above; *Encoding* = operands not")
    print("encodable, invalid operand combinations, invented instructions; *Rules / dialect* = NASM syntax on the")
    print("GNU path, the prompt's section and constant rules, label errors; *Output format* = structured schema,")
    print("malformed JSON, prose or markup instead of code; *ABI / memory* = calling convention, memory faults,")
    print("syscall misuse. The largest group per model is in bold.\n")
    order = sorted(prof, key=lambda m: (is_local(m), sum(prof[m].values())))
    print("| Model | Failures | " + " | ".join(g for g, _ in groups) + " | Other |")
    print("|---|---|" + "---|" * (len(groups) + 1))
    for m in order:
        c = prof[m]
        vals = [sum(c[k] for k in ks) for _, ks in groups]
        other = sum(c.values()) - sum(vals)
        top = max(vals)
        cellsf = [(f"**{v}**" if v == top and v else (str(v) if v else "·")) for v in vals]
        partial = len(by_fn.get(m, {})) not in (0, FULL_FN)
        print(f"| {name(m)}{' †' if partial else ''} | {sum(c.values())} | " + " | ".join(cellsf) + f" | {other or '·'} |")
    print()
    print("The QBE classes in detail, vendor models only (where QBE failures cluster):\n")
    print("| Model | " + " | ".join(k.replace("QBE: ", "") for k in groups[0][1]) + " |")
    print("|---|" + "---|" * len(groups[0][1]))
    for m in order:
        if is_local(m) or not sum(prof[m][k] for k in groups[0][1]):
            continue
        print(f"| {name(m)} | " + " | ".join(str(prof[m][k]) or "·" for k in groups[0][1]) + " |")
    print()


# ------------------------------------------------------------------------------------------------ 6. variants
def variants(by_fn):
    print("## 6. Prompt-variant effects\n")
    print("Each variant was run on the same cells as the model's baseline, so the effect is a paired difference.")
    print("*Gained* = cells failed on the first attempt at baseline and passed with the variant; *lost* the reverse.")
    print("Exact McNemar on those two counts (unadjusted; the variants answer different questions).\n")
    vrecs = {}
    hexbase = {}
    for f in sorted(glob.glob("eval/runs/*/**/results.jsonl", recursive=True)):
        if "smoke" in f:
            continue
        rd = f.split("/")[2]
        for line in open(f):
            r = json.loads(line)
            v = r.get("variant", "baseline")
            bat = r.get("battery") or ("round2" if "round2" in rd else "round1")
            path = r.get("path", r["cell"].split(".")[1])
            m = label_of(r)
            if v == "baseline":
                if bat == "apps" and path == "hex":
                    hexbase[(m, r["cell"])] = r
                continue
            vrecs[(m, v, bat, r["cell"])] = r
    groups = collections.defaultdict(dict)
    for (m, v, bat, c), r in vrecs.items():
        base = hexbase.get((m, c)) if bat == "apps" else by_fn.get(m, {}).get((bat, c))
        if base is not None:
            batg = "apps (hex path)" if bat == "apps" else ("rounds 1 + 2" if bat in ("round1", "round2") else bat)
            groups[(v, batg, m)][(bat, c)] = (base, r)
    print("| Variant | Cells | Model | Baseline | Variant | Gained / lost | p | Within 4: baseline → variant |")
    print("|---|---|---|---|---|---|---|---|")
    for (v, batg, m), pairs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        n = len(pairs)
        b1 = sum(bool(b["pass_at_1"]) for b, _ in pairs.values())
        v1 = sum(bool(x["pass_at_1"]) for _, x in pairs.values())
        g = sum(1 for b, x in pairs.values() if x["pass_at_1"] and not b["pass_at_1"])
        lo = sum(1 for b, x in pairs.values() if b["pass_at_1"] and not x["pass_at_1"])
        b4 = sum(bool(b["pass_final"]) for b, _ in pairs.values())
        v4 = sum(bool(x["pass_final"]) for _, x in pairs.values())
        print(f"| `{v}` | {batg} ({n}) | {name(m)} | {pct(b1, n)} | {pct(v1, n)} | +{g} / −{lo} | "
              f"{pfmt(mcnemar_exact(g, lo))} | {b4} → {v4} |")
    print()
    refusals = collections.Counter()
    for (m, v, bat, c), r in vrecs.items():
        if r["categories"] and r["categories"][0] == "refusal":
            refusals[(m, v)] += 1
    if refusals:
        print("First-attempt refusals inside the variant runs: " + "; ".join(
            f"{name(m)} `{v}` {k}" for (m, v), k in sorted(refusals.items())) + ".\n")


def main():
    by_fn, by_apps = load()
    confidence(by_fn, by_apps)
    repair(by_fn, by_apps)
    difficulty(by_fn, by_apps)
    isa_path(by_fn, by_apps)
    error_classes(by_fn, by_apps)
    variants(by_fn)


if __name__ == "__main__":
    main()
