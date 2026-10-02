"""Baseline vs `assisted` (bca/liveeval/assist.py) on rounds 1 + 2, per model. Baseline cells come from every
committed baseline run (the same records eval/xv_tables.py reads); assisted cells from runs whose variant is
"assisted". Only models with an assisted run are shown. Run from the repo root:
.venv/bin/python eval/assist_tables.py"""
import collections
import glob
import json

BATTERIES = ("round1", "round2")
PATHS = ("stream", "structured", "ir")
NAMES = {"claude-haiku-4-5-20251001": "Claude Haiku 4.5", "codex:gpt-6-luna": "Codex GPT-6 Luna",
         "copilot:mai-code-1.1-flash": "Microsoft MAI-Code 1.1 Flash (Copilot)",
         "local:qwen3-coder-next:q4_K_M": "Qwen3-Coder-Next Q4 (local)"}


def label_of(r: dict, rd: str) -> str:
    subj = r.get("subject") or "opus"
    if subj.startswith(("codex_", "kimi_", "agy_", "grok_", "copilot_")):
        return subj.replace("_", ":", 1).replace("kimi-code_", "kimi-code/")
    if subj.startswith("local_"):
        return "local:" + (r.get("model") or subj[6:])
    if subj.startswith("claude-"):
        return subj
    return r.get("model") or subj


recs = {"baseline": {}, "assisted": {}}
for f in sorted(glob.glob("eval/runs/*/**/results.jsonl", recursive=True) + glob.glob("eval/runs/*/results.jsonl")):
    if "smoke" in f:
        continue
    rd = f.split("/")[2]
    for line in open(f):
        r = json.loads(line)
        v = r.get("variant", "baseline")
        if v not in recs:
            continue
        bat = r.get("battery") or ("round2" if "round2" in rd else "round1")
        path = r["cell"].split(".")[1]
        if bat not in BATTERIES or path not in PATHS:
            continue
        recs[v][(label_of(r, rd), r["cell"])] = r

models = sorted({m for m, _ in recs["assisted"]}, key=lambda m: list(NAMES).index(m) if m in NAMES else 99)


def stats(rs):
    n = len(rs)
    p1 = sum(bool(r["pass_at_1"]) for r in rs)
    fin = sum(bool(r["pass_final"]) for r in rs)
    att = sum(r["attempts"] for r in rs) / n if n else 0
    return n, p1, fin, att


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "—"


print("| Model | Variant | Cells | First attempt | Within 4 | Mean attempts | Stream | Structured | IR (QBE) |")
print("|---|---|---|---|---|---|---|---|---|")
for m in models:
    a_cells = {c for mm, c in recs["assisted"] if mm == m}
    for v in ("baseline", "assisted"):
        rs = [recs[v][(m, c)] for c in sorted(a_cells) if (m, c) in recs[v]]
        n, p1, fin, att = stats(rs)
        per = []
        for p in PATHS:
            pr = [r for r in rs if r["cell"].split(".")[1] == p]
            per.append(pct(sum(bool(r["pass_at_1"]) for r in pr), len(pr)))
        print(f"| {NAMES.get(m, m)} | {v} | {n} | **{pct(p1, n)}** | {pct(fin, n)} | {att:.2f} | {' | '.join(per)} |")
print()
print("First-attempt failure categories (cells where the first attempt failed), baseline -> assisted")
print()
cats = ["compile", "syntax", "format", "crosscheck", "wrong_result", "abi", "crash", "oob", "hang",
        "misaligned_call", "subject_timeout", "refusal"]
print("| Model | " + " | ".join(cats) + " | other |")
print("|---|" + "---|" * (len(cats) + 1))


def first_cats(v, m, cells):
    c = collections.Counter()
    for cell in cells:
        r = recs[v].get((m, cell))
        if r is None or r["pass_at_1"]:
            continue
        cat = (r.get("categories") or [None])[0] or "other"
        c[cat if cat in cats else "other"] += 1
    return c


for m in models:
    a_cells = sorted({c for mm, c in recs["assisted"] if mm == m})
    both = [c for c in a_cells if (m, c) in recs["baseline"]]
    b, a = first_cats("baseline", m, both), first_cats("assisted", m, both)
    print(f"| {NAMES.get(m, m)} | " + " | ".join(f"{b[k]}→{a[k]}" for k in cats + ["other"]) + " |")
