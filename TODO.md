# TODO — analytics backlog

Further reporting on the recorded live-eval data (`eval/runs/`, ~15,000 attempts across ~35 models and
variants). Everything here is analysis of committed data — no model calls, no plan usage — except item 10, which
re-executes passing programs in the emulator. Each item lands as a script in `eval/` plus a section in a report.

## Done (`eval/analytics.py`, `reports/live-model-eval-analytics.md`)

1. **Statistical confidence.** Wilson 95% intervals on every pass rate; paired exact McNemar tests on the cells
   two models share, to say which ranking gaps are real; app-battery sample-to-sample variance from the three
   samples. → `eval/analytics.py`, `reports/live-model-eval-analytics.md`.
2. **Repair dynamics.** For cells that fail attempt 1: how often each model recovers, by failure category and by
   attempt number; "right first time" against "good debugger". Same script and report.
3. **Task difficulty and discrimination.** Per-cell pass rate across models; cells nobody passes (and whether any
   are suspect), cells everybody passes, and the cells that decide the ranking (item-rest correlation). Same
   script and report.
4. **ISA and path gap.** x86-64 vs AArch64 and stream / structured / QBE for every model in one cross-model view.
5. **One error classification across models.** Normalise failure messages into shared classes (encoding limits,
   invented mnemonics, ABI / callee-saved violations, format / JSON, QBE syntax, syscall misuse) and profile each
   model.
6. **Prompt-variant effects.** Paired deltas against baseline on the same cells: `misleading_hint`, `terse`,
   `assisted`, `declared`.
7. **Refusals.** Fable 5.1 (52), Opus 5.5 (56), Sonnet 5.5 via Copilot (52): which tasks and variants trigger
   them; read against the CVP caveat.
8. **Within-vendor generations.** Opus 4.8 → 5.5, Sonnet 4.5 → 5 → 5.5, Grok 4.5 → 4.6 → 4.7, GPT-5.5 → GPT-6
   (Luna / Sol / Astra).
9. **Efficiency.** Output tokens and wall time per passing cell; accuracy against tokens spent. Caveat: local
   latencies include queue waits (Ollama's `eval_count` gives the Qwen models' token counts).
10. **Quality of passing code.** Code size (recorded) plus executed instruction counts from re-running each
    passing program in the emulator: how fast correct solutions are, not only whether they pass.
    → `eval/code_quality.py` writes `eval/code_quality.jsonl`; section 10 of the report reads it.
11. **Harness and infrastructure audit.** One summary of false rejects found and fixed, infra error rates per
    vendor, and quota behaviour (now spread across per-run notes).

## Approved pick-up runs (2026-10-22, all done)

Approved by the user on 2026-10-22. After each: by-hand false-reject review, then regenerate the tables,
`eval/analytics.py` and `eval/dashboard.py`, and update the reports.

- [x] **App battery, Grok 4.7 and Grok 4.6**: done 2026-10-22. Grok 4.7 89/96 first try, Grok 4.6 95/96 (after one
  harness false reject was fixed and its cell re-run), both 96/96 within four. Reviewed.
- [x] **Kimi K3 app samples 2 and 3**: done 2026-10-22. 83/96 first try, 96/96 within four; the † is gone. Reviewed:
  no false rejects.
- [x] **Linker false reject found in the Grok 4.6 review**: section alignment above 4 KiB (AArch64 `.align 16`)
  was refused although GNU ld accepts it. Fixed in `bca/image.py` with a test; the Grok 4.6 cell was superseded
  and re-run (passed on attempt 1).
- [x] **Re-run Kolibri-1 `sort_lines.asm.aarch64.s3`** under the fixed linker: done 2026-10-23. Failed all four
  attempts again, genuinely; totals unchanged. Container stopped again afterwards.
- [x] **Hex runs for Claude Sonnet 5**: done 2026-10-22 (functions 13/16 first try, 16/16 within four; whole
  programs 7/16 and 9/16). Reviewed: no false rejects. In `reports/live-model-eval-hex.md`.
- [x] **Stale "Pending" table in `reports/live-model-eval-hex.md`** (it said the local Qwen hex runs were waiting
  for the GPU; they were never run and are not planned). Fixed 2026-10-22.

## Done (pick-up, 2026-10-24)

