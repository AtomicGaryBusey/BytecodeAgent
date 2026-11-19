# BytecodeAgent

Can a language model write **machine code** directly, without a human-facing toolchain in the loop?
This project builds non-standard ways for a model to emit x86-64 and AArch64 code (including raw machine
code with no tool at all), a
differential harness that proves the result correct, and a live evaluation that puts models from
many vendors (through five vendors' coding CLIs, the GitHub Copilot CLI, the Azure AI Foundry and Fireworks.ai
APIs, Apple's Foundation Models on a Mac, and open-weight models served locally) through the same tasks under
the same rules.

## The emission paths

| Path | What the model writes | What turns it into bytes |
|---|---|---|
| **Stream** (`bca.stream`) | Assembly text, token by token | `StreamAssembler` encodes each instruction the moment its line is complete (keystone), cross-checked against GNU as, so generation can stop at the first bad line |
| **Structured** (`bca.structured`) | One JSON object per instruction (e.g. a tool call) | A deterministic encoder: AArch64 bit by bit, x86-64 via iced-x86; no assembly text at all |
| **IR** (`bca.ir`) | A small portable language: QBE IL, freestanding C, or WebAssembly text | An existing compiler (QBE, clang/tcc, Cranelift via wasmtime) lowers it; the function's bytes are pulled out of the ELF object |
| **Hex** (`bca.liveeval.hexpath`, opt-in `--paths hex`) | The machine code itself, as hex: AArch64 32-bit words or x86-64 instruction bytes, plus data bytes for whole programs | Nothing: the bytes run exactly as written (decoded only to check the instruction set and to explain failures). Opus 5.5, Fable 5.2 and both Mythos models: 16/16 round-1 functions on the first try; Fable 5.2: 16/16 whole programs on the first try |

Raw machine code across 29 models (functions and whole programs, mistakes models make encoding by hand, and the
safety filters that block it): [`reports/live-model-eval-hex.md`](reports/live-model-eval-hex.md).
Whether a plain, truthful statement of purpose in the prompt reduces those blocks (not reliably; they fire
on the model's own output): [`reports/live-model-eval-declared.md`](reports/live-model-eval-declared.md).

```python
from bca.structured import assemble
prog = assemble("aarch64", [{"op": "add", "dst": "x0", "src": ["x0", "x1"]}, {"op": "ret"}])
prog.code  # b'\x00\x00\x01\x8b\xc0\x03_\xd6'
```

## Proving the code correct

Every path ends in the same checker (`bca.emu`, `bca.difftest`): the bytes run under Unicorn against a
C reference compiled with `-fwrapv`, on edge-case-heavy inputs. The emulator plays adversary: registers
start as garbage, callee-saved registers and the stack are checked after return, out-of-bounds reads
and writes fault, and callbacks into the harness are hostile. Failures come back as precise categories
(`wrong_result`, `abi`, `oob`, `crash`, `hang`, ...) with the faulting source line, which the live eval
feeds back to the model for repair.

## Live evaluation (`bca.liveeval`)

Real models, called through their vendors' own coding CLIs (or their APIs directly, where noted), write code
through the stream, structured and IR paths on both ISAs (the hex path is opt-in), with up to four attempts
and repair feedback.

- **Batteries:** `round1` (8 tasks), `round2` (11: recursion, SIMD, memory), `long` (9 programs of
  ~60-250 instructions: a bytecode VM, Base64, XXH64, PackBits, multi-precision multiply, ...) and
  `adversarial` (10 bait tasks, with prompt variants such as `misleading_hint` and `terse`).
  224 cells per model at baseline. Separately, `apps` (8 whole programs, 3 samples: 96 cells; below).
- **Subjects:** Claude models via `claude -p`; `codex:<model>` (OpenAI Codex CLI), `kimi:<model>`
  (Kimi Code), `agy:<model>` (Google Antigravity), `grok:<model>` (xAI Grok Build) and `copilot:<model>`
  (GitHub Copilot CLI through the Copilot SDK: Microsoft's MAI-Code, plus other vendors' models served by
  GitHub for harness comparisons), `foundry:<deployment>` (Azure Foundry pay-per-token, called through its
  API directly, with a per-model spend cap), `fireworks:<model>` (Fireworks.ai serverless, the same way), `cerebras:<model>` (Cerebras Inference, the same way), `apple:<model>` (Apple Foundation Models through `fm serve` on the user's Mac), and `local:<model>` (open-weight models served by Ollama on this
  machine, called through its API directly).
- **Fair comparison:** every subject runs with zero tools, the same system and task prompts, a private
  home per call, and an audited CLI version (recorded per call); each call is verified against the CLI's own record of the
  request, and anything unexpected aborts the run instead of being scored. Method and known limits:
  [`reports/cross-vendor-methodology.md`](reports/cross-vendor-methodology.md).

```sh
.venv/bin/python -m bca.liveeval probe opus codex:gpt-6-luna          # one tiny call each
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/my-run --tasks round1,round2 \
    --subjects claude-sonnet-5-5,kimi:kimi-code/k3 --max-calls 300 --dry-run   # plan and estimate
.venv/bin/python -m bca.liveeval summarize eval/runs/my-run
.venv/bin/python -m bca.liveeval compare eval/runs/a eval/runs/b
```

Runs are resumable (re-run the same command), capped by `--max-calls`, and stop a subject cleanly when
its plan quota is spent.

### Results (baseline prompts, 224 cells per model)

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Gemini 4 Hydrogen High (Antigravity) | 48 | 62 | 54 | 60 | **224/224 (100%)** | 224/224 (100%) | 1.00 |
| Codex GPT-6 Astra | 46 | 61 | 54 | 60 | **221/224 (99%)** | 224/224 (100%) | 1.01 |
| Claude Mythos 5.2 | 47 | 61 | 53 | 59 | **220/224 (98%)** | 224/224 (100%) | 1.02 |
| Claude Fable 5.2 | 47 | 62 | 50 | 60 | **219/224 (98%)** | 224/224 (100%) | 1.02 |
| Claude Mythos 5.1 | 47 | 59 | 54 | 57 | **217/224 (97%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6.1 Sol | 44 | 60 | 53 | 59 | **216/224 (96%)** | 224/224 (100%) | 1.04 |
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Kimi K3.1 Pro Highspeed | 47 | 59 | 49 | 60 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6 Sol | 43 | 62 | 48 | 60 | **213/224 (95%)** | 224/224 (100%) | 1.05 |
| Grok 4.7 build-fast | 47 | 60 | 49 | 57 | **213/224 (95%)** | 223/224 (100%) | 1.08 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Grok 4.6 | 45 | 59 | 50 | 57 | **211/224 (94%)** | 223/224 (100%) | 1.07 |
| Gemini 3.8 Flash High (Antigravity) | 46 | 60 | 47 | 57 | **210/224 (94%)** | 224/224 (100%) | 1.07 |
| Claude Opus 4.7 | 42 | 58 | 51 | 58 | **209/224 (93%)** | 223/224 (100%) | 1.08 |
| Codex GPT-5.5 | 47 | 57 | 45 | 58 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Kimi K3.1 Pro | 45 | 61 | 45 | 56 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Codex GPT-6.1 Terra | 45 | 59 | 48 | 54 | **206/224 (92%)** | 224/224 (100%) | 1.09 |
| Codex GPT-5.6 Sol | 42 | 60 | 46 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.09 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Claude Haiku 5.5 | 42 | 58 | 46 | 57 | **203/224 (91%)** | 222/224 (99%) | 1.11 |
| Claude Sonnet 4.5 | 48 | 55 | 44 | 56 | **203/224 (91%)** | 221/224 (99%) | 1.14 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Claude Fable 5 | 45 | 58 | 45 | 51 | **199/224 (89%)** | 203/224 (91%) | 1.30 |
| Claude Opus 5 | 40 | 57 | 50 | 51 | **198/224 (88%)** | 224/224 (100%) | 1.12 |
| Grok 4.5 | 47 | 55 | 41 | 54 | **197/224 (88%)** | 222/224 (99%) | 1.16 |
| Gemini 4 Argon High (Antigravity) | 47 | 60 | 26 | 58 | **191/224 (85%)** | 205/224 (92%) | 1.35 |
| DeepSeek V4.1 Flash (Fireworks) | 41 | 53 | 46 | 50 | **190/224 (85%)** | 224/224 (100%) | 1.19 |
| Codex GPT-5.6 Terra | 41 | 55 | 38 | 55 | **189/224 (84%)** | 223/224 (100%) | 1.19 |
| Qwen 3.8 Max (Fireworks) | 40 | 50 | 42 | 50 | **182/224 (81%)** | 222/224 (99%) | 1.28 |
| Mistral Large 4 | 42 | 54 | 35 | 46 | **177/224 (79%)** | 223/224 (100%) | 1.29 |
| GPT-6 Luna via Copilot | 42 | 53 | 31 | 50 | **176/224 (79%)** | 224/224 (100%) | 1.29 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Claude Opus 4.8 | 35 | 48 | 49 | 43 | **175/224 (78%)** | 222/224 (99%) | 1.27 |
| Z.ai GLM-5.3 (Fireworks) | 39 | 53 | 30 | 50 | **172/224 (77%)** | 217/224 (97%) | 1.39 |
| Claude Sonnet 5.5 via Copilot | 45 | 55 | 19 | 51 | **170/224 (76%)** | 219/224 (98%) | 1.35 |
| Kimi K2.8 (for-coding) | 37 | 46 | 28 | 46 | **157/224 (70%)** | 213/224 (95%) | 1.52 |
| Codex GPT-5.6 Luna | 40 | 44 | 12 | 46 | **142/224 (63%)** | 214/224 (96%) | 1.62 |
| DeepSeek-V4-Pro high (Azure Foundry) | 33 | 45 | 20 | 41 | **139/224 (62%)** | 217/224 (97%) | 1.60 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| MiniMax M3 (Fireworks) | 31 | 36 | 13 | 37 | **117/224 (52%)** | 184/224 (82%) | 2.00 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 26 | 34 | 12 | 35 | **107/224 (48%)** | 151/224 (67%) | 2.29 |
| Qwen 3.8 27B (Cerebras) | 29 | 34 | 10 | 29 | **102/224 (46%)** | 142/224 (63%) | 2.39 |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | 20 | 28 | 13 | 25 | **86/224 (38%)** | 151/224 (67%) | 2.41 |
| Microsoft MAI-Thinking-1 (Azure Foundry) | 22 | 22 | 2 | 26 | **72/224 (32%)** | 144/224 (64%) | 2.58 |
| OpenAI gpt-oss-120b (Cerebras) | 26 | 21 | 3 | 21 | **71/224 (32%)** | 117/224 (52%) | 2.78 |
| OpenAI gpt-oss-120b (Fireworks) | 24 | 22 | 4 | 21 | **71/224 (32%)** | 120/224 (54%) | 2.71 |
| Apple Private Cloud Compute † | 2 | — | — | — | **2/48 (4%)** | 5/48 (10%) | 3.77 |


† Gemini 3.8 Flash High's function batteries were quota-paced on Google AI Plus from 2026-09-28; its last cell (`stats_f.ir.aarch64`) ran on 2026-11-15 (Google AI Pro, Antigravity 1.5.7), so the row is now complete: 210/224 first attempt, 224 within four (211 before one output-limit attempt was scored under the 2026-11-15 rule; see the methodology notes).

**Gemini 4 Hydrogen High (Antigravity; added 2026-11-15) is the first model at 224/224 on the first attempt**, and
leads the app battery (93/96). Gemini 4 Argon High (Antigravity, Google AI Pro; added 2026-11-14) is 191/224: Gemini's safety filter blocked 29
cells on the first attempt, 28 of them in the long battery; on the 195 it could answer first time it passed 191.
In progress and not in the tables until complete: Claude Sonnet 4.6, Opus 4.6 and Opus 4.5, Kimi K2.7 Code
Highspeed (running between Kimi's usage caps; K3.1 Pro Highspeed is complete: 86/96 apps first attempt).

Added 2026-11-11 and 11-12: five Codex models (GPT-6.1 Sol and Terra; GPT-5.6 Sol, Terra and Luna), Claude Opus 5,
Fable 5 and Opus 4.7, Kimi K3.1 Pro Highspeed, OpenAI's open-weight gpt-oss-120b on two hosts (Fireworks and
Cerebras) and Qwen 3.8 27B on Cerebras. Two rows need their notes read:
- **Kimi K3.1 Pro is now 207/224 (92%) at `high` effort**, the CLI's current default and the effort its app and hex
  batteries ran at; the earlier 218/224 (97%) was at `max`, the default in October, and is kept as an effort study
  ([`reports/live-model-eval-effort.md`](reports/live-model-eval-effort.md): what an effort setting, a host and a
  serving tier are worth on the same cells).
- **A score moves by a few cells when nothing changes.** Three Grok models re-ran the long battery (54 cells) with
  the same settings: first-attempt scores moved by +6, -4 and -3 cells, none significantly, and the three models'
  order flipped; within four attempts the runs agree to two cells (same report). Read small first-attempt gaps on
  one battery accordingly.
- **Claude Fable 5's 199/224 is held down by the API's safety classifier**: 21 cells were blocked on all four
  attempts before the model answered; on the 203 it could answer it passed 199 first time (98%).

Mistral Large 4 and Medium 3.5 (through Mistral Vibe) and Meta Muse Spark 1.3 and Muse Wonder Full 1.0 (through
Muse Code; Wonder Full, added 2026-11-13: 221/224 functions and 95/96 apps on the first attempt) are measured
**inside their vendors' agent harnesses**, which cannot be made tool-free, and are reported apart and never ranked
with the table above: [`reports/live-model-eval-agent-harness.md`](reports/live-model-eval-agent-harness.md).

† Apple Private Cloud Compute covers round 1 only: all 48 cells (completed 2026-11-09 after the daily PCC quota paused it at 37), at the model's default setting (no `reasoning_effort`). Apple's on-device model runs on the user's Mac, so it is reported with the local models.

Local models are reported separately. Apple's on-device model (AFM 3 Core Advanced, on the user's Mac; round 1,
stream and IR paths only) passed 0 of 32 cells. On this DGX Spark, the open-weight Qwen models (Qwen3-Coder-Next,
Qwen3-30B-A3B, Qwen2.5-14B, Qwen3.6) score 3-8% first try; Aleph Alpha Kolibri-1 (FP8, vLLM, given an
output-token budget equal to the most any vendor model used per call) scores 58/224 (26%), 97/224 (43%)
within four, and no QBE cell: [Local models](reports/live-model-eval-cross-vendor.md#local-models-dgx-spark-and-apples-on-device-model-on-a-mac).
An opt-in `assisted` variant (notes on AArch64 constants, x86 addressing, a QBE cheat sheet, structured
examples, and explained failures; same rules and checks) lifts weaker models a lot on rounds 1 + 2: Haiku 4.5
58% to 89%, MAI-Code 55% to 89%, GPT-6 Luna 67% to 80%, Qwen3-Coder-Next 9% to 24%, almost all of it on QBE.
Baseline stays the headline: [`reports/live-model-eval-assisted.md`](reports/live-model-eval-assisted.md).

Raw assembly is easy for nearly
every model; the QBE IR path separates them most. The Claude account is enrolled in Anthropic's Cyber
Verification Program and the ChatGPT account in OpenAI's Daybreak (Blue), so treat those rows as upper
bounds for ordinary accounts. Accounts used: Claude Ultra (formerly Max 20x), ChatGPT Pro 100 (Plus until 2026-10-04, so the Codex runs committed before
then ran on Plus), Kimi Allegretto, Google AI Pro (AI Plus 2 TB until 2026-11-06, which every reported
Antigravity run used), SuperGrok, GitHub Copilot Pro, Azure AI Foundry pay-as-you-go for the `foundry:` rows,
Fireworks.ai prepaid credit for the `fireworks:` rows, Cerebras prepaid credit for the `cerebras:` rows, and the user's Mac (on-device model; Private Cloud Compute on
the iCloud+ daily quota) for the `apple:` rows (details in the methodology report). Full comparison, per-path results and the false-reject review:
[`reports/live-model-eval-cross-vendor.md`](reports/live-model-eval-cross-vendor.md); per-run write-ups in
[`reports/`](reports/). The top of the table is a statistical tie: on paired tests, twelve models are level with
Codex GPT-6 Astra (Claude Mythos 5.2, Fable 5.2, Mythos 5.1, Codex GPT-6.1 Sol, Claude Opus 5.5, Kimi K3.1 Pro
Highspeed, GPT-6 Sol, Grok 4.7 build-fast, Fable 5.1, Grok 4.7, Grok 4.6, Claude Opus 4.7). Claude Fable 5.2, Mythos 5.1 and Mythos 5.2 were added 2026-11-10 (all four
batteries plus apps and hex; Claude Code 2.1.322, CLI default effort, the same CVP-enrolled account). Confidence
intervals, repair rates, per-cell difficulty, ISA and path gaps, a shared error classification, prompt-variant effects, refusals, within-vendor generations, token efficiency, the quality of passing code and a harness and infrastructure audit: [`reports/live-model-eval-analytics.md`](reports/live-model-eval-analytics.md).
To explore the cells yourself, open [`reports/dashboard.html`](reports/dashboard.html) in a browser (filters, a cell grid with each
cell's attempt history, head-to-head tests; built by `eval/dashboard.py`).

### App battery (whole programs, 96 cells per model)

Whole static Linux programs in machine code (no libc, raw syscalls): 8 tasks from `wc` to a Brainfuck
interpreter and a JSON validator, asm and stream paths, both ISAs, 3 samples, up to 4 attempts
(`--tasks apps`; tables from `eval/app_tables.py`).

| Model | First attempt | Within 4 | Mean attempts |
|---|---|---|---|
| Grok 4.6 | **95/96 (99%)** | 96/96 (100%) | 1.01 |
| Codex GPT-6 Astra | **94/96 (98%)** | 96/96 (100%) | 1.02 |
| Codex GPT-6.1 Terra | **92/96 (96%)** | 96/96 (100%) | 1.05 |
| Claude Mythos 5.1 | **90/96 (94%)** | 96/96 (100%) | 1.06 |
| Claude Opus 5.5 | **89/96 (93%)** | 96/96 (100%) | 1.07 |
| Codex GPT-6.1 Sol | **89/96 (93%)** | 96/96 (100%) | 1.07 |
| Grok 4.7 | **89/96 (93%)** | 96/96 (100%) | 1.08 |
| Grok 4.7 build-fast | **89/96 (93%)** | 96/96 (100%) | 1.08 |
| Claude Haiku 5.5 | **88/96 (92%)** | 96/96 (100%) | 1.09 |
| Claude Opus 4.8 | **88/96 (92%)** | 96/96 (100%) | 1.08 |
| Kimi K3.1 Pro Highspeed | **86/96 (90%)** | 96/96 (100%) | 1.10 |
| Kimi K3.1 Pro | **85/96 (89%)** | 96/96 (100%) | 1.11 |
| Claude Fable 5.1 | **84/96 (88%)** | 96/96 (100%) | 1.15 |
| Claude Fable 5 | **84/96 (88%)** | 96/96 (100%) | 1.14 |
| Claude Sonnet 5.5 | **84/96 (88%)** | 96/96 (100%) | 1.14 |
| Claude Mythos 5.2 | **84/96 (88%)** | 96/96 (100%) | 1.12 |
| Claude Opus 5 | **83/96 (86%)** | 96/96 (100%) | 1.16 |
| Claude Fable 5.2 | **83/96 (86%)** | 96/96 (100%) | 1.15 |
| Kimi K3 | **83/96 (86%)** | 96/96 (100%) | 1.15 |
| Claude Opus 4.7 | **81/96 (84%)** | 96/96 (100%) | 1.16 |
| Codex GPT-5.6 Sol | **80/96 (83%)** | 96/96 (100%) | 1.17 |
| Codex GPT-6 Sol | **78/96 (81%)** | 95/96 (99%) | 1.21 |
| Claude Sonnet 5 | **76/96 (79%)** | 95/96 (99%) | 1.27 |
| Grok 4.5 | **76/96 (79%)** | 96/96 (100%) | 1.25 |
| Codex GPT-5.6 Terra | **68/96 (71%)** | 96/96 (100%) | 1.33 |
| DeepSeek V4.1 Flash (Fireworks) | **65/96 (68%)** | 93/96 (97%) | 1.44 |
| Claude Sonnet 4.5 | **54/96 (56%)** | 93/96 (97%) | 1.64 |
| Codex GPT-5.6 Luna | **31/96 (32%)** | 80/96 (83%) | 2.31 |
| MiniMax M3 (Fireworks) | **29/96 (30%)** | 62/96 (65%) | 2.59 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | **23/96 (24%)** | 64/96 (67%) | 2.61 |
| NVIDIA Nemotron 3 Ultra NVFP4 (Fireworks) | **14/96 (15%)** | 59/96 (61%) | 3.04 |
| OpenAI gpt-oss-120b (Fireworks) | **10/96 (10%)** | 32/96 (33%) | 3.38 |
| Qwen 3.8 27B (Cerebras) | **9/96 (9%)** | 29/96 (30%) | 3.46 |
| OpenAI gpt-oss-120b (Cerebras) | **8/96 (8%)** | 34/96 (35%) | 3.45 |
| Aleph Alpha Kolibri-1 FP8, token budget (local) | **3/96 (3%)** | 16/96 (17%) | 3.78 |

Grok 4.6 and Astra are one cell apart, which is within noise. Twelve models were added on 2026-11-11 and 11-12:
Codex GPT-6.1 Terra (96%) is third here although it trails GPT-6.1 Sol on functions; Claude Fable 5, Opus 5 and
Opus 4.7 sit together at 84-88%; Grok 4.5 (79%) completes the Grok line; the open-weight gpt-oss-120b and Qwen 3.8
27B pass 8-10 of 96. Opus 4.8, Sonnet 5 and 4.5, GPT-6 Sol, Kimi K3.1 Pro
and three Fireworks models were added on 2026-11-10/11; Opus 4.8 (92%) is level with Opus 5.5 here although QBE
holds it to 78% on functions, and GPT-6 Sol (81%) trails Astra by 16 cells although the two are tied on functions. Of the local models only Kolibri-1 runs the app battery (token-budget mode); the Ollama-served Qwen models do not, by decision. The frontier models' most common first-attempt error is an AArch64
`mov` of an immediate no single instruction can encode. Report, including the sandbox-escape fix and the
asm/stream acceptance parity work: [`reports/live-model-eval-apps.md`](reports/live-model-eval-apps.md).

## Demo: a text editor written by a model in AArch64 assembly

[`demo/editor/`](demo/editor/README.md) holds `edit`, a kilo-style terminal text editor for AArch64 Linux.
Codex GPT-6 Astra wrote all of it as one assembly file, on the first attempt:
- **How it's built:** GNU as and `ld` turn the assembly into a 5 KB static executable. There's no compiler and
  no libc: 477 instructions and six system calls.
- **What it does:** arrow keys, Home/End and PgUp/PgDn; insert, Enter, Backspace and Delete with line joins;
  Ctrl-S to save; Ctrl-Q to quit, guarded when there are unsaved changes; a status bar; vertical and
  horizontal scrolling.
- **How it was tested:** 12 keystroke scenarios driven through a pseudo-terminal in a sandboxed container
  (no network).
- **How to run it:** `./demo/editor/edit FILE`.

[`demo/editor-hex/`](demo/editor-hex/README.md) is the same editor with **no assembler at all**: Astra wrote all
354 instruction words and the data bytes as hex (every branch offset and address its own arithmetic), and the
harness only copied them into an ELF container. It passed all 12 scenarios on the fourth round (rounds 1-3
failed on cursor logic, a hang, and mis-computed branch offsets). Run it with `./demo/editor-hex/edit FILE`.

## Setup

Python 3.12 with `keystone-engine`, `unicorn`, `iced-x86`, `capstone`, `wasmtime`, `fastjsonschema` and `pytest`, plus
these tools on `PATH`: `qbe`, `clang` and/or `tcc`, `x86_64-linux-gnu-as` and `aarch64-linux-gnu-as`
(the GNU as cross-check), and `wasmtime`. Developed on an NVIDIA DGX Spark (aarch64, Ubuntu 24.04).

```sh
python3 -m venv .venv && .venv/bin/pip install keystone-engine unicorn iced-x86 capstone wasmtime fastjsonschema pytest
.venv/bin/python -m pytest -q          # ~2,200 tests, no network or model calls
```

The live eval additionally needs each vendor's CLI installed and logged in (`claude`, `codex`, `kimi`,
`agy`, `grok`). The Codex, Kimi, Antigravity, Grok and Copilot adapters refuse to run on CLI versions they
were not audited against; the Claude adapter records the `claude` version on every call. The Copilot
subject needs Node.js, `@github/copilot` 1.0.107 and `@github/copilot-sdk` 1.0.29 (installed under
`~/.local/opt/copilot/`, or set `BCA_COPILOT_CLI` / `BCA_COPILOT_SDK`) and a logged-in `gh` (its headless
runtime authenticates through the GitHub CLI).

The direct-API subjects need no CLI: `foundry:` takes a Microsoft Entra ID token from a signed-in Azure CLI
(`az login`; no key is stored), `fireworks:` reads `FIREWORKS_API_KEY` or `~/.config/bca/fireworks.key`, `cerebras:` reads `CEREBRAS_API_KEY` or
`~/.config/bca/cerebras.key`, and
`apple:` talks to Apple's `fm serve` at `BCA_APPLE_ENDPOINT`. The pay-per-token routes stop each model at a
spend cap (`BCA_FOUNDRY_CAP_USD`, default $25).

`python -m bca.liveeval models [--latest]` checks the six CLI vendors (Claude, Codex, Kimi, Antigravity, Grok,
Copilot) at once without calling a model: each
CLI's version against the audited ones (and, with `--latest`, the newest Codex/Copilot on npm), the
sign-in state, the account's current model list, and which of those models no run has benchmarked yet.

## Layout

```
bca/isa.py, emu.py, difftest.py   ISA table, Unicorn runner, differential tests
bca/image.py, process.py          whole programs: GNU as + a tiny static linker, and a Linux process under Unicorn
bca/apps/                         the app battery's tasks, references and test generators
bca/stream.py                     streaming assembler with the GNU as oracle
bca/structured/                   JSON instruction encoders, schemas, tool definitions
bca/ir/                           QBE / C / WAT lowering and a minimal ELF extractor
bca/liveeval/                     live-model eval: tasks, prompts, pipelines, runner, vendor adapters (CLI and direct API)
eval/runs/                        recorded runs (prompts, responses, verdicts; raw vendor streams kept off git)
eval/*.py                         table generators (xv_, app_, hex_, declared_, assist_tables), analytics, code quality, dashboard
eval/regenerate.py                every generated table, the analytics report and the dashboard in one command
eval/status.py, watch.py, stop.py running the eval: one status report, an event watcher, stop-by-PID
eval/pin.py, pins.json            pinned vendor CLI binaries (a CLI update needs an audit and a re-pin)
eval/audits/                      request captures behind each CLI audit (raw captures kept off git)
eval/backlog-queue.md             the live work queue
tests/                            the test suite (pytest)
reports/                          eval write-ups, methodology, literature survey and its 2026-11 landscape update
research_notes/                   the source notes behind the 2026-09 survey
examples/stream_demo.py           the stream path end to end
demo/editor/                      a model-written text editor (AArch64 assembly -> GNU as -> static ELF), sandbox tests
demo/editor-hex/                  the same editor written directly as machine code (hex -> ELF container, no assembler)
```

## License

Code is under the [MIT license](LICENSE). The reports in `reports/` and the recorded run data in `eval/runs/`
are under [CC BY 4.0](LICENSE-CC-BY-4.0): reuse them freely, with credit. `eval/runs/` holds raw model outputs
recorded from each vendor's service; those vendors' own terms may also apply to their outputs. The raw vendor
event streams behind each answer (`*.raw.jsonl`, 20 GB uncompressed) are not in the repository: they were removed
from its history on 2026-11-11 and are kept gzipped on the machine that ran the eval.
