#!/bin/bash
# Resume the Kimi Highspeed queue after Allegretto's 5-hour usage limit (hit 2026-11-12 00:23): probe every 15 minutes,
# then re-run the queue, which resumes every run dir where it stopped; repeat if the limit trips again.
cd /home/aharmon/BytecodeAgent
T=20261111
while true; do
  until .venv/bin/python -m bca.liveeval probe kimi:kimi-code/k3.1-pro-highspeed 2>&1 | grep -q '"ok": true'; do sleep 900; done
  echo "$(date +%T) Kimi answers again: resuming"
  eval/runs/kimi-highspeed-$T.sh >> eval/runs/kimi-highspeed-$T.log 2>&1; rc=$?
  [ $rc -eq 3 ] || { echo "$(date +%T) queue finished (exit $rc)"; break; }
done
