# Status and adjustments (local:qwen3:30b-a3b-instruct-2507-q8_0, local:qwen2.5:14b)

**Status: PARTIAL, will not be completed.** The run covered round1 (48 cells) and round2 (62) and stopped early in
the long battery: 115/224 cells for Qwen3-30B-A3B and 114/224 for Qwen2.5-14B. It was paused on 2026-09-30 to give the
DGX Spark's GPU to the newer local models, and by the user's decision on 2026-10-01 it stays partial; the planned app
battery for these two models was dropped. At about 3-5% first-attempt passes, finishing would cost days of GPU time
without changing the picture.

Served by Ollama 0.30.10 (container bca-ollama, models volume mounted read-only), model-default sampling, num_ctx
65536 (Qwen3-30B-A3B) / 32768 (Qwen2.5-14B), calls serialised on one GPU (recorded wall times include queue waits).

## Review (2026-10-01)

Every failed attempt was replayed through the current pipeline (QBE, the stream assembler with the GNU as
cross-check and fallbacks of 1502d04e / 366f29f9 / 83c313e, the structured encoder, the emulator checks). Everything
is genuine except one group:

**Unresolved, harness-affected (not rescorable): Qwen2.5-14B, 6 stream attempts on AArch64.** Generation was stopped
at a line keystone refused but GNU as accepts; since 1502d04e the stream path takes GNU as's bytes for such lines.
The answers were cut off at that line, so they cannot be rescored, and the run will not be resumed:

| Cell | Attempts | Line |
|---|---|---|
| add_overflow.stream.aarch64 | 1, 3 | `cmp x8, #0x8000000000000000` (GNU as encodes `cmn x8, #0`) |
| dot_i32_simd.stream.aarch64 | 2, 3 | `add x3, x19, x8, uxtw #2` (GNU as reads `x8` as `w8`) |
| popcount.stream.aarch64 | 3, 4 | `mov x10, x9, lsr #8` / `asr #8` (alias of `orr x10, xzr, x9, ...`) |

All three cells failed all four attempts; add_overflow's first attempt is among them, so Qwen2.5-14B's first-attempt
count could be at most 1 higher (4/114 instead of 3/114) and its final count at most 3 higher. Not counted in the
tables (no rescorable answer).

Genuine failures, in brief: invalid QBE (missing result classes, invented ops, `@label:` with a colon), invented
mnemonics, `x31` as a register name, malformed structured JSON (`["imm":0,...]`), `{"reg": ...}` operands the
schema does not allow, ABI and runtime errors, and Qwen3-30B-A3B structured answers of ~23 KB that ran out the
400 s long-battery limit on every attempt.
