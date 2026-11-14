#!/bin/bash
# Codex gpt-6.1-sol, full set (named by the user 2026-11-11; ChatGPT Pro plan): the four function batteries, hex
# functions, the app battery, then hex programs if hex functions reach >= 12/16 within four. Effort pinned at the live
# catalog default, as for every Codex model; Codex CLI 0.167.0 (audited).
cd /home/aharmon/BytecodeAgent
T=20261111
P=".venv/bin/python -m bca.liveeval run"; X="--model codex:gpt-6.1-sol --jobs 3"
$P --run-dir eval/runs/codex-gpt-6.1-sol-$T --tasks all $X --max-calls 500 > eval/runs/codex-gpt-6.1-sol-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/hex-codex-gpt-6.1-sol-$T --tasks round1 --paths hex $X --max-calls 70 > eval/runs/hex-codex-gpt-6.1-sol-$T.out 2>&1; echo "$(date +%T) hex exit $?"
$P --run-dir eval/runs/apps-codex-gpt-6.1-sol-$T --tasks apps $X --max-calls 300 > eval/runs/apps-codex-gpt-6.1-sol-$T.out 2>&1; echo "$(date +%T) apps exit $?"
n=$(grep -c "final=1" eval/runs/hex-codex-gpt-6.1-sol-$T.out); echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then
  $P --run-dir eval/runs/apps-hex-codex-gpt-6.1-sol-$T --tasks apps --paths hex --samples 1 $X --max-calls 70 > eval/runs/apps-hex-codex-gpt-6.1-sol-$T.out 2>&1; echo "$(date +%T) hex programs exit $?"
else echo "gate not met, no hex programs"; fi
echo ALLDONE
