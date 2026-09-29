# Live-model eval: ten models from five vendors

> **Account caveat.** The Claude account is enrolled in Anthropic's **Cyber Verification Program** and the
> ChatGPT account in OpenAI's **Daybreak** (Blue tier; no Red / `*-cyber` models); both relax cyber safeguards,
> so treat those rows as upper bounds for ordinary accounts. The Kimi, Google and xAI accounts have no known
> equivalent. Plans: Claude Max 20x, ChatGPT Plus, Kimi Allegretto, Google AI Plus (2 TB), SuperGrok.
> Method, fairness contract and known limits: [cross-vendor-methodology.md](cross-vendor-methodology.md).

Every model got the same 224 cells: rounds 1 + 2, the long-program battery and the adversarial battery
(baseline prompts), across the stream, structured-JSON and QBE-IR paths on x86-64 and AArch64, with up to
four attempts and repair feedback. Each ran through its vendor's own coding CLI with zero tools, the same
system and task prompts, and its CLI's default effort (as the Claude runs did). Runs date from
2026-09-27/29; CLI versions: claude 2.1.283/2.1.284, codex 0.157.1, kimi 2.1.1, agy 1.2.12, grok 1.0.41.

## Results (first-attempt passes per battery)

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |
| Gemini 3.8 Flash High (Antigravity) | 46 | 21/21† | — | — | **67/69 (97%)** | 69/69 (100%) | 1.03 |

† Partial: Antigravity is slowed by Google AI Plus's weekly per-account quota (~75 calls/week) and resumes
after each reset; its cells so far are mostly the easier early ones, so its percentage is not comparable yet.
Codex Daybreak Blue includes one reviewed harness false reject counted as a pass (below).

## By emission path (first-attempt passes, complete runs)

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Claude Opus 5.5 | 74/76 (97%) | 71/74 (96%) | 70/74 (95%) |
| Claude Fable 5.1 | 72/76 (95%) | 69/74 (93%) | 71/74 (96%) |
| Grok 4.7 | 76/76 (100%) | 71/74 (96%) | 64/74 (86%) |
| Kimi K3 | 75/76 (99%) | 66/74 (89%) | 63/74 (85%) |
| Codex Daybreak Blue | 71/76 (93%) | 69/74 (93%) | 61/74 (82%) |
| Claude Sonnet 5.5 | 73/76 (96%) | 68/74 (92%) | 60/74 (81%) |
| Claude Sonnet 5 | 63/76 (83%) | 53/74 (72%) | 59/74 (80%) |
| Codex GPT-6 Luna | 60/76 (79%) | 45/74 (61%) | 25/74 (34%) |
| Claude Haiku 4.5 | 61/76 (80%) | 54/74 (73%) | 1/74 (1%) |

## Findings

- **Four models are within five points at the top**: Opus 5.5 (96%), Fable 5.1 (95%), Grok 4.7 (94%) and
  Kimi K3 (91%). Kimi, Daybreak Blue and Opus reach 224/224 within four attempts.
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
- **Fable 5.1's** one final failure is a refusal: Anthropic's cyber classifier blocked
  `fill_down.ir.x86_64` on all four attempts, even on the CVP account.

## False-reject review

Every non-Claude run's failures were checked by hand as the Claude runs were: compile errors re-run through
QBE, stream rejects re-assembled with GNU as, structured rejects checked against the prompt's schema,
runtime verdicts read against the spec (some confirmed by running the code natively), and raw event streams
rebuilt to the recorded answers (all matched). One false reject was found in all of it: Daybreak Blue
`max_byte.stream.aarch64` attempt 1, where AArch64 `uxtw x0, w2` was refused because keystone and GNU as
emit different (equivalent) encodings. Fixed (`bca/stream.py`: a same-size disagreement now takes GNU as's
bytes); counted as a pass above. Kimi and Grok: none.

Latency figures are not comparable across vendors (whole-message CLIs; and Kimi's recorded durations in
this run include waits for the credential-refresh lock, fixed for future runs).
