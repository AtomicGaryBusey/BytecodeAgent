# Live-model eval: effort, serving and run-to-run studies

> One model, run two ways (or twice the same way), on the same cells. These runs answer side questions the main
> tables cannot: what a reasoning-effort setting is worth, whether the host or the serving tier changes the result,
> and how much a result moves when nothing is changed at all.
> The two runs made only for an effort comparison (`effort-study` in the run directory's name) are left out of
> every other table; the other runs here are the models' ordinary rows. Account caveats and method:
> [live-model-eval-cross-vendor.md](live-model-eval-cross-vendor.md),
> [cross-vendor-methodology.md](cross-vendor-methodology.md).

Every pair is first attempt against first attempt on identical cells, tested with the exact McNemar test on the
cells where the two runs disagree. Same prompts, same time limits (180 s; 400 s on the long battery), same four
attempts. The effort, host and tier pairs cover the 224 function cells; the second samples cover the long battery
(54 cells), where the models differ most. From `eval/effort_tables.py`:

| Comparison | A | B | First attempt A → B | Only A / only B passed | p | Within 4 A → B | Median call A → B | Median output tokens A → B | Timeouts A → B |
|---|---|---|---|---|---|---|---|---|---|
| Effort | OpenAI gpt-oss-120b (Cerebras), default effort | the same, `reasoning_effort: high` | 71 → 97 of 224 | 10 / 36 | < 0.001 | 117 → 138 | 1.4 s → 7.6 s | 1,997 → 12,149 | 1 → 18 |
| Effort | Kimi K3.1 Pro, `high` (catalog default since Kimi Code 2.5.3) | the same, `max` (catalog default on 2026-10-23) | 207 → 218 of 224 | 4 / 15 | 0.019 | 224 → 223 | 37.4 s → 77.8 s | 846 → 1,974 | 0 → 5 |
| Host | OpenAI gpt-oss-120b on Fireworks | the same on Cerebras | 71 → 71 of 224 | 16 / 16 | 1 | 120 → 117 | 11.5 s → 1.4 s | 2,077 → 1,997 | 0 → 1 |
| Serving tier | Kimi K3.1 Pro | Kimi K3.1 Pro Highspeed | 207 → 215 of 224 | 6 / 14 | 0.12 | 224 → 224 | 37.4 s → 19.3 s | 846 → 815 | 0 → 0 |
| Second sample (long battery) | Grok 4.7 build-fast, 2026-09-29 | the same, 2026-11-13 | 46 → 52 of 54 | 1 / 7 | 0.07 | 53 → 54 | 145.0 s → 144.4 s | 15,621 → 15,366 | 8 → 3 |
| Second sample (long battery) | Grok 4.7, 2026-09-28 | the same, 2026-11-13 | 48 → 44 of 54 | 8 / 4 | 0.39 | 50 → 52 | 275.5 s → 318.7 s | 16,024 → 15,639 | 18 → 21 |
| Second sample (long battery) | Grok 4.6, 2026-09-29 | the same, 2026-11-13 | 50 → 47 of 54 | 5 / 2 | 0.45 | 53 → 54 | 175.8 s → 128.9 s | 11,823 → 11,868 | 3 → 1 |

Where the disagreeing cells fall (only A passed / only B passed, by battery) and the effort each run recorded:

| A | B | Round 1 | Round 2 | Long | Adversarial | Recorded effort A | Recorded effort B |
|---|---|---|---|---|---|---|---|
| OpenAI gpt-oss-120b (Cerebras), default effort | the same, `reasoning_effort: high` | 3 / 4 | 4 / 16 | 2 / 5 | 1 / 11 | model-default | high |
| Kimi K3.1 Pro, `high` (catalog default since Kimi Code 2.5.3) | the same, `max` (catalog default on 2026-10-23) | 1 / 3 | 2 / 1 | 0 / 7 | 1 / 4 | high | cli-default, max |
| OpenAI gpt-oss-120b on Fireworks | the same on Cerebras | 1 / 3 | 7 / 6 | 1 / 0 | 7 / 7 | model-default | model-default |
| Kimi K3.1 Pro | Kimi K3.1 Pro Highspeed | 1 / 3 | 3 / 1 | 2 / 6 | 0 / 4 | high | high |
| Grok 4.7 build-fast, 2026-09-29 | the same, 2026-11-13 | 0 / 0 | 0 / 0 | 1 / 7 | 0 / 0 | cli-default | cli-default |
| Grok 4.7, 2026-09-28 | the same, 2026-11-13 | 0 / 0 | 0 / 0 | 8 / 4 | 0 / 0 | cli-default | cli-default |
| Grok 4.6, 2026-09-29 | the same, 2026-11-13 | 0 / 0 | 0 / 0 | 5 / 2 | 0 / 0 | cli-default | cli-default |

## Findings

- **More effort buys first-attempt accuracy in both models, at a cost that differs by an order of magnitude.**
  gpt-oss-120b gains 26 cells (32% to 43%; 36 gained, 10 lost) for six times the output tokens and five times the
  time per call, and starts hitting the time limit (18 timeouts against 1). Kimi K3.1 Pro gains 11 cells (92% to
  97%; 15 gained, 4 lost) for 2.3 times the tokens and twice the time. Both differences are significant.
- **For Kimi K3.1 Pro the extra effort does not change what gets solved, only how soon.** At `high` it recovers all
  224 cells within four attempts; at `max`, 223. The first-attempt gap is in the harder batteries: 7 of the 15
  cells only `max` passed are in the long battery and 4 in the adversarial one.
- **gpt-oss-120b's ceiling is the model.** `high` is the top of the three levels Cerebras accepts for it
  (`low`, `medium`, `high`); at that level it is still at 43% and recovers 138 of 224 within four.
- **The host does not matter for gpt-oss-120b's accuracy.** Fireworks and Cerebras each score 71 of 224 and
  disagree on 32 cells, split exactly 16 / 16. Cerebras is eight times faster per call (1.4 s against 11.5 s) at
  about 1.6 times Fireworks' cost for this model ($1.96 against $1.21 charged for the function batteries). Speed changes
  nothing that is scored here, because neither host is near the time limits on this model.
- **Kimi's Highspeed tier did not cost accuracy.** K3.1 Pro Highspeed scored 215 against the standard tier's 207 at
  the same `high` effort; the 8-cell difference is not significant (p = 0.12; the two runs disagree on 20 cells, 6 one way and 14 the
  other). Highspeed answers in half the time for twice the list price and twice the plan quota. The app and hex batteries
  (finished 2026-11-14) agree: 86/96 apps first attempt against 85/96, all 96 within four in both; 14/16 hex
  functions against 11/16; 4/16 hex programs first attempt in both (16 and 15 within four).

- **Run-to-run variation is real and about as large as some of the differences above.** Three Grok models re-ran
  the long battery on 2026-11-13 with the same CLI (grok 1.0.41), effort and limits as in September. Their first-attempt
  scores moved by +6 (Grok 4.7 build-fast, 46 -> 52), -4 (Grok 4.7, 48 -> 44) and -3 (Grok 4.6, 50 -> 47) of 54,
  with the two runs disagreeing on 8, 12 and 7 cells. None of the three changes is significant (p = 0.07, 0.39,
  0.45), and within four attempts the runs agree to within two cells. Tokens and time per call barely moved, so it is
  the answers that vary, not the load. Two consequences: the order of the three Groks on the long battery flips
  between samples (50 / 48 / 46 in September, 47 / 44 / 52 now), so **differences of a few cells on one battery
  should not be read as a ranking**; and the first-attempt figure is the noisy one, while within-four is stable.
  The second samples are not any model's row in the main tables: those keep the first run.

## Why the Kimi K3.1 Pro pair exists

K3.1 Pro's function batteries first ran on 2026-10-23 and 10-24 at `max`, the effort Kimi Code 2.3.1's catalog gave
the model by default. After a new `kimi login` on 2026-11-11 the catalog default was `high`, and the model's app and
hex batteries of 2026-11-10 had already run at `high`. Every row in the main tables is at its CLI's default effort,
so the function batteries were re-run at `high` on 2026-11-11 (Kimi Code 2.5.3, re-audited) and that run is now the
model's row: 207/224. The `max` run (218/224) is the same harness, prompts and limits, and is kept here. It records
its effort in each cell's metadata (`cli-default`, `max`), not in each attempt, because per-attempt recording was
added to the Kimi adapter later.

## Not covered

- No effort comparison for any Claude, Codex, Grok or Gemini model: each ran at its CLI's default only. The Codex
  rows differ in catalog default (`low` for GPT-6.1 Sol, GPT-6.1 Terra and GPT-5.6 Sol; `medium` for GPT-5.6 Terra
  and Luna, as for GPT-6 Luna), which is a difference between rows, not a study.
- Run-to-run variation is measured for three Grok models on one battery only (above). Second long-battery samples
  for Claude Opus 5.5 and Fable 5.1 are queued (they wait on the Claude plan), and for the Codex models on the user
  naming them. For the other pairs there is one run per setting; as a rough guide, the two Kimi serving tiers at
  the same effort differ on 20 of 224 cells and the two gpt-oss-120b hosts on 32.
- The Muse tier comparison (paid against contributor tier, same model) is half run; it waits for Muse's weekly quota.
