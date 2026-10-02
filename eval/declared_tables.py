"""Statement-of-purpose tables (bca/liveeval/declared.py): whole programs as raw hex (app battery, sample 1),
each model's `declared` / `declared_annotated` runs next to its committed baseline, per ISA: first attempts
passed, cells passed within four attempts, and how many attempts the vendor's safety system blocked
(category `refusal`). Run from the repo root: .venv/bin/python eval/declared_tables.py"""
import collections
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bca.liveeval.summarize import load

NAMES = {"claude-opus-5-5": "Claude Opus 5.5", "codex_gpt-6-astra": "Codex GPT-6 Astra"}
VARIANTS = ("baseline", "declared", "declared_annotated")
groups = collections.defaultdict(list)  # (subject, variant) -> group dirs
for f in sorted(glob.glob("eval/runs/apps-hex-*/*/*/results.jsonl")):
    g = Path(f).parent
    subj, var = g.parent.name, g.name
    if "smoke" in f or var not in VARIANTS:
        continue
    groups[(subj, var)].append(g)
subjects = sorted({s for s, v in groups if v != "baseline"}, key=lambda s: NAMES.get(s, s))

print("| Model | Prompt | ISA | First attempt | Within 4 | Attempts | Blocked attempts | Cells blocked on every attempt |")
print("|---|---|---|---|---|---|---|---|")
for s in subjects:
    for v in VARIANTS:
        cells, atts = [], []
        for g in groups.get((s, v), []):
            c, a = load(g)
            cells += [r for r in c if r.get("sample") == 1]
            atts += a
        if not cells:
            continue
        for isa in ("x86_64", "aarch64", None):
            cs = [r for r in cells if isa is None or r["isa"] == isa]
            at = [a for a in atts if isa is None or a["isa"] == isa]
            ids = {r["cell"] for r in cs}
            at = [a for a in at if a["cell"] in ids]
            blocked = [a for a in at if a.get("category") == "refusal"]
            per_cell = collections.Counter(a["cell"] for a in at)
            all_blocked = sum(1 for c in ids if per_cell[c] and
                              all(a.get("category") == "refusal" for a in at if a["cell"] == c))
            p1 = sum(bool(r["pass_at_1"]) for r in cs)
            pf = sum(bool(r["pass_final"]) for r in cs)
            name = f"**{NAMES.get(s, s)}**" if isa is None else NAMES.get(s, s)
            print(f"| {name} | {v} | {isa or 'both'} | {p1}/{len(cs)} | {pf}/{len(cs)} | {len(at)} | "
                  f"{len(blocked)}/{len(at)} | {all_blocked} |")
