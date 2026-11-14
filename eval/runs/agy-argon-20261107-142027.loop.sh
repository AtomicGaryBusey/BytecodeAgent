#!/bin/bash
# Resume Gemini 4 Argon High every 8 h until the run completes. Same command, same settings (180 s etc.).
cd /home/aharmon/BytecodeAgent
RD=eval/runs/agy-argon-20261107-142027
L=$RD.loop
for try in $(seq 1 30); do
  echo "$(date '+%m-%d %H:%M') try $try" >> $L
  .venv/bin/python -m bca.liveeval run --run-dir $RD --tasks round1,round2,long,adversarial --subjects agy:gemini-4-argon-high --max-calls 1200 --jobs 2 >> $RD.log 2>&1
  rc=$?
  n=$(wc -l < $RD/agy_gemini-4-argon-high/baseline/results.jsonl)
  echo "$(date '+%m-%d %H:%M') exit $rc, cells done $n/224" >> $L
  [ $rc -eq 0 ] && { echo "COMPLETE" >> $L; exit 0; }
  if ! tail -5 $RD.log | grep -q 'Individual quota reached'; then echo "STOPPED: not a quota error, see $RD.log" >> $L; exit 1; fi
  sleep 28800
done
echo "GAVE UP after 30 tries" >> $L
