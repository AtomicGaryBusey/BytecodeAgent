#!/bin/bash
# Finish qwen-3.8-27b's function batteries on Cerebras (the run stopped at its 700-call cap with 34 cells left).
# The app battery and hex programs are NOT run: at ~$0.03 a call Qwen's apps would cost about $20-25, more than the
# remaining balance ($11.30 on 2026-11-11 13:36). Waits for the hex-functions run to finish first.
cd /home/aharmon/BytecodeAgent
while pgrep -f "run-dir eval/runs/hex-cerebras-qwen-3.8-27b-20261111 " >/dev/null; do sleep 20; done
export BCA_CEREBRAS_CAP_USD=28
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/cerebras-qwen-3.8-27b-20261111 --tasks all --model cerebras:qwen-3.8-27b --jobs 4 --max-infra-errors 20 --max-calls 900 >> eval/runs/cerebras-qwen-3.8-27b-20261111.out 2>&1
echo "$(date +%T) qwen functions finish exit $?"; echo ALLDONE
