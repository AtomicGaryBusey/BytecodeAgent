#!/bin/bash
# Parity pick-up (approved 2026-11-10): Kimi K3.1 Pro hex functions, apps, then hex programs if hex functions reach >= 12/16 within four.
cd /home/aharmon/BytecodeAgent
P=".venv/bin/python -m bca.liveeval run"; S=kimi:kimi-code/k3.1-pro
$P --run-dir eval/runs/hex-kimi-k31pro-20261110 --tasks round1 --paths hex --subjects $S --jobs 2 --max-calls 40 > eval/runs/hex-kimi-k31pro-20261110.out 2>&1
$P --run-dir eval/runs/apps-kimi-k31pro-20261110 --tasks apps --subjects $S --jobs 2 --max-calls 160 > eval/runs/apps-kimi-k31pro-20261110.out 2>&1
n=$(grep -c "final=1" eval/runs/hex-kimi-k31pro-20261110.out)
echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then
  $P --run-dir eval/runs/apps-hex-kimi-k31pro-20261110 --tasks apps --paths hex --samples 1 --subjects $S --jobs 2 --max-calls 60 > eval/runs/apps-hex-kimi-k31pro-20261110.out 2>&1
else echo "gate not met: no hex programs"; fi
echo ALLDONE
