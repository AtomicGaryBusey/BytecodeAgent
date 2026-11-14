#!/bin/bash
# Mistral Vibe agent-harness study (approved 2026-11-11): Mistral Large 4 and Mistral Medium 3.5 through Vibe 3.1.2,
# on the user's Vibe Pro plan (no per-call dollars). Per model: the four function batteries, hex functions, the app
# battery, then hex programs if hex functions reach >= 12/16 within four. Reported apart from the tool-free rows.
cd /home/aharmon/BytecodeAgent
T=20261111
P=".venv/bin/python -m bca.liveeval run"
S=vibe:mistral-large-4-0-3,vibe:mistral-medium-3.5
X="--subjects $S --jobs 4"
$P --run-dir eval/runs/vibe-$T --tasks all $X --max-calls 1200 > eval/runs/vibe-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/hex-vibe-$T --tasks round1 --paths hex $X --max-calls 140 > eval/runs/hex-vibe-$T.out 2>&1; echo "$(date +%T) hex exit $?"
$P --run-dir eval/runs/apps-vibe-$T --tasks apps $X --max-calls 700 > eval/runs/apps-vibe-$T.out 2>&1; echo "$(date +%T) apps exit $?"
for m in mistral-large-4-0-3 mistral-medium-3.5; do
  n=$(grep "^vibe_$m/" eval/runs/hex-vibe-$T.out | grep -c "final=1"); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then
    $P --run-dir eval/runs/apps-hex-vibe-$m-$T --tasks apps --paths hex --samples 1 --model vibe:$m --jobs 2 --max-calls 70 > eval/runs/apps-hex-vibe-$m-$T.out 2>&1; echo "$(date +%T) $m hex programs exit $?"
  else echo "$m: gate not met"; fi
done
echo ALLDONE
