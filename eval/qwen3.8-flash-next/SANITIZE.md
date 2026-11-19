# Sanitised for publication (2026-11-19)

This folder was generalised before it entered the public history. The serving host's own values were replaced:

| What | Where | Now |
|---|---|---|
| Home-directory paths (models, venv, scripts, logs) | `download-flash-next.sh`, both run scripts, both systemd units | `/opt/flash-next/...` |
| Service user and group | both systemd units | `flashnext` |
| The two nodes' QSFP link addresses | run scripts, download script | `HEAD_IP` / `WORKER_IP`, required environment variables (set them in the units with `Environment=` or an `EnvironmentFile=`) |
| The API bind address | head run script, head unit comment, `bench/decode-bench.py` | `API_BIND` (default 127.0.0.1); the benchmark takes `BENCH_URL` |
| Image tag and labels | Dockerfile, run scripts, PROVENANCE.json | `vllm-ray:26.10`, `org.flashnext.*` |
| A reference to the private source repository | PROVENANCE.json | removed |

Kept on purpose: the NIC and RDMA device names (the standard DGX Spark ConnectX-7 names), port 8110, the `gym-eval`
route alias, and the comments about the host stack.

Not present, and keep it that way: the proxy hostname, API keys or key aliases, Tailscale names, the operator's
email, private addresses and internal issue numbers. The scripts ran as served with the original values; with the
placeholders they need `HEAD_IP`, `WORKER_IP` and (optionally) `API_BIND` set.

Licences: the scripts are ours. The model is under the NVIDIA Open Model License plus the Qwen licence. This folder
ships no weights, only the pinned download script.
