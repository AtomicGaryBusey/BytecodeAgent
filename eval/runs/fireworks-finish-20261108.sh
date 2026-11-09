#!/bin/bash
# User's choice 2026-11-08: finish MiniMax M3, then GLM 5.3, inside the credit; Qwen 3.8 Max stays partial.
cd /home/aharmon/BytecodeAgent
for m in minimax-m3 glm-5p3; do
  RD=eval/runs/fireworks-$m-20261108-121635
  .venv/bin/python -m bca.liveeval run --run-dir $RD --tasks round1,round2,long,adversarial --model fireworks:$m --max-calls 1000 --jobs 1 --max-infra-errors 20 > $RD.finish.out 2>&1
  echo "$(date +%T) $m exit $?" >> eval/runs/fireworks-batch-20261108-121635.watch.log
done
