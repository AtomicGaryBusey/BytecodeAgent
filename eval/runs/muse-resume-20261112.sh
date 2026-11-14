#!/bin/bash
# Resumes eval/runs/muse-study-20261111.sh after the 2026-11-12 power loss (functions and hex complete; apps 90/96).
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261111
X="--model muse:muse-spark-1.3-contributor --jobs 3"
step eval/runs/muse-$T 224 --tasks all $X --max-calls 500
step eval/runs/hex-muse-$T 16 --tasks round1 --paths hex $X --max-calls 70
step eval/runs/apps-muse-$T 96 --tasks apps $X --max-calls 300
n=$(gate eval/runs/hex-muse-$T); echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-muse-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; else echo "gate not met"; fi
echo ALLDONE
