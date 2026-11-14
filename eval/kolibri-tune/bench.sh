#!/bin/bash
# Like-for-like Kolibri-1 decode benchmark, the method behind the 2026-10-05 numbers (31.9 / 27.8 default,
# 35.3 / 28.2 tuned; session d37cd089 apply_and_bench.sh / bench_backends.sh): two sequential non-streamed
# /v1/chat/completions calls, {"max_tokens":1500,"ignore_eos":true}, user "Explain how a CPU pipeline works, in
# detail.", no system prompt, default sampling; rate = completion_tokens / wall time (prefill included); the first
# call is the first request after startup. Server flags as serve.sh, except the config mounted (or none).
# Usage: bench.sh <label> <config file | none> [max-model-len, default 131072 as served now; 65536 in October]
set -euo pipefail
cd "$(dirname "$0")"
L=$1; C=$2; LEN=${3:-131072}
D=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/fused_moe/configs
NAME='E=384,N=512,device_name=NVIDIA_GB10,dtype=fp8_w8a8,block_shape=[128,128].json'
M=(); [ "$C" != none ] && M=(-v "$PWD/$C:$D/$NAME:ro")
docker rm -f bca-vllm-kolibri-bench >/dev/null 2>&1 || true
docker run -d --name bca-vllm-kolibri-bench --gpus all --ipc host -p 127.0.0.1:8000:8000 -e HF_HUB_OFFLINE=1 \
  -v bca-hf-models:/root/.cache/huggingface "${M[@]}" bca-vllm-kolibri:0.29.0-aa1.0.0 Aleph-Alpha/Kolibri-1 \
  --revision e52eb4627d11516b0c01de49210ab5a4e4061444 --kv-cache-dtype fp8 --reasoning-parser kolibri1 \
  --max-model-len "$LEN" --gpu-memory-utilization 0.80 --host 0.0.0.0 --port 8000 --moe-backend triton >/dev/null
for i in $(seq 1 120); do curl -s 127.0.0.1:8000/v1/models >/dev/null && break; sleep 10; done
dflt=$(docker logs bca-vllm-kolibri-bench 2>&1 | grep -c "Using default MoE config" || true)
r=$(python3 - <<'EOF'
import json, time, urllib.request
body = json.dumps({"model": "Aleph-Alpha/Kolibri-1", "max_tokens": 1500, "ignore_eos": True,
                   "messages": [{"role": "user", "content": "Explain how a CPU pipeline works, in detail."}]}).encode()
out = []
for _ in range(2):
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions", body,
                                         {"Content-Type": "application/json"}), timeout=900))
    out.append(r["usage"]["completion_tokens"] / (time.time() - t))
print(" / ".join(f"{x:.1f}" for x in out))
EOF
)
docker rm -f bca-vllm-kolibri-bench >/dev/null
echo "$(date '+%F %T') $L (config $C, max-model-len $LEN, default-config warnings $dflt): $r tok/s"
