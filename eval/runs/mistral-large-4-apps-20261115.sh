#!/bin/bash
# Mistral Large 4 apps and hex programs (it passed the gate: 16/16 hex functions within four). User's portal,
# 2026-11-15 13:08: $10.48 of the $30 API allowance used ($3.02 before our runs), so functions + hex functions cost
# $7.46 real against $13.79 booked: the real rate is ~0.54 of the booked (higher published) price. $19.52 is left.
# Caps are in BOOKED dollars (cumulative for the model): apps to 28.00 (real ~$7.7 more), hex programs to 31.00;
# real total for the month then stays under ~$20 of $30.
# 16:55: apps reached the 28.00 cap at 70/96 ($13.08 booked for 70 cells, more than estimated). Caps raised to 32.5 / 35
# booked: real worst case 10.48 + 0.54 x (35 - 13.79) = ~$21.9 of the $30 the user released.
# 17:43: held at 85/96 ($31.29 booked). User's portal 2026-11-16 08:24: $20.54, so the eval's real rate is
# (20.54 - 3.02) / 31.29 = 0.56. Caps 37 / 40.5 booked (user go-ahead 11-16): real worst case
# 20.54 + 0.56 x (40.5 - 31.29) = ~$25.7 of $30.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261115
M="--model mistral:mistral-large-4 --jobs 4"
export BCA_MISTRAL_CAP_USD=37
step eval/runs/apps-mistral-large-4-$T 96 --tasks apps $M --max-calls 400 || exit 3
export BCA_MISTRAL_CAP_USD=40.5
step eval/runs/apps-hex-mistral-large-4-$T 16 --tasks apps --paths hex --samples 1 $M --max-calls 70
echo ALLDONE
