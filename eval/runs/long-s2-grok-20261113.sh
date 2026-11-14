#!/bin/bash
# Second long-battery sample (queue item 4), the SuperGrok members only: Grok 4.7 build-fast, Grok 4.7, Grok 4.6.
# The Claude members (Opus 5.5, Fable 5.1) are held: the user stopped Claude-plan runs on 2026-11-13 for usage.
# A fresh, independent run of the 54 long cells per model in its own run dir (no sample suffix), grok 1.0.41.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
for m in grok-4.7-build-fast grok-4.7 grok-4.6; do
  step eval/runs/long-s2-$m-$T 54 --tasks long --model grok:$m --jobs 2 --max-calls 250
  tail -n 3 eval/runs/long-s2-$m-$T.out | grep -qiE "run aborted|usage limit|limit reached" && { echo "$(date +%T) $m stopped short: stopping"; exit 3; }
done
echo ALLDONE