- [x] **Kimi K3.1 Pro, function batteries**: 218/224 first try (97%), 223/224 within four; second to Astra (221)
  and statistically level with it. Kimi CLI 2.3.1 re-audited (f9078a56). Reviewed: no false rejects; one odd
  reply is logged for follow-up in the gitignored `followups/` folder. Not yet decided: apps (96 cells), hex
  (32 cells), and the K3.1 Pro Highspeed variant.

## Done (2026-11-07/08)

- [x] **Claude Haiku 5.5** (via `claude -p` only): functions 203/224, apps 88/96, hex functions 13/16 and whole
  programs 11/16 (all 16/16 or 96/96 within four, except functions 222/224). Reviewed: no false rejects.
- [x] **Azure AI Foundry route** (`foundry:`, pay-as-you-go, $25 per-model spend cap): MAI-Thinking-1 72/224
  ($24.52; cap raised to $28 to finish) and DeepSeek-V4-Pro at reasoning high 139/224 ($11.88), function
  batteries only. Reviewed: no false rejects. mai-code-1.1-flash skipped (GlobalStandard needs a quota grant);
  Muse-Glimmer-30B skipped (managed compute only).
- [x] **Fireworks.ai** (`fireworks:`), function batteries 2026-11-08/09 on $101 of prepaid credit: DeepSeek V4.1
  Flash 190/224, Qwen 3.8 Max 182/224, GLM-5.3 172/224, MiniMax M3 117/224, Nemotron 3 Ultra 86/224 ($72.20
  booked). Reviewed: no false rejects.
- [x] **Apple Foundation Models, round 1**: on-device 0/32 (stream + IR; structured deferred to PCC), PCC 2/48
  (5/48 within four), completed 2026-11-09.
- [x] **PCC with a reasoning setting** (`--effort`, 2026-11-10): closed out by the user. At `low` and `medium`
  PCC ran out of output on `sum_to.stream.x86_64` (about 6 minutes of reasoning per request, no answer;
  `outputTokenLimitReached`), where no reasoning answers in about 2 s. Other cells not tried; 7 PCC calls.
- [x] Re-audits: Codex 0.167.0, Antigravity 1.5.3, Copilot 1.0.107 / SDK 1.0.29; `python -m bca.liveeval models`.
- [x] Landscape update (`reports/landscape-2026-11.md`); licence (MIT; CC BY 4.0 for reports/ and eval/runs/);
  documentation review (2026-11-08).

## Done (2026-11-10)

- [x] **Claude Fable 5.2, Mythos 5.1, Mythos 5.2** (via `claude -p`, Claude Code 2.1.322, CLI-default effort):
  functions 219 / 217 / 220 of 224 (all 224/224 within four), hex functions 16/16 each, apps 83 / 90 / 84 of 96
  (all 96/96 within four), hex apps 16 / 7 / 15 of 16 (within four 16 / 15 / 16). 1,127 calls. Reviewed: no
  false rejects, no refusals. Harness fix: a prompt over 100 KB goes to `claude -p` on stdin (E2BIG on a
  135 KB repair prompt).

- [x] **App / hex parity pick-up** (approved 2026-11-10, "#1-#4 as-scoped"; DeepSeek hex programs approved 11-11):
  apps for Claude Opus 4.8 88/96, Sonnet 5 76/96, Sonnet 4.5 54/96, Codex GPT-6 Sol 78/96, Kimi K3.1 Pro 85/96,
  DeepSeek V4.1 Flash 65/96, MiniMax M3 29/96, Nemotron 3 Ultra 14/96; hex functions for K3.1 Pro 11/16, DeepSeek
  16/16, MiniMax 8/16, Nemotron 3/16; hex programs for K3.1 Pro 4/16 and DeepSeek 4/16. Fireworks spend $32.03
  (after a $50 top-up). Reviewed: one harness false reject (stream alignment cap; fixed, 4 cells re-run) and one
  adapter fix (`IncompleteRead`); no refusals. Cost section added to the analytics (`eval/api_prices.py`).

## Waiting

