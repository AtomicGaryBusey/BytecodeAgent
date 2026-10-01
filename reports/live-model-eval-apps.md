# Live-model eval: the app battery (whole programs in machine code)

> **Account caveat.** The Claude account is enrolled in Anthropic's **Cyber Verification Program** and the
> ChatGPT account in OpenAI's **Daybreak** (Blue tier); both relax cyber safeguards, so treat those rows as upper
> bounds for ordinary accounts. Plans: Claude Max 20x, ChatGPT Plus, SuperGrok, GitHub Copilot Pro. Method and
> fairness contract: [cross-vendor-methodology.md](cross-vendor-methodology.md); design and decisions:
> [design-app-battery.md](design-app-battery.md).

The function batteries ask for one function of at most ~250 instructions. This battery asks whether a model can
write a **whole program** in machine code: `_start`, every function, every table and string, the system calls,
all consistent across 80 to ~1,500 instructions.

## The battery

- **What the model writes:** one complete static Linux program for x86-64 (GNU as, Intel syntax) or AArch64,
  with **no libc and no helper code**. Allowed system calls: `read`, `write`, `exit`/`exit_group`; memory is a
  fixed zero-initialised `.bss` (size stated per task) plus a 1 MiB stack. Entry state is the kernel's
  (sp aligned, argc = 0, registers zero); input arrives on stdin.
- **Eight tasks**, roughly by size (reference sizes ~80 to ~1,500 instructions):

  | Task | What it does |
  |---|---|
  | `wc` | count lines, words, bytes; print `L W B` |
  | `rev_lines` | print each input line reversed (lines split across read boundaries) |
  | `hexdump` | canonical `hexdump -C`-style output |
  | `sort_lines` | sort lines byte-wise in `.bss` |
  | `rpn` | evaluate one int64 RPN expression per line, or print `error` |
  | `base64` | encode stdin |
  | `bf` | a Brainfuck interpreter: program, `!`, then the program's input |
  | `json_lint` | `ok` or the byte offset of the first syntax error in a JSON document |

