# TODO — analytics backlog

Further reporting on the recorded live-eval data (`eval/runs/`, ~13,300 attempts across ~30 models and
variants). Everything here is analysis of committed data — no model calls, no plan usage — except item 10, which
re-executes passing programs in the emulator. Each item lands as a script in `eval/` plus a section in a report.

## Done (`eval/analytics.py`, `reports/live-model-eval-analytics.md`)

1. **Statistical confidence.** Wilson 95% intervals on every pass rate; paired exact McNemar tests on the cells
   two models share, to say which ranking gaps are real; app-battery sample-to-sample variance from the three
   samples. → `eval/analytics.py`, `reports/live-model-eval-analytics.md`.
2. **Repair dynamics.** For cells that fail attempt 1: how often each model recovers, by failure category and by
   attempt number; "right first time" against "good debugger". Same script and report.
3. **Task difficulty and discrimination.** Per-cell pass rate across models; cells nobody passes (and whether any
   are suspect), cells everybody passes, and the cells that decide the ranking (item-rest correlation). Same
   script and report.
4. **ISA and path gap.** x86-64 vs AArch64 and stream / structured / QBE for every model in one cross-model view.
5. **One error classification across models.** Normalise failure messages into shared classes (encoding limits,
   invented mnemonics, ABI / callee-saved violations, format / JSON, QBE syntax, syscall misuse) and profile each
   model.
6. **Prompt-variant effects.** Paired deltas against baseline on the same cells: `misleading_hint`, `terse`,
   `assisted`, `declared`.

## Backlog

7. **Refusals.** Fable 5.1 (52), Opus 5.5 (56), Sonnet 5.5 via Copilot (52): which tasks and variants trigger
   them; read against the CVP caveat.
8. **Within-vendor generations.** Opus 4.8 → 5.5, Sonnet 4.5 → 5 → 5.5, Grok 4.5 → 4.6 → 4.7, GPT-5.5 → GPT-6
   (Luna / Sol / Astra).
9. **Efficiency.** Output tokens and wall time per passing cell; accuracy against tokens spent. Caveats: the
   Ollama-served Qwen models record no token counts; local latencies include queue waits.
10. **Quality of passing code.** Code size (recorded) plus executed instruction counts from re-running each
    passing program in the emulator: how fast correct solutions are, not only whether they pass.
11. **Harness and infrastructure audit.** One summary of false rejects found and fixed, infra error rates per
    vendor, and quota behaviour (now spread across per-run notes).

Optional: an interactive dashboard (filter by model, battery, path, ISA) over the same data.
