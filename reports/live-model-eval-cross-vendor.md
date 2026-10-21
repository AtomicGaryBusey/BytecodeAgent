# Live-model eval: models from five vendors

> **Account caveat.** The Claude account is enrolled in Anthropic's **Cyber Verification Program** and the
> ChatGPT account in OpenAI's **Daybreak** (Blue tier; no Red / `*-cyber` models); both relax cyber safeguards,
> so treat those rows as upper bounds for ordinary accounts. The Kimi, Google and xAI accounts have no known
> equivalent. Plans: Claude Max 20x, ChatGPT Plus, Kimi Allegretto, Google AI Plus (2 TB), SuperGrok.
> Method, fairness contract and known limits: [cross-vendor-methodology.md](cross-vendor-methodology.md).

Every model got the same 224 cells: rounds 1 + 2, the long-program battery and the adversarial battery
(baseline prompts), across the stream, structured-JSON and QBE-IR paths on x86-64 and AArch64, with up to
four attempts and repair feedback. Each ran through its vendor's own coding CLI with zero tools, the same
system and task prompts, and its CLI's default effort (as the Claude runs did). Runs date from
2026-09-27/29; CLI versions: claude 2.1.283-2.1.285, codex 0.157.1, kimi 2.1.1, agy 1.2.12, grok 1.0.41.

## Results (first-attempt passes per battery)

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Codex GPT-6 Astra | 46 | 61 | 54 | 60 | **221/224 (99%)** | 224/224 (100%) | 1.01 |
| Claude Opus 5.5 | 45 | 60 | 53 | 57 | **215/224 (96%)** | 224/224 (100%) | 1.04 |
| Codex GPT-6 Sol | 43 | 62 | 48 | 60 | **213/224 (95%)** | 224/224 (100%) | 1.05 |
| Grok 4.7 build-fast | 47 | 60 | 49 | 57 | **213/224 (95%)** | 223/224 (100%) | 1.08 |
| Claude Fable 5.1 | 44 | 62 | 52 | 54 | **212/224 (95%)** | 223/224 (100%) | 1.08 |
| Grok 4.7 | 48 | 59 | 48 | 56 | **211/224 (94%)** | 220/224 (98%) | 1.10 |
| Grok 4.6 | 45 | 59 | 50 | 57 | **211/224 (94%)** | 223/224 (100%) | 1.07 |
| Codex GPT-5.5 | 47 | 57 | 45 | 58 | **207/224 (92%)** | 224/224 (100%) | 1.08 |
| Kimi K3 | 45 | 58 | 45 | 56 | **204/224 (91%)** | 224/224 (100%) | 1.11 |
| Claude Sonnet 4.5 | 48 | 55 | 44 | 56 | **203/224 (91%)** | 221/224 (99%) | 1.14 |
| Codex Daybreak Blue | 45 | 55 | 42 | 59 | **201/224 (90%)** | 224/224 (100%) | 1.12 |
| Claude Sonnet 5.5 | 47 | 56 | 45 | 53 | **201/224 (90%)** | 223/224 (100%) | 1.12 |
| Grok 4.5 | 47 | 55 | 41 | 54 | **197/224 (88%)** | 222/224 (99%) | 1.16 |
| GPT-6 Luna via Copilot | 42 | 53 | 31 | 50 | **176/224 (79%)** | 224/224 (100%) | 1.29 |
| Claude Sonnet 5 | 40 | 49 | 41 | 45 | **175/224 (78%)** | 222/224 (99%) | 1.30 |
| Claude Opus 4.8 | 35 | 48 | 49 | 43 | **175/224 (78%)** | 222/224 (99%) | 1.27 |
| Claude Sonnet 5.5 via Copilot | 45 | 55 | 19 | 51 | **170/224 (76%)** | 219/224 (98%) | 1.35 |
| Kimi K2.8 (for-coding) | 37 | 46 | 28 | 46 | **157/224 (70%)** | 213/224 (95%) | 1.52 |
| Codex GPT-6 Luna | 33 | 41 | 10 | 46 | **130/224 (58%)** | 209/224 (93%) | 1.71 |
| Claude Haiku 4.5 | 30 | 34 | 17 | 35 | **116/224 (52%)** | 158/224 (71%) | 2.19 |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 26 | 34 | 12 | 35 | **107/224 (48%)** | 151/224 (67%) | 2.29 |
| Gemini 3.8 Flash High (Antigravity) † | 46 | 60 | 47/53 | — | **153/163 (94%)** | 163/163 (100%) | 1.07 |

