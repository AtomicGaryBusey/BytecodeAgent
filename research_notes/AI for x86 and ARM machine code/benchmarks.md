# Benchmarks and Datasets for AI Assembly / Machine-Code Generation (x86 and ARM)

> Source note for [the 2026-09-27 survey](../../reports/AI%20for%20x86%20and%20ARM%20machine%20code.md), current as of 2026-09-26; for later work see [the 2026-11 landscape update](../../reports/landscape-2026-11.md), and for this project's own measurements (which fill several gaps named here) the [README](../../README.md).

Scope note: about 17 tool calls (searches and fetches), current as of 2026-09-26. Most numbers come from primary arXiv, OpenReview or ICLR papers. Leaderboard aggregators are flagged where used. "Asm" means assembly.

## Which benchmarks exist (task, ISA, size, metric)?

### Takeaway
There is no single canonical "HumanEval for assembly" leaderboard. The field uses a patchwork of paper-specific benchmarks, most of them built on HumanEval/MBPP translated into C and compiled at -O0 to -O3, or on large C corpora (AnghaBench, ExeBench, The Stack). The main task families are:
- decompilation (Decompile-Eval/HumanEval-Decompile, Decompile-Bench-Eval, GitHub2025)
- neural compilation, i.e. C or IR to asm (ExeBench, NeuComBack)
- asm superoptimization (SuperCoder benchmark)
- ISA translation x86 to ARM/RISC-V (Guess & Sketch, CRT/Guaranteed Guess on HumanEval-C and BringUpBench)
- SIMD intrinsics (SimdBench)
- binary similarity (BinaryCorp)
- CTF/pwn agent benchmarks (Cybench, NYU CTF Bench)

x86-64 dominates. ARM coverage is secondary.

### Cited Findings

