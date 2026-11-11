#!/bin/bash
# Resume MiniMax M3 apps after the IncompleteRead crash (fixed in vendor_foundry.py: HTTPException is now a transient
# infrastructure error). Same settings as parity-fireworks-minimax-20261110.sh.
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=45
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/apps-fireworks-minimax-20261110 --tasks apps --subjects fireworks:minimax-m3 --jobs 4 --max-calls 300 --max-infra-errors 20 >> eval/runs/apps-fireworks-minimax-20261110.out 2>&1
echo "$(date +%T) apps exit $?"; echo ALLDONE
