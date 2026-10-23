# Adjustments to this run

## hexdump.asm.aarch64.s2 — harness false reject; superseded and re-run (2026-10-22)

Attempt 1 was refused at link time: "section .bss: unsupported alignment 65536". The program puts its buffer in
`.bss` after `.align 16`, which on AArch64 means 2^16 = 64 KiB. GNU ld accepts any power-of-two section alignment,
and the prompt sets no limit, but the harness linker refused anything above a 4 KiB page: a harness false reject.
With only that line changed to a 4 KiB alignment (`.p2align 12`, neutral for a zero-initialised buffer), the recorded
answer passes all 42 test runs.

Fixed in bca/image.py (the linker accepts section alignments up to 16 MiB and starts each segment on its largest
section alignment; tests/test_process.py::test_section_alignment_above_a_page_links_like_gnu_ld fails before the
fix and passes after). The asm and stream paths share this linker, so the fix applies to both.

The cell was moved to `superseded/` (with its results/attempts lines; it had passed on attempt 2) and re-run from
scratch with the fixed linker, so its verdict comes from the same rules as every other cell.

Re-run result (2026-10-22): passed on attempt 1.

## Review (2026-10-22)

All other failed attempts were reviewed by hand: one, `hexdump.stream.aarch64.s1` attempt 1, stopped at
`ccmp w0, #0x7f, #2, hs` (AArch64 `ccmp` takes an immediate of 0-31; GNU as rejects it too). Genuine; the cell passed
on attempt 2. Two transient grok service errors were retried and never scored.
