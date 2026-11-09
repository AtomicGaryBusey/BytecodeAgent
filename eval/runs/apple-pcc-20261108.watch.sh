#!/bin/bash
# Run the PCC battery and stop it the moment /health reports the PCC quota approaching or reached.
cd /home/aharmon/BytecodeAgent
RD=eval/runs/apple-pcc-20261108
.venv/bin/python -m bca.liveeval run --run-dir $RD --tasks round1 --model apple:pcc --max-calls 400 --jobs 1 --max-infra-errors 20 > $RD.out 2>&1 &
PID=$!
while kill -0 $PID 2>/dev/null; do
  h=$(curl -s -m 10 http://127.0.0.1:18976/health)
  q=$(printf %s "$h" | .venv/bin/python -c 'import json,sys
try:
  d=json.load(sys.stdin); p=[m for m in d["models"] if m["name"]=="pcc"][0]
  print("stop" if (p.get("quota",{}).get("approaching_limit") or p.get("quota",{}).get("limit_reached") or not p.get("available")) else "ok")
except Exception: print("unknown")')
  echo "$(date +%T) $q" >> $RD.watch.log
  if [ "$q" = stop ]; then echo "$(date +%T) STOP: $h" >> $RD.watch.log; kill -INT $PID; sleep 20; kill $PID 2>/dev/null; fi
  sleep 20
done
wait $PID; echo "run exit $?" >> $RD.watch.log
