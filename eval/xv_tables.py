"""Cross-vendor tables from every recorded baseline run in eval/runs (skips smokes), with the reviewed
false-reject corrections (ADJ) applied. Run from the repo root: .venv/bin/python eval/xv_tables.py"""
import json, glob, collections
recs = {}
for f in sorted(glob.glob("eval/runs/*/**/results.jsonl", recursive=True) + glob.glob("eval/runs/*/results.jsonl")):
    if "smoke" in f or "untuned-trial" in f: continue  # the untuned Kolibri wall-clock trial is not reported
    rd = f.split("/")[2]
    for l in open(f):
        r = json.loads(l)
        if r.get("variant", "baseline") != "baseline": continue
        subj = r.get("subject") or "opus"
        if subj.startswith(("codex_", "kimi_", "agy_", "grok_", "copilot_")):
            label = subj.replace("_", ":", 1).replace("kimi-code_", "kimi-code/")
        elif subj.startswith("local_"):  # @think / @nothink runs are separate rows
            think = (r.get("local") or {}).get("think")
            label = "local:" + (r.get("model") or subj[6:]) + ("" if think is None else "@think" if think else "@nothink")
        elif subj.startswith("vllm_"):  # vLLM-served open-weight models sit with the local ones
            label = "local:vllm:" + (r.get("model") or subj[5:])
        elif subj.startswith("claude-"): label = subj
        else: label = r.get("model") or subj
        bat = r.get("battery") or ("round2" if "round2" in rd else "round1")
        if bat == "apps": continue  # the app battery has its own tables
        if r.get("path", r["cell"].split(".")[1]) == "hex": continue  # the opt-in hex path: eval/hex_tables.py
        recs[(label, bat, r["cell"])] = r
names = {"claude-opus-5-5": "Claude Opus 5.5", "claude-fable-5-1": "Claude Fable 5.1", "grok:grok-4.7": "Grok 4.7",
 "grok:grok-4.7-build-fast": "Grok 4.7 build-fast", "kimi:kimi-code/k3": "Kimi K3", "kimi:kimi-code/kimi-for-coding": "Kimi K2.8 (for-coding)",
 "claude-sonnet-5-5": "Claude Sonnet 5.5", "codex:gpt-daybreak-blue-latest": "Codex Daybreak Blue", "codex:gpt-6-astra": "Codex GPT-6 Astra",
 "codex:gpt-6-sol": "Codex GPT-6 Sol", "codex:gpt-5.5": "Codex GPT-5.5", "grok:grok-4.6": "Grok 4.6", "grok:grok-4.5": "Grok 4.5",
 "claude-sonnet-5": "Claude Sonnet 5", "codex:gpt-6-luna": "Codex GPT-6 Luna", "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
 "claude-opus-4-8": "Claude Opus 4.8", "claude-sonnet-4-5-20250929": "Claude Sonnet 4.5",
 "agy:gemini-3.8-flash-high": "Gemini 3.8 Flash High (Antigravity)",
 "copilot:gpt-6-luna": "GPT-6 Luna via Copilot", "copilot:mai-code-1.1-flash": "Microsoft MAI-Code 1.1 Flash (Copilot)",
 "copilot:claude-sonnet-5.5": "Claude Sonnet 5.5 via Copilot",
 "local:qwen3:30b-a3b-instruct-2507-q8_0": "Qwen3-30B-A3B Instruct 2507 Q8 (local)", "local:qwen2.5:14b": "Qwen2.5-14B Q4 (local)",
 "local:qwen3-coder-next:q4_K_M": "Qwen3-Coder-Next Q4 (local)",
 "local:vllm:Aleph-Alpha/Kolibri-1": "Aleph Alpha Kolibri-1 FP8, token budget (local)",
 "local:qwen3.6:35b-a3b-q8_0@nothink": "Qwen3.6-35B-A3B Q8, thinking off (local)",
 "local:qwen3.6:35b-a3b-q8_0@think": "Qwen3.6-35B-A3B Q8, thinking on (local)"}
# reviewed harness false rejects counted as first-attempt passes: label -> [(battery, path, n)]
ADJ = {"codex:gpt-daybreak-blue-latest": [("adversarial", "stream", 1)],
       "grok:grok-4.7-build-fast": [("long", "structured", 3)],
       "copilot:gpt-6-luna": [("long", "stream", 1)],  # keystone refused an AArch64 mov-with-shift alias GNU as takes
       # +1 mat_pow.structured.x86_64: the encoder read a jump to the program's own label `cl` as register cl;
       # -1 vm_run.structured.aarch64: passed on a fresh re-run after the pre-fix output-cap abort; under the
       #    current rule the capped answer is scored, and it fails (format)
       "copilot:claude-sonnet-5.5": [("long", "structured", -1)]}  # vm_run output-cap do-over (pre-929d4577), scored as capped
bats = ["round1", "round2", "long", "adversarial"]; tot = {"round1": 48, "round2": 62, "long": 54, "adversarial": 60}
agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0.0]))
pth = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
for (label, bat, cell), r in recs.items():
    a = agg[label][bat]; a[0] += bool(r["pass_at_1"]); a[1] += bool(r["pass_final"]); a[2] += 1; a[3] += r["attempts"]
    p = cell.split(".")[1]; pth[label][p][0] += bool(r["pass_at_1"]); pth[label][p][1] += 1
for label, adj in ADJ.items():
    for b, p, n in adj:
        agg[label][b][0] += n; pth[label][p][0] += n
rows = []
for m, d in agg.items():
    P = sum(v[0] for v in d.values()); F = sum(v[1] for v in d.values()); N = sum(v[2] for v in d.values())
    rows.append((N == 224, P / N, m, d, P, F, N, sum(v[3] for v in d.values()) / N))
rows.sort(key=lambda x: (-x[0], -x[1]))


def main_table(rows):
    print("| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |")
    print("|---|---|---|---|---|---|---|---|")
    for full, _, m, d, P, F, N, A in rows:
        cells = " | ".join(str(d[b][0]) if d[b][2] == tot[b] else (f"{d[b][0]}/{d[b][2]}" if d[b][2] else "—") for b in bats)
        tag = "" if full else " †"
        print(f"| {names.get(m, m)}{tag} | {cells} | **{P}/{N} ({100*P/N:.0f}%)** | {F}/{N} ({100*F/N:.0f}%) | {A:.2f} |")


def path_table(rows, partial_ok=False):
    print("| Model | Stream | Structured | IR (QBE) |"); print("|---|---|---|---|")
    for full, _, m, d, *_ in rows:
        if not full and not partial_ok: continue
        q = pth[m]
        tag = "" if full else " †"
        print(f"| {names.get(m, m)}{tag} | " + " | ".join(
            f"{q[k][0]}/{q[k][1]} ({100*q[k][0]/q[k][1]:.0f}%)" if q[k][1] else "—" for k in ("stream", "structured", "ir")) + " |")


# Vendor models first (complete runs; partial ones marked †), then local open-weight models in their own tables:
# their quantised weights on one shared DGX Spark GPU are not the same kind of subject.
vendor = [r for r in rows if not r[2].startswith("local:")]
local = [r for r in rows if r[2].startswith("local:")]
main_table(vendor)
print()
path_table(vendor)
print()
main_table(local)
print()
path_table(local, partial_ok=True)
