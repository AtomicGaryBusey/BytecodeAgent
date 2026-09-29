# Cross-vendor live eval: how the subjects are made comparable

Adapters: `bca/liveeval/vendor_base.py` (shared), `vendor_codex.py`, `vendor_kimi.py`, `vendor_agy.py`,
registry `vendors.py`. A subject is `<vendor>:<model>` (`codex:gpt-6-luna`, `kimi:kimi-code/k3`,
`agy:gemini-3.8-flash-high`) or a Claude model (`opus`, `claude-sonnet-5-5`, `claude-fable-5-1`, ...).
Built and verified 2026-09-28 against claude 2.1.284, codex 0.157.1, kimi 2.1.1, agy 1.2.12.

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

| | Claude Code (`claude -p`) | Codex (`codex exec`) | Kimi Code (`kimi -p`) | Antigravity (`agy -p`) |
|---|---|---|---|---|
| tools off by | `--tools ""`, no MCP/settings/plugins | a stripped copy of the model's catalog entry (`model_catalog_json`) + config switches; feature flags alone left 9 tools in | agent file `tools: []`, `subagents: []` | custom agent excluding default components; `manage_task` cannot be removed (agy bug #1015), calling it aborts the run |
| request evidence | CLI debug log: `tools: []`; system/message contents not visible | request captured at a local recorder: `tools: []`, only SYSTEM_PROMPT + prompt, 48-58 input tokens for a one-line prompt (was ~4000); every call also guarded by an input-token bound | kimi's session record (`wire.jsonl`): tools hash of `[]`, system prompt hash = SYSTEM_PROMPT, message count checked | agy's conversation record (protobuf `gen_metadata`) |
| extra context the model sees | ~350 tokens added by Claude Code (content not visible to us) | none found | a date reminder (227 chars) and an "auto permission mode" note (508 chars) | `<System Prompt>` / `<USER_REQUEST>` wrappers, a local-time line, a "model setting changed" note |
| served model reported | yes (per message; fallbacks flagged) | no, except a cyber reroute (scored as refusal) | no (the id sent is recorded) | no (the request echoes the id) |
| default effort (runs use CLI defaults, as the Claude runs did) | CLI default | catalog default (luna/sol: medium) | high | encoded in the model id (`-high`, `-low`) |
| refusal signal | `stop_reason: refusal`; fallback turned off (`switchModelsOnFlag: false`) | cyber/bio policy errors, content_filter, cyber reroute | `provider.filtered` ("Provider safety policy blocked the response.") | content-filter / safety finish reasons |
| text delivery | streamed, killed on an early abort | one message | one message | streamed, but not killed on an early abort (its request record is written at the end of the turn) |

## Known limits

- The vendors' extra context differs (table above); none of it is removable through the CLIs, all of it is
  pinned so any growth fails the call. Claude Code's own ~350 tokens could not be inspected.
- Request evidence for Kimi and Antigravity comes from each CLI's own per-call record, not a network
  capture (a local capture proxy was refused by this machine's permission policy).
- Latency figures are not comparable across vendors (whole-message CLIs report time to the full answer).
- Plan limits differ per vendor: ChatGPT Plus makes `gpt-6-luna` the practical Codex model for full batteries.

## Changes to the harness that touch the committed Claude runs

No committed number changes (summaries and comparisons of all committed run dirs are byte-identical).
Two repair-prompt details changed for new runs: after an early abort the `<previous>` block ends at the
rejected line (it used to include up to ~18 characters of the next line), and after a timeout it is
empty (it used to show the partial text). About 28 of the committed repair prompts would be built
differently under the new rules; only attempts 2+ are affected.

Fable 5.1: Claude Code used to re-run a refused call on claude-opus-4-8 silently. The fallback is now
off, a refusal is scored, and on real task prompts Fable answers as itself.
