#!/bin/bash
# Stop every Fireworks run if the ledger total passes $50 (account credit $51; the ledger books at or above the real charge). Exits when none is running.
cd /home/aharmon/BytecodeAgent
LOG=eval/runs/fireworks-batch-20261108-121635.watch.log
while pgrep -f 'model fireworks:' >/dev/null; do
  tot=$(.venv/bin/python -c 'import json; d=json.load(open("/home/aharmon/.local/state/bca/foundry-spend.json")); print(round(sum(v["spent"] for k,v in d.items() if k.startswith("fireworks:")),2))')
  echo "$(date +%T) [global] fireworks total $tot" >> $LOG
  if .venv/bin/python -c "import sys; sys.exit(0 if $tot > 50 else 1)"; then echo "$(date +%T) [global] STOP: over \$50" >> $LOG; pkill -INT -f 'model fireworks:'; sleep 30; pkill -f 'model fireworks:'; break; fi
  sleep 60
done
echo "$(date +%T) [global] no fireworks run left" >> $LOG
