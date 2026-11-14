"""Agent-harness study tables: models run inside their vendor's own coding-agent CLI where that CLI cannot be made
tool-free or prompt-free (Mistral Vibe, Meta Muse Code). Reported apart from every tool-free row and never ranked
with them (bca/liveeval/vendor_vibe.py, vendor_muse.py). Run from the repo root: .venv/bin/python eval/harness_tables.py"""
import collections
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_names import NEW_NAMES  # noqa: E402

BATS = ["round1", "round2", "long", "adversarial"]
TOT = {"round1": 48, "round2": 62, "long": 54, "adversarial": 60}


def label(subj: str) -> str:
    return subj.replace("_", ":", 1)


def kind(rd: str) -> str:
    return "hexprog" if rd.startswith("apps-hex") else "apps" if rd.startswith("apps") else "hexfn" if rd.startswith("hex") else "fn"


cells = collections.defaultdict(dict)   # (label, kind) -> cell -> record
attempts = collections.defaultdict(list)  # (label, kind) -> attempt records
infra = collections.Counter()
for f in sorted(glob.glob("eval/runs/*/vibe_*/baseline/results.jsonl") + glob.glob("eval/runs/*/muse_*/baseline/results.jsonl")):
    rd = f.split("/")[2]
    if "smoke" in rd:
        continue
    k = kind(rd)
    for line in open(f):
        r = json.loads(line)
        cells[(label(r["subject"]), k)][r["cell"]] = r
    for line in open(f.replace("results.jsonl", "attempts.jsonl")):
        a = json.loads(line)
        attempts[(label(a["subject"]), k)].append(a)
    inf = f.replace("results.jsonl", "infra.jsonl")
    if os.path.exists(inf):
        infra[label(f.split("/")[3])] += sum(1 for _ in open(inf))
models = sorted({m for m, _ in cells}, key=lambda m: -sum(bool(r["pass_at_1"]) for r in cells[(m, "fn")].values()))
name = lambda m: NEW_NAMES.get(m, m)  # noqa: E731


def frac(rs, key):
    rs = list(rs)
    return f"{sum(bool(r[key]) for r in rs)}/{len(rs)}" if rs else "—"


print("| Model (harness) | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |")
print("|---|---|---|---|---|---|---|---|")
for m in models:
    rs = cells[(m, "fn")]
    by = collections.defaultdict(list)
    for r in rs.values():
        by[r["battery"]].append(r)
    P = sum(bool(r["pass_at_1"]) for r in rs.values()); F = sum(bool(r["pass_final"]) for r in rs.values()); N = len(rs)
    print(f"| {name(m)} | " + " | ".join(str(sum(bool(r['pass_at_1']) for r in by[b])) if len(by[b]) == TOT[b] else frac(by[b], 'pass_at_1') for b in BATS)
          + f" | **{P}/{N} ({100 * P / N:.0f}%)** | {F}/{N} ({100 * F / N:.0f}%) | {sum(r['attempts'] for r in rs.values()) / N:.2f} |")
print()
print("| Model (harness) | Stream | Structured | IR (QBE) | Apps, first attempt (96) | Apps within 4 | Hex functions, first / within 4 (16) | Hex programs, first / within 4 (16) |")
print("|---|---|---|---|---|---|---|---|")
for m in models:
    rs = cells[(m, "fn")]
    p = collections.defaultdict(list)
    for c, r in rs.items():
        p[c.split(".")[1]].append(r)
    hp = cells.get((m, "hexprog"), {})
    print(f"| {name(m)} | " + " | ".join(frac(p[k], "pass_at_1") for k in ("stream", "structured", "ir"))
          + f" | {frac(cells[(m, 'apps')].values(), 'pass_at_1')} | {frac(cells[(m, 'apps')].values(), 'pass_final')}"
          + f" | {frac(cells[(m, 'hexfn')].values(), 'pass_at_1')} / {frac(cells[(m, 'hexfn')].values(), 'pass_final')}"
          + (f" | {frac(hp.values(), 'pass_at_1')} / {frac(hp.values(), 'pass_final')} |" if hp else " | not run (gate: 12/16 hex functions within 4) |"))
print()
print("What the harness added, from each CLI's own record of every scored attempt (all batteries):")
print()
print("| Model (harness) | Scored attempts | CLI versions | Timeouts | Median call | Median input / output tokens | Denied tool attempts | Harness lookups | Guard resamples | Infra retries (not scored) |")
print("|---|---|---|---|---|---|---|---|---|---|")
for m in models:
    al = [a for (mm, _), v in attempts.items() if mm == m for a in v]
    u = [a.get("usage") or {} for a in al]
    ver = collections.Counter(str(a.get("cli_version")) for a in al)
    tools = sum(x.get("tool_attempts") or 0 for x in u); calls_tools = sum(1 for x in u if x.get("tool_attempts"))
    look = sum(x.get("harness_lookups") or 0 for x in u)
    res = sum(x.get("guard_resamples") or 0 for x in u); calls_res = sum(1 for x in u if x.get("guard_resamples"))
    med = lambda k: statistics.median([x[k] for x in u if isinstance(x.get(k), int)] or [0])  # noqa: E731
    is_vibe = m.startswith("vibe:")
    print(f"| {name(m)} | {len(al)} | " + ", ".join(f"{v} ×{n}" for v, n in ver.most_common())
          + f" | {sum(a.get('category') == 'subject_timeout' for a in al)} | {statistics.median(a['wall_s'] for a in al):.0f} s"
          + f" | {med('input_tokens'):,.0f} / {med('output_tokens'):,.0f}"
          + (f" | {tools} in {calls_tools} attempts | {look}" if is_vibe else " | none possible (no tools sent) | —")
          + (" | —" if is_vibe else f" | {res} in {calls_res} attempts ({100 * calls_res / len(al):.0f}%)")
          + f" | {infra[m]} |")
print()
if any(m.startswith("vibe:") for m in models):
    print("Tools the Vibe models reached for (every attempt was denied; none ran):")
    print()
    print("| Model (harness) | Tool | Attempts naming it |")
    print("|---|---|---|")
    for m in models:
        c = collections.Counter(t for (mm, _), v in attempts.items() if mm == m for a in v for t in (a.get("usage") or {}).get("tools_attempted") or [])
        for t, n in c.most_common():
            print(f"| {name(m)} | `{t}` | {n} |")
