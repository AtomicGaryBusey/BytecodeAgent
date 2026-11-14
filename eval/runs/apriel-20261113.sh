#!/bin/bash
# Apriel-1.6-15B-Thinker local baseline (queue item 8, 2026-11-13), token-budget mode like Kolibri-1. Server:
# eval/apriel/serve.sh (bca-vllm-apriel); memory guard eval/apriel/mem-guard.sh. Smoke 5/5 (4 first).
# ~7.8 tok/s on the GB10: the function batteries alone are about a day and a half of GPU time.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
export BCA_VLLM_SLOTS=4  # 2026-11-14 (user): 4 calls at once on the bandwidth-bound GB10 (each ~as fast as one alone)
M="--model vllm:ServiceNow-AI/Apriel-1.6-15b-Thinker@budget --jobs 4"
step eval/runs/apriel-budget-$T 224 --tasks all $M --max-calls 900
step eval/runs/hex-apriel-budget-$T 16 --tasks round1 --paths hex $M --max-calls 70
step eval/runs/apps-apriel-budget-$T 96 --tasks apps $M --max-calls 400
n=$(gate eval/runs/hex-apriel-budget-$T); echo "Apriel hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-apriel-budget-$T 16 --tasks apps --paths hex --samples 1 $M --max-calls 70; else echo "Apriel: gate not met"; fi
docker stop bca-vllm-apriel
echo ALLDONE
