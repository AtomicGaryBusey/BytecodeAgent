#!/bin/bash
# Stop every Fireworks run if the ledger's Fireworks total passes $97 (about $101 of credit in all, $4 margin;
# the ledger books at or above the real charge). Exits when no Fireworks run is left.
cd /home/aharmon/BytecodeAgent
LOG=eval/runs/parity-fireworks-20261110.watch.log
sleep 20
while pgrep -f 'liveeval run .*fireworks:' >/dev/null; do
  tot=$(.venv/bin/python -c 'import json; d=json.load(open("/home/aharmon/.local/state/bca/foundry-spend.json")); print(round(sum(v["spent"] for k,v in d.items() if k.startswith("fireworks:")),2))')
  echo "$(date +%T) fireworks total $tot" >> $LOG
  if .venv/bin/python -c "import sys; sys.exit(0 if $tot > 97 else 1)"; then echo "$(date +%T) STOP: over \$97" >> $LOG; pkill -INT -f 'liveeval run .*fireworks:'; sleep 30; pkill -f 'liveeval run .*fireworks:'; break; fi
  sleep 60
done
echo "$(date +%T) no fireworks run left" >> $LOG
