# Adjustments to this run

## json_lint.stream.aarch64.s1 — harness false reject; superseded and re-run (2026-09-29)

The stream assembler stopped generation of attempt 1 at line 304, `adrp x26, lt`, refusing `lt` as "a
register name or keyword, not a label". GNU as accepts `lt` as a label name there, and no prompt says
otherwise: a harness false reject, fixed in 83c313e (the stream path accepts every label name GNU as
accepts). The recorded 304 lines assemble cleanly under the fix, but the answer was cut mid-line, so it cannot
be rescored as a whole. Rather than guess, the cell was moved to `superseded/` (with its results/attempts
lines; it had passed on attempt 2) and is re-run from scratch with the fixed assembler.

Re-run result (2026-09-29, assembler of 83c313e): passed on attempt 1.