- **Two paths:** `asm` (the whole file through GNU as, linked by the harness's own linker) and `stream` (fed
  line by line through `StreamAssembler`'s program mode, which stops generation at the first bad line). The
  prompt is the same for both. The IR and structured paths are not used (design decision 1).
- **Checking:** each program runs under Unicorn in a process runner that implements the syscalls, bounds-checks
  every buffer, and classifies faults (`oob`, `bad_syscall`, `no_exit`, `crash`, `write_to_text`, ...). Every
  test runs twice: stdin delivered whole, then in reads of at most 7 bytes. stdout and the exit status must match
  a Python reference exactly.
- **Protocol:** 900 s per call, up to 4 attempts with the first failing test (stdin excerpt, expected vs actual
  output window) as repair feedback, **3 independent samples per cell**: 8 tasks x 2 paths x 2 ISAs x 3 samples =
  96 cells per model. Same fairness contract as the function batteries: zero tools, the same system prompt and
  task prompt, a private home per call, and per-call verification against each CLI's own record of the request.

## Results (96 cells per model)

Tables from `eval/app_tables.py`. pass@3 counts the 32 (task, path, ISA) groups where at least one of the three
samples passed on its first attempt.

| Model | First attempt | Within 4 | Mean attempts | Sample 1 | Sample 2 | Sample 3 | pass@3 (groups) |
|---|---|---|---|---|---|---|---|
| Codex GPT-6 Astra | **94/96 (98%)** | 96/96 (100%) | 1.02 | 32/32 | 32/32 | 30/32 | 32/32 |
| Claude Opus 5.5 | **89/96 (93%)** | 96/96 (100%) | 1.07 | 31/32 | 28/32 | 30/32 | 32/32 |
| Grok 4.7 build-fast | **89/96 (93%)** | 96/96 (100%) | 1.08 | 30/32 | 29/32 | 30/32 | 32/32 |
| Claude Fable 5.1 | **84/96 (88%)** | 96/96 (100%) | 1.15 | 26/32 | 27/32 | 31/32 | 31/32 |
| Claude Sonnet 5.5 | **84/96 (88%)** | 96/96 (100%) | 1.14 | 29/32 | 28/32 | 27/32 | 32/32 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | **23/96 (24%)** | 64/96 (67%) | 2.61 | 6/32 | 7/32 | 10/32 | 15/32 |

Pending: **Kimi K3** (Kimi's 7-day plan window reopens 2026-10-03). The local open-weight models do not run
the app battery (decision 2026-10-01: at 3-8% first-attempt on the function batteries, days of 900 s app attempts
would not change the picture; see the local section of the cross-vendor report).

### Per task (first-attempt passes, 12 cells each)

| Model | wc | rev_lines | hexdump | sort_lines | rpn | base64 | bf | json_lint |
|---|---|---|---|---|---|---|---|---|
| Codex GPT-6 Astra | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 10/12 | 12/12 |
| Claude Opus 5.5 | 11/12 | 11/12 | 10/12 | 11/12 | 11/12 | 12/12 | 11/12 | 12/12 |
| Grok 4.7 build-fast | 11/12 | 12/12 | 12/12 | 12/12 | 12/12 | 10/12 | 11/12 | 9/12 |
| Claude Fable 5.1 | 12/12 | 9/12 | 10/12 | 12/12 | 9/12 | 10/12 | 11/12 | 11/12 |
| Claude Sonnet 5.5 | 12/12 | 11/12 | 11/12 | 12/12 | 9/12 | 11/12 | 12/12 | 6/12 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 3/12 | 5/12 | 2/12 | 5/12 | 0/12 | 3/12 | 5/12 | 0/12 |

### Per path and ISA (first-attempt passes, 24 cells each)

| Model | asm x86-64 | asm AArch64 | stream x86-64 | stream AArch64 |
|---|---|---|---|---|
| Codex GPT-6 Astra | 24/24 | 23/24 | 24/24 | 23/24 |
| Claude Opus 5.5 | 23/24 | 23/24 | 23/24 | 20/24 |
| Grok 4.7 build-fast | 24/24 | 22/24 | 23/24 | 20/24 |
| Claude Fable 5.1 | 19/24 | 23/24 | 23/24 | 19/24 |
| Claude Sonnet 5.5 | 23/24 | 20/24 | 22/24 | 19/24 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 4/24 | 8/24 | 2/24 | 9/24 |

### First-attempt failure categories

| Model | bad_syscall | crash | crosscheck | format | no_exit | oob | syntax | wrong_output |
|---|---|---|---|---|---|---|---|---|
| Codex GPT-6 Astra | · | · | · | · | · | · | 2 | · |
| Claude Opus 5.5 | · | · | 1 | · | · | · | 6 | · |
| Grok 4.7 build-fast | · | · | · | 6 | · | · | · | 1 |
| Claude Fable 5.1 | · | · | · | · | · | · | 9 | 3 |
| Claude Sonnet 5.5 | · | · | · | · | · | · | 11 | 1 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 2 | 1 | · | · | 3 | 8 | 39 | 20 |

## Findings

- **Frontier models can write whole programs in machine code.** Codex GPT-6 Astra wrote 94 of 96 programs
  correctly on the first try (samples 1 and 2 perfect); Opus 5.5 and Grok 4.7 build-fast 89; Fable 5.1 and
  Sonnet 5.5 84. Every one of these five models passed **every** cell within four attempts, including the
  Brainfuck interpreter and the recursive-descent JSON validator, run whole and with 7-byte short reads.
- **Their first-attempt misses are mostly assembler errors, not wrong programs.** Of the five frontier models'
  40 first-attempt failures, 35 were rejected before running (`syntax`, `crosscheck`, `format`) and only 5 were
  wrong output. The single most common mistake, for every frontier model, is an **AArch64 `mov` of an immediate
  that no single instruction can encode** (e.g. `mov x2, #65600` to size a buffer): 20 of the 40, including
  both of Astra's misses. Others: x86 `[rip + symbol + register]` addressing (not encodable), labels named like
  x86 operators (`le`, `lt`, which GNU as itself rejects), and breaking a rule the prompt states (a constant used
  before its definition, data in `.text`). Grok's 6 `format` failures are prose preambles ("I'll implement ...
  and verify it before emitting only the source"), a tool-using habit with no tools to use.
- **Three samples matter.** Per-sample first-attempt scores move by up to 5 cells for the same model (Fable
  26, 27, 31); single-sample rankings of the frontier models would not be stable.
- **Microsoft MAI-Code 1.1 Flash is far behind:** 23/96 (24%) on the first attempt, 64/96 (67%) within four, and
  it is the only model that failed cells outright (32). Its typical errors: RIP-relative addressing combined
  with an index register (18 first attempts), duplicate labels in long programs, `.quad` in `.bss`, constants
  used before definition, programs that never exit on empty input, and swapped syscall arguments (a buffer
  address passed as the file descriptor). It never produced a correct `rpn` or `json_lint` on the first try.

## Harness integrity

- **Sandbox escape found and closed before any live run.** An adversarial review showed that a program could
  read any host file on the `asm` path: inside a `.macro`/`.irpc` body GNU as's empty paste `\()` builds
  `.incbin` without the literal name appearing, which the literal-name filter missed. Fixed (32d7354): the asm
  path refuses the prompt's whole "not allowed" list (macros, repetition blocks, conditionals, file inclusion,
  `.fill`, `.org`, `.comm`, ...) wherever it appears outside a string or comment, refuses backslashes outside
  literals, and runs GNU as under a 1 GiB address-space limit. The process runner itself (host-side instruction
  counter, bounds checks, syscall semantics) was reviewed and found sound.
- **Both paths accept exactly the same programs.** The same prompt goes to both paths, so any difference in
  acceptance is a fairness bug. Fixed as found:
  - the asm path now enforces the placement and constant rules the prompt states and the stream path already
    enforced (instructions only in `.text`, data only in data sections, `.bss` only `.zero`/`.space`, constants
    defined before use; c7df64d);
  - the stream path accepts every label name GNU as accepts (it had refused AArch64 `lt`; 95dcdb0);
  - the stream path uses GNU as's bytes when keystone refuses a line GNU as accepts (c1c1df7f);
  - `.end`, `.arch`/`.cpu` (only at the prompt's ISA level, on both paths) and GNU's one-quote character
    literal `'c` (79a275c1).

  After the fixes, every complete recorded app answer (717) was run through both paths: they accept and refuse
  the same programs.
- **Superseded and re-run cells.** Where a fix changed a recorded verdict, the cell was moved to the run's
  `superseded/` directory and re-run from scratch under the corrected rules, never rescored by hand (details in
  each run's `ADJUSTMENTS.md`):
  - Claude Opus 5.5 `rpn.asm.aarch64.s1`: attempt 2 had passed with data in `.text`, which the prompt forbids;
    the re-run passed on attempt 1.
  - Claude Sonnet 5.5 `json_lint.stream.aarch64.s1`: attempt 1 was cut off at the refused label `lt`; the
    re-run passed on attempt 1.
  - MAI-Code `wc.stream.aarch64.s2` (refused `.arch armv8-a`) and `json_lint.stream.aarch64.s1` (refused `.end`,
    which changed the repair feedback): re-run; the first now passes on attempt 2 after a genuine failure, the
    second failed all four attempts genuinely.
- **Every failure was reviewed by hand** for all six models (the Copilot adapter's own transient errors, such
  as a broken stream or a network timeout, void the attempt and are retried, never scored).

## Limitations

- One run per model and date; 3 samples per cell. CLI defaults for effort, as in the function batteries (Astra
  ran at Codex's default, low).
- The stream path cannot accept forward references to constants (`msg_len` used before `msg_len = . - msg`);
  the prompt states the rule for both paths and both enforce it, but it is a restriction GNU as does not have.
- Tests are fixed and public in this repository; the repair feedback reveals at most the first failing test's
  stdin excerpt and a 160-byte output window per round.
- The Claude and ChatGPT rows carry the CVP / Daybreak caveat above.
