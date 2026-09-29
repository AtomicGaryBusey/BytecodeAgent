# Live-model eval: claude-sonnet-5-5 on every battery

> **Account caveat (Claude results).** Every Claude run in this repository used a Claude Max subscription enrolled in Anthropic's **Cyber Verification Program** (relaxed cyber safeguards for verified security researchers; confirmed active 2026-09-28). Claude's safety classifiers are less restrictive on this account than on one without CVP, so refusal rates here are likely lower, and pass rates likely higher, than a non-CVP org or subscription would see on this machine-code work. Treat Claude numbers as an upper bound for non-CVP accounts. Whether the other vendors' accounts carry comparable programs is unknown.

Run: `eval/runs/sonnet55-all-20260928-182447` (2026-09-28; claude CLI 2.1.284, CLI-default effort, model pinned by its explicit id, and
every cell reported claude-sonnet-5-5). Rounds 1 + 2, the long battery and the adversarial battery
(baseline prompts): 224 cells, up to 4 attempts with repair feedback. 250 calls, no infrastructure errors.

## pass@1 against the other Claude models (baseline prompts, same cells)

| model | round1 | round2 | long | adversarial | all 224 | pass within 4 |
|---|---|---|---|---|---|---|
| claude-opus-5-5 | 45/48 | 60/62 | 53/54 | 57/60 | 215 (96%) | 224 (100%) |
| **claude-sonnet-5-5** | **47/48** | **56/62** | **45/54** | **53/60** | **201 (90%)** | **223 (99.6%)** |
| claude-sonnet-5 | 40/48 | 49/62 | 41/54 | 45/60 | 175 (78%) | 222 (99%) |
| claude-haiku-4-5 | 30/48 | 34/62 | 17/54 | 35/60 | 116 (52%) | 158 (71%) |

(opus and sonnet-5 ran on CLI 2.1.283; the prompts and the harness are identical, apart from the
one-character-literal fix, which affected none of the cells above.)

## Findings

- **Sonnet 5.5 closes most of the gap to opus**: 90% pass@1 vs 78% for Sonnet 5 and 96% for opus, with
  1.12 mean attempts. It is best-in-class on round 1 (47/48).
- **Its big gain is structured x86-64**: 34/37 pass@1 vs 17/37 for Sonnet 5 — the `{"reg": "rdi"}`
  habit nearly disappeared (one occurrence left).
- **QBE is still its weakest path** (60/74 = 81% pass@1, the same as Sonnet 5): phi syntax
  (`phi @a, 1, @b, %x` with commas between label and value; phis after ordinary instructions), a
  reused SSA name, and twice its own reasoning leaking into the output ("Wait —", "Signed ...").
  Every IR cell was repaired.
- **The one cell it never solved** (binary_search, structured x86-64) is a real bug repeated verbatim
  on all 4 attempts: `xor ecx, 0` where `xor ecx, ecx` was meant, so the loop index starts as garbage.
- All 23 first-attempt failures were read; none were harness false rejects. It also reached for
  `adc` on AArch64 (the structured-schema gap noted in the adversarial report).
