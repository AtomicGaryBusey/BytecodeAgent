#!/usr/bin/env bash
# Qwen3.8-Flash-Next NVFP4 (nvidia/Qwen3.8-Flash-Next-NVFP4) for TP=2 across both Sparks.
# Revision PINNED (huggingface_hub verifies LFS sha256 against a fixed commit). Started 2026-11-16.
: "${HEAD_IP:?set HEAD_IP to the head node QSFP address}" "${WORKER_IP:?set WORKER_IP to the worker node QSFP address}"
set -euo pipefail
VENV=/opt/flash-next/.venv/bin/python
DEST=/opt/flash-next/models/qwen3.8-flash-next-nvfp4
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$DEST"
echo "=== download start $(date -u +%FT%TZ) ==="
DEST="$DEST" "$VENV" - <<'PY'
import os
from huggingface_hub import snapshot_download
print("SNAPSHOT:", snapshot_download(repo_id="nvidia/Qwen3.8-Flash-Next-NVFP4",
      revision="fc694b54fb0174e0913e6adf86691ef85a4ead47",
      local_dir=os.environ["DEST"], max_workers=4))
PY
bytes=$(find "$DEST" -type f -name '*.safetensors' -printf '%s\n' | awk '{s+=$1} END{print s+0}')
echo "safetensors bytes: $bytes (expect 132680249378)"
[ "$bytes" = 132680249378 ] || { echo "FATAL: size mismatch -- incomplete download"; exit 1; }
echo "=== rsync to Spark 2 $(date -u +%FT%TZ) ==="
ssh ${WORKER_IP} "mkdir -p $DEST"
rsync -a --info=progress2 --exclude .cache "$DEST/" "${WORKER_IP}:$DEST/"
echo "=== done $(date -u +%FT%TZ) ==="
