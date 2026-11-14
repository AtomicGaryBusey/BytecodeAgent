#!/bin/bash
# The six backlogged Claude models (user, 2026-11-11), through Claude Code (CLI 2.1.322) on the Claude Max plan, one
# after another: the four function batteries, hex functions, the app battery, then hex programs if hex functions reach
# >= 12/16 within four. CLI-default effort per model (as every Claude row), 4 calls at a time. A plan usage limit stops
# a run cleanly; it resumes with the same command.
cd /home/aharmon/BytecodeAgent
T=20261111
P=".venv/bin/python -m bca.liveeval run"
for m in claude-opus-5 claude-fable-5 claude-opus-4-7 claude-sonnet-4-6 claude-opus-4-6 claude-opus-4-5-20251101; do
  X="--subjects $m --jobs 4"
  $P --run-dir eval/runs/$m-$T --tasks all $X --max-calls 450 > eval/runs/$m-$T.out 2>&1; echo "$(date +%T) $m functions exit $?"
  $P --run-dir eval/runs/hex-$m-$T --tasks round1 --paths hex $X --max-calls 70 > eval/runs/hex-$m-$T.out 2>&1; echo "$(date +%T) $m hex exit $?"
  $P --run-dir eval/runs/apps-$m-$T --tasks apps $X --max-calls 250 > eval/runs/apps-$m-$T.out 2>&1; echo "$(date +%T) $m apps exit $?"
  n=$(grep -c "final=1" eval/runs/hex-$m-$T.out); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then
    $P --run-dir eval/runs/apps-hex-$m-$T --tasks apps --paths hex --samples 1 $X --max-calls 70 > eval/runs/apps-hex-$m-$T.out 2>&1; echo "$(date +%T) $m hex programs exit $?"
  else echo "$m: gate not met"; fi
done
echo ALLDONE
