#!/bin/bash
# Meta Muse Code agent-harness study, round 2 (user, 2026-11-13): the three newest models in the free contributor tier
# (the user: fine to benchmark in contributor mode, the results are being open-sourced). Muse Code 1.4.18 (audited),
# auto-update off, the same protocol as muse-spark-1.3-contributor. Full set per model, one after another.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
for m in muse-wonder-full-1.0-contributor muse-wonder-1.0-contributor muse-ember-1.0-contributor; do
  X="--model muse:$m --jobs 3"; s=${m%-contributor}
  step eval/runs/$s-$T 224 --tasks all $X --max-calls 500
  step eval/runs/hex-$s-$T 16 --tasks round1 --paths hex $X --max-calls 70
  step eval/runs/apps-$s-$T 96 --tasks apps $X --max-calls 300
  n=$(gate eval/runs/hex-$s-$T); echo "$m hex functions within four: $n/16"
  if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-$s-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; else echo "$m: gate not met"; fi
done
echo ALLDONE
