# BytecodeAgent

Can a language model write **machine code** directly, without a human-facing toolchain in the loop?
This project builds non-standard ways for a model to emit x86-64 and AArch64 code (including raw machine
code with no tool at all), a
differential harness that proves the result correct, and a live evaluation that puts models from
six vendors through the same tasks under the same rules.

## The emission paths

| Path | What the model writes | What turns it into bytes |
|---|---|---|
| **Stream** (`bca.stream`) | Assembly text, token by token | `StreamAssembler` encodes each instruction the moment its line is complete (keystone), cross-checked against GNU as, so generation can stop at the first bad line |
| **Structured** (`bca.structured`) | One JSON object per instruction (e.g. a tool call) | A deterministic encoder: AArch64 bit by bit, x86-64 via iced-x86; no assembly text at all |
| **IR** (`bca.ir`) | A small portable language: QBE IL, freestanding C, or WebAssembly text | An existing compiler (QBE, clang/tcc, Cranelift via wasmtime) lowers it; the function's bytes are pulled out of the ELF object |
| **Hex** (`bca.liveeval.hexpath`, opt-in `--paths hex`) | The machine code itself, as hex: AArch64 32-bit words or x86-64 instruction bytes, plus data bytes for whole programs | Nothing: the bytes run exactly as written (decoded only to check the instruction set and to explain failures). Astra: 15/16 round-1 functions and 15/16 app-battery programs on the first try, 16/16 within four |

