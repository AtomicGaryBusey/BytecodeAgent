"""Hex-path tables (the model writes raw machine code itself; no assembler): round-1 functions and whole
programs (app battery, sample 1), each next to the same model's committed baseline results on the other
paths. Run from the repo root: .venv/bin/python eval/hex_tables.py"""
import collections
import glob
import json

NAMES = {
    "codex:gpt-6-astra": "Codex GPT-6 Astra", "codex:gpt-6-sol": "Codex GPT-6 Sol", "codex:gpt-5.5": "Codex GPT-5.5",
    "codex:gpt-daybreak-blue-latest": "Codex Daybreak Blue", "codex:gpt-6-luna": "Codex GPT-6 Luna",
    "claude-opus-5-5": "Claude Opus 5.5", "claude-fable-5-1": "Claude Fable 5.1", "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5", "claude-opus-4-8": "Claude Opus 4.8",
    "claude-sonnet-4-5-20250929": "Claude Sonnet 4.5", "copilot:mai-code-1.1-flash": "Microsoft MAI-Code 1.1 Flash (Copilot)",
    "copilot:claude-sonnet-5.5": "Claude Sonnet 5.5 via Copilot", "copilot:gpt-6-luna": "GPT-6 Luna via Copilot",
    "grok:grok-4.7-build-fast": "Grok 4.7 build-fast",
}


def label_of(r: dict) -> str:
    subj = r.get("subject") or ""
    if subj.startswith(("codex_", "kimi_", "agy_", "grok_", "copilot_")):
        return subj.replace("_", ":", 1).replace("kimi-code_", "kimi-code/")
    return subj if subj.startswith("claude-") else (r.get("model") or subj)


def load(pattern: str):
    out = {}
    for f in sorted(glob.glob(pattern, recursive=True)):
        if "smoke" in f:
            continue
        rd = f.split("/")[2]
        for line in open(f):
            r = json.loads(line)
            if r.get("variant", "baseline") != "baseline":
                continue
            # the earliest runs record no battery (as in eval/xv_tables.py: inferred from the run dir)
            bat = r.get("battery") or ("round2" if "round2" in rd else "round1")
            out[(label_of(r), bat, r["cell"])] = r
    return out


def path_of(r: dict) -> str:
    return r.get("path") or r["cell"].split(".")[1]


def frac(rs: list, key: str) -> str:
    return f"{sum(bool(r[key]) for r in rs)}/{len(rs)}" if rs else "—"


fn = load("eval/runs/*/**/results.jsonl")
by = collections.defaultdict(lambda: collections.defaultdict(list))  # label -> path -> round-1 records
for (label, bat, cell), r in fn.items():
    if bat == "round1":
        by[label][path_of(r)].append(r)
hex_models = [m for m in by if by[m]["hex"]]
hex_models.sort(key=lambda m: (-sum(bool(r["pass_at_1"]) for r in by[m]["hex"]), -sum(bool(r["pass_final"]) for r in by[m]["hex"])))

print("Round 1, functions (8 tasks x 2 ISAs = 16 cells per path): first attempt; hex also within 4")
print()
print("| Model | Hex first attempt | Hex within 4 | Stream | Structured | IR (QBE) |")
print("|---|---|---|---|---|---|")
for m in hex_models:
    p = by[m]
    print(f"| {NAMES.get(m, m)} | **{frac(p['hex'], 'pass_at_1')}** | {frac(p['hex'], 'pass_final')} | "
          f"{frac(p['stream'], 'pass_at_1')} | {frac(p['structured'], 'pass_at_1')} | {frac(p['ir'], 'pass_at_1')} |")
print()
print("Per ISA, hex first attempt")
print()
print("| Model | AArch64 | x86-64 |")
print("|---|---|---|")
for m in hex_models:
    h = by[m]["hex"]
    print(f"| {NAMES.get(m, m)} | {frac([r for r in h if r['isa'] == 'aarch64'], 'pass_at_1')} | "
          f"{frac([r for r in h if r['isa'] == 'x86_64'], 'pass_at_1')} |")

apps = collections.defaultdict(lambda: collections.defaultdict(list))  # label -> path -> sample-1 app records
for (label, bat, cell), r in load("eval/runs/apps-*/*/baseline/results.jsonl").items():
    if bat == "apps" and r.get("sample") == 1:
        apps[label][path_of(r)].append(r)
app_models = [m for m in apps if apps[m]["hex"]]
if app_models:
    app_models.sort(key=lambda m: -sum(bool(r["pass_at_1"]) for r in apps[m]["hex"]))
    print()
    print("Whole programs (app battery, sample 1: 8 tasks x 2 ISAs = 16 cells per path)")
    print()
    print("| Model | Hex first attempt | Hex within 4 | asm first attempt | stream first attempt |")
    print("|---|---|---|---|---|")
    for m in app_models:
        a = apps[m]
        print(f"| {NAMES.get(m, m)} | **{frac(a['hex'], 'pass_at_1')}** | {frac(a['hex'], 'pass_final')} | "
              f"{frac(a['asm'], 'pass_at_1')} | {frac(a['stream'], 'pass_at_1')} |")
