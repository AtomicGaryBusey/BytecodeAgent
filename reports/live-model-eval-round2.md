# Live-model evaluation, round 2: setup and smoke test

**Status:** the harness and tasks are ready and validated with the fake subject. The live run so far is
limited to an 8-call smoke test. The full round-2 matrix has **not** been run yet.

**Why round 2 exists.** Round 1 (`reports/live-model-eval.md`) scored 45/48 pass@1 on 8 easy leaf kernels.
The streaming early abort, the GNU as cross-check, the ABI checks and the OOB guard never caught a real model
mistake. Round 2 uses tasks that need the things those checks exist for: stack frames, callee-saved
registers, calls, stack-passed arguments, SIMD, bounded reads, overlapping writes, 32-bit arithmetic, flags,
and unsigned full-range edge cases.

## Tasks (`bca/liveeval/tasks2.py`)

Every task has:

- a natural-language spec;
- an exact C prototype;
- a C reference (host gcc, `-fwrapv`);
- deterministic, bounded inputs;
- a checker.

Memory tasks use the new `difftest.memory_differential`. It checks the return value and the whole memory block,
with a write guard and, where the spec bounds reads, a read guard.

| task | what it stresses | cases | stream | structured | ir (QBE) |
|---|---|---|---|---|---|
| `fib_rec(n)` | naive double recursion (n ≤ 20). **Required:** a call to its own entry, checked by disassembly. Every entry, recursive ones included, is alignment-checked | 21 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `map_sum(a, n, f)` | Σ f(a[i]) through a **hostile callback** at `CALLBACK_BASE` (details below); read guard on `a` | 50 | ✓ ✓ | ✓ ✓ ¹ | ✓ ✓ |
| `sum8(a..h)` | a−2b+3c−4d+5e−6f+7g−8h, wrapping. On x86-64 args 7 and 8 are on the **stack** | 232 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `dot_i32_simd(a, b, n)` | int32 dot product to int64, n ≤ 1000 (not a multiple of 4), only 4-byte aligned. **Required:** SIMD (disassembly). Read guard on both arrays; strict SSE alignment | 35 | ✓ ✓ | N/A ² | N/A ³ |
| `binary_search(a, n, key)` | index or −1, n ∈ [0, 200], values up to INT64_MIN/MAX; **read guard** (n = 0 reads nothing) | 60 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `memmove_i64(dst, src, n)` | overlap in both directions, disjoint, same; **write guard** = dst range; read guard = src ∪ dst | 47 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `crc32(p, n)` | bitwise reflected CRC-32, bytes, 32-bit ops, result must be zero-extended | 34 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `mulhi_u64(a, b)` | high 64 bits of the unsigned 128-bit product (QBE must split into 32-bit halves) | 344 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `add_overflow(a, b, out)` | wrapped sum to `*out`, returns the signed-overflow flag; 8-byte write/read window | 200 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `isqrt(n)` | floor √ of an unsigned 64-bit value, incl. 2^64−1 and k²±1 near 2^32 | 283 | ✓ ✓ | ✓ ✓ | ✓ ✓ |
| `collatz_max(n)` | max Collatz steps for k ≤ n ≤ 3000; nested loops, 10 M instruction budget | 26 | ✓ ✓ | ✓ ✓ | ✓ ✓ |

In each path column, the two marks are x86-64 and AArch64. That gives **62 cells**: 10 tasks × 6 cells, plus
2 cells for `dot_i32_simd`.

1. x86 structured had no indirect call. It was added: `{"op": "call", "target": "r12"}` (also `jmp`).
2. Neither structured encoder has vector registers or instructions in its schema.
3. QBE IL has no vector types.

The reference checks behave as intended:

- Every C reference, lowered by clang for both ISAs, passes its own checker (22 tests).
- The CRC-32, isqrt, mulhi, fib and Collatz references are also checked against Python models.
- clang's scalar x86 `dot_i32_simd` passes the differential test but fails the SIMD requirement.

**Hostile callback** (`bca.emu.hostile_callback`). It computes f(x) = 3x + 1 (wrapping). On every call it:

