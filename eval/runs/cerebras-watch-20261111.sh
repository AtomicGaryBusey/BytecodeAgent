#!/bin/bash
# Stop every Cerebras run if the ledger's Cerebras total passes $33 (balance $35.00 on 2026-11-11, minus the $0.03
# smoke and a margin; the ledger books at or above the real charge). Exits when no Cerebras run is left.
cd /home/aharmon/BytecodeAgent
LOG=eval/runs/cerebras-watch-20261111.log
sleep 30
while pgrep -f 'liveeval run .*cerebras:' >/dev/null; do
  tot=$(.venv/bin/python -c 'import json; d=json.load(open("/home/aharmon/.local/state/bca/foundry-spend.json")); print(round(sum(v["spent"] for k,v in d.items() if k.startswith("cerebras:")),2))')
  echo "$(date +%T) cerebras total $tot" >> $LOG
  if .venv/bin/python -c "import sys; sys.exit(0 if $tot > 33 else 1)"; then echo "$(date +%T) STOP: over \$33" >> $LOG; pkill -INT -f 'liveeval run .*cerebras:'; sleep 30; pkill -f 'liveeval run .*cerebras:'; break; fi
  sleep 60
done
echo "$(date +%T) no cerebras run left" >> $LOG
