# Live-model eval: the long-program battery (haiku, sonnet, opus)

Run: `eval/runs/long-3subj-20260927-210627` (2026-09-27; claude CLI 2.1.283, CLI-default effort). 9 programs of roughly 60-250
instructions (bytecode VM, pair quicksort, Base64, XXH64, PackBits encode/decode, 4x4 matrix power,
LCS, multi-precision multiply) × stream / structured / IR × x86-64 / AArch64 = 54 cells per model, up to
4 attempts with repair feedback. 266 calls, no infrastructure errors, every cell completed.

| model | pass@1 | pass within 4 | mean attempts |
|---|---|---|---|
| claude-opus-5-5 | 53/54 (98%) | 54/54 (100%) | 1.02 |
| claude-sonnet-5 | 41/54 (76%) | 54/54 (100%) | 1.30 |
| claude-haiku-4-5 | 17/54 (31%) | 35/54 (65%) | 2.61 |

pass@1 by path:

| model | stream | structured | IR (QBE) |
|---|---|---|---|
| opus | 17/18 | 18/18 | 18/18 |
| sonnet | 15/18 | 14/18 | 12/18 |
| haiku | 9/18 | 8/18 | 0/18 |

## Findings

- **Opus does not degrade with length:** 98% pass@1 on 60-250-instruction programs vs 95% on the short
  rounds. Its one miss (lcs_len, stream x86-64, an out-of-bounds read) was fixed on the first repair.
- **Sonnet degrades a little but repairs everything** (76% → 100%). First-attempt errors: QBE phi/type
  errors (5), impossible x86 addressing (three registers in one address), a NASM `qword [..]`, a
  callee-saved rbx clobber, crashes.
- **Haiku degrades sharply** (58% on short tasks → 31%). Besides QBE (0/18 again), it writes invalid
  addressing (two index registers, scale 16), NASM size keywords, `mul x6, x4, #32` (no immediate form
  on AArch64), and malformed JSON on the longest structured programs.
- All first-attempt failures were checked by hand. **One false reject was found and fixed:** the
  stream assembler refused one-character literals (`mov al, '/'`), which GNU as accepts on both ISAs
  and the prompt never forbade. It cost haiku one pass@1 (base64_encode, stream x86-64; fixed on
  attempt 2). The stream now passes such literals on as their values (cross-checked against GNU as).
- Harness follow-up (not done): when a checked output buffer differs, the feedback shows the first 12
  elements of each side; if they agree (lcs_len's row, a mismatch further in) the model cannot see
  where it went wrong. Showing the first differing index would make repairs more informative.
