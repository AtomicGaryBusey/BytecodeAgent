#!/usr/bin/env bash
# Spark 1 half of the Qwen3.8-Flash-Next TP=2 service (Ray head + vLLM API server).
# Start flash-next-ray-worker-run.sh on Spark 2 first or right after -- the head
# waits up to 180s for the worker to register.
#
# Derived from qwen38-vllm-run.sh (same foreground/exec shape so a vLLM crash
# exits the container instead of leaving it "Up" and deaf). Written 2026-11-16.
#
# ROUTING: served as `gym-eval` on $API_BIND:8110, which is exactly where the
# existing LiteLLM `gym-eval` entry already points -- no LiteLLM config change
# or restart needed. Also exposed as `Qwen3.8-Flash-Next` for direct calls.
#
# Model: nvidia/Qwen3.8-Flash-Next-NVFP4 @ fc694b54 (125B MoE / 6B active,
# + 51B FP8 n-gram embedding, + 4B MTP). ~124 GiB of weights -> ~62 GiB/node at TP=2
# if everything shards. NVIDIA only validated it on B200/B300, NOT GB10 -- expect
# the first launch to be a debugging session.
: "${HEAD_IP:?set HEAD_IP to the head node QSFP address}" "${WORKER_IP:?set WORKER_IP to the worker node QSFP address}"
set -euo pipefail

IMAGE=vllm-ray:26.10
MODELS=/opt/flash-next/models
MODEL=/models/qwen3.8-flash-next-nvfp4
SELF=${HEAD_IP}
API_BIND=${API_BIND:-127.0.0.1}   # the address the API listens on (in the eval: a docker bridge only the proxy reaches)
PORT=8110

# Tunables (env overrides, no edit needed):
#   GPU_UTIL  default 0.45. MEASURED 2026-11-16 (first launch, 0.80): per node
#             38 GiB weights + 3 GiB CUDA graphs + 50 GiB KV -- PLUS ~50 GiB of
#             n-gram (Engram/PLE) table that vLLM memory-maps from the checkpoint
#             ("checkpoint_mapped" on integrated GPUs). That mapped table sits
#             OUTSIDE gpu_memory_utilization's budget, so 0.80 + it + Spark 1's
#             host stack crossed Ray's 95% memory threshold and Ray OOM-killed
#             rank 0. 0.45 (~55 GiB) leaves ~12 GiB KV/node, ample at 2 concurrent.
#   MAX_LEN   default 131072 -- start conservative; native max is 262144.
#   SPEC=1    MTP speculative decoding (1 token, per the model card). Needs
#             vLLM PR #55513 (merged upstream 2026-09-08; unverified in the
#             NVIDIA 26.10 build) and adds --enable-expert-parallel because the
#             card says MTP's 128x128 FP8 blocks need EP-width experts.
#   EAGER=1   disable CUDA graphs if capture fails.
#   MAX_SEQS  default 16 (vLLM default 256). Each running sequence needs one
#             Mamba/GDN state block. With SPEC=1 (MTP + expert parallel) at 0.45
#             only 166 blocks fit and engine init FAILED ("max_num_seqs (256)
#             exceeds available Mamba cache blocks"), 2026-11-16. We serve <=8
#             concurrent, so 16 is ample.
GPU_UTIL="${GPU_UTIL:-0.50}"
MAX_SEQS="${MAX_SEQS:-16}"
MAX_LEN="${MAX_LEN:-131072}"
SPEC_FLAG=""
# SPEC_TOKENS: draft tokens per step (default 2; card documents 1, 2 measured faster).
SPEC_TOKENS="${SPEC_TOKENS:-2}"
# DEFAULTS LOCKED 2026-11-16 after measurement (bench/*.txt): no MTP 28.7 tok/s,
# MTP x1 42.6, MTP x2 47.4 single-stream; acceptance len ~2.7. SPEC=0 to disable.
[ "${SPEC:-1}" = "1" ] && SPEC_FLAG="--enable-expert-parallel --speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":${SPEC_TOKENS}}' --no-enable-flashinfer-autotune"
EXTRA=""
[ "${EAGER:-0}" = "1" ] && EXTRA="--enforce-eager"

[ -f "$MODELS/qwen3.8-flash-next-nvfp4/config.json" ] || {
  echo "FATAL: weights missing at $MODELS/qwen3.8-flash-next-nvfp4" >&2; exit 1; }

ip -4 addr show | grep -q '10\.100\.224\.1' || {
  echo "FATAL: ${HEAD_IP} absent -- QSFP link not configured yet" >&2; exit 1; }
# Jumbo path check: the 2026-10-30 reboot silently reverted both ends to MTU
# 1500. Fixed persistently 2026-11-16 (/etc/netplan/99-zz-qsfp-mtu.yaml on both
# Sparks); keep failing closed in case it regresses.
ping -c1 -W3 -M do -s 8972 ${WORKER_IP} >/dev/null 2>&1 || {
  echo "FATAL: jumbo path to Spark 2 broken (MTU regressed?)" >&2; exit 1; }

docker rm -f flash-next-vllm >/dev/null 2>&1 || true

exec docker run --rm --name flash-next-vllm \
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
  "$IMAGE" -c "
    set -e
    ray start --head --node-ip-address=${SELF} --port=6379 --num-gpus 1
    for i in \$(seq 1 90); do
      ray status 2>/dev/null | grep -qE '/2\.0 GPU' && break
      sleep 2
    done
    ray status 2>/dev/null | grep -qE '/2\.0 GPU' || {
      echo 'FATAL: Spark 2 Ray worker never registered; refusing to start vLLM' >&2
      exit 1; }
    echo 'Ray cluster has 2 GPUs; starting vLLM'
    exec vllm serve '${MODEL}' \
      --served-model-name gym-eval Qwen3.8-Flash-Next \
      --tensor-parallel-size 2 \
      --distributed-executor-backend ray \
      --quantization modelopt \
      --trust-remote-code \
      --max-model-len ${MAX_LEN} \
      --gpu-memory-utilization ${GPU_UTIL} \
      --max-num-seqs ${MAX_SEQS} \
      --enable-prefix-caching \
      --mamba-cache-mode align \
      --kv-cache-dtype fp8 \
      --reasoning-parser qwen3 \
      ${SPEC_FLAG} \
      --enable-auto-tool-choice --tool-call-parser qwen3_coder \
      --host ${API_BIND} --port ${PORT} ${EXTRA}
  "
