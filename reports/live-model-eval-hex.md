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

Two stages, first run 2026-10-01 (Grok: 2026-10-01 22:19 to 10-02 04:20; Kimi 10-03, Sonnet 5 10-22, Haiku 5.5 11-07, Fable 5.2 / Mythos 5.1 / Mythos 5.2 11-10, Kimi K3.1 Pro and three Fireworks models 11-10/11), CLI-default effort, up to four attempts
with repair feedback, the same subjects and fairness contract as every other run:

1. **Functions:** round 1 (8 tasks x 2 ISAs = 16 cells) for every model with plan room.
2. **Whole programs:** the app battery (8 programs x 2 ISAs, sample 1 = 16 cells) for every model that
   reached at least 12/16 within four attempts in stage 1.

Tables: `.venv/bin/python eval/hex_tables.py`. Runs: `eval/runs/hex-{astra-20261001-165154,claude-20261001,
codex-20261001,copilot-20261001,grok-20261001-221930,kimi-20261003-175502,sonnet5-20261022-143141,
haiku55-20261107-174207,fable52-mythos-20261110,kimi-k31pro-20261110,fireworks-20261110,
fireworks-minimax-20261110}` and `eval/runs/apps-hex-{astra-20261001-174623,claude-20261001,codex-20261001,
copilot-20261001,grok-20261001-221930,kimi-20261003-175502,sonnet5-20261022-143141,haiku55-20261107-174207}`
(the `apps-hex-declared-*` runs belong to [live-model-eval-declared.md](live-model-eval-declared.md)).

## Functions (round 1)

First attempt on the hex path, within four attempts, and the same model's committed round-1 first-attempt
results on the other paths (16 cells each):

| Model | Hex first attempt | Hex within 4 | Stream | Structured | IR (QBE) |
|---|---|---|---|---|---|
| Claude Opus 5.5 | **16/16** | 16/16 | 15/16 | 16/16 | 14/16 |
| Claude Fable 5.2 | **16/16** | 16/16 | 16/16 | 16/16 | 15/16 |
| Claude Mythos 5.1 | **16/16** | 16/16 | 15/16 | 16/16 | 16/16 |
| Claude Mythos 5.2 | **16/16** | 16/16 | 16/16 | 15/16 | 16/16 |
| DeepSeek V4.1 Flash (Fireworks) | **16/16** | 16/16 | 16/16 | 11/16 | 14/16 |
| Codex Daybreak Blue | **15/16** | 16/16 | 16/16 | 16/16 | 13/16 |
| Codex GPT-6 Astra | **15/16** | 16/16 | 16/16 | 16/16 | 14/16 |
| Claude Fable 5.1 | **15/16** | 16/16 | 15/16 | 15/16 | 14/16 |
| Codex GPT-5.5 | **14/16** | 16/16 | 16/16 | 16/16 | 15/16 |
| Codex GPT-6 Sol | **14/16** | 16/16 | 16/16 | 16/16 | 11/16 |
| Grok 4.7 | **14/16** | 16/16 | 16/16 | 16/16 | 16/16 |
| Grok 4.7 build-fast | **14/16** | 16/16 | 16/16 | 16/16 | 15/16 |
| Claude Opus 4.8 | **14/16** | 16/16 | 13/16 | 15/16 | 7/16 |
| Claude Sonnet 5.5 | **14/16** | 16/16 | 16/16 | 15/16 | 16/16 |
| Claude Haiku 5.5 | **13/16** | 16/16 | 16/16 | 12/16 | 14/16 |
| Kimi K3 | **13/16** | 16/16 | 16/16 | 14/16 | 15/16 |
| Claude Sonnet 5 | **13/16** | 16/16 | 16/16 | 10/16 | 14/16 |
| GPT-6 Luna via Copilot | **12/16** | 16/16 | 16/16 | 15/16 | 11/16 |
| Grok 4.6 | **12/16** | 16/16 | 15/16 | 16/16 | 14/16 |
| Kimi K3.1 Pro | **11/16** | 16/16 | 16/16 | 15/16 | 16/16 |
| Grok 4.5 | **10/16** | 16/16 | 16/16 | 16/16 | 15/16 |
| Claude Sonnet 4.5 | **10/16** | 16/16 | 16/16 | 16/16 | 16/16 |
| Codex GPT-6 Luna | **10/16** | 15/16 | 15/16 | 12/16 | 6/16 |
| MiniMax M3 (Fireworks) | **8/16** | 10/16 | 15/16 | 12/16 | 4/16 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | **6/16** | 11/16 | 16/16 | 10/16 | 0/16 |
| Kimi K2.8 (for-coding) | **5/16** | 15/16 | 15/16 | 13/16 | 9/16 |
| Claude Haiku 4.5 | **5/16** | 8/16 | 16/16 | 14/16 | 0/16 |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | **3/16** | 8/16 | 14/16 | 6/16 | 0/16 |
| Claude Sonnet 5.5 via Copilot | **1/16** | 16/16 | 16/16 | 15/16 | 14/16 |

