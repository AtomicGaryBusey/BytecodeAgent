#!/bin/bash
# Resumes Muse round 2 on the Muse Code Power Usage subscription (2026-11-13). Both first chains ran into the
# subscription's usage window at 02:50 ("API error 429: Subscription quota exhausted. Your usage window resets at
# ..."), which every later step then hit at once; contributor-tier calls count against the same window. This runs
# ONE model at a time, and on a 429 sleeps until the reset time Muse gives (+2 min) and resumes the same step.
# The tier comparison (paid muse-spark-1.3) is NOT here: its run stopped on an isolation abort (Muse proposed a
# `tool.muse.search` task with no tools sent), held for an audit first (eval/backlog-queue.md).
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
wait_reset() {  # $1 = run dir; returns 0 if it slept on a quota 429, 1 otherwise
  local r; r=$(tail -n 3 "$1.out" | grep -o 'resets at [0-9T:-]*Z' | tail -1 | cut -d' ' -f3)
  [ -n "$r" ] || return 1
  local s=$(( $(date -d "$r" +%s) - $(date +%s) + 120 )); [ $s -lt 60 ] && s=60
  echo "$(date +%T) Muse usage window exhausted ($1): sleeping until $(date -d "@$(( $(date +%s) + s ))" +%T)"; sleep $s
}
run() {  # run dir, expected cells, args...; resumes across usage windows
  local d=$1 n=$2
  for i in $(seq 1 20); do
    step "$@"; [ "$(done_cells $d)" -ge "$n" ] && return 0
    wait_reset $d || { echo "$(date +%T) $d stopped short, not a quota 429: stopping"; exit 3; }
  done; exit 3
}
for m in muse-wonder-full-1.0-contributor muse-wonder-1.0-contributor muse-ember-1.0-contributor; do
  X="--model muse:$m --jobs 3"; s=${m%-contributor}
  run eval/runs/$s-$T 224 --tasks all $X --max-calls 500
  run eval/runs/hex-$s-$T 16 --tasks round1 --paths hex $X --max-calls 70
  run eval/runs/apps-$s-$T 96 --tasks apps $X --max-calls 300
  n=$(gate eval/runs/hex-$s-$T); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then run eval/runs/apps-hex-$s-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; else echo "$m: gate not met"; fi
done
echo ALLDONE
