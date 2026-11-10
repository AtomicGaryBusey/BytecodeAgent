#!/bin/bash
# User's approval 2026-11-09 (credit topped up to $58.93): finish GLM 5.3 and Qwen 3.8 Max, in parallel.
# Ledger reset to the Fireworks bill ($42.07) first; per-model cap raised to $40 (Qwen needs ~$24.5 total).
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=40
for m in glm-5p3 qwen3p8-max; do
  ( RD=eval/runs/fireworks-$m-20261108-121635
    .venv/bin/python -m bca.liveeval run --run-dir $RD --tasks round1,round2,long,adversarial --model fireworks:$m --max-calls 1000 --jobs 1 --max-infra-errors 20 > $RD.finish2.out 2>&1
    echo "$(date +%T) $m exit $?" >> eval/runs/fireworks-batch-20261108-121635.watch.log ) &
done
wait