Per ISA, hex first attempt:

| Model | AArch64 | x86-64 |
|---|---|---|
| Claude Opus 5.5 | 8/8 | 8/8 |
| Claude Fable 5.2 | 8/8 | 8/8 |
| Claude Mythos 5.1 | 8/8 | 8/8 |
| Claude Mythos 5.2 | 8/8 | 8/8 |
| DeepSeek V4.1 Flash (Fireworks) | 8/8 | 8/8 |
| Codex Daybreak Blue | 8/8 | 7/8 |
| Codex GPT-6 Astra | 8/8 | 7/8 |
| Claude Fable 5.1 | 7/8 | 8/8 |
| Codex GPT-5.5 | 6/8 | 8/8 |
| Codex GPT-6 Sol | 7/8 | 7/8 |
| Grok 4.7 | 7/8 | 7/8 |
| Grok 4.7 build-fast | 6/8 | 8/8 |
| Claude Opus 4.8 | 6/8 | 8/8 |
| Claude Sonnet 5.5 | 6/8 | 8/8 |
| Claude Haiku 5.5 | 5/8 | 8/8 |
| Kimi K3 | 5/8 | 8/8 |
| Claude Sonnet 5 | 7/8 | 6/8 |
| GPT-6 Luna via Copilot | 6/8 | 6/8 |
| Grok 4.6 | 5/8 | 7/8 |
| Kimi K3.1 Pro | 3/8 | 8/8 |
| Grok 4.5 | 5/8 | 5/8 |
| Claude Sonnet 4.5 | 2/8 | 8/8 |
| Codex GPT-6 Luna | 5/8 | 5/8 |
| MiniMax M3 (Fireworks) | 2/8 | 6/8 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 2/8 | 4/8 |
| Kimi K2.8 (for-coding) | 0/8 | 5/8 |
| Claude Haiku 4.5 | 0/8 | 5/8 |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | 0/8 | 3/8 |
| Claude Sonnet 5.5 via Copilot | 1/8 | 0/8 |

## Whole programs (app battery, sample 1)

First attempt / within four on the hex path, next to the same model's committed sample-1 asm / stream app
results (— where that model did not run the app battery):

