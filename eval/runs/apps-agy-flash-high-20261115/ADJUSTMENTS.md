# Adjustments

**json_lint.asm.aarch64.s1, attempt 1, 2026-11-15 (17:45).** The call reached Gemini's output token limit and agy
made a continuation call of its own. It was logged as an infrastructure error before the output-cut rule was in the
adapter. Under that rule (the first call's text is scored as it stands; agy's continuation is dropped) the attempt was
recorded by `eval/replay_voided.py` from its kept stream (scored `syntax`; wall time 0 in the record, real time in
infra.jsonl), and the later attempts ran on the normal command.

**json_lint.stream.aarch64.s2, attempt 1.** A live cut under the rule in the adapter (509 s, 65,532 output tokens):
the first call's text was scored (`link`) and its continuation's request verified. No adjustment; noted as the first
cut handled by the adapter itself.
