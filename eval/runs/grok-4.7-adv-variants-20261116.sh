#!/bin/bash
# misleading_hint and terse on the adversarial battery for Grok 4.7 (SuperGrok), user 2026-11-16: the non-Claude
# check of the Claude-only variant findings. Same harness, CLI (grok 1.0.41), effort and limits as its baseline run
# eval/runs/grok-4.7-20260928-231546 (adversarial 56/60). 60 cells per variant, up to 4 attempts.
cd /home/aharmon/BytecodeAgent
RD=eval/runs/grok-4.7-adv-variants-20261116
.venv/bin/python -m bca.liveeval run --run-dir $RD --tasks adversarial --variant misleading_hint,terse \
  --model grok:grok-4.7 --jobs 2 --max-calls 320 >> $RD.out 2>&1
echo "$(date +%T) exit $?" >> $RD.out
