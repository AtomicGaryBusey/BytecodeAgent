#!/bin/bash
# Serve ServiceNow-AI/Apriel-1.6-15b-Thinker (MIT) for the local baseline (queue item 8, 2026-11-13). Same audited
# image and memory share as Kolibri (vLLM 0.29.0; the Aleph-Alpha plugin in the image does not touch this model),
# weights pinned to a revision, no reasoning parser (0.29.0 has none for Apriel; the card's route is an unmerged
# parser in a third-party image, not used) and no tool parser: the adapter holds back everything before
# [BEGIN FINAL RESPONSE] (bca/liveeval/vendor_vllm.py FINAL_MARKERS). Context 131072, as the model card serves it.
# From 2026-11-14 (user): n-gram speculative decoding (prompt lookup, no draft model; vLLM verifies every proposed
# token with rejection sampling, so the output distribution is unchanged) and 4 calls at once (BCA_VLLM_SLOTS=4).
set -euo pipefail
docker rm -f bca-vllm-apriel >/dev/null 2>&1 || true
docker run -d --name bca-vllm-apriel --gpus all --ipc host -p 127.0.0.1:8000:8000 -e HF_HUB_OFFLINE=1 \
  -v bca-hf-models:/root/.cache/huggingface --entrypoint vllm bca-vllm-kolibri:0.29.0-aa1.0.0 serve \
  ServiceNow-AI/Apriel-1.6-15b-Thinker --revision 5810d5f1723032300131e677450d7f95a15a05e7 \
  --max-model-len 131072 --gpu-memory-utilization 0.80 --host 0.0.0.0 --port 8000 \
  --speculative-config '{"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4, "prompt_lookup_min": 2}'
