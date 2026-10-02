# Live-model eval: raw machine code (the `hex` path), across models

> **Account caveat** (as in [live-model-eval-cross-vendor.md](live-model-eval-cross-vendor.md)): the Claude
> account is in Anthropic's Cyber Verification Program and the ChatGPT account in OpenAI's Daybreak (Blue);
> treat those rows as upper bounds for ordinary accounts.

On the `hex` path the model writes the machine code itself: one 32-bit instruction word per line on AArch64,
the instruction's bytes per line on x86-64, and for whole programs a `.data` section of bytes at a stated
fixed address. There is no assembler, linker, encoder or label anywhere: the bytes run exactly as written
(the harness asserts it), and the model computes every branch offset and address by hand. Disassembly is used
only for the feedback listing and for the instruction-set check (ARMv8.0-A with FP and NEON; the x86-64 set
the other paths allow). Method: [design-app-battery.md](design-app-battery.md),
`bca/liveeval/hexpath.py`; Astra's first runs are in [live-model-eval-apps.md](live-model-eval-apps.md).

Two stages, 2026-10-01, CLI-default effort, up to four attempts with repair feedback, the same subjects and
fairness contract as every other run:

1. **Functions:** round 1 (8 tasks x 2 ISAs = 16 cells) for every model with plan room.
2. **Whole programs:** the app battery (8 programs x 2 ISAs, sample 1 = 16 cells) for every model that
   reached at least 12/16 within four attempts in stage 1.

Tables: `.venv/bin/python eval/hex_tables.py`. Runs: `eval/runs/hex-{astra-20261001-165154,claude-20261001,
codex-20261001,copilot-20261001}` and `eval/runs/apps-hex-{astra-20261001-174623,claude-20261001,
codex-20261001,copilot-20261001}`.

## Functions (round 1)

First attempt on the hex path, within four attempts, and the same model's committed round-1 first-attempt
results on the other paths (16 cells each):

| Model | Hex first attempt | Hex within 4 | Stream | Structured | IR (QBE) |
|---|---|---|---|---|---|
| Claude Opus 5.5 | **16/16** | 16/16 | 15/16 | 16/16 | 14/16 |
| Codex Daybreak Blue | **15/16** | 16/16 | 16/16 | 16/16 | 13/16 |
| Codex GPT-6 Astra | **15/16** | 16/16 | 16/16 | 16/16 | 14/16 |
| Claude Fable 5.1 | **15/16** | 16/16 | 15/16 | 15/16 | 14/16 |
| Codex GPT-5.5 | **14/16** | 16/16 | 16/16 | 16/16 | 15/16 |
| Codex GPT-6 Sol | **14/16** | 16/16 | 16/16 | 16/16 | 11/16 |
| Claude Opus 4.8 | **14/16** | 16/16 | 13/16 | 15/16 | 7/16 |
| Claude Sonnet 5.5 | **14/16** | 16/16 | 16/16 | 15/16 | 16/16 |
| GPT-6 Luna via Copilot | **12/16** | 16/16 | 16/16 | 15/16 | 11/16 |
| Claude Sonnet 4.5 | **10/16** | 16/16 | 16/16 | 16/16 | 16/16 |
| Codex GPT-6 Luna | **10/16** | 15/16 | 15/16 | 12/16 | 6/16 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | **6/16** | 11/16 | 16/16 | 10/16 | 0/16 |
| Claude Haiku 4.5 | **5/16** | 8/16 | 16/16 | 14/16 | 0/16 |
| Claude Sonnet 5.5 via Copilot | **1/16** | 16/16 | 16/16 | 15/16 | 14/16 |

Per ISA, hex first attempt:

| Model | AArch64 | x86-64 |
|---|---|---|
| Claude Opus 5.5 | 8/8 | 8/8 |
| Codex Daybreak Blue | 8/8 | 7/8 |
| Codex GPT-6 Astra | 8/8 | 7/8 |
| Claude Fable 5.1 | 7/8 | 8/8 |
| Codex GPT-5.5 | 6/8 | 8/8 |
| Codex GPT-6 Sol | 7/8 | 7/8 |
| Claude Opus 4.8 | 6/8 | 8/8 |
| Claude Sonnet 5.5 | 6/8 | 8/8 |
| GPT-6 Luna via Copilot | 6/8 | 6/8 |
| Claude Sonnet 4.5 | 2/8 | 8/8 |
| Codex GPT-6 Luna | 5/8 | 5/8 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 2/8 | 4/8 |
| Claude Haiku 4.5 | 0/8 | 5/8 |
| Claude Sonnet 5.5 via Copilot | 1/8 | 0/8 |

## Whole programs (app battery, sample 1)

First attempt / within four on the hex path, next to the same model's committed sample-1 asm / stream app
results (— where that model did not run the app battery):