Codex Daybreak Blue includes one reviewed harness false reject counted as a pass, Grok 4.7 build-fast
three and GPT-6 Luna via Copilot one; Claude Sonnet 5.5 via Copilot is one lower than recorded (a pre-fix
output-cap do-over counted as the failure it would be now; see the false-reject review below). 
Kimi K2.8 (kimi-for-coding), completed 2026-10-17: 157/224 first try. 40 of its 128 failed attempts are
wall-clock timeouts: its median call took 113 s against the 180 s round-1/2 limit (Kimi K3: 51 s). Kimi's CLI
prints the answer only when the call ends, so a timeout carries no text; these are the same timeout rule as
every CLI vendor, not stalls. Failures reviewed by hand: no harness false rejects (QBE compares without a
result class, `xor eax, 0` used to zero a register, malformed JSON, unencodable AArch64 immediates).

† Partial, quota-paced (Google AI Plus): rounds 1 + 2 complete, long 53/54, adversarial not yet run; the hourly resume loop adds cells as the weekly quota allows. Like for like on the complete batteries, rounds 1 + 2: **106/110 (96%)**, level with Claude Fable 5.1 (106) and behind Codex GPT-6 Astra, Grok 4.7 and Grok 4.7 build-fast (107 each). Its first-attempt failures were reviewed by hand: no harness false rejects (hex literals in QBE, `%x =l %y` without `copy`, malformed JSON, an invented `subl` opcode, and one 400 s timeout spent thinking: 62,910 of 65,532 output tokens).

## By emission path (first-attempt passes, complete runs)

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Codex GPT-6 Astra | 76/76 (100%) | 74/74 (100%) | 71/74 (96%) |
| Claude Opus 5.5 | 74/76 (97%) | 71/74 (96%) | 70/74 (95%) |
| Codex GPT-6 Sol | 73/76 (96%) | 71/74 (96%) | 69/74 (93%) |
| Grok 4.7 build-fast | 75/76 (99%) | 72/74 (97%) | 66/74 (89%) |
| Claude Fable 5.1 | 72/76 (95%) | 69/74 (93%) | 71/74 (96%) |
| Grok 4.7 | 76/76 (100%) | 71/74 (96%) | 64/74 (86%) |
| Grok 4.6 | 73/76 (96%) | 73/74 (99%) | 65/74 (88%) |
| Codex GPT-5.5 | 72/76 (95%) | 71/74 (96%) | 64/74 (86%) |
| Kimi K3 | 75/76 (99%) | 66/74 (89%) | 63/74 (85%) |
| Claude Sonnet 4.5 | 74/76 (97%) | 67/74 (91%) | 62/74 (84%) |
| Codex Daybreak Blue | 71/76 (93%) | 69/74 (93%) | 61/74 (82%) |
| Claude Sonnet 5.5 | 73/76 (96%) | 68/74 (92%) | 60/74 (81%) |
| Grok 4.5 | 73/76 (96%) | 71/74 (96%) | 53/74 (72%) |
| GPT-6 Luna via Copilot | 67/76 (88%) | 61/74 (82%) | 48/74 (65%) |
| Claude Sonnet 5 | 63/76 (83%) | 53/74 (72%) | 59/74 (80%) |
| Claude Opus 4.8 | 63/76 (83%) | 66/74 (89%) | 46/74 (62%) |
| Claude Sonnet 5.5 via Copilot | 61/76 (80%) | 62/74 (84%) | 47/74 (64%) |
| Kimi K2.8 (for-coding) | 67/76 (88%) | 52/74 (70%) | 38/74 (51%) |
| Codex GPT-6 Luna | 60/76 (79%) | 45/74 (61%) | 25/74 (34%) |
| Claude Haiku 4.5 | 61/76 (80%) | 54/74 (73%) | 1/74 (1%) |
| Microsoft MAI-Code 1.1 Flash (Copilot) | 59/76 (78%) | 44/74 (59%) | 4/74 (5%) |

