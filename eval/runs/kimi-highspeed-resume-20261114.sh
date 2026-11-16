#!/bin/bash
# Kimi resumer that sleeps until the quota is due back instead of probing all week (user, 2026-11-14).
# Kimi's limit message gives no reset time. Its weekly cap runs in fixed 7-day blocks: hit 2026-11-12 01:53, lifted
# 2026-11-14 between 14:00 and 14:12 (15-minute probes), so the current block started ~14:00 on 11-14 and the next
# reset is ~14:00 on 11-21. A 5-hour cap lifts 5 hours after the window's first call; it is still probed every
# 15 minutes (it is short). Usage: kimi-highspeed-resume-20261114.sh [wake time, default 2026-11-21 13:45]
cd /home/aharmon/BytecodeAgent
T=20261111
WAKE=${1:-2026-11-21 14:03}  # Kimi subscription page (user screenshot 11-14): 7-day usage resets 11-21 14:02
s=$(( $(date -d "$WAKE" +%s) - $(date +%s) ))
[ $s -gt 0 ] && { echo "$(date '+%m-%d %T') weekly cap: sleeping until $WAKE"; sleep $s; }
while true; do
  until .venv/bin/python -m bca.liveeval probe kimi:kimi-code/k3.1-pro-highspeed 2>&1 | grep -q '"ok": true'; do sleep 900; done
  echo "$(date '+%m-%d %T') Kimi answers again: resuming"
  eval/runs/kimi-highspeed-$T.sh >> eval/runs/kimi-highspeed-$T.log 2>&1; rc=$?
  [ $rc -eq 3 ] || { echo "$(date '+%m-%d %T') queue finished (exit $rc)"; break; }
  if tail -n 40 eval/runs/kimi-*-highspeed-$T.out 2>/dev/null | grep -q "weekly (7-day) usage limit"; then
    nxt=$(date -d "$WAKE + 7 days" '+%Y-%m-%d %H:%M'); WAKE=$nxt  # the next block boundary
    s=$(( $(date -d "$WAKE" +%s) - $(date +%s) ))
    [ $s -gt 0 ] && { echo "$(date '+%m-%d %T') weekly cap again: sleeping until $WAKE"; sleep $s; }
  fi
done
