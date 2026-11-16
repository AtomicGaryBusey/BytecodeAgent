#!/bin/bash
# Mistral Large 4, tool-free, through Mistral's own API (user, 2026-11-15: the $30 monthly API allowance, $3.02 used
# before; "$30 cap ... You can use the remainder"). The pair to its Vibe agent-harness row (156/224).
# Prices booked at the higher published figure (1.36 / 4.18 per 1M); caps: functions $12, hex functions $1.50.
# Apps and hex programs are NOT here: they run only after the functions' real cost is known and fits the rest.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261115
M="--model mistral:mistral-large-4 --jobs 4"
export BCA_MISTRAL_CAP_USD=14.5  # was 12: the long battery cost $6 (booked); 49 adversarial cells left, ~$2-3
step eval/runs/mistral-large-4-$T 224 --tasks all $M --max-calls 600 || exit 3
export BCA_MISTRAL_CAP_USD=15.5
step eval/runs/hex-mistral-large-4-$T 16 --tasks round1 --paths hex $M --max-calls 70
echo ALLDONE