- overwrites all caller-saved GPRs with per-call-varying junk (x86: rcx, rdx, rsi, rdi, r8–r11; AArch64: x1–x18);
- overwrites the vector registers (x86: xmm0–15; AArch64: v0–v7, v16–v31);
- overwrites the flags;
- overwrites the 128 bytes below its sp.

It preserves only what the ABI requires. A code hook at its entry raises `MisalignedCall` when the stack is
misaligned: rsp % 16 ≠ 8 or DF set on x86, sp % 16 ≠ 0 on AArch64. The error names the address of the call instruction.

## Prompts (`bca/liveeval/prompts.py`)

Round-2 prompts keep the round-1 structure. Round-1 prompts are byte-for-byte unchanged. Round 2 adds:

- **ABI_EXTRA per ISA:**
  - stack-argument locations;
  - the caller-saved set that a call clobbers;
  - 16-byte alignment at every call, with how to re-align;
  - anything below sp may be overwritten by a callee;
  - saving x30 on AArch64;
  - DF clear.
- **Per-task call rules** that replace "no calls":
  - stream: `call fib_rec` / `bl fib_rec`, or a call through the pointer;
  - QBE: `call $fib_rec(...)` / `call %f(...)`;
  - structured: the call and blr forms.
- **QBE hints** where they are needed (`loadub`, `w` class, no mulhi).

## Harness changes (all backward compatible)

**`bca.emu.run_function`**

- **Stack arguments** follow the ABI: beyond 6 args on x86-64, beyond 8 on AArch64. The callee may write its own
  argument slots but not the rest of the caller's frame.
- **`read_guard`** raises `OutOfBoundsRead`. `guard` and `read_guard` now accept a list of windows.
- **`aligned_entries=[addr]`** raises `MisalignedCall`. Unicorn's TB cache is flushed for those addresses so the hook
  also fires on pooled machines. Without this flush it silently did not fire (found by the tests).
- **`strict_sse_alignment`** (x86) raises `MisalignedAccess` for legacy-SSE or `vmovdqa`-type 16-byte operands that
  are not 16-byte aligned. The effective address is computed from the decoded operand, because Unicorn splits
  16-byte accesses into two 8-byte ones and never faults on them itself.
- **Pooled machines** now also cover runs with `segments`. Writable segments are restored on each run.
- **New exceptions:** `InstructionLimit` (hang), `OutOfBoundsRead`, `MisalignedCall`, `MisalignedAccess`.
- **Every `EmulationError`** now has:
  - `.category`: crash, abi, oob_write, oob_read, misaligned_call, misaligned_access or hang;
  - `.pc`: an address inside the faulting instruction.
- **Callback helpers:** `CALLBACK_BASE`, `hostile_callback(isa)`, `callback_segments(isa)`.

**`bca.difftest`**

- `Mismatch` has new fields:
  - `kind`: wrong_result or the error category;
  - `pc`;
  - `expected_data` and `actual_data`, so a failing buffer is no longer re-run;
  - `case`.
- `buffer_differential` gains `read_guard=` and `**run_kwargs`.
- New: `Ptr`, `MemCase`, `memory_differential` (any signature over a memory block, with stack args).

**`bca.liveeval`**

- **Classification** uses `Mismatch.kind` (`classify_mismatch`). The text matcher `classify_error` is kept only as
  a fallback.
- **New attempt categories:** `requirement`, `oob_read`, `misaligned_call`. `oob` keeps its round-1 meaning: an
  out-of-bounds write.
- **Requirement checks:** `calls_own_entry` and `uses_simd`. They run before the differential test and return a
  specific repair message.
- **Repair feedback quotes the faulting source line.** It shows the call instruction for a misaligned call:
  - stream: `line N: \`...\``;
  - structured: `instruction N: {json}`;
  - IR: the disassembled instruction.
- **Task selection:** `--tasks round1|round2|all|<names>`. The default is still round1.
- **`--dry-run`** prints the cells and the worst-case call count.
- **N/A cells** are skipped by `run_matrix` and shown as `N/A` by `summarize`, which handles both rounds.

**Encoders**

