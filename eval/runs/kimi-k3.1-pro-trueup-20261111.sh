#!/bin/bash
# True up Kimi K3.1 Pro (user, 2026-11-11): its function battery re-run at today's catalog default effort (`high`), so
# every K3.1 Pro battery runs at one effort; the 2026-10-23 run at `max` is kept as
# eval/runs/kimi-k3.1-pro-effort-study-max-20261023-150408 (an effort study, out of the main tables). Kimi Code 2.5.3
# (audited 2026-11-11), CLI-default effort, 2 calls at a time (Allegretto's 5-hour and 7-day caps).
cd /home/aharmon/BytecodeAgent
.venv/bin/python -m bca.liveeval run --run-dir eval/runs/kimi-k3.1-pro-20261111 --tasks all --model kimi:kimi-code/k3.1-pro --jobs 2 --max-calls 400 > eval/runs/kimi-k3.1-pro-20261111.out 2>&1
echo "$(date +%T) functions exit $?"; echo ALLDONE
