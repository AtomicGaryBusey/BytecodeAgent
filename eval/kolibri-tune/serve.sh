#!/bin/bash
# (Re)create the Kolibri-1 serving container `bca-vllm-kolibri` with the tuned config mounted FROM THE REPO.
# Same image, weights revision and flags as every Kolibri battery (reports/cross-vendor-methodology.md).
# Memory rule (user, 2026-10-18): Kolibri yields to the CodeGraph agent; pause at MemFree <= 1 GB.
set -euo pipefail
cd "$(dirname "$0")"
NAME='E=384,N=512,device_name=NVIDIA_GB10,dtype=fp8_w8a8,block_shape=[128,128].json'
[ -f "$PWD/$NAME" ] || { echo "no tuned config in $PWD: run tune.sh first"; exit 1; }
docker rm bca-vllm-kolibri >/dev/null 2>&1 || true
docker run -d --name bca-vllm-kolibri --gpus all --ipc host -p 127.0.0.1:8000:8000 -e HF_HUB_OFFLINE=1 \
  -v bca-hf-models:/root/.cache/huggingface \
  -v "$PWD/$NAME:/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/fused_moe/configs/$NAME:ro" \
  bca-vllm-kolibri:0.29.0-aa1.0.0 Aleph-Alpha/Kolibri-1 --revision e52eb4627d11516b0c01de49210ab5a4e4061444 \
  --kv-cache-dtype fp8 --reasoning-parser kolibri1 --max-model-len 131072 --gpu-memory-utilization 0.80 \
  --host 0.0.0.0 --port 8000 --moe-backend triton
