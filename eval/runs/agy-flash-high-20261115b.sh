#!/bin/bash
# Gemini 3.8 Flash High after the user's decisions of 2026-11-15 (a manage_task call and an output-limit cut are each
# one scored model call, no do-over): finish the cells reopened by eval/replay_voided.py (hex programs, and
# xxh64.ir.aarch64 in the function run), then its app battery (never run before; user: "do it").
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261115
lim() { tail -n 5 "$1.out" | grep -qiE "quota reached|RESOURCE_EXHAUSTED|run aborted" && { echo "$(date +%T) stopped in $1 (quota or abort)"; exit 3; }; }
M="--model agy:gemini-3.8-flash-high --jobs 2"
step eval/runs/apps-hex-agy-flash-high-$T 16 --tasks apps --paths hex --samples 1 $M --max-calls 70; lim eval/runs/apps-hex-agy-flash-high-$T
echo "== resumed $(date '+%m-%d %T')" >> eval/runs/vendors-all-20260928-202226.out
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/vendors-all-20260928-202226 --tasks round1,round2,long,adversarial --subjects agy:gemini-3.8-flash-high --jobs 2 --max-calls 2600 >> eval/runs/vendors-all-20260928-202226.out 2>&1
echo "$(date +%T) Flash High functions: exit $?, $(find eval/runs/vendors-all-20260928-202226/agy_gemini-3.8-flash-high -name cell.json | wc -l)/224"; lim eval/runs/vendors-all-20260928-202226
step eval/runs/apps-agy-flash-high-$T 96 --tasks apps $M --max-calls 400; lim eval/runs/apps-agy-flash-high-$T
echo ALLDONE