## Findings

- **Codex GPT-6 Astra leads at 99%** (221/224; 100% on stream and structured, 224/224 within four attempts),
  ahead of Opus 5.5 (96%), Grok 4.7 build-fast and Fable 5.1 (95%), Grok 4.7 (94%) and Kimi K3 (91%).
- **Within a vendor, model choice matters as much as vendor**: Codex spans 58% (Luna) to 99% (Astra) on
  the same CLI, account and prompts.
- **The IR path separates models most.** Raw assembly (stream) is easy for nearly everyone (Grok 76/76);
  QBE punishes models that don't know it: Haiku 1/74, Luna 25/74, against 95-96% for Opus and Fable.
  Typical QBE errors: missing result classes (`%x = add`), invented opcodes (`csgt`, `mull`, `shrl`,
  `mulw`), block-argument SSA, and hex literals (QBE takes decimal only; the prompt does not say so).
- **Codex GPT-6 Luna (58%) is a real outlier, not a harness artifact.** Reviewed by hand: invalid QBE (46
  compile failures), structured-x86 schema violations, `xor reg, 0` used to "zero" a register (a no-op; 6
  cells, all failed), and calling-convention mistakes. Luna ran at medium effort, Daybreak Blue at low, on
  the same CLI with byte-identical prompts, so effort does not explain the gap.
- **Grok 4.7 is slow, not wrong**: 10 of its 13 first-attempt failures and all 4 final failures are
  timeouts (180 s; 400 s on the long battery) with the model still reasoning at the limit. Every model has
  the same limits; a longer limit would likely raise Grok's long-battery and IR scores.
- **Grok 4.7 build-fast matches Grok 4.7's accuracy** (95% vs 94%) in a fraction of the time (the whole
  battery in ~70 minutes, 3 timeouts vs 10), so the fast build loses little here.
- **Newer is not always better, and older is not always worse.** Sonnet 4.5 (91%, retired 2026-09-29)
  edges out Sonnet 5.5 (90%) and far exceeds Sonnet 5 (78%); Opus 4.8 (78%) trails Opus 5.5 (96%) by 18
  points, mostly on QBE (46/74: 23 answers omit parameter types the prompt requires). Grok descends
  4.7 / 4.6 (94%) to 4.5 (88%); Codex GPT-6 Sol (95%) and GPT-5.5 (92%) sit between Astra and Luna.
- **The front end matters as much as the effort label.** GPT-6 Luna scores 58% through Codex and 79% through
  GitHub Copilot, with byte-identical prompts, zero tools and the same served effort (medium). Through Copilot
  it reasoned 2.7x as many tokens on first attempts (3.4x on the long battery, where its passes tripled, 10 to
  30); Codex's catalog defaults (low verbosity, priority tier) or the two services' budgets for "medium" differ.
  Both are each CLI's own defaults, as the fairness contract requires: an effort label does not carry across
  front ends. **The direction depends on the model:** Claude Sonnet 5.5 goes the other way, 90% through `claude -p`
  and 76% through Copilot (170/224; long battery 45 to 19), again with byte-identical prompts and no tools.
  Through Copilot it opens 39 of 224 first answers with a prose plan ("Plan:", "Design:", "Register plan:")
  against 1 through `claude -p`, and 9 first answers carry stray tool-call markup (`<invoke ...>`,
  `</parameter>`) although the request has no tools; those account for most of its 42 cells that flip to a
  first-attempt failure (format 16, syntax 10, compile 9). Neither route shows reasoning tokens and output
  lengths are alike (median 299 vs 308 tokens). The routes differ in Claude Code's own ~350 tokens of framing
  (not visible to us) and in effort (Copilot sends `medium`; `claude -p` uses its unexposed default); either
  can explain the stricter code-only answers through `claude -p`.
