#!/bin/bash
# Meta Muse Code agent-harness study (approved 2026-11-11): muse-spark-1.3-contributor (the account default; Meta's
# contributor tier, no subscription allowance) through Muse Code 1.4.15. The four function batteries, hex functions,
# the app battery, then hex programs if hex functions reach >= 12/16 within four. Reported apart from the tool-free rows.
cd /home/aharmon/BytecodeAgent
T=20261111
P=".venv/bin/python -m bca.liveeval run"; X="--model muse:muse-spark-1.3-contributor --jobs 3"
$P --run-dir eval/runs/muse-$T --tasks all $X --max-calls 500 > eval/runs/muse-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/hex-muse-$T --tasks round1 --paths hex $X --max-calls 70 > eval/runs/hex-muse-$T.out 2>&1; echo "$(date +%T) hex exit $?"
$P --run-dir eval/runs/apps-muse-$T --tasks apps $X --max-calls 300 > eval/runs/apps-muse-$T.out 2>&1; echo "$(date +%T) apps exit $?"
n=$(grep -c "final=1" eval/runs/hex-muse-$T.out); echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then
  $P --run-dir eval/runs/apps-hex-muse-$T --tasks apps --paths hex --samples 1 $X --max-calls 70 > eval/runs/apps-hex-muse-$T.out 2>&1; echo "$(date +%T) hex programs exit $?"
else echo "gate not met"; fi
echo ALLDONE
