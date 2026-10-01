# Status and adjustments (local:qwen3-coder-next:q4_K_M)

**Status: COMPLETE** on the four function batteries (224 cells: round1, round2, long, adversarial). By the user's
decision of 2026-10-01 no local model runs the app battery.

Served by Ollama 0.30.10 (container bca-ollama2, its own model volume), Q4_K_M quantisation, model-default
sampling (temperature 1, top_k 40, top_p 0.95; the model never emits a reasoning trace), num_ctx 65536. The GPU was
shared with the Qwen3.6 runs, calls serialised; per-call time limits count only the model's own time, but recorded
wall times include queue waits, so they are not comparable with the vendor runs.

## Review (2026-10-01)

All 804 failed attempts (of 835 calls) were replayed through the current pipeline: no verdict changes, no false
rejects, no ADJ. Genuine failures, in brief:
- compile (295): invalid QBE: missing result classes, invented operators, wrong label syntax;
- syntax (314): stream lines GNU as also rejects (invented mnemonics such as `cdiv`) and structured-schema misuse
  (`{"reg": "rdi"}` operands, an immediate as the first operand of AArch64 `cmp`, entries without `op`);
- crosscheck (3): x86 addresses GNU as rejects (`[r10 + rdi*0]`, a cross-section label difference);
- format (33): malformed JSON (`"dst={"reg":...`);
- runtime: crash 50, wrong_result 39, abi 34, oob_read 21, hang 5, oob 4, misaligned_call 4;
- subject_timeout (2).
