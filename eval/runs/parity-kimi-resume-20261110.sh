#!/bin/bash
# Resume Kimi K3.1 Pro apps and hex programs (approved 2026-11-10) once the network recovers: the first runs
# aborted at 22:10 on 5 consecutive "OAuth ... fetch failed" errors while internet RTT was ~1.1 s (Node's fetch
# gives each address ~250 ms). Waits until Node can reach auth.kimi.ai three times in a row, then resumes.
cd /home/aharmon/BytecodeAgent
ok=0
while [ $ok -lt 3 ]; do
  if node -e "fetch('https://auth.kimi.ai/',{signal:AbortSignal.timeout(10000)}).then(()=>process.exit(0)).catch(()=>process.exit(1))"; then ok=$((ok+1)); else ok=0; fi
  echo "$(date +%T) node->auth.kimi.ai ok streak $ok"
  sleep 60
done
P=".venv/bin/python -m bca.liveeval run"; S=kimi:kimi-code/k3.1-pro
$P --run-dir eval/runs/apps-kimi-k31pro-20261110 --tasks apps --subjects $S --jobs 2 --max-calls 160 >> eval/runs/apps-kimi-k31pro-20261110.out 2>&1
echo "$(date +%T) apps exit $?"
$P --run-dir eval/runs/apps-hex-kimi-k31pro-20261110 --tasks apps --paths hex --samples 1 --subjects $S --jobs 2 --max-calls 60 >> eval/runs/apps-hex-kimi-k31pro-20261110.out 2>&1
echo "$(date +%T) apps-hex exit $?"
echo ALLDONE
