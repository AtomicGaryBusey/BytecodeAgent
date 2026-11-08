# Specialized AI Models for x86/ARM Assembly, Machine Code, Compiler IR and Binaries (as of 2026-09-26)

> Source note for [the 2026-09-27 survey](../../reports/AI%20for%20x86%20and%20ARM%20machine%20code.md), current as of 2026-09-26; for later work see [the 2026-11 landscape update](../../reports/landscape-2026-11.md), and for this project's own measurements (which fill several gaps named here) the [README](../../README.md).

## Catalog of major specialized models (who, size, data, ISAs, tasks, results, license)

### Takeaway
The field is split into four families: (1) compiler/IR foundation models (Meta LLM Compiler, 2024, the only "big lab" open model on x86_64 + ARM); (2) neural decompilers (LLM4Decompile v1.5/v2 in 2024, SK²Decompile in 2025–26, Nova, Idioms, ReSym, and 2026 RL multi-turn systems such as AutoDecompiler); (3) assembly-to-assembly translators (Guess & Sketch in 2024, CRT / "Guaranteed Guess" in 2024–25, x86 to ARM/RISC-V); (4) assembly superoptimizers (AlphaDev in 2023, SuperCoder in 2025). Nearly all decompilers target x86_64 Linux/GCC only. ARM coverage is thin: mainly LLM Compiler, and ARM as a *target* in the transpilers.

### Cited Findings

