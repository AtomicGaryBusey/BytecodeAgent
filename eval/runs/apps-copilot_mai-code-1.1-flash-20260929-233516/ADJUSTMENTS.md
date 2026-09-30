# Adjustments to this run

Found in the by-hand review of this run (2026-09-30). Every rejected answer was replayed through the current asm
and stream paths; Copilot delivers whole answers, so every recorded answer was complete.

## wc.stream.aarch64.s2 — harness false reject; superseded and re-run

Attempt 1 was refused by the stream path at line 2, `.arch armv8-a`, as an unsupported directive. GNU as accepts it
(it restates GNU as's own AArch64 default, the prompt's ARMv8.0-A level) and the prompt does not forbid it. The asm
path accepts the same answer and it passes all 48 test runs; the stream path passes it too with that line dropped.
The recorded cell failed attempt 1 and passed on attempt 3. Fixed in 79a275c1 (both paths accept `.arch armv8-a` /
`.cpu generic`, and refuse any `.arch` / `.cpu` beyond the prompt's level alike).

## json_lint.stream.aarch64.s1 — stream/asm asymmetry; superseded and re-run

Attempt 1 was refused by the stream path at `.end`, which GNU as accepts (it stops reading there). The answer fails
anyway on the asm path (wrong output on the `empty` test), but the model got syntax feedback instead of wrong-output
feedback, which may have changed attempts 2-4 (the cell failed all four). Fixed in 79a275c1 (both paths stop
reading at `.end`, as GNU as does).

Both cells were moved to `superseded/` (with their results/attempts lines) and re-run from scratch with the fixed
code, so their verdicts come from the same rules as every other cell.

A third item changes no verdict: the stream path refused GNU's one-quote character literal (`cmp w3, #'{`), seen
only in json_lint.asm.aarch64.s1 attempts 2-4 (asm path; accepted, then looped forever at runtime). Both paths now
accept it (79a275c1).

## Re-run results (2026-09-30, code of 79a275c1)

- wc.stream.aarch64.s2: attempt 1 wrong_output (`empty` test), attempt 2 passed (was: attempt 1 false reject,
  passed on attempt 3). First attempt: still a fail; within 4: still a pass.
- json_lint.stream.aarch64.s1: failed all four again. Attempt 1: `msg_ok_len` used at line 76, defined at line 726
  (constants must be defined before use: the prompt's rule, refused alike by both paths); attempt 2: a duplicate
  label (the asm path also refuses the same answer, for the constant order); attempt 3: `.quad` in `.bss` (both
  paths); attempt 4: wrong output (`object_trailing_comma`). All genuine; both paths agree on every answer.

Run totals are unchanged: 23/96 first attempt, 64/96 within 4.
