#!/bin/bash
# qwen-3.8-27b on Cerebras, after the user's $25 top-up (2026-11-11; $33.35 available, $60 bought in all): the
# remaining function cells, the app battery, then hex programs if hex functions reached >= 12/16 within four.
# 2 calls at a time (Cerebras' 150K tokens-per-minute limit for this model); per-model cap $56; the watcher stops
# every Cerebras run past a ledger total of $58. Waits for finish2's hex cells.
cd /home/aharmon/BytecodeAgent
T=20261111
while pgrep -f "cerebras-qwen-finish2-$T.sh" >/dev/null; do sleep 20; done
export BCA_CEREBRAS_CAP_USD=56
P=".venv/bin/python -m bca.liveeval run"; X="--model cerebras:qwen-3.8-27b --jobs 2 --max-infra-errors 40"
$P --run-dir eval/runs/cerebras-qwen-3.8-27b-$T --tasks all $X --max-calls 1100 >> eval/runs/cerebras-qwen-3.8-27b-$T.out 2>&1; echo "$(date +%T) functions exit $?"
$P --run-dir eval/runs/apps-cerebras-qwen-3.8-27b-$T --tasks apps $X --max-calls 900 > eval/runs/apps-cerebras-qwen-3.8-27b-$T.out 2>&1; echo "$(date +%T) apps exit $?"
n=$(grep -c "final=1" eval/runs/hex-cerebras-qwen-3.8-27b-$T.out); echo "hex functions within four: $n/16 (counted from the log)"
if [ "$n" -ge 12 ]; then
  $P --run-dir eval/runs/apps-hex-cerebras-qwen-3.8-27b-$T --tasks apps --paths hex --samples 1 $X --max-calls 140 > eval/runs/apps-hex-cerebras-qwen-3.8-27b-$T.out 2>&1; echo "$(date +%T) hex programs exit $?"
else echo "gate not met, no hex programs"; fi
echo ALLDONE
