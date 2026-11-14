#!/bin/bash
# Restarts the older-Claude chain after an abort caused by Cloudflare "Attention Required" 403s in front of
# Anthropic's API (2026-11-13: two bursts, 01:47-01:51 and 02:16-02:20, across many cells; probes between bursts
# answer normally). Retried calls are infrastructure errors, never scored. Waits 20 min and resumes; a real plan
# usage limit (no 403 in the abort line) stops it for good. Gives up after 12 restarts.
cd /home/aharmon/BytecodeAgent
for i in $(seq 1 12); do
  eval/runs/claude-older-resume-20261112.sh >> eval/runs/claude-older-resume-20261112.log 2>&1; rc=$?
  tail -n 2 eval/runs/claude-older-resume-20261112.log | grep -q ALLDONE && { echo "$(date +%T) chain done"; exit 0; }
  last=$(ls -t eval/runs/*claude-*-20261111.out | head -1)
  if [ $rc -eq 3 ] && tail -n 3 "$last" | grep -q "success 403"; then
    echo "$(date +%T) Cloudflare 403 abort in $last (restart $i): waiting 20 min"; sleep 1200
  else echo "$(date +%T) chain stopped, rc=$rc, last $last: not a 403 abort, leaving it"; exit $rc; fi
done
echo "$(date +%T) 12 restarts used: giving up"
