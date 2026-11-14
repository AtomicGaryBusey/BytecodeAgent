#!/bin/bash
# Kimi K3 through GitHub Copilot (user, 2026-11-12): the front-end comparison with Kimi K3 through Kimi Code (204/224).
# Copilot CLI 1.0.107 / SDK 1.0.29 (audited), Copilot's default effort for the model, 2 calls at a time, Copilot Pro
# AI credits (overage off: a spent allowance stops the run cleanly). Functions first; hex functions, apps and gated
# hex programs follow only while at least 30% of the month's credits remain after the step before.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261112
X="--model copilot:kimi-k3 --jobs 2"
left() { .venv/bin/python -c "
import json,glob
v=[(json.loads(l).get('usage') or {}).get('plan_remaining_pct') for f in sorted(glob.glob('eval/runs/*copilot-kimi-k3-$T/*/baseline/attempts.jsonl')) for l in open(f)]
v=[x for x in v if isinstance(x,(int,float))]; print(int(min(v)) if v else 100)"; }
gatecredits() { l=$(left); echo "$(date +%T) Copilot credits remaining: $l%"; [ "$l" -ge 30 ] || { echo "below 30%: stopping before $1"; exit 4; }; }
step eval/runs/copilot-kimi-k3-$T 224 --tasks all $X --max-calls 450 || exit 3
gatecredits "hex functions"; step eval/runs/hex-copilot-kimi-k3-$T 16 --tasks round1 --paths hex $X --max-calls 70 || exit 3
gatecredits "apps"; step eval/runs/apps-copilot-kimi-k3-$T 96 --tasks apps $X --max-calls 250 || exit 3
n=$(gate eval/runs/hex-copilot-kimi-k3-$T); echo "hex functions within four: $n/16"
if [ "$n" -ge 12 ]; then gatecredits "hex programs"; step eval/runs/apps-hex-copilot-kimi-k3-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70; else echo "gate not met"; fi
echo ALLDONE
