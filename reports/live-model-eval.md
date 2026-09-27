# Live-model end-to-end evaluation (first real run)

**Run:** `eval/runs/20260927-130149/`, 2026-09-27. **Subject:** `claude-opus-5-5`. The model id comes from the
`system/init` and `message_start` events of all 52 streams. Claude Code CLI 2.1.283, invoked as `claude -p`.
**Harness:** `bca/liveeval/` (new). **Tests:** `tests/test_liveeval.py` (29 tests, fake subject, no quota).
The full suite passes: 553 tests (524 existing + 29 new).

## Headline

| | pass@1 | pass within 4 attempts | subject calls |
|---|---|---|---|
| all 48 cells (8 tasks × 3 paths × 2 ISAs) | **45/48 (94%)** | **48/48 (100%)** | 52 (+1 isolation probe = 53 total) |

Only 4 of the 52 attempts failed, and every failing cell was repaired within 3 attempts. On these tasks the
model is near the ceiling. As a result, most of the pipeline safety nets were **not exercised by real
mistakes** in this run:

- The streaming early-abort never fired.
- The GNU as cross-check never rejected anything.
- No ABI violation or out-of-bounds write occurred.

The catches that did happen are listed below. Treat this run as a validation of the harness plus a first
baseline, not as a stress test of the pipelines.

## Setup

**Tasks** (`bca/liveeval/tasks.py`). Each task has a natural-language spec, an exact C prototype, a C reference
compiled for the host (`-fwrapv`), and deterministic inputs:

- `sum_to`: 57 cases, n ∈ [-1000, 1000] plus INT64_MIN and -2^31.
- `gcd`: 232 non-negative pairs.
- `popcount`: 81 full-range values.
- `fib`: n = 0..90.
- `bmax`: 143 buffers (n ≥ 1).
- `reverse`: 147 buffers.
- `bubble_sort`: 117 buffers (n ≤ 64).
- `count_nonzero_prefix`: 90 buffers.

Scalar tasks are scored with `bca.difftest.differential` and buffer tasks with `buffer_differential`. Both run
with register garbage, ABI checks and a data-write guard. Every task/ISA pair was first checked against its
own C reference lowered with `lower(c_ref, "c", ...)`, and all 16 passed. This rules out false failures from
the harness.

**Paths** (`bca/liveeval/pipelines.py`, prompts in `bca/liveeval/prompts.py`). All prompts share one structure:

1. The signature.
2. The ISA and ABI: argument and return registers, callee-saved registers, stack alignment, and the caller's frame.
3. Path-specific format rules.
4. The spec.
5. On repair rounds only: `<previous>` code and `<failure>` text.

The three paths:

- **stream**: GNU syntax (Intel noprefix on x86, standard AArch64). Rules include: start with the label
  `name:`, branch targets must be labels, no directives, no `;`, lines under 120 characters, no fences or
  prose. Each `text_delta` from `--output-format stream-json --include-partial-messages` is fed to
  `StreamAssembler.feed` as it arrives. On the first error event the harness SIGKILLs the process group
  (`bca/liveeval/subject.py`).
- **structured**: the prompt includes `tool_definitions(isa)[0]` (name and description) and
  `input_schemas(isa)["program"]` (about 14 KB on x86, about 18 KB on AArch64). The output is parsed as JSON
  (fences stripped) and passed to `Session(isa).emit_program`.
- **ir**: QBE IL for `export function l $name(...)`. Lowered with `lower(src, "qbe", isa, name)`.

**Scoring and repair** (`bca/liveeval/runner.py`). Each cell gets attempt 1 plus up to 3 repair rounds. Each
repair is a fresh call whose prompt includes the previous code and the exact failure. For the stream path the
previous code is the text up to the abort. The failure is one of:

- the assembler or encoder message;
- the QBE diagnostics;
- the first 3 differential mismatches (inputs, expected, actual, emulation error, and for buffer tasks the
  expected and actual array).

Every prompt, raw event stream (`attemptN.raw.jsonl`), response, verdict (with code hex and disassembly) and the
final disassembly is saved per cell. `attempts.jsonl` and `results.jsonl` hold the aggregate records. The run
used 4 parallel subjects, is resume-safe (a finished cell has `cell.json`), and has a hard call budget persisted
in `calls.log` (cap 195).

**Isolation.** Every call used these flags:

```
claude -p --output-format stream-json --include-partial-messages --verbose --tools "" \
  --strict-mcp-config --disable-slash-commands --no-session-persistence --setting-sources "" \
  --safe-mode --system-prompt "<neutral one-liner>"
```

Each call ran from a fresh empty `/tmp/subj-XXXX` directory. A probe call (its result is not in the run dir)
confirmed:

- `"tools":[]`, `"mcp_servers":[]`, `"slash_commands":[]`, `"skills":[]`;
- no CLAUDE.md is visible;
- the model reports no tools.

The probe also showed two leaks, covered under Limitations:

- The cwd is visible to the model. The first probe ran from a scratchpad whose path contained the repo name,
  which is why the harness uses a neutral `/tmp/subj-*` directory.
