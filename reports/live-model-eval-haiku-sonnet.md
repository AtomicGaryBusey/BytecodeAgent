# Live-model eval: haiku and sonnet on rounds 1 + 2

> **Account caveat (Claude results).** Every Claude run in this repository used a Claude Max subscription enrolled in Anthropic's **Cyber Verification Program** (relaxed cyber safeguards for verified security researchers; confirmed active 2026-09-28). Claude's safety classifiers are less restrictive on this account than on one without CVP, so refusal rates here are likely lower, and pass rates likely higher, than a non-CVP org or subscription would see on this machine-code work. Treat Claude numbers as an upper bound for non-CVP accounts. The ChatGPT account used for Codex is enrolled in OpenAI's equivalent **Daybreak** program at the **Blue** tier only (no Daybreak Red / `*-cyber` models); whether Daybreak relaxes anything for the non-Daybreak models evaluated here (e.g. `gpt-6-luna`) is unknown. The Kimi and Google accounts have no known equivalent enrolment.

Run: `eval/runs/r12-haiku-sonnet-20260927-193906` (2026-09-27; claude CLI 2.1.283, CLI-default effort —
the same setting as the opus runs, so the three are comparable). 110 cells per model (both batteries ×
stream / structured / IR × x86-64 / AArch64), up to 4 attempts with repair feedback. 368 calls, no
infrastructure errors. The first pass stopped at its 350-call cap with 10 cells unfinished; it was resumed
(cap 400) and every cell completed.

## Results against opus

| model | pass@1 | pass within 4 | mean attempts |
|---|---|---|---|
| claude-opus-5-5 (rounds 1–2, earlier runs) | 105/110 (95%) | 110/110 (100%) | 1.05 |
| claude-sonnet-5 | 89/110 (81%) | 109/110 (99%) | 1.27 |
| claude-haiku-4-5 | 64/110 (58%) | 79/110 (72%) | 2.07 |

pass@1 by path:

| model | stream | structured | IR (QBE) |
|---|---|---|---|
| opus | 37/38 (97%) | 36/36 (100%) | 32/36 (89%) |
| sonnet | 32/38 (84%) | 26/36 (72%) | 31/36 (86%) |
| haiku | 35/38 (92%) | 29/36 (81%) | **0/36 (0%)** |

## What the failures are (all checked by hand: no harness false rejects found)

- **haiku cannot write QBE.** 128 of its 133 compile failures are QBE syntax: it drops the result class
  on every assignment (`%c = csgtl %n, 0` instead of `%c =w csgtl %n, 0`, 62 attempts), adds `:` after
  labels, and invents opcodes (`addl`, `subl`). QBE's diagnostics are terse ("invalid class specifier"),
  so repair rounds rarely help: 6/36 IR cells passed within 4 attempts. On raw assembly (stream) haiku is
  nearly as good as opus (92% pass@1, 100% within 4).
- **sonnet's weak spot is structured x86-64 (8/18 pass@1).** Six first attempts wrote register operands
  as `{"reg": "rdi"}` instead of `"rdi"`. The schema is unambiguous (a string enum), but its shared
  definition is named `$defs/reg`, which plausibly invites the object form — a prompt-design effect
  worth knowing about. Repair fixed it every time (17/18 within 4). The rest were genuine wrong results
  (32-bit registers where 64 were needed: garbage upper halves).
- **sonnet on stream AArch64 (13/19 pass@1):** symbolic operand names left in (`sub x10, n, #1`), a
  placeholder register (`x0dummy`), an off-by-one in `fib_rec`, a crash, a hang, an OOB read in the SIMD
  dot product. All fixed on repair.

## Takeaways

- The ranking is opus > sonnet > haiku, as expected, but the gap is path-dependent: raw assembly is the
  most robust interface for the small model, while an unfamiliar IR (QBE) is where it collapses.
- Repair feedback is worth a lot for sonnet (81% → 99%) and little for haiku (58% → 72%), because
  haiku's dominant error (QBE syntax) gets feedback it cannot act on.
- Possible follow-ups (not done, would change comparability with earlier rounds): echo the offending
  source line with QBE errors; rename the schema's `$defs/reg` to `$defs/register_name`.