- `bca.structured.x64`: indirect `call` and `jmp` through a 64-bit register.
- `bca.stream`, **bug fix**: x86 memory operands that use the same register as base and index were refused or
  falsely rejected. `[rdx + rdx*2 + 1]` failed with "invalid address expression". `[rsi + rsi]` was sent to
  keystone as `[rsi*2]`, so GNU as disagreed and the cross-check rejected correct code. The fix recovers
  base=index=r from the source text, and GNU as confirms every form in the tests. Round 1 never hit this, but
  the `lea r, [r + r*2]` (×3) idiom is likely in round 2, for example in `collatz_max`.

## Tests

`tests/test_liveeval_round2.py` adds 137 tests, all with the fake subject and no quota. With
`tests/liveeval_solutions.py`, the full suite is **690 passed**: the 553 existing tests plus 137 new ones.

- **Every feasible cell (62) passes with a hand-written correct answer through its real pipeline.** The structured
  answers are converted from the assembly. This proves each task can be solved on each path and that references
  and checkers agree.
- **18 canned wrong answers each hit a specific category:**
  - `misaligned_call` ×3: the x86 callback, the AArch64 callback, and a recursive x86 call;
  - `abi`: x19 clobbered;
  - `requirement` ×2: iterative fib, scalar dot product;
  - `oob_read` ×2: a NEON loop past the end, and `hi = n` in the binary search;
  - `oob`: a memmove terminator store;
  - `hang`: Collatz;
  - `crosscheck`: `cmp sil, ch`, with the stream killed mid-output;
  - `syntax`: a bad line mid-stream, killed early;
  - `crash`: `movdqa` on 4-byte-aligned data;
  - `wrong_result` ×5, including a sum kept in a caller-saved register or below rsp across the hostile call, and
    a 64-bit `mvn` in the structured CRC.
- Unit tests cover:
  - stack arguments and argument-slot ownership;
  - read and write window lists;
  - error categories and pc;
  - hostile-callback semantics;
  - the misaligned-call site;
  - strict SSE alignment;
  - pooled segment restore;
  - `Mismatch` kinds and data;
  - `memory_differential`;
  - the x86 indirect call;
  - the same-register-base/index fix;
  - classification that uses kind, not message text;
  - the prompts: round 1 unchanged, round 2 extras;
  - repair-round call-site feedback;
  - N/A skipping and the summary, and the CLI dry run.

## Live smoke test (8 calls, the hard cap)

Run dir: `eval/runs/round2-smoke-20260927-145143/` (`summary.md` included). Subject: `claude-opus-5-5`, with the
same isolation flags as round 1.

| cell | result | notes |
|---|---|---|
| fib_rec.stream.x86_64 | pass@1 | push rbx/r12 + `sub rsp, 8`, so the stack is aligned at both calls |
| fib_rec.stream.aarch64 | pass@1 | `stp x29,x30,[sp,#-32]!`, x19/x20 saved |
| map_sum.stream.x86_64 | pass@1 | 5 pushes (aligned); state in rbx/r12–r15 |
| map_sum.stream.aarch64 | pass@1 | 48-byte frame, `ldr x0,[x19],#8`, `blr x21` |
| dot_i32_simd.stream.x86_64 | pass@1 | `pmovsxdq`/`pmuldq`/`paddq`, 4 per iteration in 2 accumulators, scalar tail |
| dot_i32_simd.stream.aarch64 | pass@1 | `ld1 {v.4s}` + `smlal`/`smlal2`, scalar tail |
| crc32.structured.x86_64 | pass@1 | 32-bit ops, `and r8d, -306674912`, `not eax` zero-extends |
| isqrt.ir.aarch64 | pass@1 | bit-by-bit (digit) square root with unsigned compares |

All 8 passed on the first attempt, and no safety net fired. The cost reported by the CLI was about $0.21 in
total. The median wall time was about 6 s; the SIMD x86 attempt took 10.7 s.

This is a warning sign: the ceiling effect may carry over to round 2. The full matrix will show whether the
harder tasks produce real catches. The smoke covered only 8 of 62 cells, with a single sample each.

