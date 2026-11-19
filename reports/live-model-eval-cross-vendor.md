# Live-model eval: cross-vendor results

> **Account caveat.** The Claude account is enrolled in Anthropic's **Cyber Verification Program** and the
> ChatGPT account in OpenAI's **Daybreak** (Blue tier; no Red / `*-cyber` models); both relax cyber safeguards,
> so treat those rows as upper bounds for ordinary accounts. The Kimi, Google and xAI accounts have no known
> equivalent. Plans: Claude Ultra (formerly Max 20x), ChatGPT Pro 100 (Plus until 2026-10-04; Codex runs committed
> before then used Plus), Kimi Allegretto, Google AI Pro (AI Plus 2 TB until 2026-11-06; every reported Antigravity
> run used Plus), SuperGrok, GitHub Copilot Pro. The Azure Foundry rows
> are pay-as-you-go API calls on the user's Azure subscription, with Azure's default content filter on.
> The Fireworks rows are pay-per-token calls to Fireworks.ai's serverless API on the user's prepaid account ($51
> of credit, topped up by $50 on 2026-11-10). The Cerebras rows are pay-per-token calls to Cerebras' inference API on
> the user's prepaid account ($60 of credit in all). The Apple rows run on the user's Mac: the on-device model through `fm serve` behind the Mac session's
> memory and rate governor, and Private Cloud Compute on the user's iCloud+ account's daily quota.
> Method, fairness contract and known limits: [cross-vendor-methodology.md](cross-vendor-methodology.md).

Every model got the same 224 cells: rounds 1 + 2, the long-program battery and the adversarial battery
(baseline prompts), across the stream, structured-JSON and QBE-IR paths on x86-64 and AArch64, with up to
four attempts and repair feedback. Each ran with zero tools, the same system and task prompts, and its route's default effort
unless a row says otherwise: most through their vendor's own coding CLI (Claude Code, Codex, Kimi Code,
Antigravity, Grok Build), the `copilot:` rows through GitHub Copilot's SDK runtime, the `foundry:` rows through
the Azure AI Foundry API directly, the `fireworks:` rows through Fireworks.ai's API directly, the `cerebras:` rows through Cerebras' API directly, the `apple:` rows through Apple's `fm serve` on the user's Mac, and the local rows through Ollama or vLLM on this machine. Runs date from
2026-09-27 to 2026-11-12. Every result records the CLI or API version it ran on (`cli_version` in each
`results.jsonl`); the audited versions are in the methodology report.