- **Microsoft MAI-Code 1.1 Flash** (Microsoft's own model, reached through GitHub Copilot Pro, the only
  Copilot-exclusive model) scores 48% first attempt (107/224) and 67% within four attempts, between Haiku 4.5 and
  Luna via Codex. Raw assembly is its strength (stream 78%); QBE is almost entirely beyond it (4/74: 261 compile
  failures, mostly `%x = copy 0` without a result class, and invented opcodes such as `addl`), and its structured
  answers are often malformed JSON. On the app battery (whole programs) it managed 23/96 first attempt and 64/96
  within four (two cells re-run after a stream fix; the totals did not change): the only model tested so far that does not
  eventually write every program.
- **Fable 5.1's** one final failure is a refusal: Anthropic's cyber classifier blocked
  `fill_down.ir.x86_64` on all four attempts, even on the CVP account.

## Local open-weight models (DGX Spark)

Open-weight models served on this machine, an NVIDIA DGX Spark (GB10, 128 GB unified memory), by Ollama 0.30.10
(`local:<model>`; setup and isolation in [cross-vendor-methodology.md](cross-vendor-methodology.md#local-models-localmodel))
or, for Aleph Alpha Kolibri-1, by vLLM 0.29.0 in token-budget mode (`vllm:<model>@budget`: each call's limit is
an output-token budget, the most any vendor model used for one call in that battery, instead of wall-clock time;
see the methodology report).
Same prompts, same zero-tool contract, same per-call time limits and attempts as every vendor subject; each model's
own Modelfile sampling; `num_ctx` set explicitly to the native window or 65536, whichever is smaller. Weights are
quantised (Q4_K_M or Q8_0) and every model shares the one GPU with calls serialised, so the time limits count only
a call's own model time, but recorded latencies include queue waits and are not comparable with the vendor runs.
These results say what such a desktop setup can do, not what the same models do at full precision elsewhere.

| Model | Round 1 (48) | Round 2 (62) | Long (54) | Adversarial (60) | Pass@1 | Within 4 | Mean attempts |
|---|---|---|---|---|---|---|---|
| Aleph Alpha Kolibri-1 FP8, token budget (local) | 20 | 21 | 1 | 16 | **58/224 (26%)** | 97/224 (43%) | 2.99 |
| Qwen3-Coder-Next Q4 (local) | 5 | 5 | 0 | 2 | **12/224 (5%)** | 31/224 (14%) | 3.73 |
| Qwen3.6-35B-A3B Q8, thinking on (local) † | 4 | 5 | — | — | **9/110 (8%)** | 15/110 (14%) | 3.68 |
| Qwen3-30B-A3B Instruct 2507 Q8 (local) † | 5 | 1 | 0/5 | — | **6/115 (5%)** | 13/115 (11%) | 3.75 |
| Qwen3.6-35B-A3B Q8, thinking off (local) † | 1 | 3 | — | — | **4/110 (4%)** | 15/110 (14%) | 3.77 |
| Qwen2.5-14B Q4 (local) † | 1 | 2 | 0/4 | — | **3/114 (3%)** | 5/114 (4%) | 3.89 |

| Model | Stream | Structured | IR (QBE) |
|---|---|---|---|
| Aleph Alpha Kolibri-1 FP8, token budget (local) | 36/76 (47%) | 22/74 (30%) | 0/74 (0%) |
| Qwen3-Coder-Next Q4 (local) | 12/76 (16%) | 0/74 (0%) | 0/74 (0%) |
| Qwen3.6-35B-A3B Q8, thinking on (local) † | 5/38 (13%) | 4/36 (11%) | 0/36 (0%) |
| Qwen3-30B-A3B Instruct 2507 Q8 (local) † | 5/40 (12%) | 1/38 (3%) | 0/37 (0%) |
| Qwen3.6-35B-A3B Q8, thinking off (local) † | 3/38 (8%) | 1/36 (3%) | 0/36 (0%) |
| Qwen2.5-14B Q4 (local) † | 2/40 (5%) | 1/38 (3%) | 0/36 (0%) |

† partial, by decision (2026-10-01): local runs were trimmed to save days of GPU time once it was clear they score
in single digits. Qwen3-Coder-Next ran all four batteries; Qwen3-30B-A3B and Qwen2.5-14B stopped early in the long
battery (115 and 114 of 224 cells); Qwen3.6-35B-A3B ran round1 and round2 only (110 cells), once with thinking off
and once with thinking on (`local:<model>@nothink` / `@think`, Ollama's `think` field). Kolibri-1 ran all four batteries and
is the only local model that runs the app battery (3/96 first attempt, 16/96 within four:
[app report](live-model-eval-apps.md)).

- **Aleph Alpha Kolibri-1 is the best local model by far, still half the weakest frontier model.** 58/224 first
  try (26%) and 97/224 (43%) within four: round 1 20/48, round 2 21/62, long 1/54, adversarial 16/60. It passes
  47% of stream cells and 30% of structured cells on the first try, and no QBE cell (0/74; 282 of its 669 function-battery calls
  ended in a QBE compile failure). Token budgets bind on the adversarial battery (24,576 tokens: 52 of 176 calls
  hit it) and rarely elsewhere (2, 2 and 10 calls on rounds 1, 2 and long; 1 of 363 app calls). At ~21 tokens/s
  a call takes 10-18 minutes on average; the four function batteries took 151 hours.
- **Open coding models with strong published scores fail at tool-less machine code.** Qwen3-Coder-Next, Qwen's
  dedicated agentic coding model, passes 12/224 first try (5%), all on the stream path; 0/74 structured and 0/74
  QBE. The older Qwen3-30B-A3B and Qwen2.5-14B do no better on the cells they ran (5% and 3%).
- **Qwen3.6-35B-A3B, the newest Qwen MoE, is no better.** Thinking on: 9/110 first try (8%); thinking off:
  4/110 (4%); both 15/110 within four attempts. With thinking on, 376 of its 405 attempts ran out the 180 s
  function limit (374 before writing any answer text): at ~45 tokens/s it reasons for 12-14 K characters even on
  round 1's easiest tasks. Thinking off never returned reasoning (the adapter fails closed if it does) and answers
  in seconds, but wrongly: 144 QBE compile failures, 120 syntax and 42 format rejects, 43 wrong results.
- **Typical errors:** invalid QBE (missing result classes, invented operators, `@label:` with a colon); invented
  mnemonics (`cdiv`, `srsl`) and `x31` as a register name; structured operands written `{"reg": "rdi"}`, which
  the schema does not allow, and malformed JSON; ABI and runtime errors; and on the long battery, structured answers
  of ~23 KB that run out the 400 s limit on every attempt (Qwen3-30B-A3B at ~50 tokens/s).
- **False-reject review:** every failed attempt was replayed through the current pipeline. Qwen3-Coder-Next and
  Qwen3-30B-A3B: none. Qwen2.5-14B: 6 stream attempts on AArch64 (3 cells) were stopped at a line keystone refused
  but GNU as accepts (fixed since in c1c1df7f); the answers were cut off there, so they cannot be rescored. Its
  first-attempt count could be at most one higher; details in the run's `ADJUSTMENTS.md`.
  Qwen3.6-35B-A3B (both modes, 790 failed attempts): none; the one `crosscheck` reject (`lea r9, [r13 - r13]`) is
  refused by GNU as too.
  Kolibri-1 (all failed first attempts in every battery, read by hand): none. Its errors are its own:
  `movzx rax, eax`, `{"reg": "rdi"}` operands, invalid QBE, `idiv` without `cqo`, an unhandled `INT_MIN / -1`,
  a clobbered callee-saved `x19`.
- **Conclusion.** No open-weight model that runs on this desktop machine writes tool-less machine code usefully.
  The Qwen models manage 3-8% first try and about 14% within four attempts, almost all of it on easy stream
  cells. Kolibri-1, given as many output tokens as any vendor model used, reaches 26% first try and 43% within
  four, but only half the weakest frontier model's first-try rate and 3% on the app battery. No local model
  passes a single QBE cell on the first try. Published agentic-coding scores
  (Qwen3-Coder-Next, Qwen3.6) do not transfer to this task; the gap to the weakest frontier model here (Claude
  Haiku 4.5, 52%) is an order of magnitude for them.

## Adversarial prompt variants

| Model | baseline | misleading_hint | terse |
|---|---|---|---|
| Claude Opus 5.5 | 57/60 | 59/60 | 60/60 |
| Claude Fable 5.1 | 54/60 | 48/60 | 55/60 |
| Claude Sonnet 5 | 45/60 | 38/60 | 43/60 |
| Claude Haiku 4.5 | 35/60 | 27/60 | 33/60 |

(first-attempt passes of 60 cells). Sonnet and Haiku lose points by *following* the wrong tips. Fable loses
them differently: the misleading tips (about writing past a buffer, reading out of bounds, callee behaviour)
push 11 prompts over Anthropic's cyber classifier, which refuses them on every attempt (all six `fill_down`
cells, three `bmin_empty`, two `map_into`), even on the CVP account; its answers are not misled.

## False-reject review

Every non-Claude run's failures were checked by hand as the Claude runs were: compile errors re-run through
QBE, stream rejects re-assembled with GNU as, structured rejects checked against the prompt's schema,
runtime verdicts read against the spec (some confirmed by running the code natively), and raw event streams
rebuilt to the recorded answers. Two harness problems were found in all of it. First: Daybreak Blue
`max_byte.stream.aarch64` attempt 1, where AArch64 `uxtw x0, w2` was refused because keystone and GNU as
emit different (equivalent) encodings. Fixed (`bca/stream.py`: a same-size disagreement now takes GNU as's
bytes); counted as a pass above. Second: grok 1.0.41's streaming output occasionally repeats a text chunk; three
Grok 4.7 build-fast answers reached the scorer beginning with a doubled `{"` the model never wrote (grok's own
session record shows the real answer). Rescored with the duplicate removed they pass, and are counted as passes
above (details in that run's `ADJUSTMENTS.md`); the adapter now scores grok's session record, not the stream.
GPT-6 Luna via Copilot: one, `mat_pow.stream.aarch64` attempt 1, rejected at `mov x17, x20, lsl #3`, which keystone
refuses outright but GNU as accepts (an alias of `orr x17, xzr, x20, lsl #3`); rescored with the recorded answer it
passes, and it counts as a pass above (fixed in c1c1df7f: the stream path takes GNU as's bytes when keystone refuses).
Claude Sonnet 5.5 via Copilot: none. Two answers define x86 labels named like registers (`cl`, `ch`) and jump to
them; the encoder refused the jump. That is genuine: the x86 jump schema in the prompt says register names are not
usable as labels, and GNU as also rejects `jmp cl` with a label `cl` defined (d7df9dda now refuses such a label at
its definition, with a clearer message; no verdict changes). Also, `vm_run.structured.aarch64` passed on a fresh re-run after the pre-fix output-cap abort (the runtime's
continuation event stopped the subject); under the current rule the capped 34,574-character answer (ending in a
run of repeated characters) is scored, and it fails as `format`, so that cell counts as a first-attempt failure.
Kimi K3, Grok 4.7, Grok 4.6, Grok 4.5, Codex Astra, Sol and GPT-5.5, Claude Sonnet 4.5, Opus 4.8 and MAI-Code: none
(Astra's 227 attempts were all rescored and reproduced; 181 failed attempts reviewed across the last six).

Latency figures are not comparable across vendors (whole-message CLIs; and Kimi's recorded durations in
this run include waits for the credential-refresh lock, fixed for future runs).
