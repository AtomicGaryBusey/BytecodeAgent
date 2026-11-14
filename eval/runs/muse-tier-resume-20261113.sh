#!/bin/bash
# Resumes the Muse tier comparison (paid muse-spark-1.3) after the 02:40 isolation abort (a `tool.muse.search`
# task proposed with no tools sent). Audit eval/audits/muse-1.4.18-search-20261113: the cell replayed 3 times through
# the recorder gave one model task each and no tool-call items; not reproduced. The adapter still fails closed on it
# (nothing runs, nothing is scored); if it recurs this script stops, not retries. 429-aware like muse-resume-20261113.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
wait_reset() {
  local r; r=$(tail -n 3 "$1.out" | grep -o 'resets at [0-9T:-]*Z' | tail -1 | cut -d' ' -f3)
  [ -n "$r" ] || return 1
  local s=$(( $(date -d "$r" +%s) - $(date +%s) + 120 )); [ $s -lt 60 ] && s=60
  echo "$(date +%T) Muse usage window exhausted ($1): sleeping ${s}s"; sleep $s
}
run() { local d=$1 n=$2; for i in $(seq 1 20); do step "$@"; [ "$(done_cells $d)" -ge "$n" ] && return 0
  wait_reset $d || { echo "$(date +%T) $d stopped short, not a quota 429: stopping"; exit 3; }; done; exit 3; }
X="--model muse:muse-spark-1.3 --jobs 2"
run eval/runs/muse-tier-paid-spark-1.3-$T 224 --tasks all $X --max-calls 500
run eval/runs/apps-muse-tier-paid-spark-1.3-$T 96 --tasks apps $X --max-calls 300
echo ALLDONE
