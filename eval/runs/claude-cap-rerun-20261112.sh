#!/bin/bash
# Re-runs the 46 cells superseded on 2026-11-12 (Claude Code continued calls past max_tokens; the adapter now stops at
# the cap), with each run's own command and the subject that owns the cells, then resumes the older-Claude chain
# (Sonnet 4.6's 18 function and 11 app cells are re-run by it, then Opus 4.6 and Opus 4.5).
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh
cap() { echo $(( $(wc -l < $1/calls.log) + 80 )); }
for spec in "apps-hex-claude-20261001 claude-opus-4-8" "apps-hex-sonnet5-20261022-143141 claude-sonnet-5" \
            "apps-hex-fable52-mythos-20261110 claude-mythos-5-1" "apps-hex-claude-opus-4-7-20261111 claude-opus-4-7"; do
  set -- $spec; rd=eval/runs/$1
  echo "== resumed $(date '+%m-%d %T') (cap re-run)" >> $rd.out
  $P --run-dir $rd --tasks apps --paths hex --samples 1 --subjects $2 --jobs 4 --max-calls $(cap $rd) >> $rd.out 2>&1
  echo "$(date +%T) $1 $2: exit $?"
done
exec eval/runs/claude-older-resume-20261112.sh
