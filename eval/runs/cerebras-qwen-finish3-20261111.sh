#!/bin/bash
# The remaining qwen-3.8-27b function cells (finish2's function step was refused: the old runner still held the lock).
cd /home/aharmon/BytecodeAgent
while pgrep -f "cerebras-qwen-finish2-20261111.sh" >/dev/null; do sleep 20; done
export BCA_CEREBRAS_CAP_USD=28
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/cerebras-qwen-3.8-27b-20261111 --tasks all --model cerebras:qwen-3.8-27b --jobs 2 --max-infra-errors 40 --max-calls 1100 >> eval/runs/cerebras-qwen-3.8-27b-20261111.out 2>&1
echo "$(date +%T) functions exit $?"; echo ALLDONE
