#!/bin/bash
# Fetch Apriel-1.6-15b-Thinker at the pinned revision into the bca-hf-models volume (no HF token needed; MIT).
# 2026-11-13: 18 files, 29,745,200,287 bytes on disk in the volume (weights, tokenizer, config).
set -euo pipefail
REV=5810d5f1723032300131e677450d7f95a15a05e7
docker run --rm -v bca-hf-models:/root/.cache/huggingface --entrypoint python3 bca-vllm-kolibri:0.29.0-aa1.0.0 -c \
  "from huggingface_hub import snapshot_download as s; print('DONE', s('ServiceNow-AI/Apriel-1.6-15b-Thinker', revision='$REV'))"
