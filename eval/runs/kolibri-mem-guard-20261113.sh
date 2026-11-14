#!/bin/bash
# A stricter memory guard beside the one inside kolibri-hex-20261113.sh (which applies the user's rule, MemFree <= 1 GB).
# Added 2026-11-13 after the COBOL Agent session reported 11 GB available and 4.6 GB of swap in use with Kolibri's
# vLLM holding ~104 GB (gpu-memory-utilization 0.80 of unified memory); the machine hung under memory pressure on
# 11-12 (NVRM NV_ERR_NO_MEMORY). Pauses Kolibri if MemAvailable <= 4 GB or swap use >= 8 GB. Restart needs the user.
cd /home/aharmon/BytecodeAgent
while sleep 20; do
  pgrep -f "kolibri-hex-20261113.sh" >/dev/null || exit 0
  a=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
  s=$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{print int((t-f)/1024)}' /proc/meminfo)
  if [ "$a" -le 4096 ] || [ "$s" -ge 8192 ]; then
    echo "$(date +%T) MemAvailable ${a} MB, swap used ${s} MB: pausing Kolibri; restart needs the user's OK"
    for p in $(ps -eo pid,args | awk '/kolibri-hex-20261113.sh/ && !/awk/{print $1}'); do kill $p; done
    for p in $(ps -eo pid,args | awk '/bca.liveeval run --run-dir eval\/runs\/[a-z-]*kolibri/ && !/awk/{print $1}'); do kill $p; done
    docker stop bca-vllm-kolibri-defcfg; exit 0
  fi
done