## Results (first-attempt passes per battery)

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Gemini 4 Hydrogen High (Antigravity) | 48 | 62 | 54 | 60 | **224/224 (100%)** | 224/224 (100%) | 1.00 |
| Codex GPT-6 Astra | 46 | 61 | 54 | 60 | **221/224 (99%)** | 224/224 (100%) | 1.01 |
| Claude Mythos 5.2 | 47 | 61 | 53 | 59 | **220/224 (98%)** | 224/224 (100%) | 1.02 |
| Claude Fable 5.2 | 47 | 62 | 50 | 60 | **219/224 (98%)** | 224/224 (100%) | 1.02 |
| Claude Mythos 5.1 | 47 | 59 | 54 | 57 | **217/224 (97%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6.1 Sol | 44 | 60 | 53 | 59 | **216/224 (96%)** | 224/224 (100%) | 1.04 |
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Kimi K3.1 Pro Highspeed | 47 | 59 | 49 | 60 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6 Sol | 43 | 62 | 48 | 60 | **213/224 (95%)** | 224/224 (100%) | 1.05 |
| Grok 4.7 build-fast | 47 | 60 | 49 | 57 | **213/224 (95%)** | 223/224 (100%) | 1.08 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Grok 4.6 | 45 | 59 | 50 | 57 | **211/224 (94%)** | 223/224 (100%) | 1.07 |
| Gemini 3.8 Flash High (Antigravity) | 46 | 60 | 47 | 57 | **210/224 (94%)** | 224/224 (100%) | 1.07 |
| Claude Opus 4.7 | 42 | 58 | 51 | 58 | **209/224 (93%)** | 223/224 (100%) | 1.08 |
| Codex GPT-5.5 | 47 | 57 | 45 | 58 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Kimi K3.1 Pro | 45 | 61 | 45 | 56 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Codex GPT-6.1 Terra | 45 | 59 | 48 | 54 | **206/224 (92%)** | 224/224 (100%) | 1.09 |
| Codex GPT-5.6 Sol | 42 | 60 | 46 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.09 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Claude Haiku 5.5 | 42 | 58 | 46 | 57 | **203/224 (91%)** | 222/224 (99%) | 1.11 |
| Claude Sonnet 4.5 | 48 | 55 | 44 | 56 | **203/224 (91%)** | 221/224 (99%) | 1.14 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Claude Fable 5 | 45 | 58 | 45 | 51 | **199/224 (89%)** | 203/224 (91%) | 1.30 |
| Claude Opus 5 | 40 | 57 | 50 | 51 | **198/224 (88%)** | 224/224 (100%) | 1.12 |
| Grok 4.5 | 47 | 55 | 41 | 54 | **197/224 (88%)** | 222/224 (99%) | 1.16 |
| Gemini 4 Argon High (Antigravity) | 47 | 60 | 26 | 58 | **191/224 (85%)** | 205/224 (92%) | 1.35 |
| DeepSeek V4.1 Flash (Fireworks) | 41 | 53 | 46 | 50 | **190/224 (85%)** | 224/224 (100%) | 1.19 |
| Codex GPT-5.6 Terra | 41 | 55 | 38 | 55 | **189/224 (84%)** | 223/224 (100%) | 1.19 |
| Qwen 3.8 Max (Fireworks) | 40 | 50 | 42 | 50 | **182/224 (81%)** | 222/224 (99%) | 1.28 |
| Mistral Large 4 | 42 | 54 | 35 | 46 | **177/224 (79%)** | 223/224 (100%) | 1.29 |
| GPT-6 Luna via Copilot | 42 | 53 | 31 | 50 | **176/224 (79%)** | 224/224 (100%) | 1.29 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Claude Opus 4.8 | 35 | 48 | 49 | 43 | **175/224 (78%)** | 222/224 (99%) | 1.27 |
| Z.ai GLM-5.3 (Fireworks) | 39 | 53 | 30 | 50 | **172/224 (77%)** | 217/224 (97%) | 1.39 |
| Claude Sonnet 5.5 via Copilot | 45 | 55 | 19 | 51 | **170/224 (76%)** | 219/224 (98%) | 1.35 |
| Kimi K2.8 (for-coding) | 37 | 46 | 28 | 46 | **157/224 (70%)** | 213/224 (95%) | 1.52 |
| Codex GPT-5.6 Luna | 40 | 44 | 12 | 46 | **142/224 (63%)** | 214/224 (96%) | 1.62 |
| DeepSeek-V4-Pro high (Azure Foundry) | 33 | 45 | 20 | 41 | **139/224 (62%)** | 217/224 (97%) | 1.60 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| MiniMax M3 (Fireworks) | 31 | 36 | 13 | 37 | **117/224 (52%)** | 184/224 (82%) | 2.00 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 26 | 34 | 12 | 35 | **107/224 (48%)** | 151/224 (67%) | 2.29 |
| Qwen 3.8 27B (Cerebras) | 29 | 34 | 10 | 29 | **102/224 (46%)** | 142/224 (63%) | 2.39 |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | 20 | 28 | 13 | 25 | **86/224 (38%)** | 151/224 (67%) | 2.41 |
| Microsoft MAI-Thinking-1 (Azure Foundry) | 22 | 22 | 2 | 26 | **72/224 (32%)** | 144/224 (64%) | 2.58 |
| OpenAI gpt-oss-120b (Cerebras) | 26 | 21 | 3 | 21 | **71/224 (32%)** | 117/224 (52%) | 2.78 |
| OpenAI gpt-oss-120b (Fireworks) | 24 | 22 | 4 | 21 | **71/224 (32%)** | 120/224 (54%) | 2.71 |
| Apple Private Cloud Compute † | 2 | — | — | — | **2/48 (4%)** | 5/48 (10%) | 3.77 |

Codex Daybreak Blue includes one reviewed harness false reject counted as a pass, Grok 4.7 build-fast
three and GPT-6 Luna via Copilot one; Claude Sonnet 5.5 via Copilot is one lower than recorded (a pre-fix
output-cap do-over counted as the failure it would be now; see the false-reject review below). 
**Kimi K3.1 Pro's row changed on 2026-11-12: 207/224 first try (92%) at `high` effort, replacing 218/224 (97%) at
`max`.** Kimi Code's catalog default effort for the model moved from `max` (Kimi Code 2.3.1, 2026-10-23) to `high`
(Kimi Code 2.5.3, after a new `kimi login` on 2026-11-11), and the model's app and hex batteries (2026-11-10) had
already run at `high`. So the function batteries were re-run at `high` (241 calls, CLI 2.5.3, re-audited), so that
every K3.1 Pro battery is at one effort, the CLI default, like every other row. The `max` run is kept as an effort
study and left out of every table here: `max` passed 15 cells on the first attempt that `high` failed and `high`
passed 4 that `max` failed (exact McNemar p = 0.019), most of them in the long and adversarial batteries; `high`
recovered all 224 cells within four attempts (`max`: 223) in half the time per call (37 s against 78 s) and under
half the output tokens ([effort and serving studies](live-model-eval-effort.md)). At `high`, K3.1 Pro is level with
Kimi K3 (204; +17 / −14, p = 0.72). The notes below describe the `max` run of 2026-10-24: 218/224 first try,
statistically level with Codex GPT-6 Astra on the same cells (they disagreed on 9 cells, p = 0.51), and
significantly ahead of Kimi K3 (19 cells against 5, p = 0.007), through Kimi Code CLI 2.3.1; median call 43 s. Its
first-attempt failures were reviewed by hand: no harness false rejects. Three are 180 s timeouts; one is a wrong base64 result; one is invalid QBE (`%digit
ch`, a missing `%`); and two answers had nothing to do with the task although the adapter verified the request
exactly: an "I do not have access to your previous conversation" reply that drifts into mixed English and Chinese,
and "Ready when you are." (`count_nonzero_prefix.structured.aarch64`, its one cell not passed within four).

Kimi K2.8 (kimi-for-coding), completed 2026-10-17: 157/224 first try. 40 of its 128 failed attempts are
wall-clock timeouts: its median call took 113 s against the 180 s round-1/2 limit (Kimi K3: 51 s). Kimi's CLI
prints the answer only when the call ends, so a timeout carries no text; these are the same timeout rule as
every CLI vendor, not stalls. Failures reviewed by hand: no harness false rejects (QBE compares without a
result class, `xor eax, 0` used to zero a register, malformed JSON, unencodable AArch64 immediates).

† Gemini 3.8 Flash High's function batteries were quota-paced on Google AI Plus from 2026-09-28; its last cell (`stats_f.ir.aarch64`) ran on 2026-11-15 (Google AI Pro, Antigravity 1.5.7), so the row is now complete: 210/224 first attempt, 224 within four (211 before one output-limit attempt was scored under the 2026-11-15 rule; see the methodology notes).

† Apple Private Cloud Compute covers round 1 only: all 48 cells (completed 2026-11-09 after the daily PCC quota paused it at 37), at the model's default setting (no `reasoning_effort`). Apple's on-device model runs on the user's Mac, not on Apple's servers, so it is listed with the [local models](#local-models-dgx-spark-and-apples-on-device-model-on-a-mac).

**In progress on 2026-11-12, and left out of every table until complete** (`eval/model_names.py`): Claude Sonnet
4.6, Opus 4.6 and Opus 4.5 (the rest of the older-Claude set); Kimi K2.7 Code Highspeed (running; Kimi K3.1 Pro Highspeed's app and hex-program batteries
finished on 2026-11-14 and are in the tables). Gemini 4 Argon High finished on 2026-11-14 and is in the tables (all 224
cells on Antigravity 1.5.7; see the finding below).

Versions behind the 2026-11-11 and 11-12 rows: Codex CLI 0.167.0; Kimi Code 2.5.3 (K3.1 Pro's app and hex batteries
of 2026-11-10: 2.3.1); Claude Code 2.1.322 (2.1.323, after the CLI updated itself, for Opus 4.7's last three
hex-program attempts and for the hex-program cells re-run after the output-cap fix; the eval now runs a pinned
2.1.323); Grok Build 1.0.41; the Fireworks and Cerebras APIs directly. The machine lost power on 2026-11-12
at 03:52 and was rebooted three more times that day for system updates: no record was damaged, a call in flight
left no attempt record and ran again, and every chain resumed with the same commands and settings.

## By emission path (first-attempt passes, complete runs)

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Gemini 4 Hydrogen High (Antigravity) | 76/76 (100%) | 74/74 (100%) | 74/74 (100%) |
| Codex GPT-6 Astra | 76/76 (100%) | 74/74 (100%) | 71/74 (96%) |
| Claude Mythos 5.2 | 75/76 (99%) | 72/74 (97%) | 73/74 (99%) |
| Claude Fable 5.2 | 75/76 (99%) | 71/74 (96%) | 73/74 (99%) |
| Claude Mythos 5.1 | 70/76 (92%) | 74/74 (100%) | 73/74 (99%) |
| Codex GPT-6.1 Sol | 76/76 (100%) | 73/74 (99%) | 67/74 (91%) |
| Claude Opus 5.5 | 74/76 (97%) | 71/74 (96%) | 70/74 (95%) |
| Kimi K3.1 Pro Highspeed | 73/76 (96%) | 71/74 (96%) | 71/74 (96%) |
| Codex GPT-6 Sol | 73/76 (96%) | 71/74 (96%) | 69/74 (93%) |
| Grok 4.7 build-fast | 75/76 (99%) | 72/74 (97%) | 66/74 (89%) |
| Claude Fable 5.1 | 72/76 (95%) | 69/74 (93%) | 71/74 (96%) |
| Grok 4.7 | 76/76 (100%) | 71/74 (96%) | 64/74 (86%) |
| Grok 4.6 | 73/76 (96%) | 73/74 (99%) | 65/74 (88%) |
| Gemini 3.8 Flash High (Antigravity) | 75/76 (99%) | 69/74 (93%) | 66/74 (89%) |
| Claude Opus 4.7 | 70/76 (92%) | 71/74 (96%) | 68/74 (92%) |
| Codex GPT-5.5 | 72/76 (95%) | 71/74 (96%) | 64/74 (86%) |
| Kimi K3.1 Pro | 73/76 (96%) | 70/74 (95%) | 64/74 (86%) |
| Codex GPT-6.1 Terra | 75/76 (99%) | 72/74 (97%) | 59/74 (80%) |
| Codex GPT-5.6 Sol | 75/76 (99%) | 68/74 (92%) | 61/74 (82%) |
| Kimi K3 | 75/76 (99%) | 66/74 (89%) | 63/74 (85%) |
| Claude Haiku 5.5 | 75/76 (99%) | 59/74 (80%) | 69/74 (93%) |
| Claude Sonnet 4.5 | 74/76 (97%) | 67/74 (91%) | 62/74 (84%) |
| Codex Daybreak Blue | 71/76 (93%) | 69/74 (93%) | 61/74 (82%) |
| Claude Sonnet 5.5 | 73/76 (96%) | 68/74 (92%) | 60/74 (81%) |
| Claude Fable 5 | 69/76 (91%) | 67/74 (91%) | 63/74 (85%) |
| Claude Opus 5 | 72/76 (95%) | 67/74 (91%) | 59/74 (80%) |
| Grok 4.5 | 73/76 (96%) | 71/74 (96%) | 53/74 (72%) |
| Gemini 4 Argon High (Antigravity) | 59/76 (78%) | 62/74 (84%) | 70/74 (95%) |
| DeepSeek V4.1 Flash (Fireworks) | 73/76 (96%) | 55/74 (74%) | 62/74 (84%) |
| Codex GPT-5.6 Terra | 68/76 (89%) | 69/74 (93%) | 52/74 (70%) |
| Qwen 3.8 Max (Fireworks) | 73/76 (96%) | 72/74 (97%) | 37/74 (50%) |
| Mistral Large 4 | 67/76 (88%) | 66/74 (89%) | 44/74 (59%) |
| GPT-6 Luna via Copilot | 67/76 (88%) | 61/74 (82%) | 48/74 (65%) |
| Claude Sonnet 5 | 63/76 (83%) | 53/74 (72%) | 59/74 (80%) |
| Claude Opus 4.8 | 63/76 (83%) | 66/74 (89%) | 46/74 (62%) |
| Z.ai GLM-5.3 (Fireworks) | 68/76 (89%) | 64/74 (86%) | 40/74 (54%) |
| Claude Sonnet 5.5 via Copilot | 61/76 (80%) | 62/74 (84%) | 47/74 (64%) |
| Kimi K2.8 (for-coding) | 67/76 (88%) | 52/74 (70%) | 38/74 (51%) |
| Codex GPT-5.6 Luna | 59/76 (78%) | 41/74 (55%) | 42/74 (57%) |
| DeepSeek-V4-Pro high (Azure Foundry) | 67/76 (88%) | 42/74 (57%) | 30/74 (41%) |
| Codex GPT-6 Luna | 60/76 (79%) | 45/74 (61%) | 25/74 (34%) |
| MiniMax M3 (Fireworks) | 63/76 (83%) | 43/74 (58%) | 11/74 (15%) |
| Claude Haiku 4.5 | 61/76 (80%) | 54/74 (73%) | 1/74 (1%) |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 59/76 (78%) | 44/74 (59%) | 4/74 (5%) |
| Qwen 3.8 27B (Cerebras) | 52/76 (68%) | 50/74 (68%) | 0/74 (0%) |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | 57/76 (75%) | 27/74 (36%) | 2/74 (3%) |
| Microsoft MAI-Thinking-1 (Azure Foundry) | 40/76 (53%) | 24/74 (32%) | 8/74 (11%) |
| OpenAI gpt-oss-120b (Cerebras) | 50/76 (66%) | 21/74 (28%) | 0/74 (0%) |
| OpenAI gpt-oss-120b (Fireworks) | 48/76 (63%) | 23/74 (31%) | 0/74 (0%) |

## Findings

- **Codex GPT-6 Astra leads at 99%** (221/224; 100% on stream and structured, 224/224 within four attempts),
  ahead of Claude Mythos 5.2 and Fable 5.2 (98%), Claude Mythos 5.1 (97%), Codex GPT-6.1 Sol, Opus 5.5 and Kimi
  K3.1 Pro Highspeed (96%), Codex GPT-6 Sol, Grok 4.7 build-fast and Fable 5.1 (95%), Grok 4.7 and 4.6 (94%),
  Claude Opus 4.7 (93%) and Kimi K3.1 Pro (92% at `high` effort; 97% at `max`, see above).
- **Within a vendor, model choice matters as much as vendor**: Codex spans 58% (Luna) to 99% (Astra) on
  the same CLI, account and prompts.
- **The IR path separates models most.** Raw assembly (stream) is easy for nearly everyone (Grok 76/76);
  QBE punishes models that don't know it: Haiku 1/74, Luna 25/74, against 95-96% for Opus and Fable.
  Typical QBE errors: missing result classes (`%x = add`), invented opcodes (`csgt`, `mull`, `shrl`,
  `mulw`), block-argument SSA, and hex literals (QBE takes decimal only; the prompt does not say so).
- **Codex GPT-6 Luna (58%) is a real outlier, not a harness artifact.** Reviewed by hand: invalid QBE (46
  compile failures), structured-x86 schema violations, `xor reg, 0` used to "zero" a register (a no-op; 6
  cells, all failed), and calling-convention mistakes. Luna ran at medium effort, Daybreak Blue at low, on
  the same CLI with byte-identical prompts, so effort does not explain the gap.
- **Grok 4.7 is slow, not wrong**: 10 of its 13 first-attempt failures and all 4 final failures are
  timeouts (180 s; 400 s on the long battery) with the model still reasoning at the limit. Every model has
  the same limits; a longer limit would likely raise Grok's long-battery and IR scores.
- **Grok 4.7 build-fast matches Grok 4.7's accuracy** (95% vs 94%) in a fraction of the time (the whole
  battery in ~70 minutes, 3 timeouts vs 10), so the fast build loses little here.
- **Newer is not always better, and older is not always worse.** Sonnet 4.5 (91%, retired 2026-09-29)
  edges out Sonnet 5.5 (90%) and far exceeds Sonnet 5 (78%); Opus 4.8 (78%) trails Opus 5.5 (96%) by 18
  points, mostly on QBE (46/74: 23 answers omit parameter types the prompt requires). Grok descends
  4.7 / 4.6 (94%) to 4.5 (88%); Codex GPT-6 Sol (95%) and GPT-5.5 (92%) sit between Astra and Luna.
- **Claude Fable 5.2, Mythos 5.1 and Mythos 5.2 (added 2026-11-10) are the strongest Claude rows**: Mythos
  5.2 220/224 (98%), Fable 5.2 219/224 (98%), Mythos 5.1 217/224 (97%), all three 224/224 within four, at
  1.02-1.04 attempts per cell. All three are statistically level with Astra and with each other (paired exact
  McNemar, Holm-corrected; see the analytics report); Fable 5.1 to 5.2 is +11 / −4 (p = 0.12) and Mythos 5.1 to
  5.2 +7 / −4 (p = 0.55), so neither step is significant on its own. Run through `claude -p` (Claude Code
  2.1.322) on the Claude Ultra account at CLI-default effort, like every other Claude row (CVP caveat applies),
  691 calls for the three; no refusals and no infrastructure errors. All 16 first-attempt failures were
  reviewed by hand; none is a false reject. Mythos 5.1's stream score (70/76) is its weak spot: four of its
  misses open with a sentence of prose ("Simple scalar loop is sufficient.") on a path that takes
  instructions only; Mythos 5.2 once sent a broken JSON program followed by a "Correction" with a second one.
- **Five more Codex models (added 2026-11-11) fill in OpenAI's tiers**: GPT-6.1 Sol 216/224 (96%), GPT-6.1 Terra
  206 (92%), GPT-5.6 Sol 204 (91%), GPT-5.6 Terra 189 (84%) and GPT-5.6 Luna 142 (63%). All ran through Codex CLI
  0.167.0 on ChatGPT Pro at each model's catalog default effort, pinned in the adapter: `low` for both GPT-6.1
  models and GPT-5.6 Sol, `medium` for GPT-5.6 Terra and Luna. Tier decides more than release: within a tier,
  GPT-5.6 Terra to GPT-6.1 Terra is the only significant step (+29 / −12, p = 0.012); GPT-5.6 Sol to GPT-6 Sol
  (+18 / −9, p = 0.12), GPT-6 Sol to GPT-6.1 Sol (+10 / −7, p = 0.63) and GPT-5.6 Luna to GPT-6 Luna (+29 / −41,
  p = 0.19) are not. GPT-6.1 Sol is level with Astra's neighbours at a fifth of Astra's list price and 12 s a call.
  The Luna tier stays the outlier on QBE (57% and 34%) and the long battery (12 and 10 of 54). GPT-6.1 Sol, GPT-6.1
  Terra and GPT-5.6 Sol passed all 224 cells within four attempts.
- **Three older Claude models (added 2026-11-12; Sonnet 4.6, Opus 4.6 and Opus 4.5 are still running)**: Opus 4.7
  209/224 (93%), Fable 5 199 (89%) and Opus 5 198 (88%), through `claude -p` at CLI-default effort on the Claude
  Ultra account (CVP caveat applies). The Opus line is not monotonic: Opus 4.7 is ahead of both Opus 4.8 (175;
  +10 / −44 going from 4.7 to 4.8, p < 0.001, most of it on QBE, 92% against 62%) and Opus 5 (198), and only Opus
  5.5 (215) passes it. **Fable 5's row is held down by the API's safety classifier, not by the model**: 21 of its
  224 cells were blocked on all four attempts ("Fable 5's safeguards flagged this message", the cyber category)
  before the model answered, 17 of them x86-64 and 11 on the QBE path (`fill_down` 5 cells, `vm_run` 4,
  `packbits_decode` 3, seven other tasks 1-2 each). They are scored as refusals, like every safety block here, which is why only
  203 cells pass within four. On the 203 cells it was allowed to answer, Fable 5 passed 199 on the first attempt
  (98%) and all 203 within four. Its hex and app batteries drew no blocks. Opus 5 and Opus 4.7 were never blocked.
- **Gemini 4 Hydrogen High (Antigravity, Google AI Pro; 2026-11-15) is the first model to pass every function cell
  on the first attempt: 224/224**, one call per cell (224 attempts, no infrastructure retries), a median 18 s and
  about 2,800 thinking tokens a call, and no safety-filter block on any function cell (Argon drew 29 on the same
  prompts the day before). It also leads the app battery, 93/96 first attempt and 96 within four, and wrote 15/16
  hex functions first time (16 within four). Whole programs in hex are where it gives ground: 10/16 first attempt,
  15 within four, with Gemini's filter blocking 5 of the 16 cells on the first attempt and `bf` (x86-64) on all
  four; on the 11 it could answer first time it passed 10. Hydrogen draws on Antigravity's general Gemini quota,
  not Argon's separate weekly meter.
- **Gemini 4 Argon High (Antigravity, Google AI Pro; 2026-11-14): 191/224 first attempt (85%), 205 within four,
  and held down by Gemini's safety filter, not by the model.** 29 cells were blocked on the first attempt ("This
  request was blocked by Gemini's filters"), 28 of them in the long battery, and 19 on all four attempts (`xxh64`
  and `vm_run` 3 cells each; `base64_encode`, `packbits_encode`, `packbits_decode`, `mat_pow`, `mp_mul` 2 each).
  They are scored as refusals, like every safety block here. On the 195 cells it was allowed to answer first time
  it passed 191 (98%), and it recovered every cell that was not blocked throughout. Without the long battery it is
  165/170 (97%). **The row measures Argon High as Google served it from 2026-11-13**: a week earlier the same
  setting thought four to five times longer (a median 29,800 thinking tokens on the long battery against 6,650,
  121 s a passing call against 24 s) and 62 of 180 attempts ran out the 180 s limit, with no filter blocks. Those
  122 earlier cells were superseded and re-run so the row is one configuration
  ([methodology](cross-vendor-methodology.md), operations notes).
