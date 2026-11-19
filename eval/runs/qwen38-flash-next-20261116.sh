#!/bin/bash
# Qwen3.8-Flash-Next on the user's main DGX Spark cluster (user, 2026-11-16: jumps the queue; separate from the
# backlogged Qwen3.8-27B). Served by the Aurora SRE session behind its LiteLLM gateway; the endpoint and key live in
# ~/.config/bca/qwen-flash-next.{env,key}, not in the repo. Config fixed 2026-11-16 ~22:15 UTC (Aurora SRE): NVIDIA
# vLLM 0.31.0+88641915.nv26.10, TP=2 + expert parallel over two Sparks, max-model-len 131072, MTP speculative
# decoding x2, FP8 KV cache, max-num-seqs 16; checkpoint nvidia/Qwen3.8-Flash-Next-NVFP4 @ fc694b54fb01 (published,
# not re-quantised); thinking on at the template default (reasoning_effort xhigh), checkpoint sampling (T 1.0,
# top_p 0.95, top_k 20). Audit 11-16: no tools, reasoning only in reasoning_content, every chunk names the model.
# Smoke 5/5 first attempt. Token-budget mode (no wall-clock limit), 2 calls at once (enforced on the key).
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; . ~/.config/bca/qwen-flash-next.env; T=20261116
M="--model vllm:Qwen3.8-Flash-Next@budget --jobs 2"
step eval/runs/qwen38-flash-next-budget-$T 224 --tasks all $M --max-calls 900 || exit 3
step eval/runs/hex-qwen38-flash-next-budget-$T 16 --tasks round1 --paths hex $M --max-calls 70 || exit 3
step eval/runs/apps-qwen38-flash-next-budget-$T 96 --tasks apps $M --max-calls 400 || exit 3
n=$(gate eval/runs/hex-qwen38-flash-next-budget-$T); echo "Qwen3.8-Flash-Next hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-qwen38-flash-next-budget-$T 16 --tasks apps --paths hex --samples 1 $M --max-calls 70; else echo "gate not met"; fi
echo ALLDONE
