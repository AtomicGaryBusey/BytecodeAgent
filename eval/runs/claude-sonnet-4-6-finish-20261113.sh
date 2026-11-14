#!/bin/bash
# Finishes Sonnet 4.6 only (user, 2026-11-13: once Sonnet is done, pause the remaining Claude models; Opus 4.6 and
# Opus 4.5 go back to the backlog). Waits for the app run already in flight, then resumes it until complete
# (20-min wait after a Cloudflare 403 abort, as in claude-older-403-resumer), then the hex-program gate.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261111; m=claude-sonnet-4-6; X="--subjects $m --jobs 4"
while pgrep -f "run-dir eval/runs/apps-$m-$T " >/dev/null; do sleep 60; done
for r in "apps-$m-$T 96 --tasks apps $X --max-calls 250" "GATE" "apps-hex-$m-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70"; do
  if [ "$r" = GATE ]; then n=$(gate eval/runs/hex-$m-$T); echo "$m hex functions within four: $n/16"; [ "$n" -ge 12 ] || break; continue; fi
  set -- $r; d=eval/runs/$1; shift
  for i in $(seq 1 12); do
    step $d "$@"
    [ "$(done_cells $d)" -ge "$1" ] && break
    if tail -n 3 $d.out | grep -q "success 403"; then echo "$(date +%T) 403 abort in $d: waiting 20 min"; sleep 1200
    else echo "$(date +%T) $d stopped short, not a 403: leaving it"; exit 3; fi
  done
done
echo ALLDONE