- **Serving speed did not cost Kimi K3.1 Pro accuracy.** K3.1 Pro Highspeed (twice the price and plan quota) scored
  215/224 against K3.1 Pro's 207 at the same `high` effort (6 cells only the standard tier passed, 14 only
  Highspeed; p = 0.12, not significant), at 19 s a call against 37 s; both recover all 224 within four.
- **OpenAI's open-weight gpt-oss-120b scores the same on two hosts**: 71/224 (32%) on Fireworks and 71/224 on
  Cerebras at each host's default settings, disagreeing on 32 cells split 16 / 16 (p = 1), while Cerebras answers in
  1.4 s a call against 11.5 s. Its ceiling is the model, not the host: 296 of its failed attempts on each host are
  invalid QBE. At `reasoning_effort: high` on Cerebras it reaches 97/224 (36 cells gained, 10 lost, p < 0.001) for
  six times the output tokens. **Qwen 3.8 27B on Cerebras: 102/224 (46%)**, with 191 of its 404 failed attempts
  timeouts at the uniform limits even at Cerebras' speed (median 33,000 output tokens on a first attempt).
- **Two vendor CLIs could not be made tool-free and are reported apart**, never ranked with these rows: Mistral
  Large 4 and Medium 3.5 through Mistral Vibe (156/224 and 91/224 first attempt) and Meta Muse Spark 1.3 through
  Muse Code (222/224). Their requests carry the vendor's own agent prompt, and Vibe's carry tool definitions the
  model cannot use: [agent-harness studies](live-model-eval-agent-harness.md).
