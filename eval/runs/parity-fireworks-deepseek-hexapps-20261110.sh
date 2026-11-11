#!/bin/bash
# DeepSeek V4.1 Flash hex programs (approved 2026-11-11 after it reached 16/16 hex functions; estimate $1-3).
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=40
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/apps-hex-fireworks-deepseek-20261110 --tasks apps --paths hex --samples 1 --subjects fireworks:deepseek-v4p1-flash --jobs 4 --max-calls 70 --max-infra-errors 20 > eval/runs/apps-hex-fireworks-deepseek-20261110.out 2>&1
echo "$(date +%T) exit $?"; echo ALLDONE
