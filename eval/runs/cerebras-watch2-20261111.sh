#!/bin/bash
# Stop every Cerebras run if the ledger's Cerebras total passes $58 (after the $25 top-up: $60 bought, ledger $27.73 against $26.65 really spent at $33.35 available; ~$3 margin
# ; the ledger books at or above the real charge). Exits when no Cerebras run is left.
cd /home/aharmon/BytecodeAgent
LOG=eval/runs/cerebras-watch-20261111.log
sleep 30
while pgrep -f 'liveeval run .*cerebras:' >/dev/null; do
  tot=$(.venv/bin/python -c 'import json; d=json.load(open("/home/aharmon/.local/state/bca/foundry-spend.json")); print(round(sum(v["spent"] for k,v in d.items() if k.startswith("cerebras:")),2))')
  echo "$(date +%T) cerebras total $tot" >> $LOG
  if .venv/bin/python -c "import sys; sys.exit(0 if $tot > 58 else 1)"; then echo "$(date +%T) STOP: over \$58" >> $LOG; pkill -INT -f 'liveeval run .*cerebras:'; sleep 30; pkill -f 'liveeval run .*cerebras:'; break; fi
  sleep 60
done
echo "$(date +%T) no cerebras run left" >> $LOG
