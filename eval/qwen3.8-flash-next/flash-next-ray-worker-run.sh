#!/usr/bin/env bash
# Spark 2 half of the Qwen3.8-Flash-Next TP=2 service (Ray worker).
# Derived from qwen38-ray-worker-run.sh; only the image and container name differ.
# Runs in the foreground (--block). Written 2026-11-16.
: "${HEAD_IP:?set HEAD_IP to the head node QSFP address}" "${WORKER_IP:?set WORKER_IP to the worker node QSFP address}"
set -euo pipefail

IMAGE=vllm-ray:26.10     # MUST match the head's image/Ray version
MODELS=/opt/flash-next/models
HEAD=${HEAD_IP}
SELF=${WORKER_IP}

ip -4 addr show | grep -q '10\.100\.224\.2' || {
  echo "FATAL: ${WORKER_IP} absent -- QSFP link not configured yet" >&2; exit 1; }
[ -f "$MODELS/qwen3.8-flash-next-nvfp4/config.json" ] || {
  echo "FATAL: weights not synced to Spark 2 yet" >&2; exit 1; }

docker rm -f flash-next-ray-worker >/dev/null 2>&1 || true

# VLLM_USE_DEEP_GEMM=0 must be set here too: Ray workers inherit this
# container's env, not the head's.
exec docker run --rm --name flash-next-ray-worker \
  --runtime nvidia --gpus all \
  --network host --ipc host --shm-size 16g \
  --device /dev/infiniband \
  --cap-add IPC_LOCK \
  -v "$MODELS":/models:ro \
  -e NCCL_SOCKET_IFNAME=enp1s0f0np0 \
  -e GLOO_SOCKET_IFNAME=enp1s0f0np0 \
  `# NCCL_SOCKET_IFNAME only pins the OOB socket. First launch 2026-11-16 showed
   # NET/IB using BOTH rocep1s0f0 (link 1, MTU 9000) and roceP2p1s0f0 (link 2,
   # MTU 1500, documented as must-not-carry-NCCL). Pin the HCA explicitly.` \
  -e NCCL_IB_HCA==rocep1s0f0 \
  -e VLLM_HOST_IP="$SELF" \
  -e NCCL_DEBUG=INFO \
  -e VLLM_USE_DEEP_GEMM=0 \
  --entrypoint bash \
  "$IMAGE" -c "exec ray start --address=${HEAD}:6379 --node-ip-address=${SELF} --num-gpus 1 --block"
