#!/bin/bash
# Waits for eval/runs/kolibri-hex-20261113.sh to finish (it stops its container at the end), then re-tunes
# (user, 2026-11-13: "Re-tune it and SAVE IT TO THE REPO this time"). Log: eval/kolibri-tune/tune-20261113.log
cd /home/aharmon/BytecodeAgent
while ps -eo args | grep -v grep | grep -q "kolibri-hex-20261113.sh"; do sleep 120; done
docker stop bca-vllm-kolibri-defcfg >/dev/null 2>&1 || true
echo "$(date '+%F %T') hex run done; tuning"
eval/kolibri-tune/tune.sh
echo "$(date '+%F %T') TUNEDONE"
