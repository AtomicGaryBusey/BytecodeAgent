#!/bin/bash
# Finish DeepSeek V4.1 Flash / Nemotron 3 Ultra apps: the first run stopped at its 400-call cap with 15 cells left
# (Nemotron averages ~2.5 attempts a cell). Same settings, cap raised to 480. Waits for the first run to exit.
cd /home/aharmon/BytecodeAgent
while pgrep -f "run-dir eval/runs/apps-fireworks-20261110 " >/dev/null; do sleep 20; done
export BCA_FOUNDRY_CAP_USD=40
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/apps-fireworks-20261110 --tasks apps --subjects fireworks:deepseek-v4p1-flash,fireworks:nemotron-3-ultra-nvfp4 --jobs 4 --max-calls 480 --max-infra-errors 20 >> eval/runs/apps-fireworks-20261110.out 2>&1
echo "$(date +%T) apps finish exit $?"; echo ALLDONE
