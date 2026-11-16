# Adjustments

**count_nonzero_prefix.ir.x86_64, attempt 2 (2026-11-15 21:13).** Muse proposed its built-in `tool.muse.search`
task after the model step, with no tools sent and no answer text written; the adapter failed closed and the run
stopped. Under the rule the user adopted on 2026-11-16 (a search proposal stops the call and the text written before
it is scored; no second try), the attempt was recorded as attempt 2 by `eval/replay_voided.py` from its kept stream
(an empty answer, a failure; wall time 0 in the record, 15 s real in infra.jsonl). The run then continued from
attempt 3 with the rule in the adapter.