- **Claude Haiku 5.5 jumps 39 points over Haiku 4.5** (91% vs 52%, 203/224; 222/224 within four), level
  with Kimi K3 and Sonnet 4.5 and just ahead of Sonnet 5.5 (90%). Run 2026-11-07 through `claude -p` on the
  Claude Ultra account (CVP caveat as for every Claude row), never through another vendor. QBE went from 1/74
  to 69/74. Its weak spot is the structured path on AArch64 (25/37 first attempt): in 11 cells it wrote the
  schema's family title as the opcode (`"op": "arith"` with `add` in the comment), which the schema's `op`
  enum rules out; every such cell passed on the retry. All 21 first-attempt failures were reviewed by hand;
  none is a false reject. The README's earlier "Haiku" rows are Haiku 4.5 (`claude-haiku-4-5-20251001`).
- **The front end matters as much as the effort label.** GPT-6 Luna scores 58% through Codex and 79% through
  GitHub Copilot, with byte-identical prompts, zero tools and the same served effort (medium). Through Copilot
  it reasoned 2.7x as many tokens on first attempts (3.4x on the long battery, where its passes tripled, 10 to
  30); Codex's catalog defaults (low verbosity, priority tier) or the two services' budgets for "medium" differ.
  Both are each CLI's own defaults, as the fairness contract requires: an effort label does not carry across
  front ends. **The direction depends on the model:** Claude Sonnet 5.5 goes the other way, 90% through `claude -p`
  and 76% through Copilot (170/224; long battery 45 to 19), again with byte-identical prompts and no tools.
  Through Copilot it opens 39 of 224 first answers with a prose plan ("Plan:", "Design:", "Register plan:")
  against 1 through `claude -p`, and 9 first answers carry stray tool-call markup (`<invoke ...>`,
  `</parameter>`) although the request has no tools; those account for most of its 42 cells that flip to a
  first-attempt failure (format 16, syntax 10, compile 9). Neither route shows reasoning tokens and output
  lengths are alike (median 299 vs 308 tokens). The routes differ in Claude Code's own ~350 tokens of framing
  (not visible to us) and in effort (Copilot sends `medium`; `claude -p` uses its unexposed default); either
  can explain the stricter code-only answers through `claude -p`.
