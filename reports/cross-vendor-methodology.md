# Cross-vendor live eval: how the subjects are made comparable

> **Account caveat (Claude results).** Every Claude run in this repository used a Claude Max subscription enrolled in Anthropic's **Cyber Verification Program** (relaxed cyber safeguards for verified security researchers; confirmed active 2026-09-28). Claude's safety classifiers are less restrictive on this account than on one without CVP, so refusal rates here are likely lower, and pass rates likely higher, than a non-CVP org or subscription would see on this machine-code work. Treat Claude numbers as an upper bound for non-CVP accounts. The ChatGPT account used for Codex is enrolled in OpenAI's equivalent **Daybreak** program at the **Blue** tier only (no Daybreak Red / `*-cyber` models); whether Daybreak relaxes anything for the non-Daybreak models evaluated here (e.g. `gpt-6-luna`) is unknown. The Kimi, Google and xAI accounts have no known equivalent enrolment.

Adapters: `bca/liveeval/vendor_base.py` (shared), `vendor_codex.py`, `vendor_kimi.py`, `vendor_agy.py`,
`vendor_grok.py`, `vendor_copilot.py` (+ `copilot_driver.mjs`), the direct-API adapters `vendor_foundry.py` and
`vendor_fireworks.py`, the local ones `vendor_local.py` and `vendor_vllm.py`, registry `vendors.py`. A subject is
`<vendor>:<model>` (`codex:gpt-6-luna`, `kimi:kimi-code/k3`, `agy:gemini-3.8-flash-high`, `grok:grok-4.7`,
`copilot:mai-code-1.1-flash`) or a Claude model (`opus`, `claude-sonnet-5-5`, `claude-fable-5-1`, ...). Built and
verified 2026-09-28 against claude 2.1.284, codex 0.157.1, kimi 2.1.1, agy 1.2.12 and grok 1.0.41 (Grok Build;
pinned binary, see below); GitHub Copilot added 2026-09-30 (CLI 1.0.89 driven through the Copilot SDK 1.0.15); kimi 2.3.1 re-audited 2026-10-23 for Kimi K3.1 Pro (one new bookkeeping record in its request log, now checked; otherwise identical).
Codex was updated to 0.160.0 on 2026-10-01 and re-audited before use (same request recorder, gpt-6-luna and
gpt-6-astra: still zero tools, SYSTEM_PROMPT and the prompt only, 48 input tokens for a one-line prompt); the
committed Codex runs used 0.157.1, and each result records its CLI version. Later audits, each before first use:
Codex 0.167.0, Antigravity 1.5.3 and Copilot CLI 1.0.107 with SDK 1.0.29 (all 2026-11-07; the older Copilot pair
is still accepted). The audited versions live in each adapter's `audited_versions`.

## Accounts and plans