| Model | Hex first attempt | Hex within 4 | asm first attempt | stream first attempt |
|---|---|---|---|---|
| Claude Fable 5.2 | **16/16** | 16/16 | 13/16 | 14/16 |
| Claude Opus 4.8 | **15/16** | 16/16 | 15/16 | 13/16 |
| Codex GPT-6 Astra | **15/16** | 16/16 | 16/16 | 16/16 |
| Claude Mythos 5.2 | **15/16** | 16/16 | 14/16 | 16/16 |
| Claude Sonnet 5.5 | **13/16** | 15/16 | 14/16 | 15/16 |
| Claude Fable 5.1 | **12/16** | 16/16 | 13/16 | 13/16 |
| Claude Haiku 5.5 | **11/16** | 16/16 | 14/16 | 14/16 |
| Grok 4.6 | **11/16** | 16/16 | 16/16 | 15/16 |
| Grok 4.7 | **10/16** | 16/16 | 16/16 | 15/16 |
| Grok 4.7 build-fast | **10/16** | 16/16 | 16/16 | 14/16 |
| Claude Sonnet 5 | **7/16** | 9/16 | 12/16 | 11/16 |
| Claude Opus 5.5 | **7/16** | 12/16 | 16/16 | 15/16 |
| Claude Mythos 5.1 | **7/16** | 15/16 | 15/16 | 15/16 |
| Codex GPT-6 Sol | **6/16** | 14/16 | 13/16 | 15/16 |
| DeepSeek V4.1 Flash (Fireworks) | **4/16** | 11/16 | 12/16 | 11/16 |
| Kimi K3.1 Pro | **4/16** | 15/16 | 15/16 | 14/16 |
| Codex GPT-5.5 | **3/16** | 12/16 | — | — |
| Codex Daybreak Blue | **2/16** | 11/16 | — | — |
| GPT-6 Luna via Copilot | **2/16** | 5/16 | — | — |
| Kimi K3 | **2/16** | 15/16 | 15/16 | 14/16 |
| Kimi K2.8 (for-coding) | **2/16** | 7/16 | — | — |
| Grok 4.5 | **1/16** | 15/16 | — | — |
| Claude Sonnet 4.5 | **0/16** | 4/16 | 8/16 | 10/16 |
| Codex GPT-6 Luna | **0/16** | 0/16 | — | — |
| Claude Sonnet 5.5 via Copilot | **0/16** | 4/16 | — | — |

Those first-attempt scores mix two different things for two rows: safety blocks and wrong programs. Split
apart (first attempts only, except the last column):

| Model | First attempts | Passed | Refused | Genuine failures | Pass rate among answered | Refused attempts (all rounds) |
|---|---|---|---|---|---|---|
| Claude Fable 5.2 | 16 | 16 | 0 | 0 | 16/16 (100%) | 0 of 16 |
| Claude Opus 5.5 | 16 | 7 | 9 | 0 | 7/7 (100%) | 22 of 34 |
| Claude Opus 4.8 | 16 | 15 | 0 | 1 | 15/16 (94%) | 0 of 17 |
| Codex GPT-6 Astra | 16 | 15 | 0 | 1 | 15/16 (94%) | 0 of 17 |
| Claude Mythos 5.2 | 16 | 15 | 0 | 1 | 15/16 (94%) | 0 of 17 |
| Claude Sonnet 5.5 | 16 | 13 | 1 | 2 | 13/15 (87%) | 3 of 23 |
| Claude Fable 5.1 | 16 | 12 | 0 | 4 | 12/16 (75%) | 0 of 20 |
| Claude Haiku 5.5 | 16 | 11 | 0 | 5 | 11/16 (69%) | 0 of 25 |
| Grok 4.6 | 16 | 11 | 0 | 5 | 11/16 (69%) | 0 of 23 |
| Grok 4.7 | 16 | 10 | 0 | 6 | 10/16 (62%) | 0 of 22 |
| Grok 4.7 build-fast | 16 | 10 | 0 | 6 | 10/16 (62%) | 0 of 23 |
| Claude Sonnet 5 | 16 | 7 | 0 | 9 | 7/16 (44%) | 0 of 40 |
| Claude Mythos 5.1 | 16 | 7 | 0 | 9 | 7/16 (44%) | 0 of 34 |
| Codex GPT-6 Sol | 16 | 6 | 0 | 10 | 6/16 (38%) | 0 of 33 |
| DeepSeek V4.1 Flash (Fireworks) | 16 | 4 | 0 | 12 | 4/16 (25%) | 0 of 43 |
| Kimi K3.1 Pro | 16 | 4 | 0 | 12 | 4/16 (25%) | 0 of 34 |
| Codex GPT-5.5 | 16 | 3 | 0 | 13 | 3/16 (19%) | 0 of 37 |
| Codex Daybreak Blue | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 42 |
| GPT-6 Luna via Copilot | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 52 |
| Kimi K3 | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 41 |
| Kimi K2.8 (for-coding) | 16 | 2 | 0 | 14 | 2/16 (12%) | 0 of 55 |
| Grok 4.5 | 16 | 1 | 0 | 15 | 1/16 (6%) | 0 of 41 |
| Claude Sonnet 4.5 | 16 | 0 | 0 | 16 | 0/16 (0%) | 0 of 57 |
| Codex GPT-6 Luna | 16 | 0 | 0 | 16 | 0/16 (0%) | 0 of 64 |
| Claude Sonnet 5.5 via Copilot | 16 | 0 | 14 | 2 | 0/2 (0%) | 52 of 58 |

