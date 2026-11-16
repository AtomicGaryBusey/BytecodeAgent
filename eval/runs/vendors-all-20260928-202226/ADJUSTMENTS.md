# Adjustments

**agy_gemini-3.8-flash-high, xxh64.ir.aarch64, attempt 1 (2026-11-15).** The call reached Gemini's output token
limit and agy made a continuation call of its own; it was logged as an infrastructure error and the cell was then
re-attempted. Under the output-cut rule adopted that day (the first call's text is scored as it stands; agy's
continuation is dropped), attempt 1 was recorded by `eval/replay_voided.py` from its kept stream (a compile failure;
wall time 0 in the record) and attempt 2, which passed, was re-run on the normal command. The row moves from 211 to
210 of 224 first attempt; 224 within four is unchanged.
