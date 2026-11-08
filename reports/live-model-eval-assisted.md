# Live-model eval: the `assisted` variant (more information, same rules)

**Baseline remains the headline comparison.** This report measures something else: how much of a model's
failure rate on rounds 1 + 2 is missing knowledge and unclear feedback rather than inability. The assisted
variant (`--variant assisted`, `bca/liveeval/assist.py`) changes only what the model is told. Acceptance rules,
tests, limits, attempts (4), timeouts, the zero-tool CLIs and scoring are identical to baseline, so a score
change is attributable to information, not leniency. Same account caveats as the [cross-vendor report](live-model-eval-cross-vendor.md) (the Claude
account is CVP-enrolled and ChatGPT is Daybreak Blue, so treat those rows as upper bounds for ordinary accounts).

## What the variant adds

1. **Notes on every prompt**, per path and ISA, for the most common failures in the recorded runs:
   - AArch64: constants beyond 16 bits are built with movz/movk; and/orr/eor/tst take only logical bitmasks;
     add/sub/cmp immediates are 12 bits; `cmp` takes the register first.
   - x86-64: rip-relative addressing takes no index register (`lea` first); zero with `xor r, r`, not
     `xor r, 0`; the `imul` forms; `div`/`idiv` setup.
   - QBE: a one-page cheat sheet (grammar, classes on every result and parameter, the valid opcode names by
     type, decimal literals only, loads/stores by width, widening, the fact that a temporary may be assigned
     again so loops need no phi) and a worked example. Every opcode named and the example compile with the
     installed `qbe` on both targets (`tests/test_liveeval_assist.py`).
   - Structured: the operand forms (a register is a bare string, never `{"reg": ...}`) and example
     instructions that the real encoder accepts (tested).
   - Output discipline: the code only, no plan, no prose, no tool-call markup.
2. **On repair rounds only, notes about the failure shown**: explanations matched from the failure text the
   harness already reports (e.g. GNU as "immediate cannot be moved by a single instruction", QBE "unknown
   keyword X" with the nearest valid opcodes or the decimal-literal rule, "invalid class specifier", encoder
   schema errors, callee-saved clobbers, misaligned calls, hangs) and advisory lints of the model's previous
   code (callee-saved registers written but never saved, x86 stack alignment before a call, `xor r, 0`,
   rip+index addressing, raw-hex branch targets outside the code). Lints never fail a program.

