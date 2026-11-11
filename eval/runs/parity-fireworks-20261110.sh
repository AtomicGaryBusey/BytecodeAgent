#!/bin/bash
# Parity pick-up (approved 2026-11-10): apps, then hex functions, for DeepSeek V4.1 Flash and Nemotron 3 Ultra.
# MiniMax M3 waits for a credit top-up (about $27 left before this run). Per-model cap $40 (cumulative ledger).
cd /home/aharmon/BytecodeAgent
export BCA_FOUNDRY_CAP_USD=40
P=".venv/bin/python -m bca.liveeval run"; S=fireworks:deepseek-v4p1-flash,fireworks:nemotron-3-ultra-nvfp4
$P --run-dir eval/runs/apps-fireworks-20261110 --tasks apps --subjects $S --jobs 4 --max-calls 400 --max-infra-errors 20 > eval/runs/apps-fireworks-20261110.out 2>&1
$P --run-dir eval/runs/hex-fireworks-20261110 --tasks round1 --paths hex --subjects $S --jobs 4 --max-calls 100 --max-infra-errors 20 > eval/runs/hex-fireworks-20261110.out 2>&1
echo ALLDONE