## Full round-2 run: budget and command

- **Worst case:** 62 cells × 4 attempts = **248 calls**.
- **Expected:** round 1 used 52 calls for 48 cells, 1.08 attempts per cell. Round 2 should be harder: at 1.1–1.5
  attempts per cell, about **70–95 calls**. The smoke test (8/8 at 1.0) points to the low end. At round 1's
  ~$0.03 per call, and with the structured prompts about 16 KB, that is roughly $2–3, and at most about $8.

Launch in a fresh run dir. The smoke dir must not be reused: its cells are marked complete with fewer attempts.

```
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/round2-$(date +%Y%m%d-%H%M%S) \
    --tasks round2 --max-calls 248 --jobs 4
.venv/bin/python -m bca.liveeval summarize eval/runs/round2-<stamp>
```

`--max-calls` is persisted in `calls.log`, so it caps the total even across restarts. To check the cells and the
cap without making calls, add `--dry-run`.

## Limitations and what was not done

- **Recursion check.** It is static: it looks for a direct `call`/`bl` to the entry, or to the target of an
  entry trampoline. A call to a label placed after the prologue does not count, and the spec says so.
- **SIMD check.** It is a mnemonic and operand pattern: an x86 packed add/sub/mul/madd/hadd on xmm/ymm, or NEON
  arithmetic on arranged `v` operands. Vector code that uses only shuffles and moves would not count.
- **`map_sum` does not verify the call order or count.** The callee is pure. Because f is opaque in the
  prompt, inlining 3x+1 is only possible by guessing. The repair feedback shows expected sums, which could hint
  at f.
- **`strict_sse_alignment`** is on for every round-2 x86 check. It costs one Python hook per data or stack access
  (the `fib_rec` check takes about 0.4 s).
- **The structured and IR paths cannot do SIMD** (N/A above). Adding NEON or SSE families to the structured
  encoders would be a separate project.
- **Isolation** has the same caveats as round 1: the account email context item and a visible neutral cwd.

## Full run results (2026-09-27)

Run directory: `eval/runs/round2-20260927-150117/` (tables in `summary.md`). Subject:
`claude-opus-5-5` via isolated `claude -p`; 64 calls of the 248-call cap; reported list cost $2.23.

| path | cells | pass@1 | pass within 4 | mean attempts |
|---|---|---|---|---|
| stream | 22 | 22/22 | 22/22 | 1.00 |
| structured | 20 | 20/20 | 20/20 | 1.00 |
| ir (QBE) | 20 | 18/20 | 20/20 | 1.10 |
| **total** | **62** | **60/62 (97%)** | **62/62 (100%)** | 1.03 |

By ISA: x86-64 30/31 and AArch64 30/31 on the first attempt. Four cells (SIMD on structured and
QBE) are N/A.

**Failures.** Both first-attempt failures were the same QBE syntax mistake, `invalid class
specifier` on line 3 (`sum8.ir.x86_64`, `memmove_i64.ir.aarch64`), each fixed by the first repair.

**Safety net.** No live attempt triggered a misaligned call, callee-saved clobber, out-of-bounds
read or write, hang, missing-requirement check, GNU as rejection or streaming early abort. The
model aligned the stack for the hostile callback, kept state in callee-saved registers across it,
recursed as required, used SIMD (`pmuldq`, `smlal`/`smlal2`), respected read/write bounds, handled
the full 64-bit range in `isqrt`/`mulhi_u64`, and passed stack arguments correctly. Every check is
verified to fire by the fake-subject suite (18 wrong solutions, one per category), and no check
rejected correct model code in 64 live attempts.

**Conclusion.** Across both rounds (110 cells, 116 calls) the subject passed 105/110 on the first
attempt and 110/110 within four; every miss was a syntax or format error caught by the assembler or
compiler, plus one uninitialised-register bug caught by the garbage-filled differential run (round
1). At this task difficulty the pipelines' main measured value is fast, precise error feedback and
guaranteeing that what passes is correct; distinguishing the paths or stressing the safety net
needs harder, longer programs, weaker subjects, or adversarial prompts.