Read Claude Opus 5.5's 7/16 and Claude Sonnet 5.5 via Copilot's 0/16 as what each front end's safety filter
let through, not as ability: every first attempt Opus 5.5 was allowed to answer passed.

## Findings

- **Writing raw machine code is within reach of the frontier models.** On functions, Opus 5.5 was perfect
  (16/16 first attempt), and Astra, Daybreak Blue and Fable 5.1 missed one cell each. Twenty-three of the twenty-nine
  model rows pass every function cell within four attempts. Hex costs the strongest models little against
  their assembly results; it costs mid-tier models much more (Sonnet 4.5 10/16, Luna 10/16 through Codex,
  MAI-Code 6/16, Haiku 4.5 5/16). Haiku 5.5 is the exception among small models: 13/16 on functions (Haiku
  4.5: 5/16) and 11/16 on whole programs first attempt, 16/16 on both within four, with no refusals.
- **Claude Fable 5.2, Mythos 5.1 and Mythos 5.2 (2026-11-10) are perfect on hex functions** (16/16 first
  attempt each, with Opus 5.5). On whole programs Fable 5.2 wrote 16/16 correctly first time, the first model
  to do so, and Mythos 5.2 15/16 (16/16 within four); neither was refused once. Mythos 5.1 wrote 7/16 first
  attempt and 15/16 within four (`rpn` x86-64 lost): in 10 of its 32 hex-program responses it wrote Bash
  tool-call markup as plain text (`<invoke name="Bash">`, tools are disabled for every subject), invented the
  tool output, and then gave hex built on it. It never did this on any other path or battery, and the other
  two models never did it at all. One of those responses ran to 128 KB, and quoting it in the repair prompt
  exceeded Linux's per-argument limit; the harness now sends such prompts on stdin (see the methodology).
- **Kimi K3.1 Pro and three Fireworks models (2026-11-10/11).** DeepSeek V4.1 Flash wrote all 16 hex functions
  correctly on the first attempt (more than its 11/16 structured or 14/16 QBE), but only 4/16 whole programs
  (11/16 within four). Kimi K3.1 Pro: 11/16 functions first attempt and 16/16 within four; 4/16 programs first
  attempt (9 of its 12 misses out-of-bounds accesses), 15/16 within four. MiniMax M3 (8/16, 10/16 within four)
  and Nemotron 3 Ultra (3/16, 8/16) stayed below the 12/16 gate, so they ran no hex programs. No refusals. DeepSeek's
  16 hex programs cost about $2.40.
- **AArch64 is harder to hand-encode than x86-64 for most models** (fixed 32-bit words with packed
  bit fields: shift and bitmask immediates, register fields, 19- and 26-bit branch offsets), e.g. Sonnet 4.5
  2/8 vs 8/8, Haiku 4.5 0/8 vs 5/8. Grok 4.6's AArch64 misses (5/8 vs 7/8) are all time-outs: it was still
  reasoning at 180 s.
- **Whole programs separate the models sharply.** Astra and Opus 4.8 wrote 15 of 16 programs correctly on
  the first attempt; Sonnet 5.5 13 and Fable 5.1 12. The Codex models other than Astra fall to 0-6/16 first
  attempt (most still recover within four: Sol 14/16), Sonnet 4.5 to 0/16 (4/16 within four).