- **Microsoft MAI-Code 1.1 Flash** (Microsoft's own model, reached through GitHub Copilot Pro, the only
  Copilot-exclusive model) scores 48% first attempt (107/224) and 67% within four attempts, just below Haiku 4.5 (52%)
  and above only MAI-Thinking-1 (32%) among the cloud models. Raw assembly is its strength (stream 78%); QBE is almost entirely beyond it (4/74: 261 compile
  failures, mostly `%x = copy 0` without a result class, and invented opcodes such as `addl`), and its structured
  answers are often malformed JSON. On the app battery (whole programs) it managed 23/96 first attempt and 64/96
  within four (two cells re-run after a stream fix; the totals did not change): the only model tested so far that does not
  eventually write every program.
- **Two Azure Foundry models, run 2026-11-07/08: Microsoft MAI-Thinking-1 at 32% (72/224; 144/224 within four)
  and DeepSeek-V4-Pro at 62% (139/224; 217/224 within four).** They are the first cloud rows not reached through a coding
  CLI or Copilot: `foundry:<deployment>` calls the Foundry chat-completions API directly on the user's pay-as-you-go Azure
  subscription (Global Standard, serverless), with the same system and task prompts, no tools, the same time
  limits, and the served model checked on every chunk. Azure's content filter stays on (MAI-Thinking-1:
  `Microsoft.MAIDefault`; DeepSeek: `Microsoft.DefaultV2`); neither model had a refusal or a filter block.
  Cost: MAI-Thinking-1 $24.52 for 577 attempts (the $25 per-model cap stopped it with 10 cells left, and the
  user raised it to $28 to finish them); DeepSeek-V4-Pro $11.88 for 359. **DeepSeek ran at `reasoning_effort`
  high**, not the API default: at the default a 12-cell smoke test returned no reasoning and passed 4/12,
  against 10/12 at high, so the default row would have measured the switch rather than the model.
  MAI-Thinking-1 ran at its default. MAI-Thinking-1 is weakest on the long battery (2/54) and on QBE (8/74):
  most QBE answers omit the result class (`%c = ceql %n, 0` for `%c =w ceql`) or invent opcodes (`addl`,
  `mull`, `shrl`, `copyl`), and its structured answers often misplace fields (an `ldp` with its destination
  registers in `src`). DeepSeek's 7 never-passed cells are block-argument SSA and hex literals in QBE, and
  XXH64 wrong on every case. Both runs were reviewed by hand: no harness false rejects. Three MAI-Thinking-1
  attempts hit the 180 s limit, scored as failures like any other model's.