Every CLI subject ran on one consumer subscription per vendor, logged in through that vendor's own CLI, with no
API keys and no second account for any vendor. The exceptions are the direct-API routes: Azure AI Foundry
(`foundry:`) runs on the user's pay-as-you-go Azure subscription, signed in with `az`, and Fireworks.ai
(`fireworks:`, no runs yet) would use an API key; see [Direct API routes](#direct-api-routes-foundry-fireworks).

| Vendor / CLI | Plan | Safety program | Limits seen in these runs |
|---|---|---|---|
| Anthropic / Claude Code | **Claude Max 20x** | Cyber Verification Program (relaxed cyber safeguards) | none hit |
| OpenAI / Codex CLI | **ChatGPT Pro 100** since 2026-10-04 (**Plus** before; every committed Codex run used Plus) | Daybreak, Blue tier (no Red / `*-cyber` models) | the 5-hour window, once (Astra's app battery); weekly not reached |
| Moonshot / Kimi Code | **Kimi Allegretto** | none known | a 5-hour window (~125-160 calls), a 7-day window on Code usage (reached 100% on 2026-09-29, 10-04 and 10-11) and monthly credits; the 7-day window is the binding one (see below) |
| Google / Antigravity | **Google AI Pro** since 2026-11-06 (**AI Plus 2 TB** before; every reported Antigravity run used Plus) | none known | on Plus, ~75 calls per week, per account (not per model); on Pro, a Gemini 4 Argon High run stopped on quota at 181 calls |
| Microsoft / Azure AI Foundry API | **pay-as-you-go** (Global Standard, serverless) | Azure's default content filter (`Microsoft.MAIDefault`, `Microsoft.DefaultV2`) | a per-model spend cap in the harness ($25; raised to $28 for MAI-Thinking-1); token-rate 429s at 2 parallel jobs for DeepSeek |
| xAI / Grok Build | **SuperGrok** | none known | weekly included usage ~70% at most (app calls cost ~0.4% each); not exceeded, no paid credits used |
| GitHub / Copilot CLI | **GitHub Copilot Pro** (1,500 AI credits a month, billed per token) | none known | the September credits, spent 2026-09-30 (overage off; resumed after the monthly reset) |

Plan tier matters: it decides which models are available (e.g. Kimi's high-speed model needs a higher tier)
and how quickly a full battery can run, and the safety programs change refusal behaviour. Results describe
these accounts, not every account on each service.

## The contract every subject meets

| | how | verified by |
|---|---|---|
| Same prompts | the harness system prompt and task prompt, byte for byte | each CLI's own record of the request (below) |
| No tools | every tool the CLI can offer is switched off | the request's tool list, checked on **every call**; a call that shows a tool, a tool call, an unknown/malformed event or an unverifiable request is an `isolation:` error that aborts the run (never scored) |
| No state | a private temporary home per call holding only copied credentials; empty temp workdir; allowlisted environment | nothing written to the real config homes except a refreshed token (validated, one refresh at a time) |
| Same scoring | identical pipelines, repair rounds, attempt limits and per-battery timeouts | replay of all 1852 recorded answers streamed vs whole: 0 verdict differences |
| Refusals | a safety block by any vendor is a scored `refusal` attempt; no vendor-internal retry or fallback is credited | per-vendor refusal signals (below) |
| Model identity | the model that served the call when the CLI reports it; otherwise shown as "(requested; not reported)" | summaries label it |
| Version pin | a CLI adapter runs only on the CLI version it was audited against (Claude Code excepted, below); an API adapter checks the deployment's model, version and SKU | an upgraded CLI aborts the run until re-audited |

## Per vendor

| | Claude Code (`claude -p`) | Codex (`codex exec`) | Kimi Code (`kimi -p`) | Antigravity (`agy -p`) | Grok Build (`grok --single`) | GitHub Copilot (SDK → `copilot --headless`) |
|---|---|---|---|---|---|---|
| tools off by | `--tools ""`, no MCP/settings/plugins | a stripped copy of the model's catalog entry (`model_catalog_json`) + config switches; feature flags alone left 9 tools in | agent file `tools: []`, `subagents: []` | custom agent excluding default components; `manage_task` cannot be removed (agy bug #1015), calling it aborts the run | custom agent (`promptMode: full`, no default tools), hosted web/X search disabled; a private `HOME` too, since grok otherwise reads `~/.claude` and `~/.cursor` config | a Copilot SDK session: system message replaced, `availableTools: []`, every tool source excluded, built-in GitHub MCP server disabled, every permission rejected. `copilot -p` could not do it: it always sends Copilot's own 17-28k-char system prompt, `--available-tools=` restricted nothing (18 tools sent), a custom agent's `tools: []` left `skill` and `sql` |
| request evidence | CLI debug log: `tools: []`; system/message contents not visible | request captured at a local recorder: `tools: []`, only SYSTEM_PROMPT + prompt, 48-58 input tokens for a one-line prompt (was ~4000); every call also guarded by an input-token bound | kimi's session record (`wire.jsonl`): tools hash of `[]`, system prompt hash = SYSTEM_PROMPT, message count checked | agy's conversation record (protobuf `gen_metadata`) | request captured at a local recorder (no `tools` key; server echoes `tools: []`), then grok's per-call session record: empty tool definitions, system prompt = SYSTEM_PROMPT, one model call, input tokens bounded | the runtime's own debug log of the wire request, per call: system prompt = SYSTEM_PROMPT, the prompt verbatim, no `tools` key, empty tool fingerprint, `tool_count` 0; plus the stream's request capture (0 tools, one system segment `agent_replacement`); one model call; input tokens bounded |
| extra context the model sees | ~350 tokens added by Claude Code (content not visible to us) | none found | a date reminder (227 chars) and an "auto permission mode" note (508 chars) | `<System Prompt>` / `<USER_REQUEST>` wrappers, a local-time line, a "model setting changed" note | an empty first user message; ~1152 tokens of server-side context xAI adds itself (not visible); the prompt's final newline is trimmed | none in the request (the runtime's `<current_datetime>` prompt prefix is removed by a hook); the Anthropic path trims the prompt's final newline; Grok models add ~1500 tokens of server-side context (not visible) |
| served model reported | yes (per message; fallbacks flagged) | no, except a cyber reroute (scored as refusal) | no (the id sent is recorded) | no (the request echoes the id) | yes, from the response (`grok-4.7` is served as `grok-4.7-build`); any other model is a fatal reroute | yes, per call (model.call_start / usage / message); a model switch (rate-limit auto switch, "refusal fallback") is a fatal reroute |
| default effort (runs use CLI defaults, as the Claude runs did) | CLI default | pinned per model since 2026-10-04: astra and daybreak-blue low; luna, sol and gpt-5.5 medium (the defaults recorded for their committed runs; see below) | per model, from the CLI's catalog (`high` for K3 and K2.8; `max` for K3.1 Pro when it ran, since changed to `high`) | encoded in the model id (`-high`, `-low`) | high (served effort recorded; a mismatch is fatal) | per model (medium seen for GPT, MAI and Claude; high for Kimi K3 and Grok 4.7); served effort recorded, a requested effort must be served (fatal otherwise) |
| refusal signal | `stop_reason: refusal`; fallback turned off (`switchModelsOnFlag: false`) | cyber/bio policy errors, content_filter, cyber reroute | `provider.filtered` ("Provider safety policy blocked the response.") | content-filter / safety finish reasons | HTTP 403 content-safety blocks (account 403s stay errors), `stopReason: refusal` | `finishReason: content_filter` (Anthropic `refusal` is normalised to it), `contentFilterTriggered`, an OpenAI-style `refusal` field |
| text delivery | streamed, killed on an early abort | one message | one message | streamed, but not killed on an early abort (its request record is written at the end of the turn) | streamed, not killed early (session record written at the end of the turn) | one message, handed over after the call is verified |

**Codex effort is pinned (2026-10-04).** Without `--effort`, the Codex CLI uses the effort in its *live*
model catalog, and that default follows the ChatGPT plan: for `gpt-6-astra` it is medium on Pro 100. The
adapter recorded the default from the CLI's offline *bundled* catalog, because each run describes its subject
before the first call fetches the live one. So the committed records ("cli-default (low)" for Astra and
Daybreak Blue, "cli-default (medium)" for Luna, Sol and GPT-5.5) show the bundled default, not a
server-confirmed setting. Those runs were all made on ChatGPT Plus. A calibration on 2026-10-04 re-sent four
historical first-attempt Astra prompts at a pinned low and a pinned medium. The original runs' reasoning tokens
were nearer low in three cells and tied in the fourth:

| Cell | Original | Pinned low | Pinned medium |
|---|---|---|---|
| base64.hex.aarch64 | 1,006 | 1,034 | 1,226 |
| wc.hex.x86_64 | 655 | 744 | 859 |
| rpn.stream.x86_64 | 136 | 117 | 116 |
| sort_lines.asm.aarch64 | 153 | 186 | 257 |

That is consistent with low, but medium costs Astra only 15-40% more reasoning here, so it is evidence, not
proof. From 2026-10-04 every Codex call passes its effort explicitly. Known models default to the pins above
(`CODEX_PINNED_EFFORT`), any other model must be given `--effort`, and each call records the effort it ran at
("low (pinned)" or "... (requested)").

## Direct API routes (`foundry:`, `fireworks:`)

`foundry:<deployment>` (`vendor_foundry.py`) calls an Azure AI Foundry deployment's chat-completions API over
HTTPS, streaming; `fireworks:<model id>` (`vendor_fireworks.py`) is the same adapter pointed at Fireworks.ai's
serverless API. There is no CLI and no local home, so the contract is met differently:

- **Same prompts, no tools:** the request body is built by the harness itself: `messages` = the harness system
  prompt and the task prompt, byte for byte; `stream: true` with usage; no `tools` key, no sampling fields and
  no `max_tokens`. `--effort` adds `reasoning_effort`; without it nothing is sent (recorded "model-default").
  DeepSeek-V4-Pro ran at `high`, because the API default returned no reasoning (a 12-cell smoke test: 4/12 at the
  default, 10/12 at high).
- **Model identity:** every streamed chunk that names a model must name the deployed model; anything else is a
  fatal reroute. A tool call in the stream, or `finish_reason: length`, fails closed (`isolation:`).
- **Refusals:** a content-filter block (an HTTP 400 `content_filter`, a `SafetyBlockedError` event or
  `finish_reason: content_filter`) is a scored refusal.
- **Deployment checks (Foundry):** the deployment's model, version, SKU (Global Standard only) and content-filter
  policy are read with `az` and recorded with each result; there is no Azure resource name in the run data.
- **Cost:** each call is priced from the public Azure (or Fireworks) price list and charged to a per-model ledger
  (`~/.local/state/bca/foundry-spend.json`). A call is refused before it is sent if its worst case would cross
  the cap ($25 a model by default, `BCA_FOUNDRY_CAP_USD`), and that stop is fatal. Rejected requests (HTTP 429)
  cost nothing.
- **State:** one independent HTTPS request per call; nothing is carried between calls.

## Local models (`local:<model>`)

Open-weight models run on this machine, an NVIDIA DGX Spark (GB10, 128 GB unified memory), through Ollama
0.30.10 in its own container (`bca-ollama`, bound to 127.0.0.1:11435; `OLLAMA_NO_CLOUD=1`, so no request
can be forwarded to ollama.com; `OLLAMA_NUM_PARALLEL=1`). The weights come from an existing Ollama model
volume mounted read-only. No plan quota applies.

| | Local (`vendor_local`, Ollama `/api/chat`) |
|---|---|
| tools off by | construction: the harness sends the request itself; no `tools`, no `format` |
| request | `[system: SYSTEM_PROMPT, user: prompt]`, `stream: true`, options = `{num_ctx}` only |
| context | set explicitly to min(native, 65536): Ollama's default would truncate silently. A call whose prompt plus answer reached the window fails closed |
| sampling | each model's own Modelfile defaults, recorded per run (the analogue of a CLI's default settings); no effort knob |
| served model | the stream's model name, and after each call `/api/ps` must show the requested model under the digest recorded at setup with our context length; anything else is fatal |
| reasoning | a `thinking` field or a leading `<think>` block is recorded in notes, never scored |
| thinking on/off | per subject, by spec suffix: `local:<model>@think` / `@nothink` sends Ollama's `think: true / false` (only for a model whose `/api/show` capabilities list `thinking`, else fatal); no suffix sends nothing and the model's template default applies (recorded as `model-default`). The two variants are separate subjects with their own directories and table rows. With `@nothink`, any reasoning returned fails the call closed (the server did not honour the request); with `@think`, a call without reasoning is noted. Reasoning tokens count against the context window (Ollama's `eval_count` includes them) |
| server | `BCA_OLLAMA_URL` picks the server for a run: `bca-ollama` at 127.0.0.1:11435 (the read-only ZebethRadio volume) or `bca-ollama2` at 127.0.0.1:11436 (a writable volume for models downloaded for this eval, same image and settings) |
| timing | calls are serialised per server (file lock); the per-call clock starts once the lock is held and the model is resident (weights are warmed up outside the timed call). Run with `--jobs 1`: a cell's recorded wall time otherwise includes waiting for the lock (timeouts are unaffected) |
| version | the server version must be an audited one (0.30.10) |

Models: `qwen3:30b-a3b-instruct-2507-q8_0` (Qwen3 30B-A3B Instruct 2507, mixture of experts, 3B active,
Q8_0, context 65536 of a native 262144; Modelfile sampling temperature 0.7, top_p 0.8, top_k 20) and
`qwen2.5:14b` (Q4_K_M, native context 32768; Ollama's default sampling). Added on `bca-ollama2`:
`qwen3-coder-next:q4_K_M` (80B total / 3B active; it never thinks, so no suffix) and `qwen3.6:35b-a3b-q8_0`
(35B / 3B active; thinks by default, run both `@nothink` and `@think`). These are quantised open-weight
models on a single desktop-class machine: their results say what such a setup can do, not what the same
models do at full precision on a provider's servers. `qwen3-coder-next:q4_K_M` was removed from the volume
on 2026-10-04 to make room for Kolibri-1; its results stay committed, and a re-run would download it again.
`qwen3.6:35b-a3b-q8_0` (Ollama ID `0218f872e86b`) was archived to network storage on 2026-10-10 (its runs
are complete); restoring its `manifests/` and `blobs/` into the volume brings back the exact weights tested.

**vLLM-served models (`vllm:<model>`, `vendor_vllm`).** For models Ollama cannot run, the same contract holds
through vLLM's OpenAI-compatible server. The request is `POST /v1/chat/completions`, streamed, with
`[system: SYSTEM_PROMPT, user: prompt]` and no `tools`, `tool_choice`, `response_format` or sampling fields,
so sampling is the checkpoint's `generation_config.json`. `--effort` maps to
`chat_template_kwargs.reasoning_effort`. The served model must be listed by `/v1/models` and named by every
stream chunk, the server version must be an audited one (0.29.0), and a call ending with
`finish_reason: length` reached the context window and fails closed. Reasoning arrives in separate deltas (or a
`<think>` block) and is recorded, never scored. A tool call fails closed. Calls are serialised per server as
for Ollama. First model: Aleph Alpha's Kolibri-1 (78.1B total / 3.46B active, FP8, Apache 2.0). It runs in
`bca-vllm-kolibri`: the official vLLM 0.29.0 arm64 image plus Aleph Alpha's `aleph-alpha-inference` 1.0.0
plugin (their own image is x86-only). The server binds 127.0.0.1:8000, the weights come from the
`bca-hf-models` volume, `HF_HUB_OFFLINE=1` is set, and a reasoning parser is on with no tool parser.

**Token-budget mode (`vllm:<model>@budget`, `bca.liveeval.token_budget`), decided 2026-10-05.** Kolibri-1
generates about 30 tokens/s on the GB10. At its default (high) reasoning effort it spent about 3,000 tokens
on a trivial function. Under the wall-clock limits, 13 of the first 15 round-1 attempts timed out at 180 s
(run kept as `kolibri-untuned-trial-r1-*`, not reported). The vendor models are served at 100-300
tokens/s, so those limits never constrained how long they reasoned: in round 1 one used 27,661 output
tokens in a single call. A local subject in budget mode is therefore limited by output tokens, reasoning
included, not by time. Each battery's budget is the largest output any vendor model produced for one call
in it (measured over all committed vendor attempts), rounded up:

| Battery | Most any vendor model used | Budget |
|---|---|---|
| round1 | 27,661 | 32,768 |
| round2 | 39,297 | 49,152 |
| long | 44,729 | 49,152 |
| adversarial | 22,159 | 24,576 |
| apps | 76,794 | 81,920 |

The budget is sent as `max_tokens`. Reaching it is a timeout, recorded and scored as one, with the same
error text, so repair prompts are unchanged; the call's notes record the budget. A wall-clock ceiling
(budget / 8 tokens/s + 300 s) still guards against a hung server. The server runs with
`--max-model-len 131072`, so prompt plus budget always fits (Kolibri supports 1M); a call that stops at
the context window short of its budget still fails closed. The model keeps its default sampling and
effort. Budget-mode results are a separate subject and table row: they measure what the model does given
the vendor models' thinking room, not at their speed.

**Kolibri-1's serving setup.** Tried on 2026-10-04/05 to fix the speed:
- **Expert-layer backends** via `--moe-backend`: `triton` 31.9 / 27.8 tok/s, `marlin` 29.9 / 27.3; `b12x`,
  `flashinfer_cutlass` and `cutlass` fail to start on this model and GPU.
- **Tuned Triton config** for its expert-layer shape (`E=384,N=512`, FP8 128x128 blocks, which ships for no
  GPU): batch size 1 tuned on the GB10 by vLLM's `benchmark_moe.py` (tuning batch size 2048 here was
  stopped, its slowest configurations ran ~49 s each); the 2048 entry borrowed from vLLM's shipped
  `E=256,N=512` B200 config, with `num_stages` 4 -> 3 for the GB10's smaller shared memory. Result:
  35.3 / 28.2 tok/s.

- **A GB10-native vLLM build** (2026-10-06): `eugr/spark-vllm:nightly-20261002`
  (`sha256:d8686cc0da9de31df2df477cebbb3f7f473ac771c8be1c0774f94e477d35220b`, vLLM
  `0.30.1rc1.dev565+g8e58ac22a`, sm_121), reported on NVIDIA's forum at ~48 tok/s for other MoE models,
  plus the same plugin (`--no-deps`, as it pins vLLM < 0.30), same flags and the same tuned config (the image
  ships GB10 configs for E=256 and E=512, not 384). Same API (version, `max_model_len`, `reasoning`
  deltas, `finish_reason`), but 35.2 / 28.2 / 28.2 tok/s over 1,024 tokens and 28.1 / 28.1 over 4,096,
  against 30.4 / 28.3 on the current stack measured just before. No gain, so it was not adopted and is not
  in `AUDITED_VERSIONS`.

The kernels are not the bottleneck; the tuned config is kept, and it changes kernel speed only, never
outputs. **Stack per battery:** every Kolibri battery runs on the official vLLM 0.29.0 image + plugin
1.0.0 + tuned config.

## Known limits

- The vendors' extra context differs (table above); none of it is removable through the CLIs, all of it is
  pinned so any growth fails the call. Claude Code's own ~350 tokens could not be inspected.
- Request evidence for Kimi and Antigravity comes from each CLI's own per-call record, not a network
  capture (a local capture proxy was refused by this machine's permission policy).
- Latency figures are not comparable across vendors (whole-message CLIs report time to the full answer).
- Safety-program enrolment differs per account: Claude is in Anthropic's Cyber Verification Program (account-wide
  relaxed cyber safeguards); ChatGPT is in OpenAI's Daybreak program, Blue tier only (no Red / `*-cyber` models;
  the catalog lists `gpt-daybreak-blue-latest`, and whether enrolment affects `gpt-6-luna` is unknown); Kimi and
  Google have no known equivalent. Refusal rates are therefore not directly comparable.
- Plan limits differ per vendor: on ChatGPT Plus `gpt-6-luna` was the practical Codex model for full batteries
  (the account has been on Pro 100 since 2026-10-04).
- Grok Build sends the prompt to xAI a second time in a session-title side request (there is no switch to
  turn it off in 1.0.41); the adapter points that request at a non-existent model so it is rejected before any
  inference, and checks per call that it was. Hosted server-side tools are disabled and cannot be declared
  per call by the client, but their declaration is not recorded either; the input-token bound and the
  one-model-call check are what guard it.

- GitHub Copilot's headless runtime does not use the Copilot CLI's own keyring login; it authenticates through
  the GitHub CLI (`gh auth token`, the user's gh login), so the runtime holds a token with that account's scopes.
  The model has no tools to use it; the harness never reads it. Most of Copilot's selectable models are other
  vendors' models (Claude, GPT, Gemini-class, Grok, Kimi) served through GitHub: running one there is a harness
  comparison against the same model on its own vendor's CLI, not a new model. MAI-Code (`mai-code-1.1-flash`,
  Microsoft) is the one model only Copilot serves. On Copilot Pro, Opus, Fable, GPT-6 Astra / Sol and GPT-5.5
  are not selectable (Pro+ and up).
- Copilot's runtime does things the other CLIs do not, each handled so that one attempt stays one model call
  with nothing injected:
  - With the Responses API (GPT models) the model's encrypted reasoning arrives as a separate, empty
    `assistant.message` beside the answer, in the same call; it is recognised and neither scored nor counted.
    The reasoning itself is authenticated ciphertext under a provider-held key and cannot be read (examined
    locally only; replaying it to the model to recover the reasoning was deliberately not attempted).
  - After a broken stream or a failed model call (`model.turn_retry`, `model.call_failure`,
    `model.model_call_failure`) the runtime would call the model again; the attempt is voided at once as a
    transient error and the runner retries the cell.
  - At its 32,000-token output cap the runtime would nudge the model into a continuation
    (`model.telemetry` `model_turn_continuation`); the call is stopped there and the text written within the
    cap is scored as it stands, as `claude -p` scores a max-token answer: no continuation and no do-over. Any
    other telemetry event still fails closed.
- The front end matters as much as the model: with byte-identical prompts, no tools and the same served
  effort, GPT-6 Luna scores 58% through Codex and 79% through Copilot (Copilot's route reasoned 2.7x more), and
  Claude Sonnet 5.5 90% through `claude -p` but 76% through Copilot (more prose plans before the code and stray
  tool-call markup). Both are each CLI's own defaults; an effort label does not carry across front ends.
- Local models share one GPU and their calls are serialised; a cell's time limit counts only the model's own
  time, but the recorded wall time includes the wait for the server, so local latencies are not comparable.

## Plan quotas and interruptions

Each subject has its own infrastructure-error guard: a vendor whose plan quota is spent stops its own cells
only, and the others keep running; the same command resumes it later. Seen so far:

- **Antigravity (Google AI Plus):** the quota is per account, not per model, about 75 calls a week
  ("Individual quota reached ... Resets in 166h"). Antigravity was therefore run slowly, resuming after each
  weekly reset, and is reported as partial until complete (Gemini 3.8 Flash High stopped at 223/224; its loop
  was stopped on 2026-10-24). On AI Pro (from 2026-11-06) the limit is higher but still binding: the Gemini 4
  Argon High run stopped on quota after 181 calls and resumes on an 8-hourly retry. The user's second Google account is not used, so
  the subject stays one account.
- **Antigravity** also reports a failed model request as a bare `error_message` step; it is a retried
  infrastructure error, not an isolation failure.
- **Grok CLI stream bug:** grok 1.0.41's `streaming-json` occasionally repeats a text chunk (apparently when
  it restarts generation after the first chunk was sent). Three answers were scored on corrupted text; the adapter
  now delivers grok's session-record answer once at the end (`CliSubject.deliver_at_end`), and the affected run
  documents the correction in `ADJUSTMENTS.md`.
- **Antigravity's quota came back early:** its error said "Resets in 166h", but a probe the next afternoon
  answered, matching Google's usage page (weekly usage 0%). Probing beats the error's reset estimate.
- **Claude Code is not version-pinned** like the other CLIs; it auto-updates, and the committed Claude runs span
  2.1.283 to 2.1.319. Each result records its CLI version; the no-fallback setting was re-verified on 2.1.285.
- **Grok:** xAI prompt caching covers shared prompt text in 256-token blocks beyond its 1152-token server
  prefix, so the adapter bounds total input tokens rather than the cached part.
- **ChatGPT Plus 5-hour window:** the app battery's whole-program calls exhausted it after 12 Astra cells; the
  run waited for the window (the CLI's own "try again at" time) and finished in one go afterwards.
- **GitHub Copilot Pro credits** (1,500 token-based AI credits a month, overage off): spent on 2026-09-30 at
  203/224 cells of Sonnet 5.5 via Copilot (HTTP 402 on every call; never billed); the run finished after the
  monthly reset. Anthropic models cost about twice what GPT-6 Luna did per call through Copilot.
- **Kimi Allegretto 7-day window:** reached 100% on 2026-09-29 (all of it this eval); the last Kimi K2.8 cell
  and Kimi K3's app battery waited for it to reopen (2026-10-03 15:02 PDT; both have since completed). It reached 100% again on 2026-10-04
  and 2026-10-11; each time the 5-hour window had blocked calls many times first.
- **Kimi Allegretto's monthly credits cannot all be used.** The plan has three meters on Code usage: a 5-hour
  window, a 7-day window and the monthly credits. On 2026-10-11 (Kimi's subscription page, 13:56 PDT), after
  the 7-day window had been used to 100% three times in the billing month that renews 2026-10-18, the monthly
  meter read 62.57%: one full week is worth roughly 21% of the month. A 30-day month is about 4.3 weeks, so
  even with every week used to its limit the monthly meter tops out near 89%, and this month, whose 7-day window
  reopens only on 2026-10-17 15:02, a day before the renewal, will end lower. This is inferred from the meters
  (it assumes every week weighs the same; Kimi does not publish the conversion); in practice the 7-day window
  is the plan's capacity, and a Kimi battery takes calendar weeks. The account has Kimi's "Extra Usage"
  enabled (limit $50 a cycle) with a $0 balance, so nothing beyond the plan was or could be billed.
- **Antigravity and the call cap:** quota refusals count as calls, and the run directory's `--max-calls` cap is
  shared by every subject in it; days of refused retries used it up. The resume loops ran under a higher cap (hourly for
  Gemini 3.8 until stopped on 2026-10-24; 8-hourly for Gemini 4 Argon). A refused call costs nothing.

## Changes to the harness that touch the committed Claude runs

No committed number changes (summaries and comparisons of all committed run dirs are byte-identical).
Two repair-prompt details changed for new runs: after an early abort the `<previous>` block ends at the
rejected line (it used to include up to ~18 characters of the next line), and after a timeout it is
empty (it used to show the partial text). About 28 of the committed repair prompts would be built
differently under the new rules; only attempts 2+ are affected.

Assembler and encoder fixes made during the cross-vendor and app runs, each so a path accepts exactly what
GNU as (and the prompt) accept; every recorded answer was replayed after each one:

| Change | What it fixed | Effect on committed results |
|---|---|---|
| 95dcdb0 | stream: labels named like AArch64 keywords or registers (`lt`, `x0`, ...) that GNU as accepts | one app cell re-run (Sonnet 5.5) |
| c7df64d | asm path (apps): the prompt's placement and constant rules, as the stream path enforces them | one app cell re-run (Opus 5.5) |
| c1c1df7f | stream: keystone refuses a line GNU as accepts (e.g. `mov x17, x20, lsl #3`) -> GNU as's bytes | GPT-6 Luna via Copilot +1 (ADJ); 3 Qwen2.5 cells cut off, not rescorable (documented) |
| 79a275c1 | stream: `.end`, `.arch`/`.cpu` within the prompt's ISA level (both paths), GNU's one-quote `'c` | two MAI-Code app cells re-run |
| d7df9dda | structured x86-64: a label named like a register is refused where it is defined (the prompt forbids it; GNU as rejects the jump) | none (the affected attempts had failed genuinely) |

Fable 5.1: Claude Code used to re-run a refused call on claude-opus-4-8 silently. The fallback is now
off, a refusal is scored, and on real task prompts Fable answers as itself.