| Model | Hex first attempt | Hex within 4 | asm first attempt | stream first attempt |
|---|---|---|---|---|
| Codex GPT-6 Astra | **15/16** | 16/16 | 16/16 | 16/16 |
| Claude Opus 4.8 | **15/16** | 16/16 | — | — |
| Claude Sonnet 5.5 | **13/16** | 15/16 | 14/16 | 15/16 |
| Claude Fable 5.1 | **12/16** | 16/16 | 13/16 | 13/16 |
| Claude Opus 5.5 | **7/16** | 12/16 | 16/16 | 15/16 |
| Codex GPT-6 Sol | **6/16** | 14/16 | — | — |
| Codex GPT-5.5 | **3/16** | 12/16 | — | — |
| Codex Daybreak Blue | **2/16** | 11/16 | — | — |
| GPT-6 Luna via Copilot | **2/16** | 5/16 | — | — |
| Claude Sonnet 4.5 | **0/16** | 4/16 | — | — |
| Codex GPT-6 Luna | **0/16** | 0/16 | — | — |
| Claude Sonnet 5.5 via Copilot | **0/16** | 4/16 | — | — |

Those first-attempt scores mix two different things for two rows: safety blocks and wrong programs. Split
apart (first attempts only, except the last column):

| Model | First attempts | Passed | Refused | Genuine failures | Pass rate among answered | Refused attempts (all rounds) |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 16 | 7 | 9 | 0 | 7/7 (100%) | 22 of 34 |
| Codex GPT-6 Astra | 16 | 15 | 0 | 1 | 15/16 (94%) | 0 of 17 |
| Claude Opus 4.8 | 16 | 15 | 0 | 1 | 15/16 (94%) | 0 of 17 |
| Claude Sonnet 5.5 | 16 | 13 | 1 | 2 | 13/15 (87%) | 3 of 23 |
| Claude Fable 5.1 | 16 | 12 | 0 | 4 | 12/16 (75%) | 0 of 20 |
| Codex GPT-6 Sol | 16 | 6 | 0 | 10 | 6/16 (38%) | 0 of 33 |
| Codex GPT-5.5 | 16 | 3 | 0 | 13 | 3/16 (19%) | 0 of 37 |
| Codex Daybreak Blue | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 42 |
| GPT-6 Luna via Copilot | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 52 |
| Claude Sonnet 4.5 | 16 | 0 | 0 | 16 | 0/16 (0%) | 0 of 57 |
| Codex GPT-6 Luna | 16 | 0 | 0 | 16 | 0/16 (0%) | 0 of 64 |
| Claude Sonnet 5.5 via Copilot | 16 | 0 | 14 | 2 | 0/2 (0%) | 52 of 58 |

Read Claude Opus 5.5's 7/16 and Claude Sonnet 5.5 via Copilot's 0/16 as what each front end's safety filter
let through, not as ability: every first attempt Opus 5.5 was allowed to answer passed.

## Findings

- **Writing raw machine code is within reach of the frontier models.** On functions, Opus 5.5 was perfect
  (16/16 first attempt), and Astra, Daybreak Blue and Fable 5.1 missed one cell each. Eleven of the fourteen
  model rows pass every function cell within four attempts. Hex costs the strongest models little against
  their assembly results; it costs mid-tier models much more (Sonnet 4.5 10/16, Luna 10/16 through Codex,
  MAI-Code 6/16, Haiku 4.5 5/16).
- **AArch64 is harder to hand-encode than x86-64 for most models** (fixed 32-bit words with packed
  bit fields: shift and bitmask immediates, register fields, 19- and 26-bit branch offsets), e.g. Sonnet 4.5
  2/8 vs 8/8, Haiku 4.5 0/8 vs 5/8.
- **Whole programs separate the models sharply.** Astra and Opus 4.8 wrote 15 of 16 programs correctly on
  the first attempt; Sonnet 5.5 13 and Fable 5.1 12. The Codex models other than Astra fall to 2-6/16 first
  attempt (most still recover within four: Sol 14/16), Sonnet 4.5 to 0/16 (4/16 within four).
- **The Codex models other than Astra mostly fail whole programs on memory layout, not encoding.** Their
  first-attempt app failures are out-of-bounds accesses and wrong output: writing to the data base 0x500000
  without giving a data section, to 0x700000 just past the 1 MiB work area, through a null or unset pointer, or
  mishandling empty input. The prompt states the fixed layout exactly; computing every address by hand is
  where they slip. Most recover within four attempts (Sol 14/16, GPT-5.5 12/16, Daybreak 11/16); Luna does
  not (0/16).