- **Five models on Fireworks.ai, run 2026-11-08/09: DeepSeek V4.1 Flash 85% (190/224; 224/224 within four),
  Qwen 3.8 Max 81% (182/224; 222/224), Z.ai GLM-5.3 77% (172/224; 217/224), MiniMax M3 52% (117/224; 184/224) and
  NVIDIA Nemotron 3 Ultra NVFP4 38% (86/224; 151/224).** GLM-5.3 and Qwen 3.8 Max stopped when the first $51 of
  credit ran out and were finished on 2026-11-09 after the user topped it up. `fireworks:<model id>` calls Fireworks'
  serverless chat-completions API directly, on the same contract as `foundry:` (same prompts, no tools, no sampling
  fields and no `max_tokens`, the same time limits, the served model checked on every chunk). Every model ran at its
  default effort, and all five reasoned by default (median reasoning tokens per answer: DeepSeek 3,752, MiniMax
  11,076, Nemotron 1,635, GLM 5,357, Qwen 10,659). **DeepSeek V4.1 Flash** is the strongest direct-API row: it
  eventually writes every cell, and its first-try rate is above DeepSeek-V4-Pro high on Foundry (62%), led by QBE
  (62/74, 84%). **Qwen 3.8 Max** and **GLM-5.3** come next, both strongest on the stream path (73/76 and
  68/76) and weakest on QBE (37/74 and 40/74). GLM's long answers ran into the time limits: 65 of its 312 attempts
  timed out (none at an output cap), against Qwen's 18. **MiniMax M3** and **Nemotron** fall down on QBE (11/74 and 2/74): most of their IR answers leave out
  the result class (`%s = add %a, %b` for `%s =l add`), 60 and 116 compile failures of that one kind. Fireworks
  applies an undocumented default output cap per model (65,536 tokens for MiniMax and DeepSeek, 32,768 for
  Nemotron); an answer that spends all of it reasoning and never answers is scored as a timeout (MiniMax 20
  attempts, Qwen 3, DeepSeek 1, Nemotron 1), the same rule for every model, never a raised cap. Qwen 3.8 Max's stream names
  the model by its display name ("Qwen 3.8 Max"); only that exact name, as Fireworks' own model API reports it,
  is accepted for it. Cost: $72.20 booked across the five runs (DeepSeek $2.79 for 267 attempts, MiniMax $10.08
  for 448, Nemotron $6.75 for 540, GLM $22.13 for 312, Qwen $30.45 for 287), an upper bound: a call that ended
  without a usage report was booked from what it had streamed, and before 2026-11-08 13:00 at the worst case. A
  DNS outage on this machine (about 16:00-16:40 on 11-08) stopped three runs, and intermittent DNS and TLS failures
  on 11-09 (14:26-15:19) cost GLM 26 retries; those calls were retried, never scored. Never-passed cells were reviewed by hand: no harness false rejects (QBE without result classes, x86
  scale 16 and register subtraction in addresses, `movzx` into an 8-bit register, mixed-width AArch64 operands,
  structured registers written as `{"reg": "rbx"}`, malformed JSON).
