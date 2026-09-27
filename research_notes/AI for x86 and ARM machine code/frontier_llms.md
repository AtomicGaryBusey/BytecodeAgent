# Frontier General-Purpose LLMs Writing x86-64 and ARM Assembly / Machine Code

Scope note: current as of 2026-09-26. Many published numbers use models that are 1-2 generations old (GPT-4/4o, Claude 3.5/3.7, Gemini 2.0/2.5 Flash). Each finding is tagged with the model generation and paper date. There is still no widely adopted, continuously updated leaderboard for "write assembly from scratch".

## Q1. Which general-purpose models do best at assembly/machine code as of 2026, and by how much?

### Takeaway
In every head-to-head evaluation found, Anthropic's Claude models rank at or near the top on assembly and low-level tasks (SuperCoder 2025/COLM 2026: Claude Opus 4 first; CodegenBench June 2026: Claude Opus 4.7 first; LLM-as-compiler 2025: Claude 3.5 Sonnet first). On SIMD intrinsics in 2025, reasoning models (DeepSeek-R1, Gemini 2.5 Flash) led by a wide margin. Absolute success rates are low. Without scaffolding, the best models pass roughly 30-75% of tests depending on the task, and general-purpose models score near 0% on whole-program ISA-to-ISA translation.

### Cited Findings
**SuperCoder / "Improving Assembly Code Performance with LLMs" (arXiv 2505.11480 v4; COLM 2026). Task: rewrite gcc -O3 x86-64 assembly to run faster; 23 LLMs; 8,072 real C programs**
- Best model was claude-opus-4: 90.0% compile, 51.5% test pass, 1.43x average speedup over gcc -O3. Next were claude-sonnet-4 (87.0% / 37.0% / 1.30x), claude-3.7-sonnet (94.5% / 58.5% / 1.22x) and DeepSeek-V3 (94.0% / 43.0% / 1.21x) — [SuperCoder, arXiv 2505.11480](https://arxiv.org/pdf/2505.11480)
- Other frontier models did poorly. gpt-5: 78.5% compile, 6.0% test pass, 1.13x. gpt-4o: 81.0%, 5.0%, 1.02x. o4-mini: 25.0%, 4.5%. gemini-2.0-flash-001: 57.5%, 4.0%. Llama-4-Maverick: 77.5%, 7.0%. Qwen2.5-72B: 59.5%, 7.5%. DeepSeek-R1: 0% compile, 0% test — [SuperCoder](https://arxiv.org/pdf/2505.11480)
- A 7B model fine-tuned with RL (SuperCoder, from Qwen2.5-Coder-7B-Instruct, which starts at 61.4% correct and 1.10x) reached 95.0% correctness and 1.46x speedup, beating every frontier model — [SuperCoder](https://arxiv.org/pdf/2505.11480)
- GEPA prompt evolution (gpt-4o under evaluation, gpt-5 as reflector) moved gpt-4o only from 81.0% to 84.0% compile and from 5.0% to 7.5% test pass — [SuperCoder](https://arxiv.org/pdf/2505.11480)

**CodegenBench (arXiv 2606.04023, June 2026). Task: C code with hardware intrinsics for BLAS-style kernels on x86_64, Kunpeng (ARM) and Sunway**
- Models tested: Claude Sonnet 4.6, Claude Opus 4.6/4.7, Qwen 3.6 Plus, DeepSeek V3.2, V4 Flash, V4 Pro, Qwen 3.5 Plus, Qwen 3.6 Flash. No OpenAI or Google models were included — [CodegenBench](https://arxiv.org/html/2606.04023)
- x86_64 BLAS Pass@1 ranged 0.28-0.74, with Claude Opus 4.7 best at 0.74. Kunpeng (ARM) BLAS Pass@1 ranged 0.06-0.76; Opus 4.7 scored 0.76 (Pass@5 0.85). On Sunway most models scored 0.00 and Opus 4.7 scored 0.48 — [CodegenBench](https://arxiv.org/html/2606.04023)
- The authors report a "substantial performance degradation across all models as the target platform transitions from x86 to ARM", although the top model held up on Kunpeng (the wide 0.06-0.76 range shows weaker models collapse) — [CodegenBench](https://arxiv.org/html/2606.04023)
- Functionally correct kernels ran 6-10x slower than reference libraries (no cache blocking or vectorization) — [CodegenBench](https://arxiv.org/html/2606.04023)

**"Exploring the Feasibility of End-to-End LLM as a Compiler" (arXiv 2511.04132, IJCNN 2025). Task: C to assembly directly; x86, ARM and RISC-V; CompilerEval, 20 cases**
- Models: GPT-4o, Claude-3.5-Sonnet, Gemini-2.0-Flash-Exp, LLaMA-3.1-405B, plus o1 and smaller Llamas. Claude-3.5-Sonnet had the highest compile-success rates — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- Average success@1 by ISA: ARM 35.02%, RISC-V 32.30%, x86 27.85%. Generated ARM/RISC-V code outperformed generated x86 code, and the authors cite x86 memory-addressing and register complexity — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- Prompt engineering added +2.32 pp on average (+7.5 pp for Claude). The o1 reasoning model added about +5 pp overall and 30+ pp on some kernels (rotate, resize, saxpy). Going from Llama-70B to 405B added +1.55 pp — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)

**SimdBench (arXiv 2507.15224, July 2025). 136 tasks; SSE, AVX, Neon, SVE, RVV intrinsics; 18 LLMs**
- Pass@1 for DeepSeek-R1 (the best model in all six scenarios): scalar 89.12, SSE 65.15, AVX 70.29, Neon 62.21, SVE 40.74, RVV 19.26 — [SimdBench](https://arxiv.org/pdf/2507.15224)
- Gemini-2.5-Flash was second: SSE 62.21, AVX 60.29, Neon 46.62, SVE 8.82, RVV 0.15 — [SimdBench](https://arxiv.org/pdf/2507.15224)
- Claude-3.5-Sonnet: SSE 31.18, AVX 38.09, Neon 33.38, SVE 12.65. GPT-4o: SSE 27.35, AVX 34.71, Neon 27.65, SVE 8.97, RVV 0.00. Qwen-Coder-Plus scored only 5-7% on SSE/AVX/Neon despite 82.94% on scalar code — [SimdBench](https://arxiv.org/pdf/2507.15224)
- Every model except Gemini-2.0-Flash scored above 75% Pass@1 on scalar code, and every model dropped on SIMD. The ordering was x86 (SSE/AVX) >= Neon > SVE >> RVV, following how much training data each ISA has — [SimdBench](https://arxiv.org/pdf/2507.15224)

**Agentic, long-horizon evidence (Anthropic, Feb 2026)**
- Sixteen parallel Claude Opus 4.6 agents wrote a 100k-line Rust C compiler with x86-64/x86-32, ARM and RISC-V backends. It builds Linux 6.9, QEMU, FFmpeg, SQLite, PostgreSQL, Redis and Doom, and passes about 99% of most compiler test suites including the GCC torture tests. The run used about 2B input and 140M output tokens and cost under $20k — [Anthropic engineering blog](https://www.anthropic.com/engineering/building-c-compiler)
- Limitations: the 16-bit x86 real-mode output was correct but over 60KB, beyond Linux's 32KB limit, so that stage falls back to GCC. The self-written assembler and linker were "still somewhat buggy". Generated code is less efficient than gcc -O0. ARM and RISC-V builds needed no external GCC — [Anthropic](https://www.anthropic.com/engineering/building-c-compiler); [InfoQ](https://www.infoq.com/news/2026/02/claude-built-c-compiler/); [GitHub anthropics/claudes-c-compiler](https://github.com/anthropics/claudes-c-compiler)

### Inferences
- Claude models rank first across SuperCoder, CodegenBench and LLM-as-compiler. However, CodegenBench omitted GPT and Gemini, and SuperCoder tested gpt-5 but not a newer Gemini, so there is no clean 2026 head-to-head across all vendors on pure assembly.
- GPT-5's result on SuperCoder (6% tests passed) is anomalously low next to its general coding strength. This may reflect output-format or prompt issues, like DeepSeek-R1's 0% (see Q3), rather than true inability.
- Small models fine-tuned specifically for assembly (SuperCoder-7B, GG-1.5B) beat general frontier models on narrow assembly tasks by large margins.

### Gaps
- I found no published benchmark evaluating GPT-5.x, Gemini 3.x and Claude 4.6/4.7 together on hand-written x86-64 or AArch64 assembly as of Sept 2026.
- I found no quantitative evaluation on ARM32/Thumb/Thumb-2 (Cortex-M) assembly generation by frontier models. The LLM-as-compiler paper tested "ARM" on embedded boards but the summary does not separate AArch64 from ARM32.
- The OASIF paper (arXiv 2606.29155, June 2026) compares GPT-5, Claude Sonnet 4.5, Opus 4.6 and Gemini 2.5 Pro, but on understanding obfuscated x86 assembly rather than generating it. I could not extract its numbers.

## Q2. What published evaluations exist (hand-written asm, SIMD, shellcode, ISA-to-ISA translation)?

### Takeaway
The main evaluations are Guess & Sketch (ICLR 2024; ARMv8<->RISC-V), CRT / Guaranteed Guess (2024-2025; x86 to ARMv5/ARMv8/RISC-V), the C-to-x86 neural-compilation study (EMNLP Findings 2024), SuperCoder (x86-64 superoptimization), LLM-as-compiler (IJCNN 2025), SimdBench (2025), VecIntrinBench (Neon to RVV) and CodegenBench (2026). In whole-program assembly translation, general-purpose LLMs used alone scored only 0-10%.

### Cited Findings
- **Guess & Sketch** (Lee et al., ICLR 2024, arXiv 2309.14396; *older, GPT-4 era*). A neurosymbolic transpiler: the LM's guess plus a Rosette solver sketch for ARMv8<->RISC-V. It transpiles 57.6% more examples than GPT-4 and 39.6% more than an engineered transpiler — [arXiv 2309.14396](https://arxiv.org/pdf/2309.14396)
- Few-shot GPT-4 on RISC-V to ARMv8: Project Euler 11.1%, Benchmx 0%, Unix commands 18.2%. On ARMv8 to RISC-V: 4.44%, 0%, 27.3%. Guess & Sketch scored 80% / 18.8% / 81.2% and 75.6% / 25.0% / 81.2% — [Guess & Sketch](https://arxiv.org/pdf/2309.14396)
- **CRT, "From CISC to RISC"** (arXiv 2411.16341, Nov 2024) and **Guaranteed Guess (GG)** (arXiv 2506.14606; EMNLP 2025 Findings). A fine-tuned small LM translates x86 to ARM. CRT reports 79.25% x86 to ARMv5 and 88.68% x86 to RISC-V, and code 1.73x faster than Rosetta 2 on Apple M2 with 2.41x better memory use and 1.47x better energy — [arXiv 2411.16341](https://arxiv.org/abs/2411.16341)
- GG baselines on HumanEval x86 to ARM (-O0 / -O2). GPT-4o: ARMv5 8.48% / 3.64%, ARMv8 10.3% / 4.24%, BringupBench ARMv8 1.54% / 0%. Qwen2.5-Coder-1.5B/3B, StarCoder2-3B and DeepSeek-R1-distills (1.5B, 7B) scored about 0%. GG-1.5B scored 93.71% / 50.30% on ARMv5, 99.39% / 45.12% on ARMv8, and 49.23% / 15.38% on BringupBench — [GG, arXiv 2506.14606](https://arxiv.org/pdf/2506.14606); [ACL Anthology EMNLP Findings 2025](https://aclanthology.org/2025.findings-emnlp.1330.pdf)
- The GG authors call GPT-4o's 1.5-8% accuracy "far below usable thresholds", which they say highlights that general-purpose LLMs are unsuited to this task without specialization — [GG](https://arxiv.org/pdf/2506.14606)
- **Neural compilation, C to x86 assembly** (Zhang et al., EMNLP Findings 2024; *older*). A 13B fine-tuned model reached 91.88% IO accuracy against 40.85% for GPT-4 Turbo. Base LLMs got only 3.8% on NumericBench, which requires converting numeric constants such as float literals into the hex or integer bit patterns placed in `.LC0` data — [ACL Anthology 2024.findings-emnlp.55](https://aclanthology.org/2024.findings-emnlp.55.pdf)
- **SuperCoder** (x86-64 superoptimization, 23 LLMs) — see Q1 — [arXiv 2505.11480](https://arxiv.org/pdf/2505.11480)
- **LLM-as-compiler / CompilerEval** (x86, ARM, RISC-V; GPT-4o, Claude 3.5 Sonnet, Gemini 2.0 Flash, Llama 3.1 405B) — see Q1 — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- **SimdBench** (SSE, AVX, Neon, SVE, RVV) — see Q1 — [arXiv 2507.15224](https://arxiv.org/pdf/2507.15224). **VecIntrinBench** evaluates migrating Arm Neon intrinsics to RVV — [arXiv 2511.18867](https://arxiv.org/pdf/2511.18867)
- **CodegenBench** (x86_64, Kunpeng ARM, Sunway; 2026 Claude/DeepSeek/Qwen models) — see Q1 — [arXiv 2606.04023](https://arxiv.org/html/2606.04023)
- A master's thesis evaluates automated program repair of ARM assembly with LLMs (IST Lisbon); I did not extract its numbers — [Técnico Lisboa thesis](https://fenix.tecnico.ulisboa.pt/downloadFile/844820067129635/96248-Dissertacao_Final.pdf)

### Inferences
- A consistent pattern: general LLM plus few-shot prompting is weak (about 0-40%). Adding a verifier or solver loop (Guess & Sketch), fine-tuning a small model on paired assembly (GG, CRT, neural compilation), or iterating in an agent loop against tests (Carlini's compiler) raises results dramatically.
- Whole-function or whole-program translation between ISAs is much harder than writing a short function, because errors compound with length (GG attributes 17% of its failures to context truncation).

### Gaps
- I found no peer-reviewed benchmark measuring LLM shellcode generation (x86 or ARM) with quantitative pass rates. Searches returned only generic code-security benchmarks.
- I found no rerun of Guess & Sketch or GG baselines on 2026 frontier models (Claude 4.x, GPT-5.x, Gemini 3.x).

## Q3. Typical failure modes

### Takeaway
Frontier models mostly produce syntactically plausible assembly; the failures are semantic. The main ones are ABI and calling-convention violations, stack and register mismanagement, broken data flow, wrong memory addressing, hallucinated instructions or intrinsics, and numeric-constant encoding errors. x86 is reported as harder than ARM or RISC-V for direct generation. Fine-tuned models reach near-zero syntax errors and fail mainly on data flow.

### Cited Findings
- GPT-4o's failures on x86-64 (81% compile, 5% correct) fall into four groups: (1) missing directives such as stack-canary setup and `.cfi_*` metadata, causing crashes; (2) calling-convention errors, e.g. repeated `scanf` calls without proper argument setup; (3) semantic errors such as wrong pointer use or altered algorithms; (4) oversimplified stack and register management causing memory errors or invalid control flow. The paper summarises that GPT-4o "generates syntactically valid assembly but frequently violates low-level conventions" — [SuperCoder](https://arxiv.org/pdf/2505.11480)
- DeepSeek-R1 scored 0% compile on SuperCoder because it "spend[s] its entire output length on reasoning about instruction semantics" instead of emitting code — [SuperCoder](https://arxiv.org/pdf/2505.11480)
- Error counts across C-to-asm generation (x86/ARM/RISC-V): instruction errors (hallucinated or misused instructions) 7,269; symbol redefinition 2,556; unrecognized character 2,135; invalid register usage 2,052; absolute-expression errors 1,880; segfaults 1,156; wrong results 622; illegal instruction 438; undefined reference 267 — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- The same study found x86 the hardest target (27.85% vs ARM 35.02%), citing x86 memory-access instructions and register handling — [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- Guess & Sketch failure breakdown for few-shot GPT-4 (Project Euler, 45 programs, ARM<->RISC-V): 62 of the 90 translation attempts failed to compile due to ISA errors (wrong-ISA or invalid instructions). Other failures: memory 10, math 7, references 3; 5 correct — [Guess & Sketch](https://arxiv.org/pdf/2309.14396)
- GG's error categories for x86 to ARM: register allocation, flag handling, calling conventions, memory addressing and constant materialization (e.g. literal pool vs immediate loads), plus duplicate code, invalid control flow, misused registers or intermediate values, and stack errors. These mostly reflect "broken data flow rather than syntax issues". Example: a register overwrite corrupting a loop counter — [GG](https://arxiv.org/pdf/2506.14606)
- Accuracy falls sharply on optimized source assembly: GG-1.5B drops from 99.39% (-O0) to 45.12% (-O2) on ARMv8; GPT-4o drops from 10.3% to 4.24% — [GG](https://arxiv.org/pdf/2506.14606)
- Numeric-constant conversion: base LLMs got only 3.8% on NumericBench, since compilers emit float and other constants as raw bit patterns — [EMNLP Findings 2024](https://aclanthology.org/2024.findings-emnlp.55.pdf)
- Hallucinated SIMD intrinsics are common, e.g. `svcmul_f64_z` and `_mm256_reduce_add_ps` (neither exists). Complex arithmetic scores about 8 pp lower than real arithmetic — [CodegenBench](https://arxiv.org/html/2606.04023). SimdBench likewise finds "use of undeclared identifier" errors caused by invented intrinsics with names similar to real ones — [SimdBench](https://arxiv.org/pdf/2507.15224)
- Stock Qwen/DeepSeek tokenizers split ARM mnemonics and registers badly (e.g. `ldr r1, r2` becomes fragments like `ld r ␣ r 1`). GG extended the tokenizer to fix this — [GG](https://arxiv.org/pdf/2506.14606)
- ISA coverage follows training-data availability: correctness falls from SSE/AVX to Neon to SVE to RVV — [SimdBench](https://arxiv.org/pdf/2507.15224) — and collapses on proprietary ISAs such as Sunway — [CodegenBench](https://arxiv.org/html/2606.04023)
- Code size and encoding limits: Claude's compiler emitted correct 16-bit x86 using 66/67 operand/address-size prefixes but exceeded Linux's 32KB real-mode limit. Its assembler and linker (instruction encoding, relocations) were the last and buggiest parts — [Anthropic](https://www.anthropic.com/engineering/building-c-compiler)

### Inferences
- The failure profile of frontier models on assembly has shifted from "invalid syntax" (GPT-4 era, ISA errors dominate) to "valid but ABI- or semantics-incorrect" (GPT-4o/Claude 4 era). Test-driven feedback loops are therefore the main lever.
- Evidence on ARM vs x86 is mixed. Direct C-to-asm generation is easier on ARM (regular load/store ISA), while SIMD intrinsics are easier on x86 (more training data). SVE and RVV, with predicated or length-agnostic vectors, are hardest.

### Gaps
- I found no systematic study isolating condition-flag errors (x86 EFLAGS vs ARM NZCV and conditional execution) or Thumb IT-block errors in frontier-model output.
- I found no study quantifying AAPCS64 vs SysV x86-64 ABI errors separately.

## Q4. Does direct machine-code (hex byte) emission work, or is an assembler always used?

### Takeaway
Every benchmark found has the model emit assembly text (or C with intrinsics), which is then assembled or compiled by gcc/as/clang. None measures raw opcode-byte emission. The only direct comment is an unquantified blog claim that small functions of a few dozen bytes can be produced correctly and that reliability breaks down quickly with size. Agentic systems write assembler or encoder code rather than emitting bytes token by token.

### Cited Findings
- SuperCoder, GG/CRT, Guess & Sketch, LLM-as-compiler and the EMNLP neural-compilation work all generate assembly text and verify it by assembling, linking and running tests (e.g. QEMU emulation, unit tests) — [SuperCoder](https://arxiv.org/pdf/2505.11480); [GG](https://arxiv.org/pdf/2506.14606); [Guess & Sketch](https://arxiv.org/pdf/2309.14396); [arXiv 2511.04132](https://arxiv.org/html/2511.04132)
- Practitioner claim, with no experimental data: if the ISA is specified, correct machine code is produced "with a high probability at the level of small functions (about several tens of bytes)". Reliability "breaks down rapidly as the scale increases", and hex bytes are a token-inefficient representation — [Kumamemo blog](https://kumagi.com/blog/en/posts/does-ai-emit-machine-code) (opinion, unverified)
- In Carlini's project, Claude produced machine code by writing an assembler and linker in Rust (instruction encoders), not by emitting bytes. Those components remained the buggiest — [Anthropic](https://www.anthropic.com/engineering/building-c-compiler)
- Tokenization is a documented obstacle even for assembly text (GG's tokenizer fertility work). Raw hex would be worse, but this is not measured — [GG](https://arxiv.org/pdf/2506.14606)

### Inferences
- The practical standard is: LLM emits assembly text or calls an encoder library or tool, then an assembler (as/llvm-mc/keystone) produces the bytes, followed by testing or emulation. Direct hex emission is at best suitable for tiny snippets and needs disassembly or execution checks.
- Instruction encoding is a deterministic, rule-based task that tools do perfectly. Given the models' documented weaknesses with numeric constants and tokenization, having an LLM hand-emit bytes adds risk with little benefit.

### Gaps
- I found no peer-reviewed or benchmarked measurement of frontier LLMs emitting raw x86 or ARM opcode bytes (e.g. accuracy of hex encoding for given instructions, or full shellcode byte strings). This is an open evaluation gap as of Sept 2026.
