#!/bin/bash
# Parity pick-up (approved 2026-11-10, after the user's $50 top-up): MiniMax M3 apps, then hex functions.
# Per-model cap $45 (cumulative ledger; $10.64 already booked from its function batteries).
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=45
P=".venv/bin/python -m bca.liveeval run"; S=fireworks:minimax-m3
$P --run-dir eval/runs/apps-fireworks-minimax-20261110 --tasks apps --subjects $S --jobs 4 --max-calls 300 --max-infra-errors 20 > eval/runs/apps-fireworks-minimax-20261110.out 2>&1
$P --run-dir eval/runs/hex-fireworks-minimax-20261110 --tasks round1 --paths hex --subjects $S --jobs 4 --max-calls 60 --max-infra-errors 20 > eval/runs/hex-fireworks-minimax-20261110.out 2>&1
echo ALLDONE
