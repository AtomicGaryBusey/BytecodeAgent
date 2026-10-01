# Adjustments to this run (copilot:claude-sonnet-5.5)

Reviewed by hand: 83 failed attempts, 130 voided infrastructure attempts (129 HTTP 402: Copilot's monthly
credits ran out at 203/224; the last 21 cells ran after the 2026-10-01 reset; 1 output-cap continuation, below).

Counted in eval/xv_tables.py ADJ as +1 and -1 (net 0, both long battery, structured path):

- `mat_pow.structured.x86_64` attempt 1: **harness false reject.** The program defines a label named `cl` and
  jumps to it; the structured encoder read the target as the 8-bit register `cl` ("an indirect jmp needs a 64-bit
  register, got cl"). The prompt does not forbid register-shaped label names. With the label renamed the recorded
  answer passes the real pipeline. Counted as a first-attempt pass.
- `vm_run.structured.aarch64`: the first call hit Copilot's 32,000-token output cap and the runtime emitted a
  `model_turn_continuation` (thinking-exhausted-continuation) event, which at the time aborted the subject as an
  isolation failure (fixed in 929d4577). The cell was re-run fresh and passed on attempt 1. Under the current
  rule the capped text is scored as it stands: 34,574 characters ending in a long run of a repeated character,
  which fails as `format`. Counted as a first-attempt failure. (Whether later attempts would have passed is
  unknown; the cell's within-four result is left as recorded.)

Not counted (no score change): `base64_encode.structured.x86_64` attempt 2 is the same encoder false reject (label
`ch`); attempt 1 of that cell had already failed genuinely (prose before the JSON).

Everything else is genuine: prose plans before the code (39 first answers), stray tool-call markup in 9
answers, invalid QBE, GNU-as-rejected stream lines, `xor reg, imm 0` used to zero a register, logic errors.
