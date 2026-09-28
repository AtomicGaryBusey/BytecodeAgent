# Live-model eval: the adversarial battery (haiku, sonnet, opus)

Run: `eval/runs/adv-3subj-20260927-232218` (2026-09-27/28; claude CLI 2.1.283, CLI-default effort). 10 bait tasks (off-by-one
comparisons, empty inputs, callee-saved registers the natural code clobbers, unsigned/signed traps,
shift counts >= 64, a hostile callback) × 3 paths × 2 ISAs = 60 cells per (model, variant), up to 4
attempts with repair feedback. Variants: `baseline`; `misleading_hint` (the prompt adds a plausible
wrong tip); `terse` (a stripped-down prompt). opus × terse was added
the next day (claude CLI 2.1.284; same model id, prompts and harness). 832 calls, no infrastructure errors, all 540 cells completed.

| model | variant | pass@1 | pass within 4 | mean attempts |
|---|---|---|---|---|
| opus | baseline | 57/60 (95%) | 60/60 | 1.05 |
| opus | misleading_hint | 59/60 (98%) | 60/60 | 1.02 |
| opus | terse | 60/60 (100%) | 60/60 | 1.00 |
| sonnet | baseline | 45/60 (75%) | 59/60 | 1.35 |
| sonnet | misleading_hint | 38/60 (63%) | 57/60 | 1.55 |
| sonnet | terse | 43/60 (72%) | 60/60 | 1.42 |
| haiku | baseline | 35/60 (58%) | 44/60 | 2.02 |
| haiku | misleading_hint | 27/60 (45%) | 41/60 | 2.27 |
| haiku | terse | 33/60 (55%) | 42/60 | 2.20 |

## Findings

- **Opus is immune to the misleading hints** (98% pass@1 with them vs 95% without). Its 3 baseline
  misses were one quirk repeated: structured AArch64 JSON with a stray `"mov": null` key next to
  `"op": "mov"` (the schema forbids extra fields); each was fixed on the first repair.
- **The hints cost sonnet 12 points and haiku 13 points of pass@1**, and the damage follows the hints:
  e.g. every `shift_sat` failure under the hint returns 2 for `shift_sat(1, 64)` (the count wrapped
  mod 64, as the tip suggested), and `fill_down` / `bmin_empty` failures are the empty-input cases the
  hints talked the models out of guarding. Sonnet's hint-induced losses (12 cells that pass at
  baseline) are spread over stream, structured and IR; repair still recovers most (95% within 4).
- **Terse prompts matter little** (opus +5, sonnet -3, haiku -2 points pass@1): the task statements, not the
  surrounding guidance, carry what the models need.
- **Haiku's QBE weakness dominates its IR column again** (1/20 pass@1); on stream and structured it
  scores 80-90% pass@1 at baseline, close to sonnet.

## Checked for harness false rejects

All first-attempt failures of opus and sonnet, and every cell the hint flipped from pass to fail,
were read. None were false rejects. One interface gap (not a bug) showed up: the structured AArch64
schema has no extend ops (`sxtw`, `sxtb`, `uxtb`) and no add/subtract-with-carry (`adc`, `sbc`),
though x86 has `movsx` / `adc`. The schema's op list is explicit, so using them is a genuine schema
violation, but sonnet and haiku both reached for them (here and in the long battery). Adding them is a
worthwhile follow-up; it would change the interface, so it is left for a new round.
