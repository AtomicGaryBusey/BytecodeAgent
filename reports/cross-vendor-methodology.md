# Cross-vendor live eval: how the subjects are made comparable

> **Account caveat (Claude results).** Every Claude run in this repository used a Claude Max subscription enrolled in Anthropic's **Cyber Verification Program** (relaxed cyber safeguards for verified security researchers; confirmed active 2026-09-28). Claude's safety classifiers are less restrictive on this account than on one without CVP, so refusal rates here are likely lower, and pass rates likely higher, than a non-CVP org or subscription would see on this machine-code work. Treat Claude numbers as an upper bound for non-CVP accounts. The ChatGPT account used for Codex is enrolled in OpenAI's equivalent **Daybreak** program at the **Blue** tier only (no Daybreak Red / `*-cyber` models); whether Daybreak relaxes anything for the non-Daybreak models evaluated here (e.g. `gpt-6-luna`) is unknown. The Kimi, Google and xAI accounts have no known equivalent enrolment.

Adapters: `bca/liveeval/vendor_base.py` (shared), `vendor_codex.py`, `vendor_kimi.py`, `vendor_agy.py`,
`vendor_grok.py`, registry `vendors.py`. A subject is `<vendor>:<model>` (`codex:gpt-6-luna`,
`kimi:kimi-code/k3`, `agy:gemini-3.8-flash-high`, `grok:grok-4.7`) or a Claude model (`opus`,
`claude-sonnet-5-5`, `claude-fable-5-1`, ...). Built and verified 2026-09-28 against claude 2.1.284,
codex 0.157.1, kimi 2.1.1, agy 1.2.12 and grok 1.0.41 (Grok Build; pinned binary, see below).

## Accounts and plans

Every subject ran on one consumer subscription per vendor, logged in through that vendor's own CLI. No API
keys, and no second account for any vendor.

| Vendor / CLI | Plan | Safety program | Limits seen in these runs |
|---|---|---|---|
| Anthropic / Claude Code | **Claude Max 20x** | Cyber Verification Program (relaxed cyber safeguards) | none hit |
| OpenAI / Codex CLI | **ChatGPT Plus** | Daybreak, Blue tier (no Red / `*-cyber` models) | none hit (per-model 5-hour allowances, e.g. GPT-6 Astra ~5-45 calls) |
| Moonshot / Kimi Code | **Kimi Allegretto** | none known | a 5-hour window (~125-160 calls) and a 7-day window on Code usage; monthly credits |
| Google / Antigravity | **Google AI Plus (2 TB)** | none known | ~75 calls per week, per account (not per model) |
| xAI / Grok Build | **SuperGrok** | none known | none hit |

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
| Version pin | an adapter runs only on the CLI version it was audited against | an upgraded CLI aborts the run until re-audited |

## Per vendor

| | Claude Code (`claude -p`) | Codex (`codex exec`) | Kimi Code (`kimi -p`) | Antigravity (`agy -p`) | Grok Build (`grok --single`) |
|---|---|---|---|---|---|
| tools off by | `--tools ""`, no MCP/settings/plugins | a stripped copy of the model's catalog entry (`model_catalog_json`) + config switches; feature flags alone left 9 tools in | agent file `tools: []`, `subagents: []` | custom agent excluding default components; `manage_task` cannot be removed (agy bug #1015), calling it aborts the run | custom agent (`promptMode: full`, no default tools), hosted web/X search disabled; a private `HOME` too, since grok otherwise reads `~/.claude` and `~/.cursor` config |
| request evidence | CLI debug log: `tools: []`; system/message contents not visible | request captured at a local recorder: `tools: []`, only SYSTEM_PROMPT + prompt, 48-58 input tokens for a one-line prompt (was ~4000); every call also guarded by an input-token bound | kimi's session record (`wire.jsonl`): tools hash of `[]`, system prompt hash = SYSTEM_PROMPT, message count checked | agy's conversation record (protobuf `gen_metadata`) | request captured at a local recorder (no `tools` key; server echoes `tools: []`), then grok's per-call session record: empty tool definitions, system prompt = SYSTEM_PROMPT, one model call, input tokens bounded |
| extra context the model sees | ~350 tokens added by Claude Code (content not visible to us) | none found | a date reminder (227 chars) and an "auto permission mode" note (508 chars) | `<System Prompt>` / `<USER_REQUEST>` wrappers, a local-time line, a "model setting changed" note | an empty first user message; ~1152 tokens of server-side context xAI adds itself (not visible); the prompt's final newline is trimmed |
| served model reported | yes (per message; fallbacks flagged) | no, except a cyber reroute (scored as refusal) | no (the id sent is recorded) | no (the request echoes the id) | yes, from the response (`grok-4.7` is served as `grok-4.7-build`); any other model is a fatal reroute |
| default effort (runs use CLI defaults, as the Claude runs did) | CLI default | catalog default (luna/sol: medium) | high | encoded in the model id (`-high`, `-low`) | high (served effort recorded; a mismatch is fatal) |
| refusal signal | `stop_reason: refusal`; fallback turned off (`switchModelsOnFlag: false`) | cyber/bio policy errors, content_filter, cyber reroute | `provider.filtered` ("Provider safety policy blocked the response.") | content-filter / safety finish reasons | HTTP 403 content-safety blocks (account 403s stay errors), `stopReason: refusal` |
| text delivery | streamed, killed on an early abort | one message | one message | streamed, but not killed on an early abort (its request record is written at the end of the turn) | streamed, not killed early (session record written at the end of the turn) |

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
- Plan limits differ per vendor: ChatGPT Plus makes `gpt-6-luna` the practical Codex model for full batteries.
- Grok Build sends the prompt to xAI a second time in a session-title side request (there is no switch to
  turn it off in 1.0.41); the adapter points that request at a non-existent model so it is rejected before any
  inference, and checks per call that it was. Hosted server-side tools are disabled and cannot be declared
  per call by the client, but their declaration is not recorded either; the input-token bound and the
  one-model-call check are what guard it.

## Plan quotas and interruptions

Each subject has its own infrastructure-error guard: a vendor whose plan quota is spent stops its own cells
only, and the others keep running; the same command resumes it later. Seen so far:

- **Antigravity (Google AI Plus):** the quota is per account, not per model, about 75 calls a week
  ("Individual quota reached ... Resets in 166h"). Antigravity is therefore run slowly, resuming after each
  weekly reset, and reported as partial until complete. The user's second Google account is not used, so
  the subject stays one account.
- **Antigravity** also reports a failed model request as a bare `error_message` step; it is a retried
  infrastructure error, not an isolation failure.
- **Grok:** xAI prompt caching covers shared prompt text in 256-token blocks beyond its 1152-token server
  prefix, so the adapter bounds total input tokens rather than the cached part.

## Changes to the harness that touch the committed Claude runs

No committed number changes (summaries and comparisons of all committed run dirs are byte-identical).
Two repair-prompt details changed for new runs: after an early abort the `<previous>` block ends at the
rejected line (it used to include up to ~18 characters of the next line), and after a timeout it is
empty (it used to show the partial text). About 28 of the committed repair prompts would be built
differently under the new rules; only attempts 2+ are affected.

Fable 5.1: Claude Code used to re-run a refused call on claude-opus-4-8 silently. The fallback is now
off, a refusal is scored, and on real task prompts Fable answers as itself.
