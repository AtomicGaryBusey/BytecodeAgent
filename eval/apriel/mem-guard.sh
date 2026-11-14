#!/bin/bash
# Memory guard while Apriel is served. User rule (2026-11-14, replacing the MemFree <= 1 GB rule of 2026-10-18 for
# local models): pause at MemAvailable <= 4 GB
# or swap >= 8 GB): stop the harness and the container; a restart needs the user's OK. Exits when the server stops.
cd /home/aharmon/BytecodeAgent
while sleep 20; do
  docker ps -q -f name=bca-vllm-apriel | grep -q . || exit 0
  f=$(awk '/^MemFree:/{print int($2/1024)}' /proc/meminfo); a=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
  s=$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{x=$2} END{print int((t-x)/1024)}' /proc/meminfo)
  if [ "$a" -le 4096 ] || [ "$s" -ge 8192 ]; then
    echo "$(date +%T) MemFree ${f} MB, available ${a} MB, swap ${s} MB: pausing Apriel; restart needs the user's OK"
    for p in $(ps -eo pid,args | awk '/apriel-20261113.sh/ && !/awk/{print $1}'); do kill $p; done
    for p in $(ps -eo pid,args | awk '/bca.liveeval run --run-dir eval\/runs\/[a-z-]*apriel/ && !/awk/{print $1}'); do kill $p; done
    docker stop bca-vllm-apriel; exit 0
  fi
done
