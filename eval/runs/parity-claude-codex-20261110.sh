#!/bin/bash
# Parity pick-up (approved 2026-11-10): apps for Claude Opus 4.8, Sonnet 5, Sonnet 4.5 and Codex GPT-6 Sol.
cd /home/aharmon/BytecodeAgent
P=".venv/bin/python -m bca.liveeval run"
$P --run-dir eval/runs/apps-claude-older-20261110 --tasks apps --subjects claude-opus-4-8,claude-sonnet-5,claude-sonnet-4-5-20250929 --jobs 4 --max-calls 500 > eval/runs/apps-claude-older-20261110.out 2>&1 &
$P --run-dir eval/runs/apps-codex-sol-20261110 --tasks apps --subjects codex:gpt-6-sol --jobs 3 --max-calls 160 > eval/runs/apps-codex-sol-20261110.out 2>&1 &
wait; echo ALLDONE