- **Safety filters block whole programs written as raw machine code.** No function cell was refused. On
  whole programs:
  - Anthropic's cyber classifier ("This request triggered restrictions on violative cyber content") blocked
    **Opus 5.5 on 9 of its 16 first attempts** (22 attempts in all) and Sonnet 5.5 on 3, even on the CVP
    account; Fable 5.1, Opus 4.8 and Sonnet 4.5 were never blocked. Every first attempt Opus 5.5 was allowed
    to answer passed (7/7). The same programs written as assembly were not blocked.
  - GitHub Copilot's content filter blocked **every** first attempt of Claude Sonnet 5.5 through Copilot
    (52 attempts in all, finish reason `content_filter`, no content returned).
  - **Where the blocks come from.** Opus 5.5 and Sonnet 5.5 ran through `claude -p` on the user's own Claude
    Max 20x subscription (enrolled in Anthropic's Cyber Verification Program): the API ended the stream mid-
    answer with `stop_reason: refusal`, `category: cyber`, after part of the hex program had streamed (the
    harness discards that partial text). x86-64 was refused far more than AArch64 (Opus 5.5: 18 of 22
    x86-64 attempts, 4 of 12 AArch64), consistent with x86 machine code that makes its own system calls
    looking most like shellcode. The current generation refuses where the older Opus 4.8 and Sonnet 4.5 and
    the cyber-oriented Fable 5.1 do not. The Copilot blocks are a different mechanism: GitHub's own content
    filter on its route (both ISAs alike: 27 of 30 x86-64, 25 of 28 AArch64), to which the user's CVP
    enrolment does not apply.
  Under the fairness contract a safety block is a scored refusal, as for Fable's refusals on the function
  batteries; these rows measure what each front end lets through, not only what the model can write. Raw
  machine code with system calls and no symbols plausibly resembles shellcode to such classifiers.
- **Answer discipline still matters:** Sonnet 5.5 through Copilot opened 14 of 16 function answers with a
  prose plan ("Plan:", "Euclid's algorithm...") on a path whose first line must be machine code; it passed
  all 16 within four once told so.

## The mistakes models make when they encode by hand

From a by-hand review of every failed attempt (first attempts and repairs), with examples:

| Kind | Examples |
|---|---|
| Branch / offset arithmetic | off by one instruction (Fable `17fffffc` for `17fffffb`, GPT-5.5 `cbz`/`b.ne` in `fib`), x86 rel8 targets past the end or into the middle of an instruction (Astra `7e 0f` for `7e 0b`, Daybreak `je`/`jne` in `reverse`), a `b` past the end of a whole program (Astra `json_lint`) |
| Immediate fields | AArch64 `lsr` encoded with the wrong shift (UBFM `immr`): Sol `lsr #0` for `#1`, Opus 4.8 `lsr #60` and Sonnet 5.5 `lsr #24` for `#56`; logical (bitmask) immediates wrong (Luna `0x1fff...` for `0x5555...`) |
| Register fields | a source register never written (Opus 4.8 `add x0, x6, x5`) |
| Opcode bits / undefined words | unallocated encodings (MAI-Code `d3a00000`, `8bc10003`; Luna `9a9d3b7b`; Sonnet 4.5 `d71d001d`) |
| Accidentally a newer instruction | a slip that lands on an ARMv8.1+ instruction the prompt forbids: LSE atomics (MAI-Code `ldaddl`, Luna `ldsminlb`, Sonnet 4.5 `ldumaxlb`), MTE `subp` (Sonnet 4.5) |
| Byte order | an AArch64 word written byte-swapped (Sonnet 4.5 `600400b4` for `b4000460`, a `cbz`) |
| x86 length and encoding | a truncated `movabs` (`48 be 00 00 60 00`), an invalid ModRM (`49 ff ef`), three instructions on one line (`50 52 51`, refused by the stated one-instruction-per-line rule) |
| Format | words split by spaces (`9100 0d8c`), labels or mnemonics left among the hex (Sonnet 4.5), prose plans (Sonnet 5.5 via Copilot) |
| Ordinary logic | a missing `ret` before a fall-through (GPT-5.5 `sum_to`), loops that run on zero counts, a misordered bit-count (Luna `popcount`), empty-input and buffer handling in whole programs |
| Out of time | Haiku 4.5 reasoned through the whole 180 s on 8 AArch64 function cells (about 20,800 thinking tokens, no answer); Sonnet 4.5 on 4 |

## False-reject review

Every failed attempt was reviewed by hand. One harness false reject was found and fixed: capstone does not
decode some valid ARMv8.0 encodings (`384568e7`, the unprivileged load `ldtrb w7, [x7, #86]`, which GNU as
accepts), so the hex path refused them as "does not decode". Fixed in 5dd977b1 (GNU objdump decodes a word
capstone does not know; undefined words and ARMv8.1+ instructions are still refused). The one affected attempt
(Sonnet 4.5 `sort_lines.hex.aarch64` attempt 3) still fails at runtime under the fix, so no verdict changes.
Every other pre-run refusal was checked against GNU objdump: undefined words, or ARMv8.1+ instructions the
prompt forbids. The Copilot content-filter and Anthropic cyber-classifier blocks are provider-side and are
scored as refusals, as everywhere else.

## Pending

| Models | Waiting for |
|---|---|
| Grok 4.7, Grok 4.7 build-fast, Grok 4.6, Grok 4.5 | SuperGrok weekly usage resets Sat 2026-10-03 10:42 (never into paid credits) |
| Kimi K3, Kimi K2.8 | Kimi's 7-day window reopens Sat 2026-10-03 15:02 |
| Gemini 3.8 Flash (Antigravity) | Google quota, about Mon 2026-10-06 |
| Local Qwen models | the GPU (used by the assisted-variant runs); expected near 0 given 3-8% on assembly |
| Haiku 4.5, MAI-Code (apps) | below the 12/16 bar in stage 1 (8/16 and 11/16 within four) |