- **Grok sits in the upper middle, and recovers well.** On functions Grok 4.7 and 4.7 build-fast reached
  14/16 first attempt, 4.6 12/16 and 4.5 10/16, all four 16/16 within four. On whole programs Grok 4.6 wrote
  11/16 correctly first time, 4.7 and build-fast 10/16, and all three fixed every remaining program within
  four (16/16, as only Astra, Opus 4.8, Fable 5.1 and Haiku 5.5 otherwise do). Grok 4.6's four first-attempt function
  misses were all time-outs (180 s, still reasoning), not wrong code. No Grok attempt was refused (0 of 188
  attempts).
- **Grok 4.5: 1/16 first attempt, 15/16 within four.** Its first programs were nearly right but untested
  against the edges: 13 of its 15 first-attempt failures ran and broke on one of the first five test runs,
  the smallest inputs (empty input, one byte, `"b\na\n"`, `"hello world\n"`); the other two were refused
  before running (`text` for the `.text` marker; an AArch64 slip onto the ARMv8.1 `ldaddlb`). The feedback
  (the failing input, expected against actual output, the decoded listing) was enough to fix all but `rpn`
  on AArch64, at a cost of 41 attempts for 16 cells (22-23 for the other Grok models). It also reasoned least
  before answering: a median of about 21,800 reasoning tokens on a first attempt, against 27,600 (4.6) and
  33,000 (4.7, build-fast).
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
  - **A plain statement of purpose does not stop them.** Putting a truthful paragraph before the prompt (what
    the benchmark is, what the program does, that it runs only in the emulator against fixed tests) lowered
    Opus 5.5's blocked attempts from 22 of 34 to 12 of 27, within run-to-run noise, and asking for an assembly
    comment on every line did not lower them at all (22 of 34). Every block came partway through generation,
    after hundreds to thousands of output tokens: [`live-model-eval-declared.md`](live-model-eval-declared.md).
- **Cost on SuperGrok.** The whole Grok run (four models, both stages: 195 calls, 188 scored attempts and
  7 retried, about six hours at two calls at a time) moved the SuperGrok weekly usage meter from 71% to about 88%, roughly
  0.09% of a week's allowance per call; no paid credits were used.
- **Answer discipline still matters:** Sonnet 5.5 through Copilot opened 14 of 16 function answers with a
  prose plan ("Plan:", "Euclid's algorithm...") on a path whose first line must be machine code; it passed
  all 16 within four once told so.

## The mistakes models make when they encode by hand

From a by-hand review of every failed attempt (first attempts and repairs), with examples:

