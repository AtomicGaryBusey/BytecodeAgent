#!/bin/bash
# Cerebras full sets (approved 2026-11-11, balance $35): gpt-oss-120b, then qwen-3.8-27b. Per model: the four function
# batteries, hex functions, the app battery, then hex programs if hex functions reach >= 12/16 within four.
# Per-model caps $12 (gpt-oss-120b) and $28 (qwen-3.8-27b); cerebras-watch stops everything past $33.
cd /home/aharmon/BytecodeAgent
T=20261111
P=".venv/bin/python -m bca.liveeval run"
for m in gpt-oss-120b qwen-3.8-27b; do
  [ $m = gpt-oss-120b ] && export BCA_CEREBRAS_CAP_USD=12 || export BCA_CEREBRAS_CAP_USD=28
  S=cerebras:$m; X="--model $S --jobs 4 --max-infra-errors 20"
  $P --run-dir eval/runs/cerebras-$m-$T --tasks all $X --max-calls 700 > eval/runs/cerebras-$m-$T.out 2>&1; echo "$(date +%T) $m functions exit $?"
  $P --run-dir eval/runs/hex-cerebras-$m-$T --tasks round1 --paths hex $X --max-calls 70 > eval/runs/hex-cerebras-$m-$T.out 2>&1; echo "$(date +%T) $m hex exit $?"
  $P --run-dir eval/runs/apps-cerebras-$m-$T --tasks apps $X --max-calls 400 > eval/runs/apps-cerebras-$m-$T.out 2>&1; echo "$(date +%T) $m apps exit $?"
  n=$(grep -c "final=1" eval/runs/hex-cerebras-$m-$T.out); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then
    $P --run-dir eval/runs/apps-hex-cerebras-$m-$T --tasks apps --paths hex --samples 1 $X --max-calls 70 > eval/runs/apps-hex-cerebras-$m-$T.out 2>&1; echo "$(date +%T) $m hex programs exit $?"
  else echo "$m: gate not met, no hex programs"; fi
done
echo ALLDONE
