# Design: the app battery ("can a model write a whole program in machine code?")

Status: approved 2026-09-29 (decisions below); being built. Every battery so far asks for **one function** of at most ~250 instructions,
called by the harness under Unicorn. An application is a different kind of task: several functions calling
each other, data and read-only data, a zero-initialised buffer area, an entry point that the OS jumps to, and
input/output through system calls, all of which must stay consistent across hundreds to a few thousand
instructions. This battery measures exactly that, with the same fairness contract as the existing live eval
(zero tools, same prompts, private homes, per-call request verification, false-reject review).

## What the model writes

A complete static program for Linux, x86-64 or AArch64, with **no libc and no helper code**: the model
writes `_start`, every function, every table and string, and makes the system calls itself.

- **Allowed system calls** (named in the prompt, with numbers for both ISAs): `read`, `write`, `exit` /
  `exit_group`. No `brk`/`mmap`: memory comes from a fixed zero-initialised area (`.bss`, size stated per task,
  e.g. 1 MiB) plus the stack (1 MiB). Anything else is a fault (`bad_syscall`).
- **Entry state** as the Linux kernel sets it: sp 16-byte aligned, pointing at argc = 0, argv/envp empty,
  registers zeroed except sp (and the documented auxv is empty). No arguments; input arrives on stdin.
- **Output**: stdout bytes and the exit status are the result. stderr is ignored but allowed.

## Emission paths

| Path | What the model emits | How it becomes a program |
|---|---|---|
| `asm` | one assembly file (GNU as syntax, Intel on x86-64) with `.text`, `.data`, `.rodata`, `.bss` and data directives | assembled whole-file by GNU as, linked by the harness's own linker into an image |
| `stream` | the same, fed through `StreamAssembler` line by line (early abort on the first bad line) | requires extending the stream assembler with sections and data directives (`.byte/.word/.quad/.ascii/.asciz/.zero/.align`) |
| `ir` | QBE IL: `export function $_start`, other functions, `data` definitions | QBE has no inline assembly, so it cannot make a system call; see decision 1 |
| `structured` | one JSON object per instruction | dropped for apps: a 1,000-instruction program is ~80-150 KB of JSON, past some CLIs' output limits, and data sections would need a new schema |

`asm` is the primary path (it isolates "can the model write the program" from "can it survive our streaming
assembler"); `stream` is added once its directive support has been built and cross-checked against GNU as.

## How a program is run and checked

A new **process runner** in `bca/emu.py` (next to `run_function`):
1. Map the linked image at fixed addresses (text r-x, rodata r--, data rw-, bss rw- zeroed), a 1 MiB stack,
   nothing else; set the entry state above.
2. Hook `syscall` (x86-64: `UC_HOOK_INSN`) and `svc #0` (AArch64: `UC_HOOK_INTR`): `read(0, buf, n)` serves
   the test's stdin (any chunking the task allows), `write(1|2, buf, n)` appends to stdout/stderr (bounds-checked
   against mapped memory), `exit*` stops with the status.
3. Limits: an instruction budget per test (e.g. 200 M), a stdout cap, no self-modifying code (text is not
   writable). Faults are reported with the faulting instruction and, for `asm`/`stream`, the source line.

Each test = (stdin bytes, expected stdout bytes, expected exit status), generated from a Python reference
implementation of the task with edge cases (empty input, a single byte, maximum sizes, invalid input where the
task defines behaviour). Verdict categories reuse the existing ones plus `bad_syscall`, `no_exit` (ran off the
end or looped past the budget), `wrong_output` (first differing byte and a window around it), `wrong_status`.

## Tasks (proposed: 8, graded roughly by size)

| Task | What it does | Exercises |
|---|---|---|
| `wc` | count lines, words, bytes of stdin; print `L W B\n` | buffered read loop, integer printing |
| `rev_lines` | print each input line reversed | line splitting across read-chunk boundaries |
| `hexdump` | canonical `hexdump -C`-style output | formatting tables, rodata strings |
| `sort_lines` | sort input lines byte-wise, print | memory management in .bss, a real sort, several functions |
| `rpn` | evaluate one RPN expression per line (int64, + - * / %), print results or `error` | parsing, a stack, error paths |
| `base64` | encode stdin; `-d`-free, fixed mode | a table-driven transform over a stream |
| `bf` | Brainfuck interpreter: program then `!` then input, on stdin | a small VM with a jump table |
| `json_lint` | report `ok` or the byte offset of the first syntax error in a JSON document | a recursive-descent parser (the largest) |

Expected program sizes: ~80 (wc) to ~1,500 instructions (json_lint).

## Protocol

- Same subjects and CLIs as the live eval, with a longer per-call timeout (900 s) and output-size checks per
  CLI before the run.
- **Several samples per cell** (3 by default): single functions were near-ceiling for the top models, so
  consistency matters here; report pass@1, pass@3 and mean attempts to first pass. Up to 4 repair attempts, with
  the first failing test (stdin excerpt, expected vs actual output window) as feedback.
- Scale: 8 tasks x 2 ISAs x 1-2 paths x 3 samples = 48-96 cells per model, larger calls than the function
  batteries. Suggested first set: Codex GPT-6 Astra, Claude Opus 5.5, Claude Fable 5.1, Grok 4.7 build-fast,
  Kimi K3, Claude Sonnet 5.5 (the models at >= 90% on functions).

## Build plan (DUB-style: contract, parallel builders, adversarial review, then live runs)

1. Process runner + syscall layer + image linker (extend `bca/ir/elf.py` from one function to a whole program
   with `_start`, sections and a fixed layout), with unit tests on hand-written programs for both ISAs.
2. The 8 tasks with Python references, test generators and canned correct / broken solutions for both ISAs.
3. `asm` pipeline (GNU as + linker); `stream` pipeline after the directive extension; prompts.
4. Adversarial review of the runner (escape attempts: writes to text, syscalls with bad pointers, huge outputs,
   stack exhaustion) and of the verdicts; then smokes, then full runs with plan budgets checked first.

## Decisions (made 2026-09-29)

1. **The IR path.** QBE cannot issue system calls. Options: (a) drop IR for apps; (b) give every path the same
   harness-supplied `sys_read`/`sys_write`/`sys_exit` wrappers (then "no helper code" is relaxed equally for all
   paths); (c) IR only, with the wrappers. **Decided: (a), `asm` + `stream` only in round 1.**
2. **Samples per cell: 3.**
3. **Models: the six at >= 90% on functions** (Astra, Opus 5.5, Fable 5.1, Grok 4.7 build-fast, Kimi K3, Sonnet 5.5); plan budgets checked before each run.
