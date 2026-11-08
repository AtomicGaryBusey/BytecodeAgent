# Landscape update, 2026-11-07: new models, and new work by others

An update to [AI for x86 and ARM machine code.md](AI%20for%20x86%20and%20ARM%20machine%20code.md) (2026-09-27), in
two parts. Each was researched by a web-search subagent (Claude Sonnet). The parts marked **checked** were then
confirmed by hand. **Checked** means against the cited page; for Foundry, against the user's own resource
(`az cognitiveservices account list-models`). Everything else is the subagent's reading of public pages,
much of it from aggregators, so treat it as a lead and not a finding. Benchmark figures quoted for models
are vendor-reported unless stated.

## 1. New work on LLM assembly, machine code and IR since 2026-09-27

**No new benchmark, leaderboard or cross-vendor study was found.** The search turned up few October-November papers,
so read the gap verdicts below as "no evidence found", not proof of absence.

| Item | Date | What it is | Relevance to this project |
|---|---|---|---|
| [skelpo/llm-to-native-binary](https://github.com/skelpo/llm-to-native-binary/blob/main/README.md) (GitHub), **checked** | undated (macOS 26.5, © 2026) | Claude Code hand-wrote five runnable arm64 macOS executables byte by byte, the Mach-O container and ad-hoc code signature included, with no compiler, assembler or linker (Python only as a hex editor and SHA-256 tool). The author finds the limit is register allocation, not encoding: past ~28 interacting live values, hand-writing the binary means writing a register allocator. Its 40-live-value kernel took 178 instructions and 32 spill/reload ops against clang -O2's 116 and 12. The first build gave a silent wrong answer (`x0` still held `argc`) that only a golden reference value caught. | The nearest thing to our hex path. It is one anecdote, not a benchmark, and it supports differential testing against a reference. |
| [ATLAS](https://arxiv.org/abs/2610.30877), arXiv 2610.30877 | 2026-10-22 | Multi-agent lifting of binaries to canonical LLVM IR. A Lift agent drafts the IR, an Advisor agent proposes repairs from LLVM verifier errors, and a Validation agent rechecks them. Reported 91.8% re-executability and 89.4% CFG similarity across ARM, x86-64 and RISC-V (HumanEval-Decompile, MBPP). The LLMs used are not named on the abstract page. | Verifier-error-driven IR repair, the same shape as our repair rounds on the QBE path. It lifts from binaries rather than writing IR from a spec. |
| [LLMSbt](https://arxiv.org/abs/2610.05999), arXiv 2610.05999, **checked** | posted 2026-10-05; published at EuroSys'26 (April 2026) | LLM-driven static x86-64 to AArch64 binary translation, with a verifier checking semantics snippet by snippet; compared against open-source translators and ExaGear. The abstract gives no numbers. | Both our ISAs, but translation rather than generation. Not new work: a reprint of an April paper. |
| [Rethinking Neural Decompilation](https://arxiv.org/abs/2611.02788), arXiv 2611.02788 | 2026-11-02 | Candidate selection is the bottleneck: pass@100 93% on HumanEval-Decompile; execution-guided selection gets 86.3% pass@1 on ExeBench. | Adjacent work (decompilation). It repeats the survey's point that sampling plus an execution gate beats a single shot. |
| [Verified Learning for Compiler Optimization](https://arxiv.org/abs/2609.27214), arXiv 2609.27214 | 2026-09-23 (just before the window) | LLM-proposed LLVM IR rewrites checked by Alive2: no semantic violations, but only 9.8% of benchmarks matched or beat the baseline. | Adjacent work. |
| [PTXBench](https://arxiv.org/html/2608.17379) (v3 reported 09-28) and [RESOLVE](https://arxiv.org/html/2610.05683v1) (10-05) | dates from search metadata | GPU inline-PTX writing (checks that the intended SASS executes) and binary-level GPU kernel validation. | GPU-side analogues. Not checked further. |

**The survey's four open gaps:**

1. **A benchmark of direct opcode-byte emission:** still open. Our hex path (`reports/live-model-eval-hex.md`)
   is still the only systematic measurement we know of. The skelpo project is the only new data point.
2. **A public "assembly from a spec" leaderboard:** still open.
3. **A 2026 head-to-head of current GPT, Gemini and Claude on hand-written assembly:** still open outside this
   project, whose cross-vendor report covers it.
4. **AArch64 coverage comparable to x86-64:** partly addressed, but only by translation and lifting work (LLMSbt,
   ATLAS), not by generation from a spec.

## 2. Models not yet on our awareness list

The subagent was given every model already benchmarked or already seen in a catalog, and asked for releases since
2026-09-01. Its candidates, with route availability **checked** for Azure Foundry against the user's resource
(an Azure AI Foundry resource in westus3). Fireworks.ai availability is in section 3.

| Candidate | Vendor | Reported date | Reported size / claims | On Foundry (checked) |
|---|---|---|---|---|
| Mistral Large 4 | Mistral | weights 10-29 | ~1T total, 52B active; low/medium/high reasoning; Terminal-Bench 4.0 41% (vendor) | **no** |
| MiniMax M3.1 Flash | MiniMax | weights 10-23 | 428B total, 23B active; DeepSWE v1.1 73.7% (vendor) | **no** |
| GLM-5.3 | Z.ai | 08-14 | ~744B; SWE-bench Verified 69.0% (aggregator) | **no** |
| Reflection Beam | Reflection AI | announced 10-05 | 501B total, 23B active; SWE-bench Verified 80.9 (self-reported); sources disagree on whether weights are out | **no** |
| Muse Spark Open | Meta | 10-15 | 816B total, 42B active; gated weights | **no** |
| Mellum2.1 | JetBrains | 10-08 | 12B MoE, Apache 2.0, RL-trained for coding (one tracker source only) | no |
| Step 5 Preview, MiMo-V2.6-Pro/Flash, Ling-3.1-flash, Flint, LongCat-2.5, Holo4, Particle-1 | various | 09-20 to 10-29 | mostly 500B+ open MoE models; low-confidence single-source reports | no |

**Found in the Foundry catalog but not on our list** (checked; serverless pay-per-token Global Standard):

- **mistral-medium-3-5** (Preview).
- **Kimi-K2.7-Code** (2026-06-12, Preview). A code-tuned Kimi model we have never benchmarked.
- **Kimi-K3.1-Pro** (added 2026-11-04). A second route to a model already benchmarked through the Kimi CLI
  (218/224), so it would allow a same-model, two-route comparison.

**Corrections and notes:**

- **Qwen-3.6-35B-A3B and Qwen-3.8-27B** are listed on Foundry with **no serverless SKU**: like Muse-Glimmer-30B they
  are managed compute only (hourly GPU), which the user has ruled out.
- **The Foundry catalog also serves most current Claude, GPT-6.x, Grok and DeepSeek models.** Claude is benchmarked
  through the user's Claude account, by decision (plus Sonnet 5.5 through Copilot, for a harness comparison).
  DeepSeek-V4-Pro has since been benchmarked on Foundry (139/224); the others already have a route.
- **Reported, not checked:** Foundry retiring Claude Haiku 4.5 on 2026-11-15. Not relevant here, since Haiku runs
  through `claude -p`.
- **Restricted-access models not pursued:** Mythos 5.1, GPT-6 Rosalind Research, Gemini 4 Carbon.
- **Announced, no weights found:** Qwen4. **Rumoured:** Grok 4.8, Grok 5.

Foundry has since been used for MAI-Thinking-1 (72/224) and DeepSeek-V4-Pro (139/224), function batteries
only. **Recommendation for further models (not yet approved):** first Kimi-K3.1-Pro on Foundry, for the same-model, two-route
comparison; then mistral-medium-3-5; then Mellum2.1 locally, if its model card checks out. Each would get a
price check and a smoke test first, as DeepSeek-V4-Pro did.

## 3. Fireworks.ai availability

Fireworks.ai is a separate vendor with its own account and billing. It was checked here because several
candidates are served nowhere else we can reach; a `fireworks:` adapter has since been written (no account or
runs yet). A second subagent read the public model
pages; the MiniMax and GLM-5.3 pages were **checked** by hand. Only the first page of Fireworks' 332-model
catalog loaded, so "not found" means "no page at the slugs tried, not on page one", not absent.

| Candidate | Fireworks status | Model id | Serverless $/1M in (cached) / out | Notes |
|---|---|---|---|---|
| MiniMax M3.1 Flash | **serverless** (checked) | `accounts/fireworks/models/minimax-m3p1-flash` | 0.15 (0.015) / 0.60 | 1,040K context; reasoning always on, effort low to max |
| GLM-5.3 | **serverless** (checked) | `accounts/fireworks/models/glm-5p3` | 1.40 (0.26) / 4.40 | 1,040K context; no reasoning setting stated |
| GLM-5.3 Flash | serverless | `accounts/fireworks/models/glm-5p3-flash` | 0.15 (0.03) / 0.50 | not on the candidate list; found alongside |
| Muse Spark Open | serverless (US capacity) | `accounts/fireworks/models/muse-spark-open` | 1.25 (0.15) / 4.25 | a reasoning model; no effort setting stated |
| MiMo-V2.6-Pro-RL | on-demand GPU only | `accounts/fireworks/models/mimo-v2p6-pro-rl` | none | hourly GPU, out of scope by the user's rule |
| Kimi K3.1 Pro | serverless | `fireworks/kimi-k3p1-pro` | 2.00 / 10.00 | a third route to K3.1 Pro (Kimi CLI, Foundry) |
| Mistral Large 4, Reflection Beam, Mellum2.1, Step 5 Preview, Ling-3.1-flash, Flint, Kimi-K2.7-Code, mistral-medium-3-5, Qwen4 | not found | | | Mistral Large 4: secondary sources give 2026-10-06, 1.05T total / 49B active (the first survey said 10-29) |

The model pages say the API works with the OpenAI client (OpenAI-compatible chat completions). Streaming is
standard there but not confirmed on these pages. The `fireworks:` adapter (`bca/liveeval/vendor_fireworks.py`) is a near copy of
`vendor_foundry` (API key instead of an Entra token, Fireworks prices, the same spend cap). It needs the
user's Fireworks account and a choice of models.
