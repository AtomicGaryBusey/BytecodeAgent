"""Paired same-model comparisons on the 224 function cells: one model at two reasoning efforts, or served two ways
(host, serving tier). Each pair is the same cells, first attempt, exact McNemar. The `effort-study` run directories
are excluded from every other table. Run from the repo root: .venv/bin/python eval/effort_tables.py"""
import glob
import json
import statistics
from math import comb

PAIRS = [  # (group, label A, run dir A, label B, run dir B)
    ("Effort", "OpenAI gpt-oss-120b (Cerebras), default effort", "cerebras-gpt-oss-120b-20261111",
     "the same, `reasoning_effort: high`", "cerebras-gpt-oss-120b-effort-study-high-20261111"),
    ("Effort", "Kimi K3.1 Pro, `high` (catalog default since Kimi Code 2.5.3)", "kimi-k3.1-pro-20261111",
     "the same, `max` (catalog default on 2026-10-23)", "kimi-k3.1-pro-effort-study-max-20261023-150408"),
    ("Host", "OpenAI gpt-oss-120b on Fireworks", "fireworks-gpt-oss-120b-20261111",
     "the same on Cerebras", "cerebras-gpt-oss-120b-20261111"),
    ("Serving tier", "Kimi K3.1 Pro", "kimi-k3.1-pro-20261111",
     "Kimi K3.1 Pro Highspeed", "kimi-k3.1-pro-highspeed-20261111"),
    # A second, independent run of the long battery (54 cells) at the same settings: run-to-run variation.
    # `dir/subject` picks one subject of a run directory that holds several.
    ("Second sample (long battery)", "Grok 4.7 build-fast, 2026-09-29", "grok-4.7-build-fast-20260929-081019",
     "the same, 2026-11-13", "long-s2-grok-4.7-build-fast-20261113"),
    ("Second sample (long battery)", "Grok 4.7, 2026-09-28", "grok-4.7-20260928-231546",
     "the same, 2026-11-13", "long-s2-grok-4.7-20261113"),
    ("Second sample (long battery)", "Grok 4.6, 2026-09-29", "grok-r3-20260929-164507/grok_grok-4.6",
     "the same, 2026-11-13", "long-s2-grok-4.6-20261113"),
]


def load(rd):
    rd, _, subj = rd.partition("/")
    subj = subj or "*"
    cells = {f.split("/")[-2]: json.load(open(f)) for f in glob.glob(f"eval/runs/{rd}/{subj}/baseline/cells/*/cell.json")}
    att = [json.loads(ln) for f in glob.glob(f"eval/runs/{rd}/{subj}/baseline/attempts.jsonl") for ln in open(f)]
    return cells, att


def mcnemar(b, c):
    n = b + c
    return 1.0 if not n else min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def stats(att):
    u = [a.get("usage") or {} for a in att]
    out = [next(x[k] for k in ("output_tokens", "completion_tokens") if x.get(k)) for x in u
           if x.get("output_tokens") or x.get("completion_tokens")]  # every vendor counts reasoning inside these
    eff = sorted({str(a.get("thinking_effort") or (a.get("usage") or {}).get("thinking_effort") or a.get("effort")) for a in att})
    return (f"{statistics.median(a['wall_s'] for a in att):.1f} s", f"{statistics.median(out):,.0f}" if out else "—",
            sum(a.get("category") == "subject_timeout" for a in att), ", ".join(eff))


print("| Comparison | A | B | First attempt A → B | Only A / only B passed | p | Within 4 A → B | Median call A → B | Median output tokens A → B | Timeouts A → B |")
print("|---|---|---|---|---|---|---|---|---|---|")
detail = []
for group, la, da, lb, db in PAIRS:
    (ca, aa), (cb, ab) = load(da), load(db)
    ks = sorted(set(ca) & set(cb))
    pa = sum(bool(ca[k]["pass_at_1"]) for k in ks); pb = sum(bool(cb[k]["pass_at_1"]) for k in ks)
    oa = sum(bool(ca[k]["pass_at_1"]) and not cb[k]["pass_at_1"] for k in ks)
    ob = sum(bool(cb[k]["pass_at_1"]) and not ca[k]["pass_at_1"] for k in ks)
    fa = sum(bool(ca[k]["pass_final"]) for k in ks); fb = sum(bool(cb[k]["pass_final"]) for k in ks)
    kset = set(ks)  # only the cells both runs have (a second sample covers one battery of the first run)
    sa, sb = stats([a for a in aa if a["cell"] in kset]), stats([a for a in ab if a["cell"] in kset])
    p = mcnemar(oa, ob)
    print(f"| {group} | {la} | {lb} | {pa} → {pb} of {len(ks)} | {oa} / {ob} | {'< 0.001' if p < 0.001 else f'{p:.2g}'} | {fa} → {fb} | "
          f"{sa[0]} → {sb[0]} | {sa[1]} → {sb[1]} | {sa[2]} → {sb[2]} |")
    by = {}
    for k in ks:
        if bool(ca[k]["pass_at_1"]) != bool(cb[k]["pass_at_1"]):
            b = ca[k]["battery"]; by.setdefault(b, [0, 0]); by[b][0 if ca[k]["pass_at_1"] else 1] += 1
    detail.append((la, lb, by, sa[3], sb[3]))
print()
print("Where the disagreeing cells fall (only A passed / only B passed, by battery) and the effort each run recorded:")
print()
print("| A | B | Round 1 | Round 2 | Long | Adversarial | Recorded effort A | Recorded effort B |")
print("|---|---|---|---|---|---|---|---|")
for la, lb, by, ea, eb in detail:
    print(f"| {la} | {lb} | " + " | ".join(f"{by.get(b, [0, 0])[0]} / {by.get(b, [0, 0])[1]}" for b in ("round1", "round2", "long", "adversarial"))
          + f" | {ea} | {eb} |")
