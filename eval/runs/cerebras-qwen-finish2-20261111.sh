#!/bin/bash
# Finish qwen-3.8-27b on Cerebras at 2 calls at a time: at 4 its long reasoning exceeds Cerebras' 150K tokens-per-minute
# limit (HTTP 429, retried, not billed, but each retry counts against --max-calls). Remaining function cells, then the
# 5 unfinished hex-function cells. No app battery (budget). Same settings otherwise.
cd /home/aharmon/BytecodeAgent
T=20261111
export BCA_CEREBRAS_CAP_USD=28
P=".venv/bin/python -m bca.liveeval run"; X="--model cerebras:qwen-3.8-27b --jobs 2 --max-infra-errors 40"
$P --run-dir eval/runs/cerebras-qwen-3.8-27b-$T --tasks all $X --max-calls 1100 >> eval/runs/cerebras-qwen-3.8-27b-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/hex-cerebras-qwen-3.8-27b-$T --tasks round1 --paths hex $X --max-calls 130 >> eval/runs/hex-cerebras-qwen-3.8-27b-$T.out 2>&1; echo "$(date +%T) hex exit $?"
echo ALLDONE