| Kind | Examples |
|---|---|
| Branch / offset arithmetic | off by one instruction (Fable `17fffffc` for `17fffffb`, GPT-5.5 `cbz`/`b.ne` in `fib`), an AArch64 byte offset not divided by 4 (Grok 4.5 `54fffe01` for `54ffff81` and Grok 4.7 build-fast `b5fffe01` for `b5ffff81`: -16 instructions for -16 bytes), x86 rel8 targets past the end or into the middle of an instruction (Astra `7e 0f` for `7e 0b`, Daybreak `je`/`jne` in `reverse`; Grok 4.7 `bf`, 4.6 `rpn`, 4.5 `hexdump` and `sort_lines`), a `b` past the end of a whole program (Astra `json_lint`), a branch or `bl` back out of the start of the code (Grok 4.5 and 4.6 `json_lint`) |
| Immediate fields | AArch64 `lsr` encoded with the wrong shift (UBFM `immr`): Sol `lsr #0` for `#1`, Opus 4.8 `lsr #60` and Sonnet 5.5 `lsr #24` for `#56`; logical (bitmask) immediates wrong (Luna `0x1fff...` for `0x5555...`); a post-index `strb` of `#-255` for `#1` (Grok 4.6 `381016e3` for `380016e3`); a buffer at 0x300000 for 0x600000 (Grok 4.7 `d2a00613` for `d2a00c13`) |
| Register fields | a source register never written (Opus 4.8 `add x0, x6, x5`; Grok 4.5 `add x1, x1, #0` for `add x1, x0, #1`); x86 REX bits that pick the wrong register (Grok 4.5 `49 31 c0`, `xor r8, rax`, for `4d 31 c0`, and `4c 01 c5`, `add rbp, r8`, for `49 01 c5`; Grok 4.7 `4f 8d 34 2c`, `lea r14`, for `4b 8d 34 2c`, `lea rsi`, so `read` got a null buffer); a SIB index of 100, meaning none (Grok 4.5 `lea rcx, [rdi + riz*8 - 8]` for `rsi*8`) |
| Opcode bits / undefined words | unallocated encodings (MAI-Code `d3a00000`, `8bc10003`; Luna `9a9d3b7b`; Sonnet 4.5 `d71d001d`); the wrong addressing form (Grok 4.7 build-fast `f8000465`, `str x5, [x3], #0`, for `f9000465`, `str x5, [x3, #8]`) |
| Accidentally a newer instruction | a slip that lands on an ARMv8.1+ instruction the prompt forbids: LSE atomics (MAI-Code `ldaddl`, Luna `ldsminlb`, Sonnet 4.5 `ldumaxlb`, Grok 4.5 `ldaddlb` `38690263` for `ldrb w3, [x19, x9]` `38696a63`), MTE `subp` (Sonnet 4.5) |
| Byte order | an AArch64 word written byte-swapped (Sonnet 4.5 `600400b4` for `b4000460`, a `cbz`); an x86 absolute address shifted by a byte (Grok 4.5 `00 00 10 61`, 0x61100000, for `00 10 61 00`, 0x611000) |
| x86 length and encoding | a truncated `movabs` (`48 be 00 00 60 00`), an invalid ModRM (`49 ff ef`), three instructions on one line (`50 52 51`, refused by the stated one-instruction-per-line rule) |
| Format | words split by spaces (`9100 0d8c`), labels or mnemonics left among the hex (Sonnet 4.5), prose plans (Sonnet 5.5 via Copilot), `text` for the `.text` marker (Grok 4.5) |
| Ordinary logic | a missing `ret` before a fall-through (GPT-5.5 `sum_to`), loops that run on zero counts, a misordered bit-count (Luna `popcount`), empty-input and buffer handling in whole programs |
| Out of time | Haiku 4.5 reasoned through the whole 180 s on 8 AArch64 function cells (about 20,800 thinking tokens, no answer); Sonnet 4.5 on 4; Grok 4.6 on 5 function attempts, Grok 4.5 and 4.7 on 1 each, and Grok 4.5 and 4.7 on one whole program each (900 s) |

## False-reject review

Every failed attempt was reviewed by hand. One harness false reject was found and fixed: capstone does not
decode some valid ARMv8.0 encodings (`384568e7`, the unprivileged load `ldtrb w7, [x7, #86]`, which GNU as
accepts), so the hex path refused them as "does not decode". Fixed in 769f4c23 (GNU objdump decodes a word
capstone does not know; undefined words and ARMv8.1+ instructions are still refused). The one affected attempt
(Sonnet 4.5 `sort_lines.hex.aarch64` attempt 3) still fails at runtime under the fix, so no verdict changes.
Every other pre-run refusal was checked against GNU objdump: undefined words, or ARMv8.1+ instructions the
prompt forbids. The Copilot content-filter and Anthropic cyber-classifier blocks are provider-side and are
scored as refusals, as everywhere else.

**Grok (2026-10-02): no false rejects** in 61 failed attempts (15 function, 46 whole-program).

- **Rejected before running (2):** both genuine. `text` for the `.text` marker; the model's own stream began
  with `text`, so this was not a CLI artifact. And `38690263`, which GNU objdump also decodes as the ARMv8.1
  `ldaddlb`.