**Meta LLM Compiler (June 2024, older)**
- Meta; 7B and 13B; built on Code Llama; trained on 546B tokens of LLVM-IR and assembly, plus instruction fine-tuning to emulate compiler behavior; covers x86_64 and ARM assembly — [arXiv 2407.02524](https://arxiv.org/abs/2407.02524)
- Tasks: code-size optimization (predicting LLVM opt pass lists) and disassembly (assembly to LLVM-IR). Results: 77% of the optimizing potential of an autotuning search, 45% disassembly round-trip (14% exact match) — [arXiv 2407.02524](https://arxiv.org/abs/2407.02524)
- Released under a custom commercial license ("license: other"). Variants on HF: facebook/llm-compiler-7b, -13b, -7b-ftd, -13b-ftd. All are **gated** and last updated 27 Jun 2024 (7b: ~52.7K downloads; 13b-ftd: ~25.1K) — [HF llm-compiler-7b](https://hf.co/facebook/llm-compiler-7b); [HF llm-compiler-13b](https://hf.co/facebook/llm-compiler-13b); [HF llm-compiler-13b-ftd](https://hf.co/facebook/llm-compiler-13b-ftd)

**LLM4Decompile (2024; SK²Decompile 2025–26)**
- LLM4Binary group (Hanzhuo Tan, Yuqun Zhang et al.). Models and HumanEval-Decompile re-executability: 1.3B-v1.5 27.3%; 6.7B-v1.5 45.4%; 1.3B-v2 46.0%; 6.7B-v2 52.7%; 9B-v2 64.9%; 22B-v2 63.6%. v2 models refine Ghidra pseudocode. ISA: Linux x86_64, GCC O0–O3. Benchmarks: HumanEval-Decompile (164 C functions) and ExeBench (2,621 real functions) — [GitHub LLM4Decompile](https://github.com/albertan017/LLM4Decompile)
- Decompile-Bench (May 2025): 2M binary-source function pairs for training, 70K for evaluation. Repo is licensed MIT + DeepSeek License — [GitHub LLM4Decompile](https://github.com/albertan017/LLM4Decompile)
- HF: LLM4Binary/llm4decompile-6.7b-v1.5 (MIT, ~212K downloads, the most downloaded model in this space); llm4decompile-9b-v2 (8.8B params, MIT); llm4decompile-22b-v2 (22.2B, Mistral architecture, MIT) — [HF 6.7b-v1.5](https://hf.co/LLM4Binary/llm4decompile-6.7b-v1.5); [HF 9b-v2](https://hf.co/LLM4Binary/llm4decompile-9b-v2); [HF 22b-v2](https://hf.co/LLM4Binary/llm4decompile-22b-v2)
- SK²Decompile (arXiv Sept 2025; ICLR 2026): two phases. A structure-recovery model maps binary to an IR "skeleton", then an identifier-naming model adds the "skin". Both are RL-optimized. Reports a 21.6% average re-executability gain over GPT-5-mini on HumanEval, and a 29.4% average R2I (readability) gain over Idioms on GitHub2025 — [arXiv 2509.22114](https://arxiv.org/abs/2509.22114); [ICLR 2026 proceedings](https://proceedings.iclr.cc/paper_files/paper/2026/file/9132583dd869acb0cafeb0ed25eb031a-Paper-Conference.pdf)
- HF: LLM4Binary/sk2decompile-struct-6.7b and sk2decompile-ident-6.7b (6.7B, Llama architecture, MIT, updated Jan 2026) — [HF struct](https://hf.co/LLM4Binary/sk2decompile-struct-6.7b); [HF ident](https://hf.co/LLM4Binary/sk2decompile-ident-6.7b)
- ReF-Decompile (arXiv 2502.12221): fine-tune/LoRA on top of llm4decompile-6.7b-v1.5, MIT license, on HF — [HF ylfeng/ReF-Decompile](https://hf.co/ylfeng/ReF-Decompile); [HF LoRA](https://hf.co/ylfeng/ReF-Decompile-lora)

**Nova (Purdue / lt-asset; ICLR 2025)**
- Generative LLM for assembly, further pre-trained from DeepSeek-Coder on disassembly of AnghaBench and compiled C/C++ from The Stack. Adds hierarchical attention and contrastive objectives aimed at assembly's low information density and optimization diversity. Reports up to 14.84–21.58% higher Pass@1/Pass@10 on decompilation and up to 6.17% higher Recall@1 on binary similarity detection than prior techniques — [arXiv 2311.13721](https://arxiv.org/abs/2311.13721); [ICLR poster](https://iclr.cc/virtual/2025/poster/30979)
- HF: lt-asset/nova-1.3b, nova-1.3b-bcr (1.35B), nova-6.7b-bcr (6.74B) ("bcr" = binary code recovery/decompilation). License BSD-3-Clause-Clear, updated Sept 2025 — [HF nova-1.3b](https://huggingface.co/lt-asset/nova-1.3b); [HF nova-1.3b-bcr](https://hf.co/lt-asset/nova-1.3b-bcr); [HF nova-6.7b-bcr](https://hf.co/lt-asset/nova-6.7b-bcr)

**Idioms (CMU; arXiv Feb 2025)**
- Luke Dramko, Claire Le Goues, Edward J. Schwartz. A training recipe that turns any LLM into a neural decompiler that also emits user-defined type definitions (structs). Introduces the REALTYPE dataset and uses interprocedural context. Claims state-of-the-art results at release — [arXiv 2502.04536](https://arxiv.org/abs/2502.04536)

**ReSym (Purdue; CCS 2024, Distinguished Paper, older)**
- Danning Xie, Zhuo Zhang, Nan Jiang, Xiangzhe Xu, Lin Tan, Xiangyu Zhang. Fine-tunes two LLMs (one for local variables, one for data structures) and adds a Prolog-based algorithm that aggregates and cross-checks multiple LLM queries to suppress hallucination. Recovers names and types from stripped binaries. Code and artifacts on GitHub — [GitHub lt-asset/resym](https://github.com/lt-asset/resym); [ACM DL](https://dl.acm.org/doi/10.1145/3658644.3670340)

**AutoDecompiler (arXiv June 2026, new)**
- Peipei Liu, Dan Li et al. An RL-trained decompilation LLM that does multi-turn refinement from compile, execute and I/O-test feedback. The abstract reports that it "consistently outperforms its single-turn counterparts under the same model size and input setting." The abstract gives no numeric comparisons to LLM4Decompile or SK²Decompile — [arXiv 2606.16162](https://arxiv.org/abs/2606.16162)
- Related 2025–26 methods: D-LiFT (RL with a D-SCORE accuracy + readability reward) and the DecLLM generate–verify–repair loop — [ACM survey 2026](https://dl.acm.org/doi/10.1145/3803633.3803666)

**Community HF decompiler fine-tunes (2024–2026, small, lightly documented)**
- Divij/Qwen3.5-4B-SFT-decompiler-O0-full (Apache-2.0, Apr 2026, dataset Divij/RLforDecomp); raafatabualazm/decompiler-* (Qwen3-4B-Thinking / DeepSeek-R1-Qwen3-8B LoRAs, Oct 2025–Mar 2026); Neo111x/falcon3-decompiler-3b/7b (Dec 2024); tarball0/ELF-Decompiler-GGUF (Mar 2026); Pavly03/esp32-decompiler-v1 (Qwen2.5-Coder-7B LoRA, Xtensa/ESP32) — [HF search "decompile"](https://huggingface.co/models?search=decompile); [HF Divij model](https://hf.co/Divij/Qwen3.5-4B-SFT-decompiler-O0-full)

**Assembly translation (x86 to ARM / RISC-V)**
- Guess & Sketch (Celine Lee et al., ICLR 2024, older): neurosymbolic. An LM proposes translations plus alignment and confidence, and a symbolic solver repairs low-confidence aligned segments. Evaluated on ARMv8 and RISC-V; transpiles 57.6% more examples than GPT-4 and 39.6% more than an engineered transpiler — [arXiv 2309.14396](https://arxiv.org/abs/2309.14396); [OpenReview](https://openreview.net/forum?id=qPFsIbF3V6)
- CRT, "From CISC to RISC" (arXiv Nov 2024): fine-tuned DeepSeekCoder-1.3B, quantizable for consumer hardware. 79.25% x86 to ARMv5 accuracy and 88.68% x86 to RISC-V. On Apple M2 it runs 1.73x faster than Rosetta, with 1.47x better energy efficiency and 2.41x better memory efficiency — [arXiv 2411.16341](https://arxiv.org/abs/2411.16341)
- Guaranteed Guess (GG) (Heakl, Hashmi, Abi, Lee, Mahmoud; arXiv June 2025): LLM candidate translations checked by a software-testing framework. 99% semantic correctness on HumanEval programs, 49% on BringupBench, over 98% unit-test code coverage. Same 1.73x / 1.47x / 2.41x vs Rosetta 2. Reports 0% transpilation accuracy for general coding LLMs (StarCoder2, DeepSeek, Qwen2.5). Authors "will open-source codes, data, models, and benchmarks" — [arXiv 2506.14606](https://arxiv.org/abs/2506.14606); [search summary of GG PDF](https://arxiv.org/pdf/2506.14606)

**Superoptimization**
- AlphaDev (Google DeepMind, Nature, June 2023, older): deep RL (AlphaZero-style) plays an "assembly game" over x86 instructions. Found small sorting routines, including the "swap and copy moves", that were merged into LLVM libc++. Up to 70% faster for short sequences and about 1.7% for sequences over 250K elements. Not an LLM, and no released model weights found — [Nature](https://www.nature.com/articles/s41586-023-06004-9); [DeepMind blog](https://deepmind.google/blog/alphadev-discovers-faster-sorting-algorithms/)
- SuperCoder (Anjiang Wei, Alex Aiken et al., Stanford/UIUC; arXiv May 2025): assembly superoptimization benchmark with gcc -O3 as baseline. Of 23 LLMs, Claude-opus-4 was best at 51.5% test-pass and 1.43x average speedup. RL fine-tuning of Qwen2.5-Coder-7B-Instruct, with a reward that combines correctness and speedup, raised it from 61.4% correctness / 1.10x to 95.0% / 1.46x — [arXiv 2505.11480](https://arxiv.org/abs/2505.11480); [OpenReview](https://openreview.net/forum?id=30iarHLvCS)
- An arXiv paper titled "Reinforcement Learning for Code Optimization" (2607.25970, July 2026) turned up in search. I did not read it — [arXiv PDF](https://arxiv.org/pdf/2607.25970)

### Inferences
- CRT and GG share the 1.73x / 1.47x / 2.41x Rosetta figures, so GG looks like the revised, testing-augmented version of CRT by the same MBZUAI-affiliated group.
- LLM Compiler remains the only released model trained at scale on both x86_64 and ARM assembly *and* LLVM-IR. I found no 2025–26 Meta successor.

### Gaps
- I could not confirm HF weight releases for SuperCoder, CRT/GG, Guess & Sketch, Idioms or AutoDecompiler. The HF search for "transpil" returned only unrelated Qiskit/pseudocode models.
- Base model and size for AutoDecompiler and exact ISA coverage for SK²Decompile were not in the abstracts I read.
- I found no published head-to-head of SK²Decompile vs LLM4Decompile-9B-v2 re-executability in the material I read.

## Which are open weights and usable today on Hugging Face?

### Takeaway
Freely downloadable today: LLM4Decompile (1.3B–22B, MIT), SK²Decompile (2x 6.7B, MIT), Nova (1.3B/6.7B, BSD-3-Clause-Clear), and ReF-Decompile (MIT). Meta LLM Compiler (7B/13B) is downloadable only after accepting its gated custom license. The assembly translators and superoptimizers appear to have no confirmed HF weights.

### Cited Findings
- LLM4Binary/llm4decompile-6.7b-v1.5: MIT, ~212K downloads; 9b-v2: MIT, ~32K; 22b-v2: MIT, ~12.5K — [HF](https://hf.co/LLM4Binary/llm4decompile-6.7b-v1.5); [HF](https://hf.co/LLM4Binary/llm4decompile-9b-v2); [HF](https://hf.co/LLM4Binary/llm4decompile-22b-v2)
- SK²Decompile struct/ident 6.7B: MIT, ~7.5K/9.6K downloads — [HF](https://hf.co/LLM4Binary/sk2decompile-struct-6.7b)
- Nova 1.3B/6.7B-bcr: BSD-3-Clause-Clear, with a demo Space (ejschwartz/nova-6.7b) — [HF](https://hf.co/lt-asset/nova-6.7b-bcr)
- facebook/llm-compiler-*: gated, license "other" — [HF](https://hf.co/facebook/llm-compiler-7b)
- The HF "assembly" model search is dominated by unrelated models (genome assembly, robotics, AssemblyAI) plus tiny toys (santacoder-finetuned-the-stack-assembly, gpt2-assembly) — [HF search "assembly"](https://huggingface.co/models?search=assembly)

### Inferences
- For practical x86_64 decompilation today, llm4decompile-9b-v2 (Ghidra-refine) or the SK²Decompile pair are the strongest openly licensed choices. For IR/assembly work including ARM, LLM Compiler is the only real option.

### Gaps
- ReSym and Idioms trained weights: repos exist, but I did not verify HF hosting.

## Which approaches produce the highest correctness?

### Takeaway
The biggest correctness gains come from execution feedback and verification, not from raw model size. Test-validated or RL-with-execution-reward approaches report the top numbers: SuperCoder at 95% correctness; GG at 99% on HumanEval-level x86 to RISC programs. Single-shot neural decompilers top out around 65% re-executability. A September 2026 study warns that "recompiles and passes tests" overstates true semantic preservation.

### Cited Findings
- RL with a correctness + speedup reward took Qwen2.5-Coder-7B from 61.4% to 95.0% correctness on assembly superoptimization — [arXiv 2505.11480](https://arxiv.org/abs/2505.11480)
- LLM-plus-testing (GG) reaches 99% on HumanEval programs but only 49% on BringupBench, a large drop on realistic code — [arXiv 2506.14606](https://arxiv.org/abs/2506.14606)
- Neurosymbolic LM + solver (Guess & Sketch) beats GPT-4 by 57.6% more transpiled examples — [arXiv 2309.14396](https://arxiv.org/abs/2309.14396)
- The best single-pass open decompiler is LLM4Decompile-9B-v2 at 64.9% re-executability on HumanEval-Decompile — [GitHub](https://github.com/albertan017/LLM4Decompile)
- Multi-turn RL with compile/execute feedback (AutoDecompiler) beats single-turn at equal size — [arXiv 2606.16162](https://arxiv.org/abs/2606.16162)
- Liu, Raff, Micinski (Sept 2026) evaluated 8 LLM decompiler systems in 9 configurations:
  - Functions that passed all shipped tests still diverged on synthesized inputs in 4.9% of cases overall, and up to 13% for a single system.
  - One LLM refinement of Ghidra output raised the build rate from 75% to 90% but cut the behavioral match rate from 74% to 62%.
  - Up to one tenth of CVE-grounded vulnerabilities silently disappeared from recompiled outputs.
  - The authors propose the fuzzing-based Decompile-Diverge oracle — [arXiv 2609.05370](https://arxiv.org/abs/2609.05370)
- Meta LLM Compiler's disassembly reaches only 45% round-trip and 14% exact match — [arXiv 2407.02524](https://arxiv.org/abs/2407.02524)

### Inferences
- The highest-correctness recipe is: specialized mid-size model + generate-verify-repair loop (tests, fuzzing, or a symbolic solver) + RL on execution rewards. Benchmark re-executability numbers (HumanEval-Decompile) probably overstate real-world fidelity; see the 2609.05370 findings.
- AlphaDev-style search gives *provably* correct results but only for tiny fixed kernels; LLM approaches scale to general functions at the cost of guarantees.

### Gaps
- No cross-paper benchmark compares all decompilers under the same fuzzing-based oracle; 2609.05370 names 8 systems, but I did not extract per-system names or scores.
- No ARM64-specific decompilation benchmark results were found for any open model.
