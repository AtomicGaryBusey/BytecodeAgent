# Apriel-1.6-15b-Thinker on the ZGX (one GB10, vLLM 0.29.0, token budget)

How the Apriel row of the BytecodeAgent eval was served: enough to rebuild the server and reproduce its speed.

**Throughput** (`bench/throughput.py`, computed from the run's own call log and attempt records, no extra calls):

| Server config | Calls at once | Per call (median) | Aggregate | Window |
|---|---|---|---|---|
| Plain, from 2026-11-13 23:12 | 1 | 7.7 tok/s | 7.7 tok/s at best | 7 calls, 2.5 h |
| **n-gram speculation x5 + 4 calls at once (served), from 2026-11-14 02:08** | **4** | **7.8 tok/s** | **27.5 tok/s** | 433 calls, 64 h |

The 11-14 tuning test measured 7.9 tok/s for one call alone and 7.8 each at five at once, and about +30% per call
from n-gram speculation on task prompts. In the live run the gain from speculation and the cost of batching four
roughly cancel, so each call stays near 7.5-7.8 tok/s and the 3.6x aggregate gain comes from concurrency. The 7-call
"before" window is short and includes a guard pause, so 7.7 tok/s (one call) is the fair single-stream figure, not
the 4.7 tok/s that window's wall clock gives.

## What the eval talked to

- vLLM's OpenAI-compatible server on `127.0.0.1:8000` (this machine only), adapter `vllm:` in token-budget mode
  (`vllm:ServiceNow-AI/Apriel-1.6-15b-Thinker@budget`). No gateway, no key.
- Each call: `[system: SYSTEM_PROMPT, user: prompt]`, streaming, no tools, no sampling fields, no effort setting.
  Sampling is the checkpoint's `generation_config.json` as vLLM applies it.
- Token budget per call, by battery: 32,768 output tokens (round 1), 49,152 (round 2, long), 24,576 (adversarial),
  81,920 (apps). Reaching it is scored as a timeout. A generous wall-clock ceiling remains for a hung server.
- `BCA_VLLM_SLOTS=4`: four calls at once through per-slot file locks. The run records the slot count.

## Model

- `ServiceNow-AI/Apriel-1.6-15b-Thinker` @ `5810d5f1723032300131e677450d7f95a15a05e7` (MIT): a LLaVA-style checkpoint (Mistral text model + Pixtral vision tower, used text-only), BF16 as published:
  not quantised, and never quantised to speed it up (user rule). 29.7 GB in the `bca-hf-models` volume.
- `download-apriel.sh` fetches the pinned revision. The server runs with `HF_HUB_OFFLINE=1`.

## Required customizations (each was needed)

| # | What | Where | Without it |
|---|---|---|---|
| 1 | Final-response marker: everything before `[BEGIN FINAL RESPONSE]` is reasoning, held back and never scored | `bca/liveeval/vendor_vllm.py` `FINAL_MARKERS` | vLLM 0.29.0 has no reasoning parser for Apriel (the model card's is an unmerged parser in a third-party image, not used). Without it the plain-text reasoning would be scored as the answer. |
| 2 | `--max-model-len 131072` | `serve.sh` | The model card's serving length; the long thinking answers need it. |
| 3 | `--gpu-memory-utilization 0.80` | `serve.sh` | The image and share used for Kolibri; it leaves room for the rest of the box (the CodeGraph agents outrank local models). |
| 4 | n-gram speculative decoding: `{"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4, "prompt_lookup_min": 2}` | `serve.sh` | About 30% slower per call. No draft model and no MTP head (Apriel has none). vLLM verifies every proposed token by rejection sampling, so outputs are distributed as without it. |
| 5 | `BCA_VLLM_SLOTS=4` | `eval/runs/apriel-20261113.sh` | One call at a time: a quarter of the throughput. On the memory-bandwidth-bound GB10 four calls at once cost each call almost nothing. |
| 6 | Memory guard: pause at MemAvailable <= 4 GB or swap >= 8 GB; a restart needs the user's OK | `mem-guard.sh` | The user's rule for local models (2026-11-14; before that MemFree <= 1 GB, which tripped on page cache at 00:25 on 11-14). |
| 7 | Audited server version (`AUDITED_VERSIONS` has 0.29.0) | adapter | Any other vLLM version fails closed until re-audited. |

## Image

`Dockerfile.vllm-0.29.0-aa1.0.0`, reconstructed from `docker history` of the image actually served
(`bca-vllm-kolibri:0.29.0-aa1.0.0`): the official `vllm/vllm-openai:v0.29.0` plus Aleph Alpha's
`aleph-alpha-inference` 1.0.0 plugin, installed for Kolibri-1. The plugin does not affect Apriel. The upstream image
digest was not recorded at pull time (the local image has no RepoDigest); its revision label is
`98dff2a81d747d1dba01a47f939f48c3526d4206`.

## Files

| File | What |
|---|---|
| `serve.sh` | Starts the `bca-vllm-apriel` container exactly as served (flags above) |
| `Dockerfile.vllm-0.29.0-aa1.0.0` | The served image, reconstructed |
| `download-apriel.sh` | Fetches the pinned checkpoint into the volume |
| `mem-guard.sh`, `mem-guard-20261113.log` | The memory guard and its one trip |
| `bench/throughput.py`, `bench/throughput-20261116.txt` | The throughput table above, from the run records |
| `PROVENANCE.json` | Pinned versions and where each fact comes from |
| `SANITIZE.md` | What is site-specific before any public release |

The chain script is `eval/runs/apriel-20261113.sh`. Results: `eval/runs/*apriel-budget-20261113`.