Raw machine code across 18 models (functions and whole programs, mistakes models make encoding by hand, and the
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

Real models, called through their vendors' own coding CLIs, write code through all three paths on both
ISAs, with up to four attempts and repair feedback.

- **Batteries:** `round1` (8 tasks), `round2` (11: recursion, SIMD, memory), `long` (9 programs of
  ~60-250 instructions: a bytecode VM, Base64, XXH64, PackBits, multi-precision multiply, ...) and
  `adversarial` (10 bait tasks, with prompt variants such as `misleading_hint` and `terse`).
  224 cells per model at baseline.
- **Subjects:** Claude models via `claude -p`; `codex:<model>` (OpenAI Codex CLI), `kimi:<model>`
  (Kimi Code), `agy:<model>` (Google Antigravity), `grok:<model>` (xAI Grok Build) and `copilot:<model>`
  (GitHub Copilot CLI through the Copilot SDK: Microsoft's MAI-Code, plus other vendors' models served by
  GitHub for harness comparisons), `foundry:<deployment>` (Azure Foundry pay-per-token, called through its
  API directly, with a per-model spend cap), `fireworks:<model>` (Fireworks.ai serverless, the same way), and `local:<model>` (open-weight models served by Ollama on this
  machine, called through its API directly).
- **Fair comparison:** every subject runs with zero tools, the same system and task prompts, a private
  home per call, and a pinned CLI version; each call is verified against the CLI's own record of the
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
| Codex GPT-6 Astra | 46 | 61 | 54 | 60 | **221/224 (99%)** | 224/224 (100%) | 1.01 |
| Kimi K3.1 Pro | 47 | 60 | 52 | 59 | **218/224 (97%)** | 223/224 (100%) | 1.04 |
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6 Sol | 43 | 62 | 48 | 60 | **213/224 (95%)** | 224/224 (100%) | 1.05 |
| Grok 4.7 build-fast | 47 | 60 | 49 | 57 | **213/224 (95%)** | 223/224 (100%) | 1.08 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Grok 4.6 | 45 | 59 | 50 | 57 | **211/224 (94%)** | 223/224 (100%) | 1.07 |
| Codex GPT-5.5 | 47 | 57 | 45 | 58 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Claude Haiku 5.5 | 42 | 58 | 46 | 57 | **203/224 (91%)** | 222/224 (99%) | 1.11 |
| Claude Sonnet 4.5 | 48 | 55 | 44 | 56 | **203/224 (91%)** | 221/224 (99%) | 1.14 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Grok 4.5 | 47 | 55 | 41 | 54 | **197/224 (88%)** | 222/224 (99%) | 1.16 |
| GPT-6 Luna via Copilot | 42 | 53 | 31 | 50 | **176/224 (79%)** | 224/224 (100%) | 1.29 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Claude Opus 4.8 | 35 | 48 | 49 | 43 | **175/224 (78%)** | 222/224 (99%) | 1.27 |
| Claude Sonnet 5.5 via Copilot | 45 | 55 | 19 | 51 | **170/224 (76%)** | 219/224 (98%) | 1.35 |
| Kimi K2.8 (for-coding) | 37 | 46 | 28 | 46 | **157/224 (70%)** | 213/224 (95%) | 1.52 |
| DeepSeek-V4-Pro high (Azure Foundry) | 33 | 45 | 20 | 41 | **139/224 (62%)** | 217/224 (97%) | 1.60 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 26 | 34 | 12 | 35 | **107/224 (48%)** | 151/224 (67%) | 2.29 |
| Microsoft MAI-Thinking-1 (Azure Foundry) | 22 | 22 | 2 | 26 | **72/224 (32%)** | 144/224 (64%) | 2.58 |
| Gemini 3.8 Flash High (Antigravity) † | 46 | 60 | 48 | 57/59 | **211/223 (95%)** | 223/223 (100%) | 1.06 |


† Partial, quota-paced (Google AI Plus): rounds 1 + 2 and long complete, adversarial 59/60 (one cell, `stats_f.ir.aarch64`, left); the hourly resume loop adds cells as the weekly quota allows. Like for like on the complete batteries, rounds 1 + 2 + long: **154/164 (94%)**, level with Grok 4.6 (154), just behind Grok 4.7 (155), Grok 4.7 build-fast (156), Claude Opus 5.5 and Fable 5.1 (158 each) and Codex GPT-6 Astra (161), and ahead of Codex GPT-6 Sol (153). Its first-attempt failures were reviewed by hand: no harness false rejects (hex literals in QBE, `%x =l %y` without `copy`, malformed JSON, an invented `subl` opcode, a 400 s timeout spent thinking: 62,910 of 65,532 output tokens, and two 180 s timeouts on adversarial `stats_f`).

Local open-weight models on this DGX Spark are reported separately: the Qwen models (Qwen3-Coder-Next,
Qwen3-30B-A3B, Qwen2.5-14B, Qwen3.6) score 3-8% first try; Aleph Alpha Kolibri-1 (FP8, vLLM, given an
output-token budget equal to the most any vendor model used per call) scores 58/224 (26%), 97/224 (43%)
within four, and no QBE cell: [Local open-weight models](reports/live-model-eval-cross-vendor.md#local-open-weight-models-dgx-spark).
An opt-in `assisted` variant (notes on AArch64 constants, x86 addressing, a QBE cheat sheet, structured
examples, and explained failures; same rules and checks) lifts weaker models a lot on rounds 1 + 2: Haiku 4.5
58% to 89%, MAI-Code 55% to 89%, GPT-6 Luna 67% to 80%, Qwen3-Coder-Next 9% to 24%, almost all of it on QBE.
Baseline stays the headline: [`reports/live-model-eval-assisted.md`](reports/live-model-eval-assisted.md).

Raw assembly is easy for nearly
every model; the QBE IR path separates them most. The Claude account is enrolled in Anthropic's Cyber
Verification Program and the ChatGPT account in OpenAI's Daybreak (Blue), so treat those rows as upper
bounds for ordinary accounts. Accounts used: Claude Max 20x, ChatGPT Plus, Kimi Allegretto, Google AI Plus (2 TB),
SuperGrok, GitHub Copilot Pro (details in the methodology report). Full comparison, per-path results and the false-reject review:
[`reports/live-model-eval-cross-vendor.md`](reports/live-model-eval-cross-vendor.md); per-run write-ups in
[`reports/`](reports/). The top of the table is a statistical tie: on paired tests, seven models are level with
Codex GPT-6 Astra (Kimi K3.1 Pro, Opus 5.5, GPT-6 Sol, Grok 4.7 build-fast, Fable 5.1, Grok 4.7, Grok 4.6). Confidence
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
| Claude Opus 5.5 | **89/96 (93%)** | 96/96 (100%) | 1.07 |
| Grok 4.7 | **89/96 (93%)** | 96/96 (100%) | 1.08 |
| Grok 4.7 build-fast | **89/96 (93%)** | 96/96 (100%) | 1.08 |
| Claude Haiku 5.5 | **88/96 (92%)** | 96/96 (100%) | 1.09 |
| Claude Fable 5.1 | **84/96 (88%)** | 96/96 (100%) | 1.15 |
| Claude Sonnet 5.5 | **84/96 (88%)** | 96/96 (100%) | 1.14 |
| Kimi K3 | **83/96 (86%)** | 96/96 (100%) | 1.15 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | **23/96 (24%)** | 64/96 (67%) | 2.61 |
| Aleph Alpha Kolibri-1 FP8, token budget (local) | **3/96 (3%)** | 16/96 (17%) | 3.78 |

Grok 4.6 and Astra are one cell apart, which is within noise. Of the local models only Kolibri-1 runs the app battery (token-budget mode); the Ollama-served Qwen models do not, by decision. The frontier models' most common first-attempt error is an AArch64
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

Python 3.12 with `keystone-engine`, `unicorn`, `iced-x86`, `capstone`, `wasmtime` and `pytest`, plus
these tools on `PATH`: `qbe`, `clang` and/or `tcc`, `x86_64-linux-gnu-as` and `aarch64-linux-gnu-as`
(the GNU as cross-check), and `wasmtime`. Developed on an NVIDIA DGX Spark (aarch64, Ubuntu 24.04).

```sh
python3 -m venv .venv && .venv/bin/pip install keystone-engine unicorn iced-x86 capstone wasmtime pytest
.venv/bin/python -m pytest -q          # ~2000 tests, no network or model calls
```

The live eval additionally needs each vendor's CLI installed and logged in (`claude`, `codex`, `kimi`,
`agy`, `grok`); the adapters refuse to run on CLI versions they were not audited against. The Copilot
subject needs Node.js, `@github/copilot` 1.0.107 and `@github/copilot-sdk` 1.0.29 (installed under
`~/.local/opt/copilot/`, or set `BCA_COPILOT_CLI` / `BCA_COPILOT_SDK`) and a logged-in `gh` (its headless
runtime authenticates through the GitHub CLI).

`python -m bca.liveeval models [--latest]` checks all six vendors at once without calling a model: each
CLI's version against the audited ones (and, with `--latest`, the newest Codex/Copilot on npm), the
sign-in state, the account's current model list, and which of those models no run has benchmarked yet.

## Layout

```
bca/isa.py, emu.py, difftest.py   ISA table, Unicorn runner, differential tests
bca/stream.py                     streaming assembler with the GNU as oracle
bca/structured/                   JSON instruction encoders, schemas, tool definitions
bca/ir/                           QBE / C / WAT lowering and a minimal ELF extractor
bca/liveeval/                     live-model eval: tasks, prompts, pipelines, runner, vendor adapters
eval/runs/                        recorded runs (prompts, responses, raw streams, verdicts)
reports/                          research report and eval write-ups
examples/stream_demo.py           the stream path end to end
demo/editor/                      a model-written text editor (AArch64 assembly -> GNU as -> static ELF), sandbox tests
demo/editor-hex/                  the same editor written directly as machine code (hex -> ELF container, no assembler)
```

## License

Code is under the [MIT license](LICENSE). The reports in `reports/` and the recorded run data in `eval/runs/`
are under [CC BY 4.0](LICENSE-CC-BY-4.0): reuse them freely, with credit. `eval/runs/` holds raw model outputs
recorded from each vendor's service; those vendors' own terms may also apply to their outputs.
