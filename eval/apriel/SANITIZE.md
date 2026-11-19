# Before publishing this folder

No credentials, keys or endpoints are here: the server listens on 127.0.0.1 only and needs no key. What's still
site-specific (checked 2026-11-16):

| Item | Where | Suggested replacement |
|---|---|---|
| `/home/aharmon/BytecodeAgent` | `mem-guard.sh` (`cd`), `throughput.py` docstring (relative paths only) | the repo root, or `$REPO` |
| Container and volume names `bca-vllm-apriel`, `bca-hf-models`, image tag `bca-vllm-kolibri:0.29.0-aa1.0.0` | `serve.sh`, `download-apriel.sh`, Dockerfile, PROVENANCE.json | fine to keep (project names); a neutral tag if preferred |
| Local image id `sha256:79ae3f7e...` | PROVENANCE.json | keep (identifies the exact build), or drop |
| Notes naming the box's other workloads ("CodeGraph agents") | README | fine to keep, or "other workloads" |

Not present, and keep it that way: hostnames, Tailscale names, the operator's email, API keys.

Licences: the scripts are ours; the model is MIT (ServiceNow); vLLM is Apache-2.0. No weights are shipped here.
