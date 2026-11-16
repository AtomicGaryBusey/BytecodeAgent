# Adjustments

**isqrt.ir.aarch64, attempt 1 (2026-11-13 02:40).** Muse proposed its built-in `tool.muse.search` task after the
model step, with no tools sent and no answer text written. The adapter then failed closed (an infrastructure error,
`attempt1.infra1.*`), and the cell was tried again: that second try passed and was recorded as attempt 1. Under the
rule the user adopted on 2026-11-16 (a search proposal stops the call and the text written before it is scored;
one attempt is one model call), the voided attempt was recorded as attempt 1 by `eval/replay_voided.py` from its
kept stream (an empty answer, `format`; wall time 0 in the record, 106 s real in infra.jsonl). The earlier passing
try is kept under `superseded/muse_muse-spark-1.3/partial/isqrt.ir.aarch64/`. Attempts 2 (compile failure) and 3
(pass) ran on 2026-11-16 on Muse Code 1.4.20, the pinned version; the rest of this run used 1.4.18. The tier
comparison moves from 216 to 215 of 224 first attempt; 224 within four is unchanged.
