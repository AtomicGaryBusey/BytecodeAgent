# Adjustments to this run

## rpn.asm.aarch64.s1 — superseded and re-run (2026-09-29)

Attempt 1 failed (`syntax`: `mov` immediate GNU as rejects). Attempt 2 passed on the asm path with
`errmsg: .ascii "error\n"` in `.text`. The prompt says data belongs in `.data`/`.rodata` (instructions and
alignment only in `.text`), and the stream path refuses it, but the asm path did not check that rule then.
Since c7df64d the asm path enforces the same rules, so the answer would now be rejected. The cell was moved to
`superseded/` (with its results/attempts lines) and is re-run from scratch under the corrected rules, so
this cell's verdict comes from the same rules as every other cell.

Re-run result (2026-09-29, rules of c7df64d): passed on attempt 1.