**Decompilation**
- **Decompile-Eval / HumanEval-Decompile** (LLM4Decompile, EMNLP 2024)
  - Built from HumanEval's 164 problems, translated to C and compiled at O0, O1, O2 and O3.
  - Metrics are re-compilability and re-executability (the decompiled code must pass the original tests).
  - The original 6B LLM4Decompile scored 87% re-compilability and 21% re-executability.
  - Sources: [arXiv 2403.05286](https://arxiv.org/pdf/2403.05286); [EMNLP paper](https://aclanthology.org/2024.emnlp-main.203.pdf); [GitHub](https://github.com/albertan017/LLM4Decompile)
- **Decompile-Bench** (2025)
  - 2 million binary-source function pairs, filtered from 100 million collected pairs (450 GB of binaries from permissively licensed GitHub projects).
  - Compiled for Linux x64 with Clang 19, so it is x86-64 only.
  - Source: [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- **Decompile-Bench-Eval** (2025)
  - Contains HumanEval and MBPP (C and C++), each compiled at O0 to O3, plus **GitHub2025**: about 60K new C/C++ functions from 121 repositories created after 2025, to reduce data-leakage concerns. It also includes ProRec.
  - Metrics: re-executability, R2I readability index, and edit similarity. CodeBLEU and embedding similarity are in the appendix.
  - Source: [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- **GitHub2025 in the SK2Decompile paper**
  - Described as 23,400 stripped C functions from 130 projects.
  - This does not match Decompile-Bench's "about 60K from 121 repos", so different papers appear to use different cuts of the set.
  - Source: [SK2Decompile summary](https://www.emergentmind.com/topics/sk-decompile), [arXiv 2509.22114](https://arxiv.org/abs/2509.22114)
- **"When LLM Decompilers Recompile More and Preserve Less"** (arXiv, September 2026)
  - Evaluates 8 systems in 9 configurations on existing corpora, plus 300 GitHub library functions and 287 CVE-grounded functions.
  - Adds a "Matched rate" (behavioral agreement on an input corpus) and a "crash absence" metric.
  - Source: [arXiv 2609.05370](https://arxiv.org/abs/2609.05370)

**Neural compilation (C or IR to asm)**
- **ExeBench**
  - An ML-scale dataset of executable C functions with IO tests. Its C-to-x86 neural-compilation evaluation has 17,121 cases.
  - The workflow is: translate, assemble and link, run 10 IO tests, and count the case as passing only if all tests pass.
  - Sources: [LEGO-Compiler arXiv 2505.20356](https://arxiv.org/html/2505.20356); [ExeBench](https://www.researchgate.net/publication/361263162_ExeBench_an_ML-scale_dataset_of_executable_C_functions)
- **NeuComBack** (NeurIPS 2025 / arXiv 2511.01183)
  - An IR-to-assembly benchmark for x86_64 and aarch64. Metrics are functional correctness plus performance against clang -O3.
  - The abstract does not state its size.
  - Source: [arXiv 2511.01183](https://arxiv.org/abs/2511.01183)

**Asm optimization**
- **SuperCoder benchmark** (arXiv 2505.11480)
  - 8,072 x86-64 assembly programs averaging 130 lines. Earlier superoptimization datasets were limited to 2-15-line straight-line, loop-free programs.
  - Task: given C code plus the gcc -O3 asm, produce faster asm.
  - Metrics: test-passing rate and average speedup over gcc -O3.
  - Source: [arXiv 2505.11480](https://arxiv.org/abs/2505.11480)

**ISA translation**
- **Guess & Sketch** (Lee et al., ICLR 2024)
  - LM-guided asm transpilation combined with a symbolic solver.
  - Source: [arXiv 2309.14396](https://arxiv.org/pdf/2309.14396)
- **CRT / Guaranteed Guess (GG)** (arXiv 2411.16341, 2506.14606)
  - Translates x86 to ARMv8, ARMv5 and RISC-V64.
  - Training data: 1.32M samples from AnghaBench (1.01M programs) and The Stack v2 (306k).
  - Evaluation sets: HumanEval-C (164 problems) and BringUpBench (65 bare-metal programs, 85-5,751 LOC).
  - Metric: pass@1 on unit tests, with a reported 98.81% line coverage on HumanEval.
  - Sources: [arXiv 2506.14606](https://arxiv.org/html/2506.14606); [arXiv 2411.16341](https://arxiv.org/html/2411.16341)

**SIMD / vector code**
- **SimdBench** (arXiv 2507.15224)
  - 136 tasks across SSE, AVX, Neon, SVE and RVV. 18 LLMs evaluated on correctness and performance.
  - Non-native targets are run under QEMU 10.0.0 after cross-compiling with clang 19.1.7.
  - This is intrinsics-level code, not raw asm.
  - Source: [arXiv 2507.15224](https://arxiv.org/abs/2507.15224)
- **CodegenBench** (arXiv 2606.04023)
  - 106 BLAS routines. Targets are x86_64, Sunway and Kunpeng (an ARM-based server CPU).
  - Measures efficient parallel code across architectures.
  - Source: [arXiv 2606.04023](https://arxiv.org/abs/2606.04023)
- **Build-bench** (arXiv 2511.00780)
  - 268 real-world packages that fail to build in cross-ISA migration. Agents repair the builds using tools.
  - Source: [search result for arXiv 2511.00780](https://arxiv.org/pdf/2511.00780)

**Corpora and representation learning**
- **AnghaBench**
  - One million compilable C benchmarks, originally built for code-size reduction (CGO 2021). Widely reused as asm training data.
  - Source: cited in [Decompile-Bench](https://arxiv.org/pdf/2505.12668) and [GG](https://arxiv.org/html/2506.14606)
- **BinaryCorp**
  - 48,130 binaries (ArchLinux official packages and AUR) compiled with gcc/g++ at several optimization levels, about 26M functions. BinaryCorp-3M is a subset of about 3.6M functions.
  - Task: binary code similarity detection on x86. jTrans raised the SOTA from 32.0% to 62.5%.
  - Source: [jTrans arXiv 2205.12713](https://arxiv.org/pdf/2205.12713)
- **Meta LLM Compiler** (2024)
  - Trained on 546B tokens of LLVM-IR and asm.
  - Its disassembly task (x86_64 and ARM asm back to LLVM-IR) scored a 45% round-trip success rate and 14% exact match, on 2,015 prompts from MiBench.
  - Source: [arXiv 2407.02524](https://arxiv.org/abs/2407.02524); numbers via [VentureBeat](https://venturebeat.com/ai/metas-llm-compiler-is-the-latest-ai-breakthrough-to-change-the-way-we-code)

**CTF / pwn agent benchmarks**
- **Cybench**
  - 40 professional CTF tasks from 4 competitions. Categories: crypto, web, rev, forensics, misc and pwn.
  - Its leaderboard cites results from system cards, including Claude Sonnet 4.5, Opus 4.5, Opus 4.6, Mythos Preview and Opus 4.7, Grok 4 and Muse Spark. Some of these are on 37- or 39-task subsets.
  - Source: [cybench.github.io](https://cybench.github.io/)
- **NYU CTF Bench**
  - 200 challenges covering crypto, forensics, misc, pwn, rev and web.
  - Sources: [arXiv 2406.05590](https://arxiv.org/html/2406.05590v3); [GitHub](https://github.com/NYU-LLM-CTF/NYU_CTF_Bench)

### Inferences
- Nearly all execution-based asm benchmarks are derived from HumanEval/MBPP-sized C functions, or from auto-extracted C functions (AnghaBench, ExeBench). They measure function-level, not program-level, competence.
- ARM is consistently the secondary target. It appears as the ARMv8/ARMv5 targets in GG/CRT, aarch64 in NeuComBack, and Neon/SVE in SimdBench. The large decompilation and superoptimization sets (Decompile-Bench, SuperCoder) are x86-64 only.

### Gaps
- I found no widely adopted "write asm from a natural-language spec" benchmark, i.e. a HumanEval-asm with a public leaderboard, for x86 or ARM. A targeted search returned nothing, so it may exist only as internal evaluations.
- I did not verify the NeuComBack dataset size or the task counts per Cybench category (pwn/rev). The fetched pages did not show them.
- I did not investigate CodeNet-derived asm datasets or SWE-style low-level repository tasks. No results surfaced in the searches run.

## What are the SOTA scores and which models/harnesses hold them (as of 2026)?

### Takeaway
- **Decompilation:** specialized RL-trained pipelines lead. SK2Decompile, an ICLR 2026 paper, reports about 70% re-executability on HumanEval-Decompile and about 60% on MBPP. That beats GPT-5-mini by 21.6 points on average, and Claude-Sonnet-4 reached 46.79%.
- **Neural compilation on ExeBench:** LEGO-Compiler claims over 99%.
- **Superoptimization:** RL-tuned small models (SuperCoder, 95% correct, 1.46x over gcc -O3) beat the frontier baseline (Claude Opus 4 at 51.5% correct, 1.43x).
- **x86 to ARM translation:** fine-tuned small models (GG, 99.39% on HumanEval ARMv8 at -O0) far outperform general LLMs (GPT-4o at 1.5-10%).

### Cited Findings
**Decompilation**
- Original LLM4Decompile-6B: 21% re-executability on Decompile-Eval — [arXiv 2403.05286](https://arxiv.org/pdf/2403.05286)
- Decompile-Bench paper, re-executability on HumanEval and MBPP respectively:
  - GPT-4.1-mini: 13.42% and 19.89%
  - LLM4Decompile-DCBench-1.3B: 20.89% and 24.93%
  - LLM4Decompile-DCBench-6.7B: 39.48% on HumanEval
  - Claude-Sonnet-4: 46.79% on HumanEval
  - Idioms: 30.56% at O0, about 12% at O2/O3
  - IDA Pro has comparable re-executability but an R2I of about 40, versus about 70 for LLM decompilers.
  - Source: [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- SK2Decompile (ICLR 2026) takes a two-phase approach: skeleton/IR recovery with compiler-guided RL, then an identifier-naming model.
  - It reports about 70% average re-executability on HumanEval and about 60% on MBPP.
  - It reports a 21.6% average re-executability gain over GPT-5-mini on HumanEval and a 29.4% average R2I gain over Idioms on GitHub2025.
  - Sources: [arXiv 2509.22114](https://arxiv.org/abs/2509.22114); [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10007876); [summary](https://www.emergentmind.com/topics/sk-decompile)
- The September 2026 study found that the strongest LLM model raised Ghidra's build rate from 75% to 90%, but the behavioral Matched rate fell from 74% to 62%. Test-passing candidates still diverged on 4.9% of inputs overall, and up to 13% for one system — [arXiv 2609.05370](https://arxiv.org/abs/2609.05370)

**Neural compilation**
- LEGO-Compiler scored over 99% on ExeBench C-to-x86. The previous SOTA was 91% pass@1 from a fine-tuned CodeLlama — [arXiv 2505.20356](https://arxiv.org/html/2505.20356)
- NeuComBack: self-evolving prompt optimization raised frontier-LLM correctness as follows:
  - x86_64: from 44% to 64%
  - aarch64: from 36% to 58%
  - Of 16 correct x86_64 programs, 14 (87.5%) beat clang -O3.
  - Source: [arXiv 2511.01183](https://arxiv.org/abs/2511.01183)

**Superoptimization**
- SuperCoder evaluated 23 LLMs. Best baseline was Claude-opus-4 at 51.5% test-pass and 1.43x average speedup over gcc -O3.
- RL (PPO/GRPO) on Qwen2.5-Coder-7B-Instruct took it from 61.4% correct and 1.10x speedup to 95.0% correct and 1.46x speedup.
- Source: [arXiv 2505.11480](https://arxiv.org/abs/2505.11480)

**ISA translation**
- GG pass@1 results:

  | Benchmark | ARMv8 -O0 | ARMv8 -O2 | ARMv5 | RISC-V64 |
  |---|---|---|---|---|
  | HumanEval-C | 99.39% | 45.12% | 93.71% | 89.63% |
  | BringUpBench | 49.23% | 15.38% | not reported | not reported |

- Baselines on HumanEval: GPT-4o at 1.54-10.3%, and untuned Qwen2.5-Coder, StarCoder2 and DeepSeek-R1-7B at 0%.
- On an Apple M2 Pro, GG's translated binaries ran 1.73x faster than Rosetta 2, with 1.47x better energy efficiency and 2.41x better memory use.
- Source: [arXiv 2506.14606](https://arxiv.org/html/2506.14606)
- The earlier CRT reported 79.25% for x86 to ARMv5 and 88.68% for x86 to RISC-V — [arXiv 2411.16341](https://arxiv.org/html/2411.16341)

**CTF / pwn**
- Aggregator claims for NYU CTF Bench: CRAKEN (Self-RAG + Graph-RAG) solved 44 challenges. D-CIPHER reported 22.0% on NYU CTF, 22.5% on Cybench and 44.0% on HackTheBox.
  - These come from an aggregator and were not verified against primary papers — [BenchmarkList](https://benchmarklist.com/benchmarks/nyu_llm_ctf/)
- Cybench's current top entries come from frontier-lab system cards (Claude Opus 4.x / Mythos Preview, Grok 4, Muse Spark). I could not extract the exact percentages from the page — [cybench.github.io](https://cybench.github.io/)

### Inferences
- Most "SOTA" numbers are self-reported by the paper introducing the method, on a benchmark that paper often introduced itself. There is no neutral maintained leaderboard for decompilation, neural compilation or asm optimization.
- Frontier general models (Claude Opus/Sonnet 4-class, GPT-5-mini) are strong zero-shot baselines for decompilation and superoptimization. Small RL-tuned specialist models still beat them on these narrow, test-verified tasks.
- Performance drops sharply with optimization level. GG goes from 99% at -O0 to 45% at -O2 on ARMv8, and Idioms from 31% at O0 to about 12% at O2/O3. Optimized code is the real frontier.

### Gaps
- I could not retrieve exact 2026 frontier-model Cybench percentages (for example Claude Opus 4.7 or Mythos Preview), or which harness produced them. The table did not render in the fetch.
- I found no reproducible independent re-evaluation of SK2Decompile or LEGO-Compiler claims.
- I found no data on frontier 2026 models (for example GPT-5.x or Claude Opus 5) evaluated on SuperCoder, GG/HumanEval-C translation or NeuComBack.

## How is correctness measured?

### Takeaway
Correctness is overwhelmingly measured by execution against test cases: re-executability, IO tests and unit-test pass@1. For non-native ISAs (ARM, RISC-V) the code runs natively on ARM hardware or under QEMU or Spike. Formal equivalence checking is rare in LLM benchmarks, and recent work shows that test-based metrics overstate semantic preservation.

### Cited Findings
- Re-executability means that for every test input x, the source s(x) equals the decompiled d(x), using the original HumanEval/MBPP tests — [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- ExeBench neural compilation assembles and links the asm, then runs 10 IO tests and requires all to pass. aarch64 is evaluated natively on an Apple M1 and riscv64 via the Spike simulator — [arXiv 2505.20356](https://arxiv.org/html/2505.20356)
- SimdBench cross-compiles and runs Neon, SVE and RVV code under QEMU 10.0.0 — [arXiv 2507.15224](https://arxiv.org/pdf/2507.15224)
- GG uses unit tests and reports line coverage (98.81% HumanEval, 97.32% BringUpBench) to argue the tests are adequate — [arXiv 2506.14606](https://arxiv.org/html/2506.14606)
- Guess & Sketch uses a symbolic solver to repair and verify the semantic correctness of LM-proposed translations. This is the closest to formal methods among the translation works — [arXiv 2309.14396](https://arxiv.org/pdf/2309.14396)
- SuperCoder's reward and metric combine test-based correctness with measured speedup over gcc -O3 — [arXiv 2505.11480](https://arxiv.org/abs/2505.11480)
- Test-passing decompilations still diverge on 4.9% of extra inputs (up to 13% for one system), and up to a tenth of CVE functions silently lose the crash behavior — [arXiv 2609.05370](https://arxiv.org/abs/2609.05370)
- Readability and similarity metrics used alongside execution: R2I, edit similarity, CodeBLEU and embedding similarity — [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- CTF benchmarks score by flag capture, a binary outcome — [Cybench](https://cybench.github.io/); [NYU CTF Bench](https://arxiv.org/html/2406.05590v3)

### Inferences
- Superoptimization and translation benchmarks that rely on tests alone are vulnerable to "test overfitting". A speedup claim over gcc -O3 could come partly from asm that is incorrect outside the test inputs. Formal equivalence checking (as in STOKE-style superoptimizers) is not standard in LLM asm benchmarks.

### Gaps
- I found no LLM asm benchmark that uses SMT or bounded-model-checking equivalence as its primary metric. It may exist, but it did not surface.

## Gaps: what is not benchmarked well?

### Takeaway
The weak spots are:
- writing asm from specifications
- whole-program or multi-function asm (linking, ABI, system calls)
- ARM64 parity with x86-64
- highly optimized (-O2/-O3) and obfuscated binaries
- formal-equivalence-based scoring
- neutral, maintained leaderboards with frontier-model entries

### Cited Findings
- Earlier superoptimization datasets covered only 2-15-line straight-line programs. SuperCoder is the first large-scale, loop-containing set, and it is x86-64 only — [arXiv 2505.11480](https://arxiv.org/abs/2505.11480)
- Decompile-Bench notes that HumanEval-Decompile is limited to C, and that data-leakage concerns motivated the post-2025 GitHub2025 set — [arXiv 2505.12668](https://arxiv.org/pdf/2505.12668)
- On real bare-metal programs (BringUpBench), translation accuracy collapses to 49% at -O0 and 15% at -O2, compared with 99% and 45% on HumanEval — [arXiv 2506.14606](https://arxiv.org/html/2506.14606)
- SIMD code generation shows a universal pass@k drop compared with scalar code — [arXiv 2507.15224](https://arxiv.org/abs/2507.15224)
- Recompilability and test-passing metrics mask behavioral divergence and vulnerability loss — [arXiv 2609.05370](https://arxiv.org/abs/2609.05370)
- Obfuscated asm comprehension is an emerging area. OASIF (arXiv 2606.29155) targets obfuscation-aware asm instruction following, and "Disassembling Obfuscated Executables with LLM" targets disassembly. I did not verify their details — [arXiv 2606.29155](https://arxiv.org/pdf/2606.29155); [arXiv 2407.08924](https://arxiv.org/pdf/2407.08924)

### Inferences
- There is no standardized benchmark for hand-writing correct x86/ARM asm from natural-language specs. Examples of what is missing: ABI-compliant functions, syscalls, shellcode, or inline asm in real repositories. CTF pwn tasks probe this only indirectly, and without isolating asm skill.
- ARM coverage is thin. No million-scale ARM64 decompilation set or ARM superoptimization set was found. Apple Silicon and ARM server targets are mostly evaluated through translation (x86 to ARM) rather than native generation.
- There is no SWE-bench-style benchmark of repository-level low-level tasks (kernels, bootloaders, JITs, codecs with hand-written asm) with execution-based grading. Build-bench, on cross-ISA build repair, is the nearest found.
- Contamination risk is high for anything built on HumanEval/MBPP, AnghaBench or public GitHub. Only GitHub2025-style post-cutoff sets partly address it.

### Gaps
- I did not check whether any 2026 benchmark (beyond those listed) has introduced an asm-from-spec or shellcode-generation track with a public leaderboard. I found none in the searches performed.