- [ ] **Gemini 4 Argon High (Antigravity, Google AI Pro), function batteries**: stopped on quota at 122/224 cells
  (181 calls). An 8-hourly retry loop resumes it (`eval/runs/agy-argon-20261107-142027.loop.sh`, status in
  `.loop`). Run as-is with the uniform 180 s limit (user decision 2026-11-07). When complete: by-hand review,
  then README and the cross-vendor report (state the plan: Google AI Pro).
- [ ] **Antigravity (Gemini 3.8 Flash High)**: 1 cell left (`stats_f.ir.aarch64`, adversarial). The hourly loop
  was stopped on 2026-10-24 at the user's request; the user triggers it manually after a quota reset: `.venv/bin/python -m bca.liveeval run --run-dir eval/runs/vendors-all-20260928-202226 --tasks
  round1,round2,long,adversarial --subjects agy:gemini-3.8-flash-high --max-calls 3000 --jobs 2`. After it:
  review new first-attempt failures, regenerate with `eval/xv_tables.py`, update the row and † note in
  README.md and `reports/live-model-eval-cross-vendor.md`, commit and push.

## Backlog

- [ ] The non-Claude CLI adapters (codex, kimi, agy, grok, copilot) still pass the prompt in argv, so a repair
  prompt over 128 KiB would fail with E2BIG as `claude -p` did on 2026-11-10. None has hit it; fix when one does
  or when a battery makes it likely.

**New subjects (deferred by the user)**

- [ ] **Cerebras: gpt-oss-120b and Qwen 3.8 27B** (backlogged 2026-11-11; do not start unless asked): the user has a
  Cerebras account with $15 of credit; both models are served very fast. Needs a new `cerebras:` adapter: Cerebras'
  API is OpenAI-style chat completions, so it can follow `vendor_fireworks.py` (a `FoundrySubject` subclass:
  request ours byte for byte, streamed, priced into the shared spend ledger, per-model cap). Key from
  `CEREBRAS_API_KEY` or `~/.config/bca/cerebras.key` (mode 0600, written by the user; never asked for in chat,
  never logged). Prices (cerebras.ai/pricing, given by the user 2026-11-11; no cached-input rate listed):
  gpt-oss-120b $0.35 in / $0.75 out per 1M (~3,000 tok/s); Qwen 3.8 27B $0.99 / $1.49 (~1,850 tok/s). Estimate
  from comparable token use (DeepSeek V4.1 Flash for gpt-oss, Qwen 3.8 Max for the 27B; +/-50%): gpt-oss-120b
  about $2 functions, $4 apps, <$1 hex, so ~$6-7 for everything; Qwen 3.8 27B about $7 functions and ~$15 apps.
  Both in full (~$28) exceeds the $15 credit: fits gpt-oss-120b in full plus Qwen 3.8 27B functions (~$14,
  tight), Qwen's apps after a top-up. Before any run: read the exact model ids from the console, check per-tier rate limits (requests and
  tokens per minute), context window and output cap against our longest prompts and answers, then a 5-cell smoke
  and a cost estimate against the $15. Same time limits as every model: fast serving is measured, not
  accommodated. Why both: gpt-oss-120b then has two hosts (Fireworks above, Cerebras), a serving comparison
  for one open-weight model; Qwen 3.8 27B on Cerebras is a hosted route to the same model family as the
  backlogged Qwen3.8-27B from the user's DGX cluster (the Ring0-Agent model), which allows a hosted-versus-own-
  cluster comparison if the quantisation and settings are recorded.
- [ ] **gpt-oss-120b via Fireworks** (backlogged 2026-11-11; do not start unless asked): OpenAI's open-weight
  model, not benchmarked anywhere; it would be the first OpenAI open-weight row. Fireworks' model API reports
  `accounts/fireworks/models/gpt-oss-120b` READY, serverless, 131,072-token context. Price about $0.15 / $0.015 /
  $0.60 per 1M (input / cached / output; one source quotes $0.18 / $0.018 / $0.72: confirm on fireworks.ai/pricing).
  Needs a `MODELS` entry in `vendor_fireworks.py` (price, output cap) and a test, then a 5-cell smoke. All four
  batteries plus hex about $3-6; model-default reasoning, as for the other Fireworks rows. Chosen over Antigravity's
  `gpt-oss-120b-medium`, which would compete with Argon for the Google AI Pro quota and fixes the effort at
  Medium. Not backlogged from Antigravity: the Argon and Flash low / medium effort variants (one effort per model)
  and the Claude Opus / Sonnet 5.5 front-end duplicates.
- [ ] **App battery for Grok 4.5** (backlogged 2026-11-11; do not start unless asked): the one Grok model without
  app results (functions 197/224, hex functions and programs done). 96 cells, about 110-130 calls on SuperGrok,
  Grok Build CLI 1.0.41 (audited), CLI-default effort. Fills the Grok 4.5 -> 4.6 -> 4.7 app comparison.
- [ ] **Two Copilot routes** (backlogged 2026-11-11; do not start unless asked; the user confirmed OpenAI models
  through Copilot are fine, since the Codex rule covers the ChatGPT plan only):
  - `copilot:gpt-5.3-codex`: OpenAI's Codex-tuned GPT-5.3, not in Codex CLI; Copilot is the only subscription
    route (otherwise the pay-per-token API). Its Copilot policy is "not set": enable it in GitHub's Copilot
    settings first.
  - `copilot:kimi-k3`: a front-end comparison with Kimi K3 through Kimi Code (204/224). It extends the finding
    that the front end matters (GPT-6 Luna 58% Codex vs 79% Copilot; Sonnet 5.5 90% `claude -p` vs 76% Copilot)
    to a third vendor.
  Copilot Pro's monthly premium-request credits limit this to about one or two models a month (~250 requests per
  model for the functions, more with model multipliers; MAI-Code ran out once at 203/224; resets on the 1st).
  Stay on CLI 1.0.107 / SDK 1.0.29 (1.0.109 / 1.0.31 are out, not audited). Left out: `auto` (a router; the
  answering model is unknown) and the other front-end duplicates (Claude Sonnet 5 / Haiku 5.5 / Haiku 4.5,
  GPT-5.6 / 6.1, Grok 4.7 / 4.6 through Copilot).
- [ ] **Two Kimi Code models** (backlogged 2026-11-11; do not start unless asked): `kimi-code/k3.1-pro-highspeed`
  (K3.1 Pro served faster at 2x usage cost: does the faster serving cost accuracy, as Grok 4.7 build-fast did
  not?) and `kimi-code/kimi-for-coding-highspeed` (K2.7 Code Highspeed: extends the Kimi line K2.7 -> K2.8 ->
  K3 -> K3.1 Pro). Kimi Allegretto plan (5-hour and 7-day caps), CLI 2.3.1, each model's catalog default effort.
  About 250 calls per model for the functions (the highspeed K3.1 Pro at double quota), ~400 with apps and hex.
  Left out: `kimi-code/k3-256k` (K3 with a shorter context; would repeat K3). Re-run `kimi login` first to
  refresh the catalog (as of 2026-11-07).
- [ ] **Five untested GPT models in Codex CLI** (backlogged 2026-11-11; do not start unless asked; the ChatGPT
  plan is reserved, so the user names models before any run): `gpt-6.1-sol`, `gpt-6.1-terra` (needs CLI >= 0.165.0),
  `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`. All five are in the Codex catalog (CLI 0.167.0, ChatGPT Pro) and
  in the OpenAI API list. Before running: pin each model's Codex catalog default effort (`CODEX_PINNED_EFFORT`;
  an unknown model must be given `--effort`), and expect a possible re-audit if a new catalog entry carries
  tool-ish fields outside `CATALOG_AUDITED`. Stay on 0.167.0 (0.167.1 is out, not audited). About 250 calls per
  model for the functions, ~400 with apps and hex; 1,250-2,000 for all five. Not candidates: `gpt-reserve` and
  `codex-auto-review` (hidden catalog entries). `gpt-reserve` is "Luna Reserve" (web reports, 2026-11-11, not
  official docs): a fallback Codex switches to after the plan's usage limit, on its own meter, serving a
  Luna-class model whose identity OpenAI does not state; a separate model id from `gpt-5.6-luna`, max effort
  `xhigh`. Not a distinct model to rank, and its unknown backing model would break the "named model answers"
  rule. `codex-auto-review` is Codex's review helper. API-only GPT models
  (GPT-5.4 family, gpt-5.3-codex, 5.2, 5.1, 5, -pro variants, o3, GPT-4.1, GPT-4o) would need a direct OpenAI API
  adapter (pay-per-token); a separate decision.
- [ ] **Six older Claude models not yet benchmarked** (backlogged 2026-11-11; do not start unless asked):
  `claude-opus-5` (Opus 5), `claude-fable-5` (Fable 5), `claude-opus-4-7`, `claude-opus-4-6`,
  `claude-opus-4-5-20251101` (Opus 4.5) and `claude-sonnet-4-6`. Each answered as itself through `claude -p` on
  2026-11-11 and is listed in the account's API model playground. Full set per model (functions, apps, hex
  functions, hex programs at >= 12/16 within four): roughly 350-500 calls, about 2,500 for all six, on the
  Claude Max plan (no money; CVP caveat applies). It completes the Opus 4.5 -> 4.8, Opus 5 -> 5.5, Fable 5 ->
  5.2 and Sonnet 4.5 -> 5.5 lineages. Not available: Opus 4.1 and Opus 4 (`claude -p` silently serves Opus 5.5
  for those ids), Sonnet 4 and all Claude 3.x (retired). Mythos 5.1 / 5.2 are reachable through Claude Code
  but are not in the API playground.

- [ ] **Qwen3.8-27B**, the model behind Ring0-Agent ("close the loop"). To be served from the user's other DGX
  cluster. The time-limit blocker for a dense 27B is gone (token-budget mode, built for Kolibri). Needs from the
  user: endpoint URL and serving stack, model id, quantisation, and Ring0-Agent's sampling settings.
- [ ] **App battery and hex path for MAI-Thinking-1 and DeepSeek-V4-Pro** (Azure Foundry): backlogged
  2026-11-08 on cost (projected $25-40 and $15-25). Not part of the 2026-11-10 parity pick-up.
- [ ] **App battery and hex path for GLM-5.3 and Qwen 3.8 Max** (Fireworks): the expensive two of the 2026-11-10
  estimate (most of its $120-190, plus gated hex programs). DeepSeek V4.1 Flash, MiniMax M3 and Nemotron 3 Ultra
  ran in the parity pick-up ($32 in all).
- [ ] **Further Foundry models** (recommended in the landscape update, not approved): Kimi-K3.1-Pro (a second
  route to a benchmarked model), mistral-medium-3-5.
- [ ] **Apriel-1.6-15B-Thinker** (ServiceNow, MIT): optional local baseline, practical now that token-budget mode
  exists. Its answer follows `[BEGIN FINAL RESPONSE]`, which the adapter must strip.

**Harness improvements for a new round** (each changes what models see, so not a patch to the current runs)

- [ ] Echo the offending source line with QBE compile errors (`reports/live-model-eval-haiku-sonnet.md`).
- [ ] Rename the structured schema's `$defs/reg` (e.g. `register_name`) so bare register strings are more
  obvious (same report).
- [ ] Add extend and add-with-carry ops (`sxtb`/`sxtw`, `adc`) to the structured AArch64 schema; models keep
  reaching for them (`reports/live-model-eval-adversarial.md`; x86 already has `movsx` / `adc`).
- [ ] When a checked output buffer differs, show the first differing index, not only the first 12 elements
  (`reports/live-model-eval-long.md`).

**Housekeeping**

- [ ] Docker images (~72 GB): `bca-vllm-kolibri` (22 GB, needed only to re-run Kolibri), the GB10-native test
  image `bca-vllm-kolibri-native` (25 GB, offered for deletion), `eugr/spark-vllm:nightly-20261002` (25 GB).
  The user's call.
- [ ] Tooling note: the CodeGraph MCP server in the eval session fails with the stale-build error
  (`statInode is not a function`); the CLI works and `/mcp` reconnect fixes it. Not a project bug.

**Done:** the optional interactive dashboard (`eval/dashboard.py` writes `reports/dashboard.html`; also published
as a private claude.ai artifact). Re-run the script after any new run.

## Remaining proposed pick-up runs (flagged 2026-10-20; not approved)

Gaps in the recorded data that small runs could fill. Each needs the user's go-ahead before it starts; call
counts are estimates (~1.1 attempts per cell for top models). After any of them: by-hand false-reject review,
then regenerate the tables and `eval/analytics.py`.

**Worth filling (they limit published conclusions)**

- [x] **App battery for Codex GPT-6 Sol**: done 2026-11-10 (78/96), in the parity pick-up.

**Prompt variants on non-Claude models (backlogged 2026-10-22, not approved; recommendation below)**

- [ ] **`misleading_hint` and `terse`, adversarial battery, on four non-Claude models** that mirror the spread of
  the four Claude models already run (adversarial first-try passes out of 60: Opus 5.5 57, Fable 5.1 54, Sonnet 5
  45, Haiku 4.5 35). Today both variant findings are Claude-only: "misleading hints change no model
  significantly" (they leaned on Haiku, −11 / +3, and Sonnet 5, −12 / +5) and the variant's refusals.

  | Tier | Model | Adversarial baseline | Matches | Plan |
  |---|---|---|---|---|
  | Strong | Grok 4.7 | 56/60 | Opus 5.5 (57) | SuperGrok |
  | Strong | Kimi K3 | 56/60 | Opus 5.5 (57) | Kimi Allegretto |
  | Mid | Kimi K2.8 (for-coding) | 46/60 | Sonnet 5 (45) | Kimi Allegretto |
  | Weak | MAI-Code 1.1 Flash | 35/60 | Haiku 4.5 (35) | Copilot Pro credits |

  ~690 calls (~65 per variant per strong model, ~90 for K2.8, ~125 for MAI-Code). Order: Grok 4.7 after its app
  run (same weekly allowance); the Kimis after K3's app samples (~310 calls: several 5-hour windows, maybe a 7-day
  cap); MAI-Code may not fit the month's remaining Copilot credits (reset on the 1st). Smaller option: Grok 4.7 +
  MAI-Code (~380 calls, no Kimi). Excluded: any Codex (the ChatGPT plan is reserved), Astra and Sol (60/60 at
  baseline, nothing to gain), other Groks (same vendor as 4.7), Kolibri (its adversarial token budget already cut
  52 of 176 calls; days of GPU), the Qwens (2/60), Gemini (quota-paced even on AI Pro), GPT-6 Luna via Copilot (an OpenAI model;
  left out given the Codex instruction). Needs the user's choice of set before anything runs.

**Optional (statistical power)**

- [ ] **Second sample of the long battery for the top seven** (Astra, Opus 5.5, Sol, Grok 4.7 build-fast,
  Fable 5.1, Grok 4.7, Grok 4.6): 36 of the 87 cells that split the top ten are long-battery cells. 7 × 54
  cells, ~380 calls. Sample the whole battery, not the splitting cells, to avoid post-hoc selection bias.
- [ ] **`declared` hex variants**: 16 cells is underpowered, and Astra lacks `declared_annotated` (16 cells,
  ~17 calls). A real test needs more samples of the same cells (e.g. two more each for Opus 5.5 and Astra,
  ~130 calls).
- [ ] **`assisted` on a mid-tier model**: tested only on weak models. Opus 4.8 and Sonnet 5 (78%) fail mostly on
  QBE type classes, which the assisted QBE cheat sheet targets. Rounds 1 + 2, 110 cells each, ~140 calls each
  (Claude plan).

**Hex-path holes**

- [ ] **No hex results for Gemini 3.8 Flash High or Kolibri-1 (Kimi K3.1 Pro done 2026-11-10)**: hex functions (16 cells) each;
  whole-program hex follows only at ≥ 12/16 within four (so Haiku 4.5 and MAI-Code missing from hex apps is by
  design; Haiku 5.5 qualified and ran). Kolibri ~5 GPU hours.

**Not fillable or not worth it (note in reports, do not run)**

- Codex GPT-5.5 retired 2026-10-14 (no app results possible). Claude Sonnet 4.5 (deprecated), Sonnet 5 and Opus
  4.8 got their app runs on 2026-11-10, so the Opus 4.8 → 5.5 and Sonnet 4.5 → 5 → 5.5 app comparisons now exist.
- Trimmed Qwen runs stay partial (user decision 2026-10-01).
- Token coverage: Qwen3.6 thinking-on 7% (times out before answering), Kimi K2.8 88% (its CLI prints only at
  call end); a re-run would behave the same.
- Grok 4.7's four unrecovered long cells hit the 400 s limit on every attempt: a genuine result.
