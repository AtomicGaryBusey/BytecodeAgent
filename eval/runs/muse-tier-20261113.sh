#!/bin/bash
# Muse tier comparison (user, 2026-11-13): muse-spark-1.3 on the Power Usage subscription against the contributor-tier
# run of 2026-11-11/12 (muse-spark-1.3-contributor), same cells. The client's requests are byte-identical apart from
# the model id (eval/audits/muse-1.4.18-tiers-20261113), so any difference is server-side. Functions and apps.
cd /home/aharmon/BytecodeAgent; . eval/runs/chain-lib.sh; T=20261113
X="--model muse:muse-spark-1.3 --jobs 2"
step eval/runs/muse-tier-paid-spark-1.3-$T 224 --tasks all $X --max-calls 500
step eval/runs/apps-muse-tier-paid-spark-1.3-$T 96 --tasks apps $X --max-calls 300
echo ALLDONE
