#!/bin/bash
# Kimi K3 through Copilot, the rest (user, 2026-11-12: "do as much of the remaining kimi-k3 testing as we can with the
# remaining Copilot credits"). About 470 of November's 1,500 AI credits were left after the functions (935 credits) and
# hex functions. Order: hex programs first (16 cells: the one remaining battery that can complete; hex functions passed
# the gate, 16/16 within four), then the app battery until the credits are spent (overage is off: Copilot then refuses
# the call, the run stops cleanly and nothing is scored). The app battery will stay partial until December's credits.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261112
X="--model copilot:kimi-k3 --jobs 2"
step eval/runs/apps-hex-copilot-kimi-k3-$T 16 --tasks apps --paths hex --samples 1 $X --max-calls 70 || { echo "stopped in hex programs"; exit 3; }
step eval/runs/apps-copilot-kimi-k3-$T 96 --tasks apps $X --max-calls 250 || { echo "stopped in apps"; exit 3; }
echo ALLDONE
