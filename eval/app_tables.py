"""App-battery tables from every recorded app run in eval/runs (skips smokes). A superseded cell's re-run is
already the one in results.jsonl (see each run's ADJUSTMENTS.md). Run from the repo root:
.venv/bin/python eval/app_tables.py"""
import collections
import glob
import json

recs = {}
for f in sorted(glob.glob("eval/runs/apps-*/*/baseline/results.jsonl") + glob.glob("eval/runs/kolibri-budget-apps-*/*/baseline/results.jsonl")):
    if "smoke" in f:
        continue
    for line in open(f):
        r = json.loads(line)
        if r.get("battery") != "apps" or r.get("variant", "baseline") != "baseline":
            continue
        if r.get("path") not in ("asm", "stream"):  # the opt-in hex path is reported on its own
            continue
        subj = r.get("subject") or ""
        if subj.startswith(("codex_", "kimi_", "agy_", "grok_", "copilot_", "foundry_", "fireworks_", "apple_")):
            label = subj.replace("_", ":", 1).replace("kimi-code_", "kimi-code/")
        elif subj.startswith("local_"):
            think = (r.get("local") or {}).get("think")  # @think / @nothink runs are separate rows
            label = "local:" + (r.get("model") or subj[6:]) + ("" if think is None else "@think" if think else "@nothink")
        elif subj.startswith("vllm_"):  # vLLM-served open-weight models sit with the local ones
            label = "local:vllm:" + (r.get("model") or subj[5:])
        else:
            label = subj
        recs[(label, r["cell"])] = r
names = {"claude-opus-4-8": "Claude Opus 4.8", "claude-sonnet-5": "Claude Sonnet 5", "claude-sonnet-4-5-20250929": "Claude Sonnet 4.5", "claude-haiku-4-5-20251001": "Claude Haiku 4.5", "codex:gpt-6-sol": "Codex GPT-6 Sol", "codex:gpt-6-luna": "Codex GPT-6 Luna", "codex:gpt-5.5": "Codex GPT-5.5", "codex:gpt-daybreak-blue-latest": "Codex Daybreak Blue", "kimi:kimi-code/k3.1-pro": "Kimi K3.1 Pro", "kimi:kimi-code/kimi-for-coding": "Kimi K2.8 (for-coding)", "grok:grok-4.5": "Grok 4.5", "fireworks:deepseek-v4p1-flash": "DeepSeek V4.1 Flash (Fireworks)", "fireworks:nemotron-3-ultra-nvfp4": "NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks)", "fireworks:minimax-m3": "MiniMax M3 (Fireworks)", "fireworks:glm-5p3": "Z.ai GLM-5.3 (Fireworks)", "fireworks:qwen3p8-max": "Qwen 3.8 Max (Fireworks)", "foundry:mai-thinking-1": "Microsoft MAI-Thinking-1 (Azure Foundry)", "foundry:deepseek-v4-pro": "DeepSeek-V4-Pro high (Azure Foundry)", "copilot:gpt-6-luna": "GPT-6 Luna via Copilot", "copilot:claude-sonnet-5.5": "Claude Sonnet 5.5 via Copilot", "agy:gemini-3.8-flash-high": "Gemini 3.8 Flash High (Antigravity)", "codex:gpt-6-astra": "Codex GPT-6 Astra", "grok:grok-4.7-build-fast": "Grok 4.7 build-fast", "grok:grok-4.7": "Grok 4.7", "grok:grok-4.6": "Grok 4.6",
         "claude-opus-5-5": "Claude Opus 5.5", "claude-haiku-5-5": "Claude Haiku 5.5", "claude-fable-5-1": "Claude Fable 5.1", "claude-fable-5-2": "Claude Fable 5.2", "claude-mythos-5-1": "Claude Mythos 5.1", "claude-mythos-5-2": "Claude Mythos 5.2",
         "claude-sonnet-5-5": "Claude Sonnet 5.5", "copilot:mai-code-1.1-flash": "Microsoft MAI-Code 1.1 Flash (Copilot)",
         "kimi:kimi-code/k3": "Kimi K3", "local:vllm:Aleph-Alpha/Kolibri-1": "Aleph Alpha Kolibri-1 FP8, token budget (local)",
         "local:qwen3:30b-a3b-instruct-2507-q8_0": "Qwen3-30B-A3B Q8 (local)",
         "local:qwen2.5:14b": "Qwen2.5-14B Q4 (local)", "local:qwen3-coder-next:q4_K_M": "Qwen3-Coder-Next Q4 (local)",
         "local:qwen3.6:35b-a3b-q8_0@nothink": "Qwen3.6-35B-A3B Q8, thinking off (local)",
         "local:qwen3.6:35b-a3b-q8_0@think": "Qwen3.6-35B-A3B Q8, thinking on (local)"}
