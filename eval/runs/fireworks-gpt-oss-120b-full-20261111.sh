#!/bin/bash
# gpt-oss-120b via Fireworks, full set (approved 2026-11-11; estimate $3-6): the four function batteries, hex functions,
# the app battery, then hex programs if hex functions reach >= 12/16 within four. Per-model cap $15; the Fireworks
# watcher stops every Fireworks run past a ledger total of $150 (about $151 of credit in all).
cd /home/aharmon/BytecodeAgent
T=20261111
export BCA_FOUNDRY_CAP_USD=15
P=".venv/bin/python -m bca.liveeval run"; X="--model fireworks:gpt-oss-120b --jobs 4 --max-infra-errors 20"
$P --run-dir eval/runs/fireworks-gpt-oss-120b-$T --tasks all $X --max-calls 700 > eval/runs/fireworks-gpt-oss-120b-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/hex-fireworks-gpt-oss-120b-$T --tasks round1 --paths hex $X --max-calls 70 > eval/runs/hex-fireworks-gpt-oss-120b-$T.out 2>&1; echo "$(date +%T) hex exit $?"
$P --run-dir eval/runs/apps-fireworks-gpt-oss-120b-$T --tasks apps $X --max-calls 400 > eval/runs/apps-fireworks-gpt-oss-120b-$T.out 2>&1; echo "$(date +%T) apps exit $?"
n=$(grep -c "final=1" eval/runs/hex-fireworks-gpt-oss-120b-$T.out); echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then
  $P --run-dir eval/runs/apps-hex-fireworks-gpt-oss-120b-$T --tasks apps --paths hex --samples 1 $X --max-calls 70 > eval/runs/apps-hex-fireworks-gpt-oss-120b-$T.out 2>&1; echo "$(date +%T) hex programs exit $?"
else echo "gate not met, no hex programs"; fi
echo ALLDONE
