#!/bin/bash
# Re-run the four app cells superseded on 2026-11-11 (stream-path alignment false reject, fixed in bca/stream.py).
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=40
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/apps-fireworks-20261110 --tasks apps --subjects fireworks:deepseek-v4p1-flash,fireworks:nemotron-3-ultra-nvfp4 --jobs 4 --max-calls 480 --max-infra-errors 20 >> eval/runs/apps-fireworks-20261110.out 2>&1
echo "$(date +%T) exit $?"; echo ALLDONE
