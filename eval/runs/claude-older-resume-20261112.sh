#!/bin/bash
# Resumes eval/runs/claude-older-20261111.sh after the 2026-11-12 03:52 power loss: same models, batteries, settings
# and call caps; finished steps are skipped (chain-lib.sh). Opus 5 and Fable 5 were complete; Opus 4.7 stopped in hex programs.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261111
lim() { tail -3 "$1.out" | grep -qiE "usage limit|limit reached|run aborted" && { echo "$(date +%T) plan usage limit in $1: stopping"; exit 3; }; }
for m in claude-opus-5 claude-fable-5 claude-opus-4-7 claude-sonnet-4-6 claude-opus-4-6 claude-opus-4-5-20251101; do
  X="--subjects $m --jobs 4"
  step eval/runs/$m-$T 224 --tasks all $X --max-calls 450; lim eval/runs/$m-$T
  step eval/runs/hex-$m-$T 16 --tasks round1 --paths hex $X --max-calls 70; lim eval/runs/hex-$m-$T
  step eval/runs/apps-$m-$T 96 --tasks apps $X --max-calls 250; lim eval/runs/apps-$m-$T
  n=$(gate eval/runs/hex-$m-$T); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-$m-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; lim eval/runs/apps-hex-$m-$T
  else echo "$m: gate not met"; fi
done
echo ALLDONE
