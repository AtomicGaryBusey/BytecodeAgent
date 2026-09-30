# Live-model eval: models from five vendors

> **Account caveat.** The Claude account is enrolled in Anthropic's **Cyber Verification Program** and the
> ChatGPT account in OpenAI's **Daybreak** (Blue tier; no Red / `*-cyber` models); both relax cyber safeguards,
> so treat those rows as upper bounds for ordinary accounts. The Kimi, Google and xAI accounts have no known
> equivalent. Plans: Claude Max 20x, ChatGPT Plus, Kimi Allegretto, Google AI Plus (2 TB), SuperGrok.
> Method, fairness contract and known limits: [cross-vendor-methodology.md](cross-vendor-methodology.md).

Every model got the same 224 cells: rounds 1 + 2, the long-program battery and the adversarial battery
(baseline prompts), across the stream, structured-JSON and QBE-IR paths on x86-64 and AArch64, with up to
four attempts and repair feedback. Each ran through its vendor's own coding CLI with zero tools, the same
system and task prompts, and its CLI's default effort (as the Claude runs did). Runs date from
2026-09-27/29; CLI versions: claude 2.1.283-2.1.285, codex 0.157.1, kimi 2.1.1, agy 1.2.12, grok 1.0.41.

## Results (first-attempt passes per battery)

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Codex GPT-6 Astra | 46 | 61 | 54 | 60 | **221/224 (99%)** | 224/224 (100%) | 1.01 |
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6 Sol | 43 | 62 | 48 | 60 | **213/224 (95%)** | 224/224 (100%) | 1.05 |
| Grok 4.7 build-fast | 47 | 60 | 49 | 57 | **213/224 (95%)** | 223/224 (100%) | 1.08 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Grok 4.6 | 45 | 59 | 50 | 57 | **211/224 (94%)** | 223/224 (100%) | 1.07 |
| Codex GPT-5.5 | 47 | 57 | 45 | 58 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Claude Sonnet 4.5 | 48 | 55 | 44 | 56 | **203/224 (91%)** | 221/224 (99%) | 1.14 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Grok 4.5 | 47 | 55 | 41 | 54 | **197/224 (88%)** | 222/224 (99%) | 1.16 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Claude Opus 4.8 | 35 | 48 | 49 | 43 | **175/224 (78%)** | 222/224 (99%) | 1.27 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |

Codex Daybreak Blue includes one reviewed harness false reject counted as a pass, Grok 4.7 build-fast
three (see the false-reject review below). Still running (added when complete and reviewed): Kimi K2.8 (kimi-for-coding) and Gemini 3.8 Flash on Antigravity (quota-paced).

## By emission path (first-attempt passes, complete runs)

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Codex GPT-6 Astra | 76/76 (100%) | 74/74 (100%) | 71/74 (96%) |
| Claude Opus 5.5 | 74/76 (97%) | 71/74 (96%) | 70/74 (95%) |
| Codex GPT-6 Sol | 73/76 (96%) | 71/74 (96%) | 69/74 (93%) |
| Grok 4.7 build-fast | 75/76 (99%) | 72/74 (97%) | 66/74 (89%) |
| Claude Fable 5.1 | 72/76 (95%) | 69/74 (93%) | 71/74 (96%) |
| Grok 4.7 | 76/76 (100%) | 71/74 (96%) | 64/74 (86%) |
| Grok 4.6 | 73/76 (96%) | 73/74 (99%) | 65/74 (88%) |
| Codex GPT-5.5 | 72/76 (95%) | 71/74 (96%) | 64/74 (86%) |
| Kimi K3 | 75/76 (99%) | 66/74 (89%) | 63/74 (85%) |
| Claude Sonnet 4.5 | 74/76 (97%) | 67/74 (91%) | 62/74 (84%) |
| Codex Daybreak Blue | 71/76 (93%) | 69/74 (93%) | 61/74 (82%) |
| Claude Sonnet 5.5 | 73/76 (96%) | 68/74 (92%) | 60/74 (81%) |
| Grok 4.5 | 73/76 (96%) | 71/74 (96%) | 53/74 (72%) |
| Claude Sonnet 5 | 63/76 (83%) | 53/74 (72%) | 59/74 (80%) |
| Claude Opus 4.8 | 63/76 (83%) | 66/74 (89%) | 46/74 (62%) |
| Codex GPT-6 Luna | 60/76 (79%) | 45/74 (61%) | 25/74 (34%) |
| Claude Haiku 4.5 | 61/76 (80%) | 54/74 (73%) | 1/74 (1%) |

## Findings

- **Codex GPT-6 Astra leads at 99%** (221/224; 100% on stream and structured, 224/224 within four attempts),
  ahead of Opus 5.5 (96%), Grok 4.7 build-fast and Fable 5.1 (95%), Grok 4.7 (94%) and Kimi K3 (91%).
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
- **Fable 5.1's** one final failure is a refusal: Anthropic's cyber classifier blocked
  `fill_down.ir.x86_64` on all four attempts, even on the CVP account.

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
Kimi K3, Grok 4.7, Grok 4.6, Grok 4.5, Codex Astra, Sol and GPT-5.5, Claude Sonnet 4.5 and Opus 4.8: none
(Astra's 227 attempts were all rescored and reproduced; 181 failed attempts reviewed across the last six).

Latency figures are not comparable across vendors (whole-message CLIs; and Kimi's recorded durations in
this run include waits for the credential-refresh lock, fixed for future runs).