- **Apple Foundation Models, round 1 only (2026-11-08/09): the on-device model 0/32, Private Cloud Compute 2/48
  first attempt and 5/48 within four.** The `apple:` rows reach Apple's `fm serve` (OpenAI chat completions) on the
  user's Mac through an SSH forward; the request is ours byte for byte, and no cost applies. *On-device* (AFM 3 Core
  Advanced, 8,192-token context) runs behind the Mac session's governor: 30 calls a minute, one at a time, a 120 s
  limit per call (stricter than the harness's 180 s; two attempts timed out) and a 3,072 MB cap on the inference
  service's memory. It ran the stream and IR paths (32 cells, 135 calls) after a reboot cleared a stale ~2 GB idle
  footprint (peak during the run 1,366 MB); the structured cells were deferred because their ~5.5K-token schema
  prompts push the service over the cap. It passed no cell: its QBE is invented syntax, and its assembly fails to
  assemble (`ldr x3, [x2, x1, lsl #2]` for a 64-bit load) or loops. *PCC* ran all three paths, structured
  included, without reasoning: the route's default sends no `reasoning_effort`, and no attempt (181) shows a
  reasoning token. Its 5 cells passed within four are 3 stream and 2 structured; no IR cell passes. One cell,
  `count_nonzero_prefix.structured.x86_64`, drew two guardrail refusals ("The model's safety guardrails were
  triggered", arriving as an error event after a long repetitive answer) and one 180 s timeout. The PCC quota (per
  person, daily, on the user's iCloud+ account) ran out at 12:05 on 11-08 after about 140 calls, with 11 cells
  left, finished on 11-09; `fm serve`'s `/health` kept reporting the quota as not reached, so only the
  `insufficient_quota` error stops a run. A PCC subject with reasoning was tried on 2026-11-10 and closed
  out: at `reasoning_effort` low and medium, PCC ran out of output on `sum_to.stream.x86_64`, reasoning for about
  6 minutes per request and returning no answer (the Mac's log: `outputTokenLimitReached`; `fm serve`: finish_reason
  `stop`, empty content, usage 0/0), where PCC without reasoning answers the same prompt in about 2 s. The other
  cells were not tried (7 PCC calls); there is no reasoning row. Apple publishes no
  quota size or reset time. Both models sample randomly by default, so a rerun would give different answers.
- **Fable 5.1's** one final failure is a refusal: Anthropic's cyber classifier blocked
  `fill_down.ir.x86_64` on all four attempts, even on the CVP account.

## Local models (DGX Spark, and Apple's on-device model on a Mac)

Models that run on the user's own hardware. One of them is not open-weight and not on the DGX Spark: **Apple AFM 3
Core Advanced, on-device**, runs on the user's Mac through Apple's `fm serve` (`apple:system`; the Apple finding
above and the methodology report have the details). It covers round 1 only, on the stream and IR paths (32 cells:
the structured cells were deferred to Private Cloud Compute because their ~5.5K-token schema prompts push the
on-device inference service over the Mac's 3 GB memory cap), and passed none. It moved here from the vendor table
on 2026-11-12. The rest of this section is about the open-weight models.

Open-weight models served on this machine, an NVIDIA DGX Spark (GB10, 128 GB unified memory), by Ollama 0.30.10
(`local:<model>`; setup and isolation in [cross-vendor-methodology.md](cross-vendor-methodology.md#local-models-localmodel))
or, for Aleph Alpha Kolibri-1, by vLLM 0.29.0 in token-budget mode (`vllm:<model>@budget`: each call's limit is
an output-token budget, the most any vendor model used for one call in that battery, instead of wall-clock time;
see the methodology report).
Same prompts, same zero-tool contract, same per-call time limits and attempts as every vendor subject; each model's
own Modelfile sampling; `num_ctx` set explicitly to the native window or 65536, whichever is smaller. Weights are
quantised (Q4_K_M or Q8_0) and every model shares the one GPU with calls serialised, so the time limits count only
a call's own model time, but recorded latencies include queue waits and are not comparable with the vendor runs.
These results say what such a desktop setup can do, not what the same models do at full precision elsewhere.

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Aleph Alpha Kolibri-1 FP8, token budget (local) | 20 | 21 | 1 | 16 | **58/224 (26%)** | 97/224 (43%) | 2.99 |
| Qwen3-Coder-Next Q4 (local) | 5 | 5 | 0 | 2 | **12/224 (5%)** | 31/224 (14%) | 3.73 |
| Qwen3.6-35B-A3B Q8, thinking on (local) † | 4 | 5 | — | — | **9/110 (8%)** | 15/110 (14%) | 3.68 |
| Qwen3-30B-A3B Instruct 2507 Q8 (local) † | 5 | 1 | 0/5 | — | **6/115 (5%)** | 13/115 (11%) | 3.75 |
| Qwen3.6-35B-A3B Q8, thinking off (local) † | 1 | 3 | — | — | **4/110 (4%)** | 15/110 (14%) | 3.77 |
| Qwen2.5-14B Q4 (local) † | 1 | 2 | 0/4 | — | **3/114 (3%)** | 5/114 (4%) | 3.89 |
| Apple AFM 3 Core Advanced, on-device † | 0/32 | — | — | — | **0/32 (0%)** | 0/32 (0%) | 4.00 |

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Aleph Alpha Kolibri-1 FP8, token budget (local) | 36/76 (47%) | 22/74 (30%) | 0/74 (0%) |
| Qwen3-Coder-Next Q4 (local) | 12/76 (16%) | 0/74 (0%) | 0/74 (0%) |
| Qwen3.6-35B-A3B Q8, thinking on (local) † | 5/38 (13%) | 4/36 (11%) | 0/36 (0%) |
| Qwen3-30B-A3B Instruct 2507 Q8 (local) † | 5/40 (12%) | 1/38 (3%) | 0/37 (0%) |
| Qwen3.6-35B-A3B Q8, thinking off (local) † | 3/38 (8%) | 1/36 (3%) | 0/36 (0%) |
| Qwen2.5-14B Q4 (local) † | 2/40 (5%) | 1/38 (3%) | 0/36 (0%) |
| Apple AFM 3 Core Advanced, on-device † | 0/16 (0%) | — | 0/16 (0%) |

† partial, by decision (2026-10-01): local runs were trimmed to save days of GPU time once it was clear they score
in single digits. Qwen3-Coder-Next ran all four batteries; Qwen3-30B-A3B and Qwen2.5-14B stopped early in the long
battery (115 and 114 of 224 cells); Qwen3.6-35B-A3B ran round1 and round2 only (110 cells), once with thinking off
and once with thinking on (`local:<model>@nothink` / `@think`, Ollama's `think` field). Kolibri-1 ran all four batteries and
is the only local model that runs the app battery (3/96 first attempt, 16/96 within four:
[app report](live-model-eval-apps.md)).

- **Aleph Alpha Kolibri-1 is the best local model by far, still half Claude Haiku 4.5's rate.** 58/224 first
  try (26%) and 97/224 (43%) within four: round 1 20/48, round 2 21/62, long 1/54, adversarial 16/60. It passes
  47% of stream cells and 30% of structured cells on the first try, and no QBE cell (0/74; 282 of its 669 function-battery calls
  ended in a QBE compile failure). Token budgets bind on the adversarial battery (24,576 tokens: 52 of 176 calls
  hit it) and rarely elsewhere (2, 2 and 10 calls on rounds 1, 2 and long; 1 of 363 app calls). At ~21 tokens/s
  a call takes 10-18 minutes on average; the four function batteries took 151 hours.
- **Open coding models with strong published scores fail at tool-less machine code.** Qwen3-Coder-Next, Qwen's
  dedicated agentic coding model, passes 12/224 first try (5%), all on the stream path; 0/74 structured and 0/74
  QBE. The older Qwen3-30B-A3B and Qwen2.5-14B do no better on the cells they ran (5% and 3%).
- **Qwen3.6-35B-A3B, the newest Qwen MoE, is no better.** Thinking on: 9/110 first try (8%); thinking off:
  4/110 (4%); both 15/110 within four attempts. With thinking on, 376 of its 405 attempts ran out the 180 s
  function limit (374 before writing any answer text): at ~45 tokens/s it reasons for 12-14 K characters even on
  round 1's easiest tasks. Thinking off never returned reasoning (the adapter fails closed if it does) and answers
  in seconds, but wrongly: 144 QBE compile failures, 120 syntax and 42 format rejects, 43 wrong results.
- **Typical errors:** invalid QBE (missing result classes, invented operators, `@label:` with a colon); invented
  mnemonics (`cdiv`, `srsl`) and `x31` as a register name; structured operands written `{"reg": "rdi"}`, which
  the schema does not allow, and malformed JSON; ABI and runtime errors; and on the long battery, structured answers
  of ~23 KB that run out the 400 s limit on every attempt (Qwen3-30B-A3B at ~50 tokens/s).
- **False-reject review:** every failed attempt was replayed through the current pipeline. Qwen3-Coder-Next and
  Qwen3-30B-A3B: none. Qwen2.5-14B: 6 stream attempts on AArch64 (3 cells) were stopped at a line keystone refused
  but GNU as accepts (fixed since in 1502d04e); the answers were cut off there, so they cannot be rescored. Its
  first-attempt count could be at most one higher; details in the run's `ADJUSTMENTS.md`.
  Qwen3.6-35B-A3B (both modes, 790 failed attempts): none; the one `crosscheck` reject (`lea r9, [r13 - r13]`) is
  refused by GNU as too.
  Kolibri-1 (all failed first attempts in every battery, read by hand): none. Its errors are its own:
  `movzx rax, eax`, `{"reg": "rdi"}` operands, invalid QBE, `idiv` without `cqo`, an unhandled `INT_MIN / -1`,
  a clobbered callee-saved `x19`.
- **Conclusion.** No open-weight model that runs on this desktop machine writes tool-less machine code usefully.
  The Qwen models manage 3-8% first try and about 14% within four attempts, almost all of it on easy stream
  cells. Kolibri-1, given as many output tokens as any vendor model used, reaches 26% first try and 43% within
  four, but only half Claude Haiku 4.5's first-try rate (52%), below MAI-Thinking-1's 32%, and 3% on the app battery. No local model
  passes a single QBE cell on the first try. Published agentic-coding scores
  (Qwen3-Coder-Next, Qwen3.6) do not transfer to this task; the gap to Claude Haiku 4.5 (52%) is an order of
  magnitude for them, and to the weakest cloud model, MAI-Thinking-1 (32%), still four- to tenfold.

## Adversarial prompt variants

| Model | baseline | misleading_hint | terse |
|---|---|---|---|
| Claude Opus 5.5 | 57/60 | 59/60 | 60/60 |
| Claude Fable 5.1 | 54/60 | 48/60 | 55/60 |
| Claude Sonnet 5 | 45/60 | 38/60 | 43/60 |
| Claude Haiku 4.5 | 35/60 | 27/60 | 33/60 |

(first-attempt passes of 60 cells). Sonnet and Haiku lose points by *following* the wrong tips. Fable loses
them differently: the misleading tips (about writing past a buffer, reading out of bounds, callee behaviour)
push 11 prompts over Anthropic's cyber classifier, which refuses them on every attempt (all six `fill_down`
cells, three `bmin_empty`, two `map_into`), even on the CVP account; its answers are not misled.

## False-reject review

Every non-Claude run's failures were checked by hand as the Claude runs were: compile errors re-run through
QBE, stream rejects re-assembled with GNU as, structured rejects checked against the prompt's schema,
runtime verdicts read against the spec (some confirmed by running the code natively), and raw event streams
rebuilt to the recorded answers. Two harness problems were found in all of it. First: Daybreak Blue
`max_byte.stream.aarch64` attempt 1, where AArch64 `uxtw x0, w2` was refused because keystone and GNU as
emit different (equivalent) encodings. Fixed (`bca/stream.py`: a same-size disagreement now takes GNU as's
bytes); counted as a pass above. Second: grok 1.0.41's streaming output occasionally repeats a text chunk; three
Grok 4.7 build-fast answers reached the scorer beginning with a doubled `{"` the model never wrote (grok's own
session record shows the real answer). Rescored with the duplicate removed they pass, and are counted as passes
above (details in that run's `ADJUSTMENTS.md`); the adapter now scores grok's session record, not the stream.
GPT-6 Luna via Copilot: one, `mat_pow.stream.aarch64` attempt 1, rejected at `mov x17, x20, lsl #3`, which keystone
refuses outright but GNU as accepts (an alias of `orr x17, xzr, x20, lsl #3`); rescored with the recorded answer it
passes, and it counts as a pass above (fixed in 1502d04e: the stream path takes GNU as's bytes when keystone refuses).
Claude Sonnet 5.5 via Copilot: none. Two answers define x86 labels named like registers (`cl`, `ch`) and jump to
them; the encoder refused the jump. That is genuine: the x86 jump schema in the prompt says register names are not
usable as labels, and GNU as also rejects `jmp cl` with a label `cl` defined (ea22a6bc now refuses such a label at
its definition, with a clearer message; no verdict changes). Also, `vm_run.structured.aarch64` passed on a fresh re-run after the pre-fix output-cap abort (the runtime's
continuation event stopped the subject); under the current rule the capped 34,574-character answer (ending in a
run of repeated characters) is scored, and it fails as `format`, so that cell counts as a first-attempt failure.
Kimi K3, Grok 4.7, Grok 4.6, Grok 4.5, Codex Astra, Sol and GPT-5.5, Claude Sonnet 4.5, Opus 4.8, MAI-Code, MAI-Thinking-1 and DeepSeek-V4-Pro: none
(Astra's 227 attempts were all rescored and reproduced; 181 failed attempts reviewed across the last six).

Runs of 2026-11-11 and 11-12 (Codex GPT-6.1 Sol and Terra, GPT-5.6 Sol, Terra and Luna; gpt-oss-120b on Fireworks and
Cerebras; Qwen 3.8 27B on Cerebras; Kimi K3.1 Pro at `high` and K3.1 Pro Highspeed; Claude Opus 5, Fable 5 and Opus
4.7; Grok 4.5's app battery; the Vibe and Muse harness studies): none. For the stronger models (the five Codex
models' two upper tiers, both Kimi rows, the three Claude models, Grok 4.5's apps, Mistral Large 4 and Muse Spark
1.3) every failed attempt in a category where the harness could be at fault (assembler and encoder rejections,
format, encoding, cross-check, link) was grouped by message and read: 162 attempts in 39 groups. Each rejection is
one GNU as also makes (an unencodable AArch64 immediate, `cmp x2, sp`, a 64-bit register offset with `uxtw`, three
registers in an x86 address, `rip` with an index, a symbol used before its definition, a label named `k0`) or a
rule the prompt states (the structured schema's operand forms, instructions only on the stream path). All 15
cross-check rejections in the new runs were re-read: 14 use `x31` as a register name, which keystone accepts and GNU
as does not. The remaining one, `.skip 0` in an older Nemotron run (a third attempt in a cell that failed all four),
is the stated rule that an assembler warning rejects the statement, which applies to every model. Two faults were
found in adapters and fixed before the affected cells counted. Muse Code discards a streamed sample containing
characters of another script and samples again; the first Muse adapter graded the discarded partial sample followed
by the new one (3 cells superseded and re-run; the adapter now grades Muse's final answer). The Vibe adapter retried
Vibe's turn limit as an infrastructure error, giving extra draws (19 cells superseded and re-run; now a scored
timeout). Both are in the [agent-harness report](live-model-eval-agent-harness.md), outside these tables.

Latency figures are not comparable across vendors (whole-message CLIs; and Kimi's recorded durations in
this run include waits for the credential-refresh lock, fixed for future runs).
