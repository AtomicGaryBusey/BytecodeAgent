#!/bin/bash
# Function batteries for five Fireworks models, in parallel (each --jobs 1); stop all if the ledger total passes $45.
cd /home/aharmon/BytecodeAgent
PIDS=()
for m in glm-5p3 qwen3p8-max deepseek-v4p1-flash minimax-m3 nemotron-3-ultra-nvfp4; do
  RD=eval/runs/fireworks-$m-20261108-121635
  .venv/bin/python -m bca.liveeval run --run-dir $RD --tasks round1,round2,long,adversarial --model fireworks:$m --max-calls 1000 --jobs 1 --max-infra-errors 20 > $RD.out 2>&1 &
  PIDS+=($!)
done
LOG=eval/runs/fireworks-batch-20261108-121635.watch.log
while :; do
  alive=0; for p in "${PIDS[@]}"; do kill -0 $p 2>/dev/null && alive=1; done
  [ $alive = 0 ] && break
  tot=$(.venv/bin/python -c 'import json; d=json.load(open("/home/aharmon/.local/state/bca/foundry-spend.json")); print(round(sum(v["spent"] for k,v in d.items() if k.startswith("fireworks:")),2))')
  echo "$(date +%T) fireworks total $tot" >> $LOG
  if .venv/bin/python -c "import sys; sys.exit(0 if $tot > 45 else 1)"; then echo "$(date +%T) STOP: over \$45" >> $LOG; kill -INT "${PIDS[@]}"; sleep 30; kill "${PIDS[@]}" 2>/dev/null; break; fi
  sleep 60
done
wait; echo "$(date +%T) all done" >> $LOG