- The account's user-email context item is still injected. `--safe-mode` does not remove it, and `--bare` would
  need an API key.

The init event lists built-in agent names, but with no tools the model cannot call them.

## Results

Per cell (pass@1 / pass within 4 / attempts used). This table is generated by
`python -m bca.liveeval summarize eval/runs/20260927-130149` and is also saved as `summary.md` in the run dir.

| task | stream x86_64 | stream aarch64 | structured x86_64 | structured aarch64 | ir x86_64 | ir aarch64 |
|---|---|---|---|---|---|---|
| sum_to | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| gcd | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 | 1 / ✓ / 1 |
| popcount | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 0 / ✓ / 2 |
| fib | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bmax | 1 / ✓ / 1 | 0 / ✓ / 3 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| reverse | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| bubble_sort | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |
| count_nonzero_prefix | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 | 1 / ✓ / 1 |

### Aggregates

| slice | pass@1 | pass within 4 | mean attempts |
|---|---|---|---|
| path: stream | 15/16 (94%) | 16/16 | 1.12 |
| path: structured | **16/16 (100%)** | 16/16 | 1.00 |
| path: ir | 14/16 (88%) | 16/16 | 1.12 |
| ISA: x86_64 | 23/24 (96%) | 24/24 | 1.04 |
| ISA: aarch64 | 22/24 (92%) | 24/24 | 1.12 |
| stream × x86_64 / aarch64 | 8/8 / 7/8 | 8/8 / 8/8 | 1.00 / 1.25 |
| structured × x86_64 / aarch64 | 8/8 / 8/8 | 8/8 / 8/8 | 1.00 / 1.00 |
| ir × x86_64 / aarch64 | 7/8 / 7/8 | 8/8 / 8/8 | 1.12 / 1.12 |
| task: gcd, popcount, bmax | 5/6 each | 6/6 | 1.17 / 1.17 / 1.33 |
| task: the other five | 6/6 each | 6/6 | 1.00 |

With 16 cells per path, the differences between paths are 1–2 cells. They are not statistically meaningful.

### Failure categories (all 4 failed attempts)

| category | stream | structured | ir |
|---|---|---|---|
| crash (emulation fault) | 1 | 0 | 0 |
| wrong_result | 1 | 0 | 0 |
| compile (QBE) | 0 | 0 | 2 |
| syntax/encoding, crosscheck, abi, oob, hang, format, subject timeout | 0 | 0 | 0 |

One of the two `compile` failures is really a model format error (prose before the code; see below). The IR
pipeline now labels that case `format`: this was fixed after the run, and a test was added. The recorded data
keeps the original label.

### Latency and size (medians per attempt; `summary.md`)

| path / ISA | wall s | first text s | first committed insn s | check s | final code bytes |
|---|---|---|---|---|---|
| stream x86_64 | 4.3 | 2.3 | 2.4 | 0.04 | 34 |
| stream aarch64 | 4.5 | 2.5 | 2.7 | 0.08 | 42 |
| structured x86_64 | 5.7 | 2.6 | – | 0.06 | 30.5 |
| structured aarch64 | 5.9 | 3.3 | – | 0.10 | 40 |
| ir x86_64 | 5.4 | 2.9 | – | 0.08 | 48.5 |
| ir aarch64 | 4.8 | 2.7 | – | 0.07 | 78 |

- **Streaming works live.** The first instruction was committed, encoded and GNU-as-verified a median of about
  0.17 s after the first text delta. That was a median of about 1.8 s before generation ended, and responses arrived in a
  median of 26 deltas. Stream wall time was the lowest of the three paths, because stream outputs are the
  shortest text. The structured JSON path is slower mainly because the schema makes the prompt larger.
- **QBE code is larger** than hand-written code, especially on AArch64 (median 78 bytes vs 40–42).
- The slowest attempt was the degenerate `bmax` stream attempt 2 (14.7 s, 2468 chars; see below).
- Total reported cost at list price was $1.56 for 52 calls.

## Notable transcripts

### 1. `bmax.stream.aarch64`: the hostile emulator caught an uninitialised-register bug (worst cell, 3 attempts)

Evidence: `cells/bmax.stream.aarch64/attempt1.*`. Attempt 1 compared the loop index against **x9, which
was never written**, instead of n in x1:

```
.Lcheck:
    cmp x1, x9
    b.lt .Lbody
```

It assembled cleanly, because it is a valid program. It then passed **17 of 143 cases**: the ones where the
garbage in x9 happened to end the loop. The other 126 faulted with `Invalid memory read (UC_ERR_READ_UNMAPPED)
at pc=0x100010`.

A harness that zero-fills registers would have let n=1 cases pass and could have hidden the bug. Here
`run_function`'s garbage-filled registers are what exposed it. In the recorded run it is classified `crash`.

Attempt 2 (repair) is a degenerate output: 68 `cmp x1, x1` lines chained through about 50 labels (54 label lines in total), no compare
against n, and the `mov x0, x2` removed. It returned the buffer pointer (`got 4198400` = 0x401000) for all 143
cases. Every line was valid assembly, so the stream assembler rightly accepted it, and the differential checker
caught it. Attempt 3 was correct (`cmp x3, x1`).

