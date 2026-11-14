#!/bin/bash
# Kolibri-1 (budget mode) hex functions, queue item 7 (2026-11-13). Local vLLM, no spend.
# Serving: container bca-vllm-kolibri-defcfg = the same image (bca-vllm-kolibri:0.29.0-aa1.0.0), weights revision
# and flags as bca-vllm-kolibri, but WITHOUT the tuned Triton config for E=384,N=512: that file lived in a
# /tmp scratchpad and was lost in the 2026-11-12 power loss (docker then refused to start the old container).
# vLLM uses its default MoE config. Per cross-vendor-methodology.md the tuned config changed kernel speed only
# (31.9 -> 35.3 tok/s short, 27.8 -> 28.2 long), never outputs, and budget mode has no wall-clock limit.
# Memory guard (user rule, 2026-10-18): if MemFree <= 1 GB, stop the harness and the container and leave a note;
# a restart needs the user's OK. Then hex programs if >= 12/16 hex functions pass within four.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
M="--model vllm:Aleph-Alpha/Kolibri-1@budget --jobs 2"
guard() {
  while sleep 30; do
    f=$(awk '/^MemFree:/{print int($2/1024)}' /proc/meminfo)
    if [ "$f" -le 1024 ]; then
      echo "$(date +%T) MemFree ${f} MB <= 1 GB: pausing Kolibri (user rule); restart needs the user's OK"
      for p in $(ps -eo pid,args | awk '/bca.liveeval run --run-dir eval\/runs\/[a-z-]*kolibri/ && !/awk/{print $1}'); do kill $p; done
      docker stop bca-vllm-kolibri-defcfg; kill $$; exit 0
    fi
  done
}
guard & G=$!
step eval/runs/hex-kolibri-budget-$T 16 --tasks round1 --paths hex $M --max-calls 70
n=$(gate eval/runs/hex-kolibri-budget-$T); echo "Kolibri hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then step eval/runs/apps-hex-kolibri-budget-$T 16 --tasks apps --paths hex --samples 1 $M --max-calls 70; else echo "Kolibri: gate not met"; fi
kill $G; docker stop bca-vllm-kolibri-defcfg
echo ALLDONE
