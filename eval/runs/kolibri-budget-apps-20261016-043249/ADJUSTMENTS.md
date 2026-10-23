# Adjustments to this run

## sort_lines.asm.aarch64.s3 — superseded and re-run (2026-10-23)

Attempt 1 was refused at link time: "section .bss: unsupported alignment 65536" (`.align 16` on AArch64 is 64 KiB,
which GNU ld accepts). The harness linker limit behind it was found to be a false reject in the Grok 4.6 review and
fixed on 2026-10-22 (bca/image.py; see `apps-grok_grok-4.6-20261022-191933/ADJUSTMENTS.md`). This cell's verdict could
not change (the 31-line program never sorts and reads with a zero length), but attempts 2-4 had received link
feedback instead of wrong-output feedback. As with earlier fixes, the cell was moved to `superseded/` (with its
results/attempts lines; it had failed all four attempts) and re-run from scratch with the fixed linker, on the same
server stack (vLLM 0.29.0 + aleph-alpha-inference 1.0.0, budget mode, the container restarted unchanged).

Re-run result (2026-10-23): failed all four attempts, genuinely: `.word` in `.bss` and instructions in `.bss` (both
forbidden by the prompt), and wrong output on a 1-byte input twice. Kolibri-1's totals are unchanged (3/96 first
attempt, 16/96 within four).