The failure text gave only `pc=0x100010`. Mapping the faulting pc back to the source line would probably have
made the first repair easier (see Suggestions).

### 2. `gcd.ir.x86_64`: a prose leak caught by the QBE parser

Evidence: `cells/gcd.ir.x86_64/attempt1.response.txt`. The response started with the sentence
`QBE temporaries can be assigned multiple times; QBE converts to SSA.` and then gave correct QBE. QBE rejected
it with `qbe:-:1: unknown keyword QBE`. The repair, which was the same code without the sentence, passed all 232
cases.

This is the only format slip in 52 responses. No response used Markdown fences.

### 3. `popcount.ir.aarch64`: a missing QBE class specifier

Evidence: `cells/popcount.ir.aarch64/attempt1.*`. The model wrote `%a = shr %x, 1` (no `=l`), and QBE reported
`qbe:-:3: invalid class specifier`. After adding `=l` on every line, the SWAR popcount passed all 81 cases.

### 4. Best cases

All 16 structured cells passed first try with no fences or format errors. The examples include the 14–18 KB
schema-driven JSON for the AArch64 `bubble_sort` (`ldp`/`stp` swap of adjacent pairs), in
`cells/bubble_sort.structured.aarch64/`.

In the stream path the model:

- used closed forms (`sum_to`: `lea rax,[rdi+1]; imul rax,rdi; sar rax,1`, 23 bytes);
- used SWAR bit tricks for `popcount` instead of `popcnt`/`cnt`, as the spec required (checked by grep: no
  response used either);
- wrote an early-exit bubble sort with a swapped flag on AArch64 (`cells/bubble_sort.stream.aarch64/final.disasm.txt`).

## Where the pipelines helped, and where they did not show up

- **Helped (observed):**
  - The garbage-register and fault-trapping emulator exposed a latent uninitialised-register bug (#1).
  - The differential checker caught a fully valid but semantically broken program (#1, attempt 2).
  - The QBE front end caught a format slip and a syntax slip, and the repair loop used its diagnostics well:
    both were fixed on the first retry.
- **Not exercised by real mistakes:**
  - streaming early abort;
  - the GNU as cross-check;
  - the ABI checks: no attempt touched a callee-saved register or the stack at all;
  - the OOB write guard;
  - the hang budget.

  These are covered only by the fake-subject tests: a mid-stream error stops the scripted subject at that chunk, and it SIGKILLs a fake `claude`
  executable before that executable's 5 s tail (the test asserts under 4 s), and ABI, OOB and hang verdicts are checked on crafted code. There was nothing
  for them to catch in this run. The run gives no evidence either way about their value against real model
  mistakes.
- **Hurt:** there was no observed case where a pipeline rejected correct model code. There were no
  false-positive encoder or cross-check rejections in 52 attempts. The only cost seen is the larger prompt on
  the structured path, which added about 1.3 s median latency.

## Limitations (honest)

- **Ceiling effect.** 8 small leaf-function tasks are too easy for this model (94% pass@1). The run cannot
  separate the paths statistically: 16 cells per path, and the differences are 1–2 cells. Each cell has 1 sample
  (no pass@k variance), there is 1 model, and the model uses default effort.
- **Test coverage limits.**
  - Reads past the buffer are not detected; only writes are guarded. This matters for `count_nonzero_prefix`
    and `bmax`.
  - The "no popcnt/cnt" rule is checked by grep, not by the harness.
  - `sum_to` inputs stop at n ≤ 1000, so closed forms with a multiplication overflow at large n are not
    penalised, which matches the spec.
- **Isolation.**
  - The subject still receives the account's email context item and sees its (neutral) cwd.
  - The Claude Code system prompt was replaced by a one-line `--system-prompt`, but the CLI may still add
    its own framing.
  - The model could not see the repo, tools or MCP (probe-verified).
- **Cost accounting.** Cost comes from the CLI's `result` event. A killed stream would report none, but no
  stream was killed in this run.
- **Classification.** Failure categories come from message text: emulator messages are matched by substring,
  because `Mismatch` only keeps `str(exc)`. The one prose leak on the IR path is recorded as `compile` (see
  above).

## Suggestions

- Include the faulting pc's source line in the repair feedback (`Program.text_at` on the stream path, and
  disassembly otherwise).
- Harder tasks to make the safety nets matter: functions that need callee-saved registers or a stack frame
  (recursion, >6 arguments, calls), strict alignment, wide immediates, and jump tables.
- Several samples per cell.

## Existing modules: problems found

No existing module was broken or modified. These are API observations only:

- `difftest.Mismatch` stores only the error string, so ABI, OOB and hang categories have to be recovered by
  matching messages.
- `buffer_differential` does not return the actual buffer, so the harness re-runs a failing case to show the
  model expected vs actual arrays.

## Reproduce

```
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/<stamp> --max-calls 195 --jobs 4   # live, uses quota
.venv/bin/python -m bca.liveeval summarize eval/runs/<stamp>
.venv/bin/python -m pytest -q tests/test_liveeval.py                                         # fake subject, no quota
```
