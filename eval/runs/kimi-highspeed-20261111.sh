#!/bin/bash
# Kimi round (user, 2026-11-11): K3.1 Pro Highspeed, then K2.7 Code Highspeed; full set per model, catalog-default
# effort, 2 calls at a time. Resume-safe since 2026-11-12 (chain-lib.sh); exits 3 on a Kimi usage limit, and
# kimi-highspeed-resume-20261111.sh re-runs it after the window resets.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261111
lim() { grep -q "usage limit" "$1.out" && tail -3 "$1.out" | grep -q "usage limit" && { echo "$(date +%T) Kimi usage limit in $1: stopping the queue (nothing scored)"; exit 3; }; }
for m in k3.1-pro-highspeed kimi-for-coding-highspeed; do
  X="--model kimi:kimi-code/$m --jobs 2"
  step eval/runs/kimi-$m-$T 224 --tasks all $X --max-calls 450; lim eval/runs/kimi-$m-$T
  step eval/runs/hex-kimi-$m-$T 16 --tasks round1 --paths hex $X --max-calls 70; lim eval/runs/hex-kimi-$m-$T
  step eval/runs/apps-kimi-$m-$T 96 --tasks apps $X --max-calls 250; lim eval/runs/apps-kimi-$m-$T
  n=$(gate eval/runs/hex-kimi-$m-$T); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-kimi-$m-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; lim eval/runs/apps-hex-kimi-$m-$T
  else echo "$m: gate not met"; fi
done
echo ALLDONE