TASKS = ["wc", "rev_lines", "hexdump", "sort_lines", "rpn", "base64", "bf", "json_lint"]
FULL = 96  # 8 tasks x 2 paths x 2 ISAs x 3 samples

by = collections.defaultdict(list)
for (label, cell), r in recs.items():
    by[label].append(r)


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "—"


rows = []
for m, rs in by.items():
    n = len(rs)
    p1 = sum(bool(r["pass_at_1"]) for r in rs)
    fin = sum(bool(r["pass_final"]) for r in rs)
    rows.append((n == FULL, p1 / n, m, rs, n, p1, fin, sum(r["attempts"] for r in rs) / n))
rows.sort(key=lambda x: (-x[0], -x[1]))


def name(m, full):
    return names.get(m, m) + ("" if full else " †")


print("| Model | First attempt | Within 4 | Mean attempts | Sample 1 | Sample 2 | Sample 3 | pass@3 (groups) |")
print("|---|---|---|---|---|---|---|---|")
for full, _, m, rs, n, p1, fin, att in rows:
    per_s = []
    for s in (1, 2, 3):
        sr = [r for r in rs if r.get("sample") == s]
        per_s.append(f"{sum(bool(r['pass_at_1']) for r in sr)}/{len(sr)}" if sr else "—")
    groups = collections.defaultdict(bool)
    for r in rs:
        groups[(r["task"], r["path"], r["isa"])] |= bool(r["pass_at_1"])
    g = f"{sum(groups.values())}/{len(groups)}"
    print(f"| {name(m, full)} | **{pct(p1, n)}** | {pct(fin, n)} | {att:.2f} | {' | '.join(per_s)} | {g} |")
print()
print("First-attempt passes per task (asm + stream, both ISAs, 3 samples: 12 cells each)")
print()
print("| Model | " + " | ".join(TASKS) + " |")
print("|---|" + "---|" * len(TASKS))
for full, _, m, rs, *_ in rows:
    cells = []
    for t in TASKS:
        tr = [r for r in rs if r["task"] == t]
        cells.append(f"{sum(bool(r['pass_at_1']) for r in tr)}/{len(tr)}" if tr else "—")
    print(f"| {name(m, full)} | " + " | ".join(cells) + " |")
print()
print("First-attempt passes per path and ISA (24 cells each)")
print()
print("| Model | asm x86-64 | asm AArch64 | stream x86-64 | stream AArch64 |")
print("|---|---|---|---|---|")
for full, _, m, rs, *_ in rows:
    cells = []
    for p in ("asm", "stream"):
        for isa in ("x86_64", "aarch64"):
            pr = [r for r in rs if r["path"] == p and r["isa"] == isa]
            cells.append(f"{sum(bool(r['pass_at_1']) for r in pr)}/{len(pr)}" if pr else "—")
    print(f"| {name(m, full)} | " + " | ".join(cells) + " |")
print()
print("First-attempt failure categories")
print()
cats = sorted({(r.get("categories") or [None])[0] for rs in by.values() for r in rs} - {None})
print("| Model | " + " | ".join(cats) + " |")
print("|---|" + "---|" * len(cats))
for full, _, m, rs, *_ in rows:
    c = collections.Counter((r.get("categories") or [None])[0] for r in rs if not r["pass_at_1"])
    print(f"| {name(m, full)} | " + " | ".join(str(c.get(k, 0) or "·") for k in cats) + " |")