Nothing is computed from tests: no test input, expected value or correct answer is added beyond what the
baseline failure message already shows (`test_failure_notes_reveal_no_test_data`). Baseline feedback already
names the faulting instruction on crashes; register dumps were not added (they would need emulator changes to
every run's records).

## Results (rounds 1 + 2, 110 cells per model, first attempt)

| Model | Variant | Cells | First attempt | Within 4 | Mean attempts | Stream | Structured | IR (QBE) |
|---|---|---|---|---|---|---|---|---|
| Claude Haiku 4.5 | baseline | 110 | **64/110 (58%)** | 79/110 (72%) | 2.07 | 35/38 (92%) | 29/36 (81%) | 0/36 (0%) |
| Claude Haiku 4.5 | assisted | 110 | **98/110 (89%)** | 109/110 (99%) | 1.15 | 35/38 (92%) | 29/36 (81%) | 34/36 (94%) |
| Codex GPT-6 Luna | baseline | 110 | **74/110 (67%)** | 109/110 (99%) | 1.44 | 34/38 (89%) | 26/36 (72%) | 14/36 (39%) |
| Codex GPT-6 Luna | assisted | 110 | **88/110 (80%)** | 109/110 (99%) | 1.30 | 35/38 (92%) | 25/36 (69%) | 28/36 (78%) |
| Microsoft MAI-Code 1.1 Flash (Copilot) | baseline | 110 | **60/110 (55%)** | 77/110 (70%) | 2.14 | 33/38 (87%) | 24/36 (67%) | 3/36 (8%) |
| Microsoft MAI-Code 1.1 Flash (Copilot) | assisted | 110 | **98/110 (89%)** | 108/110 (98%) | 1.17 | 34/38 (89%) | 30/36 (83%) | 34/36 (94%) |
| Qwen3-Coder-Next Q4 (local) | baseline | 110 | **10/110 (9%)** | 25/110 (23%) | 3.55 | 10/38 (26%) | 0/36 (0%) | 0/36 (0%) |
| Qwen3-Coder-Next Q4 (local) | assisted | 110 | **26/110 (24%)** | 42/110 (38%) | 3.12 | 7/38 (18%) | 2/36 (6%) | 17/36 (47%) |

First-attempt failure categories, baseline → assisted (cells whose first attempt failed):

| Model | compile | syntax | format | crosscheck | wrong_result | abi | crash | oob | hang | misaligned_call | subject_timeout | refusal | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Claude Haiku 4.5 | 33→1 | 3→2 | 5→2 | 0→0 | 1→3 | 0→1 | 1→1 | 0→0 | 0→0 | 0→0 | 3→1 | 0→0 | 0→1 |
| Codex GPT-6 Luna | 21→5 | 4→1 | 0→1 | 0→0 | 7→9 | 0→1 | 1→1 | 0→1 | 3→1 | 0→0 | 0→1 | 0→0 | 0→1 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 33→2 | 6→3 | 1→0 | 0→0 | 7→4 | 0→2 | 2→0 | 0→0 | 0→0 | 1→1 | 0→0 | 0→0 | 0→0 |
| Qwen3-Coder-Next Q4 (local) | 36→8 | 39→26 | 6→15 | 0→0 | 7→16 | 10→11 | 1→3 | 0→0 | 1→4 | 0→1 | 0→0 | 0→0 | 0→0 |

Runs: `eval/runs/assisted-haiku-20261001`, `assisted-luna-20261001`, `assisted-mai-20261001`,
`assisted-coder-next-20261001`; tables from `eval/assist_tables.py`. Baselines are each model's committed
baseline runs (Claude Haiku 4.5: `r12-haiku-sonnet-20260927-193906`; GPT-6 Luna via Codex:
`vendors-all-20260928-202226`; MAI-Code via Copilot: `copilot-r1-20260929-233516`; Qwen3-Coder-Next:
`local-r2-20260930-075050`). Efforts are each CLI's default, as in baseline. Claude Code was 2.1.283 for
Haiku's baseline and newer for the assisted run (Claude Code is not version-pinned); the other CLIs are pinned.

## Findings

- **Information is most of the gap for weaker models, and almost all of it is QBE.** First-attempt passes rose
  from 64 to 98 (Haiku 4.5), 60 to 98 (MAI-Code via Copilot), 74 to 88 (GPT-6 Luna via Codex) and 10 to 26
  (Qwen3-Coder-Next, local) of 110. Haiku's QBE path went from 0/36 to 34/36 and MAI-Code's from 3/36 to 34/36:
  their QBE knowledge, not their reasoning, was the bottleneck. QBE compile failures on the first attempt fell
  from 33 to 1 (Haiku), 33 to 2 (MAI-Code), 21 to 5 (Luna) and 36 to 8 (Coder-Next).
- **Raw assembly barely moved**, because it was already near the ceiling for these models (Haiku 35/38 both
  ways); structured improved for MAI-Code (24 to 30 of 36) and was flat for Haiku and Luna.
- **Repairs matter less with better information.** Within four attempts Haiku went from 79 to 109 and MAI-Code
  from 77 to 108 of 110; mean attempts per cell fell from about 2.1 to about 1.15 for both.
- **The weakest model gains least in absolute terms.** Qwen3-Coder-Next more than doubled (10 to 26, QBE 0 to
  17 of 36) but stays far below the cloud models; its remaining failures are malformed JSON brackets, invented
  mnemonics and logic errors, which notes do not fix. Its stream score dipped (10 to 7 of 38).
- **What this means for the main tables:** for weaker models the QBE column mostly measures whether they know
  QBE's syntax. Baseline remains the fair headline (every model gets the same prompt); the assisted numbers are
  what a better prompt reaches for the same model, rules and checks.

## False-reject review

Every failed assisted attempt was read by hand (failure text, the answer, and where relevant QBE / GNU as /
the encoder rerun on it). None is a harness false reject. Representative genuine failures: invented QBE
opcodes (`csle`, `cneqw`, `ultl`), a loop back to QBE's entry block ("invalid jump to the start block"),
temporaries used before being assigned, nested expressions inside one QBE instruction, `{"op": "label"}`
entries, hex literals inside JSON (`{"imm": 0xffffffff}` is not valid JSON), `setjo`, `mov x0, w2`, NEON
operand mismatches, immediates GNU as also rejects, and plain logic errors (signed overflow in `sum8`,
`crc32`, `memmove_i64` overlap direction).

Candidate additions for a next version (not used in these runs, so the variant stayed fixed during the
experiment): "JSON numbers are decimal" on the structured path; "the entry block cannot be a jump target"
in the QBE sheet.
