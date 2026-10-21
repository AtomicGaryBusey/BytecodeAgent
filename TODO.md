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
7. **Refusals.** Fable 5.1 (52), Opus 5.5 (56), Sonnet 5.5 via Copilot (52): which tasks and variants trigger
   them; read against the CVP caveat.
8. **Within-vendor generations.** Opus 4.8 → 5.5, Sonnet 4.5 → 5 → 5.5, Grok 4.5 → 4.6 → 4.7, GPT-5.5 → GPT-6
   (Luna / Sol / Astra).
9. **Efficiency.** Output tokens and wall time per passing cell; accuracy against tokens spent. Caveat: local
   latencies include queue waits (Ollama's `eval_count` gives the Qwen models' token counts).

## Backlog

10. **Quality of passing code.** Code size (recorded) plus executed instruction counts from re-running each
    passing program in the emulator: how fast correct solutions are, not only whether they pass.
11. **Harness and infrastructure audit.** One summary of false rejects found and fixed, infra error rates per
    vendor, and quota behaviour (now spread across per-run notes).

Optional: an interactive dashboard (filter by model, battery, path, ISA) over the same data.

## Proposed pick-up runs (flagged 2026-10-20, none run yet)

Gaps in the recorded data that small runs could fill. Each needs the user's go-ahead before it starts; call
counts are estimates (~1.1 attempts per cell for top models). After any of them: by-hand false-reject review,
then regenerate the tables and `eval/analytics.py`.

**Worth filling (they limit published conclusions)**

- [ ] **App battery for the models tied at the top**: Codex GPT-6 Sol, Grok 4.7, Grok 4.6 have function results
  level with Astra but no app results (only the 16-cell hex apps). `--tasks apps --samples 3`, 96 cells each,
  ~105 calls each. Plans: ChatGPT Pro (Sol), SuperGrok weekly allowance (Groks).
- [ ] **Kimi K3 app samples 2 and 3**: its app row is † (sample 1 only). 64 cells, ~70 calls, after the next
  7-day window.
- [ ] **`misleading_hint` and `terse` on non-Claude models**: both variants (adversarial, 60 cells) ran only on
  four Claude models, so "misleading hints change no model significantly" and the variant's refusals are
  Claude-only findings. Astra, Sol, Grok 4.7, Kimi K3: ~65 calls per model per variant (~520 in all).
- [ ] **Antigravity's last 61 cells** (adversarial 60 + long 1): the hourly loop covers it; quota only.

**Optional (statistical power)**

- [ ] **Second sample of the long battery for the top seven** (Astra, Opus 5.5, Sol, Grok 4.7 build-fast,
  Fable 5.1, Grok 4.7, Grok 4.6): 36 of the 87 cells that split the top ten are long-battery cells. 7 × 54
  cells, ~380 calls. Sample the whole battery, not the splitting cells, to avoid post-hoc selection bias.
- [ ] **`declared` hex variants**: 16 cells is underpowered, and Astra lacks `declared_annotated` (16 cells,
  ~17 calls). A real test needs more samples of the same cells (e.g. two more each for Opus 5.5 and Astra,
  ~130 calls).
- [ ] **`assisted` on a mid-tier model**: tested only on weak models. Opus 4.8 and Sonnet 5 (78%) fail mostly on
  QBE type classes, which the assisted QBE cheat sheet targets. Rounds 1 + 2, 110 cells each, ~140 calls each
  (Claude plan).

**Hex-path holes**

- [ ] **No hex results for Gemini 3.8 Flash High, Kolibri-1 or Claude Sonnet 5**: hex functions (16 cells) each;
  whole-program hex follows only at ≥ 12/16 within four (so Haiku and MAI-Code missing from hex apps is by
  design). Gemini after its function run completes; Kolibri ~5 GPU hours.

**Not fillable or not worth it (note in reports, do not run)**

- Codex GPT-5.5 retired 2026-10-14 (no app results possible); Claude Sonnet 4.5 was retiring from 2026-09-29
  (check availability first). Generational app comparisons (Opus 4.8 → 5.5, Sonnet 4.5 → 5.5) stay missing;
  Opus 4.8 and Sonnet 5 have no app runs.
- Trimmed Qwen runs stay partial (user decision 2026-10-01).
- Token coverage: Qwen3.6 thinking-on 7% (times out before answering), Kimi K2.8 88% (its CLI prints only at
  call end); a re-run would behave the same.
- Grok 4.7's four unrecovered long cells hit the 400 s limit on every attempt: a genuine result.
