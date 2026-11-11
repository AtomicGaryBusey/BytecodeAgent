#!/bin/bash
# Hex functions, apps, apps-hex for Fable 5.2 / Mythos 5.1 / Mythos 5.2 (same batteries as Haiku 5.5 on 11-07)
cd /home/aharmon/BytecodeAgent
P=.venv/bin/python
$P -m bca.liveeval run --run-dir eval/runs/hex-fable52-mythos-20261110 --tasks round1 --paths hex --subjects claude-fable-5-2,claude-mythos-5-1,claude-mythos-5-2 --jobs 4 --max-calls 80 > eval/runs/hex-fable52-mythos-20261110.out 2>&1
$P -m bca.liveeval run --run-dir eval/runs/apps-fable52-mythos-20261110 --tasks apps --subjects claude-fable-5-2,claude-mythos-5-1,claude-mythos-5-2 --jobs 4 --max-calls 450 > eval/runs/apps-fable52-mythos-20261110.out 2>&1
$P -m bca.liveeval run --run-dir eval/runs/apps-hex-fable52-mythos-20261110 --tasks apps --paths hex --samples 1 --subjects claude-fable-5-2,claude-mythos-5-1,claude-mythos-5-2 --jobs 4 --max-calls 120 > eval/runs/apps-hex-fable52-mythos-20261110.out 2>&1
echo ALLDONE