- **Faults at an address inside an instruction (4):** a branch computed into the middle of an instruction,
  each found in the listing: for example `jae 0x4000b1` into `eb a3`, whose second byte then executes as a
  store.
- **Faults outside the code (6):** branches with a wrong offset, backwards out of the start of the code or
  past its end.
- **Time-outs (9):** checked against the raw stream. Reasoning streamed at the model's normal rate (about
  35-65 characters per second, the same as answered attempts) until the limit, so these were the model
  thinking past the time limit, not a stalled service.
- **Other failures:** runtime verdicts, checked against the spec's expected output.
- **Fenced answer:** one whole-program answer wrapped in a Markdown fence ran as written after the fence was
  stripped, as on every path.
- **Infrastructure errors (7):** retried and not counted as attempts, all in the whole-program stage. Six
  were Grok CLI stream failures (`reqwest error stream: Transport error: error decoding response body`) and
  one was an HTTP 500 (`Internal error during token generation`).

Kimi K3 and Kimi K2.8 (kimi-for-coding) are now in the tables above (completed 2026-10-17, after three
maxed 7-day windows on Kimi Allegretto). Their failures were reviewed by hand: no harness false rejects
(lines that are not one instruction, 9-digit AArch64 words, wrong syscall use, out-of-bounds accesses; every
Kimi timeout is a genuine wall-clock timeout, since Kimi's CLI prints its answer only when the call ends).

**Claude Sonnet 5 (2026-10-22): no false rejects.** Hex functions 13/16 first try, 16/16 within four; whole
programs 7/16 first try, 9/16 within four. Of its 31 failed whole-program attempts, 20 are 900 s wall-clock
timeouts (every attempt on `rpn` and `json_lint`, both ISAs); its 4 failed function attempts are 3 wrong results
and 1 timeout. The others are runtime faults, wrong output, and three malformed words: a 9-digit AArch64 word (`17fffffed`, twice) and `8be00164`, which GNU
objdump also decodes as undefined (an ADD with the reserved shift type).

**Claude Haiku 5.5 (2026-11-07): no false rejects.** Run through `claude -p` on the user's own Claude
account (Max 20x, CVP-enrolled; not through Copilot or Antigravity), CLI-default effort, in
`eval/runs/hex-haiku55-20261107-174207` and `eval/runs/apps-hex-haiku55-20261107-174207`. Hex functions
13/16 first try, 16/16 within four; whole programs 11/16 first try, 16/16 within four. All 8 first-attempt
failures are on the model's side: two malformed AArch64 words rejected before running (`d3e0fd4b`, which GNU
objdump also decodes as undefined, and the 9-digit `94000004d`), two function crashes (a stray `prfm` literal
word as the first instruction of `bmax`; `reverse` loading outside the array when `n = 0`), a program that ran off the end
of its code on empty input (`rev_lines` x86-64), and three wrong outputs on the smallest inputs (`wc`, `rpn` and
`base64` on AArch64, e.g. `base64` of `"M"` printed `"T\nQ\nA\n=\n"` for `"TQ==\n"`: a newline after each character and the wrong padding). No attempt was
refused (0 of 25), unlike Opus 5.5 and Sonnet 5.5 on the same account.

## Pending and not run

| Models | Status |
|---|---|
| Gemini 3.8 Flash High (Antigravity) | not run: its function battery stopped at 223/224 (2026-10-24) and no hex run has been scheduled |
| Kimi K3.1 Pro, Gemini 4 Argon High | not run: K3.1 Pro's apps and hex are undecided; Argon's function battery is still in progress |
| MAI-Thinking-1, DeepSeek-V4-Pro (Azure Foundry) | not run: backlogged on cost (pay-per-token) |
| Local Qwen models, Kolibri-1 | not run, and not planned: they score 3-26% on the assembly paths, so the hex path would add nothing |
| Haiku 4.5, MAI-Code (apps) | not run by design: below the 12/16 bar in stage 1 (8/16 and 11/16 within four) |
