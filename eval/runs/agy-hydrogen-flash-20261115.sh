#!/bin/bash
# Google's Gemini bucket on Antigravity (Google AI Pro; separate from Argon's weekly meter), user 2026-11-15:
#   1. Gemini 4 Hydrogen High: the full set (functions, hex functions, apps, hex programs if >= 12/16). New model.
#   2. Gemini 3.8 Flash High: its last function cell (stats_f.ir.aarch64) and hex functions (queue item 3).
# agy 1.5.7 (pinned). Gemini's safety filter blocks are scored as refusals. Stops on a quota 429 (exit 3); the loop
# restarts it when the bucket refreshes.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261115
lim() { tail -n 5 "$1.out" | grep -qiE "quota reached|RESOURCE_EXHAUSTED|run aborted" && { echo "$(date +%T) stopped in $1 (quota or abort)"; exit 3; }; }
H="--model agy:gemini-4-hydrogen-high --jobs 2"
step eval/runs/agy-hydrogen-high-$T 224 --tasks all $H --max-calls 900; lim eval/runs/agy-hydrogen-high-$T
step eval/runs/hex-agy-hydrogen-high-$T 16 --tasks round1 --paths hex $H --max-calls 70; lim eval/runs/hex-agy-hydrogen-high-$T
step eval/runs/apps-agy-hydrogen-high-$T 96 --tasks apps $H --max-calls 400; lim eval/runs/apps-agy-hydrogen-high-$T
n=$(gate eval/runs/hex-agy-hydrogen-high-$T); echo "Hydrogen hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-agy-hydrogen-high-$T 16 --tasks apps --paths hex --samples 1 $H --max-calls 70; lim eval/runs/apps-hex-agy-hydrogen-high-$T; fi
F="--subjects agy:gemini-3.8-flash-high --jobs 2"
echo "== resumed $(date '+%m-%d %T')" >> eval/runs/vendors-all-20260928-202226.out
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/vendors-all-20260928-202226 --tasks round1,round2,long,adversarial $F --max-calls 2600 >> eval/runs/vendors-all-20260928-202226.out 2>&1
echo "$(date +%T) Flash High functions: exit $?, $(find eval/runs/vendors-all-20260928-202226/agy_gemini-3.8-flash-high -name cell.json | wc -l)/224"; lim eval/runs/vendors-all-20260928-202226
step eval/runs/hex-agy-flash-high-$T 16 --tasks round1 --paths hex --model agy:gemini-3.8-flash-high --jobs 2 --max-calls 70; lim eval/runs/hex-agy-flash-high-$T
echo ALLDONE
