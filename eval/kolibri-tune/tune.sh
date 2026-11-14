#!/bin/bash
# Re-tune Kolibri-1's Triton expert-layer config on this GB10 and keep the result IN THE REPO (eval/kolibri-tune/).
# The first config (2026-10-04/05) lived in a /tmp scratchpad and was lost in the 2026-11-12 power loss.
# Method, as recorded in reports/cross-vendor-methodology.md ("Kolibri-1's serving setup"):
#   - batch size 1 tuned on the GB10 by vLLM's benchmarks/kernels/benchmark_moe.py (fp8_w8a8, tp 1);
#   - the 2048 entry borrowed from vLLM's shipped E=256,N=512 NVIDIA_B200 fp8 [128,128] config, with
#     num_stages 4 -> 3 for the GB10's smaller shared memory (tuning 2048 here took ~49 s per configuration).
# Same image as the served stack (vLLM 0.29.0 + Aleph-Alpha plugin 1.0.0); ray is installed only in this throwaway
# container (the tuner imports it). run_tuner.py loads the vLLM plugins (Kolibri's config type) and maps the
# Kolibri1ForCausalLM expert shape, which the stock tuner does not know (see its docstring). Needs the GPU free: run it when no Kolibri server is up.
set -euo pipefail
cd "$(dirname "$0")"
D=$PWD
IMG=bca-vllm-kolibri:0.29.0-aa1.0.0
NAME='E=384,N=512,device_name=NVIDIA_GB10,dtype=fp8_w8a8,block_shape=[128,128].json'
mkdir -p "$D/tuned"
docker ps --format '{{.Names}}' | grep -q vllm-kolibri && { echo "a Kolibri server is running: stop it first"; exit 1; }
docker run --rm --gpus all --ipc host -e HF_HUB_OFFLINE=1 -v bca-hf-models:/root/.cache/huggingface \
  -v "$D/tuned:/out" -v "$D/run_tuner.py:/vllm-workspace/benchmarks/kernels/run_tuner.py:ro" --entrypoint bash "$IMG" -c '
    set -e; pip install -q ray 2>&1 | tail -1
    cd /vllm-workspace/benchmarks/kernels
    python3 run_tuner.py --model Aleph-Alpha/Kolibri-1 --tp-size 1 --dtype fp8_w8a8 --tune --batch-size 1 \
      --trust-remote-code --save-dir /out
    cp "/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/fused_moe/configs/E=256,N=512,device_name=NVIDIA_B200,dtype=fp8_w8a8,block_shape=[128,128].json" /out/reference-B200-E256-N512.json'
python3 - "$D" "$NAME" <<'EOF'
import json, sys, os
d, name = sys.argv[1], sys.argv[2]
tuned = json.load(open(os.path.join(d, "tuned", name)))
ref = json.load(open(os.path.join(d, "tuned", "reference-B200-E256-N512.json")))
big = dict(ref["2048"]); big["num_stages"] = 3
out = {"triton_version": tuned.get("triton_version"), "1": tuned["1"], "2048": big}
json.dump(out, open(os.path.join(d, name), "w"), indent=4)
print("wrote", name, json.dumps(out))
EOF
