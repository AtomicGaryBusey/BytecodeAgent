# Reviewed adjustments to this run

grok 1.0.41's `streaming-json` output repeated the first text chunk (`{"`) in 3 answers; grok's own session
record (the model's actual answer) did not (the adapter noted "recorded assistant text differs from the
streamed text" for each). The scorer was given the corrupted streamed text, so attempt 1 of these cells was a
harness/CLI false reject:

| cell | recorded | rescored attempt 1 (duplicate chunk removed, same scorer) |
|---|---|---|
| vm_run.structured.aarch64 | format, then pass | pass |
| qsort_pairs.structured.x86_64 | format, then pass | pass |
| packbits_decode.structured.x86_64 | format, then pass | pass |

Adjusted pass@1: 213/224 (recorded 210/224). The files in cells/ are unchanged. Since the fix (the grok
adapter now scores the session record's text, delivered once at the end) this cannot recur.
