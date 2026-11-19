# Qwen3.8-Flash-Next on two DGX Sparks (TP=2 + MTP x2)

How the Qwen3.8-Flash-Next row was served for the BytecodeAgent eval. It covers
everything needed to reproduce the server and its speed.

**Measured 2026-11-16** (`bench/`, temperature 0, thinking off, 512 tokens):

| Config | Single-stream decode | Concurrency 2, aggregate | Per stream | MTP acceptance length |
|---|---|---|---|---|
| No MTP | 28.7 tok/s | 49.5 | 26.6 | — |
| MTP x1 + EP | 42.6 | 65.6 | 37.3 | ~1.9 |
| **MTP x2 + EP (served)** | **47.4** | **71.9** | **41.5** | ~2.5-3.0 |

TTFT ~0.16-0.20 s on short prompts. GPU clocks were capped (`nvidia-smi -lgc 300,2000`)
on both nodes for all runs. During the benchmark the median call was ~50 s at ~29 tok/s
per stream, with long xhigh-thinking answers.

## What the eval talked to

- An OpenAI-compatible chat completions endpoint behind a LiteLLM proxy. Model id
  `Qwen3.8-Flash-Next`; streamed chunks report that name. vLLM also serves the alias
  `gym-eval`. The proxy URL and key are deliberately not in this repo.
- Thinking on by default (`reasoning_effort` xhigh; also medium and low, as chat template
  kwargs). Off with `chat_template_kwargs: {"enable_thinking": false}`. Reasoning parser
  `qwen3`, so reasoning arrives in `reasoning_content` and `content` holds only the answer.
- Sampling: the checkpoint's generation_config (temperature 1.0, top_p 0.95, top_k 20).
  No server override.
- max-model-len 131072. The proxy request timeout was raised to 3600 s for this run (see
  "Proxy" below). Concurrency 2, enforced on the key.

## Model

- `nvidia/Qwen3.8-Flash-Next-NVFP4` @ `fc694b54fb0174e0913e6adf86691ef85a4ead47`, the
  published checkpoint (ModelOpt NVFP4 routed experts; BF16 attention and shared experts;
  FP8 MTP head and n-gram embedding). 125B MoE / 6B active + 51B n-gram + 4B MTP. 123.57 GiB.
- `download-flash-next.sh` fetches the pinned revision, checks the exact byte count, and
  rsyncs to the second node. Each node needs its own copy.
- Why NVFP4: BF16 (~360 GB) does not fit in 2 x 128 GB, and FP8 (~180 GB) leaves almost
  nothing for KV cache.

## Required customizations (each was a failure without it)

| # | What | Where | Without it |
|---|---|---|---|
| 1 | vLLM 26.10 image + Ray | `Dockerfile.vllm-ray-26.10`, built on BOTH nodes | NVIDIA 26.07 (vLLM 0.24.0) has no `qwen4_exp` architecture. NVIDIA vLLM images ship without Ray, which multi-node TP needs. 26.10 ships xgrammar 0.2.7, so the 26.07-era xgrammar override is not needed. |
| 2 | QSFP link at MTU 9000, persistently | `netplan/99-zz-qsfp-mtu.yaml` on both nodes | NVIDIA Sync's netplan sets no MTU, so after a reboot both ends were back at 1500. A drop-in that adds only `mtu: 9000` survives NVIDIA Sync rewriting its own file. |
| 3 | `NCCL_IB_HCA==rocep1s0f0` | both run scripts | `NCCL_SOCKET_IFNAME` only pins the OOB socket. NET/IB also used the second QSFP port (MTU 1500). |
| 4 | `--gpu-memory-utilization 0.50` | run script default | vLLM memory-maps the ~50 GiB n-gram table from the checkpoint ("Engram checkpoint_mapped" on integrated-memory GPUs) OUTSIDE the utilization budget. At 0.80, Ray's memory monitor OOM-killed rank 0. |
| 5 | `--max-num-seqs 16` | run script default | With MTP + EP only 166 Mamba/GDN state blocks fit, so the default 256 aborts engine init. |
| 6 | MTP x2 + `--enable-expert-parallel` | run script defaults `SPEC=1 SPEC_TOKENS=2` | This is the speed (table above). The model card requires EP with MTP (128x128 FP8 MTP expert blocks). |
| 7 | `--quantization modelopt --trust-remote-code` | run script | Per the model card. |
| 8 | `VLLM_USE_DEEP_GEMM=0` in BOTH containers | run scripts | Carried over from Qwen3.8-27B (DeepGemm E8M0 scale bug on Blackwell). Not re-tested here. |

### Proxy (LiteLLM) fixes found during the run

- **Streams cut mid-JSON:** uvicorn 0.51's multiprocess supervisor kills any worker that
  misses a 5 s health ping. Finishing a 2+ MB SSE stream (a long xhigh answer) blocked a
  LiteLLM worker past 5 s, so every stream on that worker was cut. The fix is
  `--timeout_worker_healthcheck 120` on the litellm CLI. Signature: LiteLLM logs
  `Waiting for child process [pid]` then `Child process [pid] died`, and the reverse proxy
  logs `aborting with incomplete response ... unexpected EOF`, at the same second.
  Affected calls were retried, not scored.
- **Request timeout:** 1200 s cut full-budget answers (49k-82k output tokens at ~29 tok/s is
  1,700-2,800 s), so it was raised to 3600 s. With LiteLLM, set `--request_timeout` on the
  CLI; the YAML `request_timeout` is overridden by the CLI's default.

## Serving layout

| Node | Script | systemd unit | Role |
|---|---|---|---|
| head | `flash-next-vllm-run.sh` | `systemd/flash-next-vllm.service` | Ray head + vLLM API (bound to the docker bridge only) |
| worker | `flash-next-ray-worker-run.sh` | `systemd/flash-next-ray-worker.service` | Ray worker |

- Units set no `Environment=`, so the run script's defaults are the config. One-off
  overrides: `GPU_UTIL`, `MAX_LEN`, `MAX_SEQS`, `SPEC` (0 = no MTP), `SPEC_TOKENS`, `EAGER`.
- `StartLimitIntervalSec=0` with RestartSec 60 (head) / 15 (worker), so boot ordering across
  the two machines self-heals. The head waits 180 s for 2 GPUs, then exits and retries.
- Cold start ~5-8 min. Verify with launch args in the vLLM log
  (`'speculative_config': {'method': 'mtp', 'num_speculative_tokens': 2}`,
  `'enable_expert_parallel': True`, `'max_num_seqs': 16`), plus NCCL `NET/IB : Using [0]rocep1s0f0`.
- vLLM warns "Using default MoE config" for this model on GB10. A tuned fused-MoE config
  (as done in `../kolibri-tune/`) is an untried possible speed-up.

## Not adopted

- **DFlash2 drafter** (`incoai/Qwen3.8-Flash-Next-DFlash2`): the authors report ~13-22%
  over MTP at concurrency 1. Their route is SGLang at a pinned commit plus a feature-capture
  patch, validated at TP1 on GB300 with the FP8 target. vLLM 26.10 has a `dflash` method and
  the `DFlash2DraftModel` class, but its `qwen4_exp` model exposes none of the hidden-state
  hooks DFlash needs. Revisit when upstream vLLM adds them.
- **MTP x3:** position-2 acceptance was already down to 64-81%. Untested.

Site-specific values (paths, addresses, hostnames) are listed in `SANITIZE.md`.
