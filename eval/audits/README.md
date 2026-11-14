# CLI audits

Evidence behind each CLI adapter's audit claims (what the vendor CLI actually sends to the model), kept in the repo
rather than a temporary directory: the 1.4.15 Muse and 3.1.2 Vibe captures lived in `/tmp` and were lost in the
2026-11-12 power-loss reboot. Their findings survive in the adapter docstrings
(`bca/liveeval/vendor_muse.py`, `vendor_vibe.py`); from here on every audit is captured under this directory.

- `recorder.py` — a local forwarder put in front of the vendor's API with the CLI's base-URL override
  (`muse --base-url`, Vibe's provider URL). It saves each request's method, path and JSON body, **never headers**, so
  no credentials are recorded.
- `agy_audit.py` — the same for Antigravity, which has no base-URL override: one call through the adapter with the
  version pin lifted, keeping agy's own stream and the model request it recorded in its conversation database.
- `<vendor>-<version>-<date>/raw/` — the full captures (request bodies, the CLI's own event log, stderr, the prompt).
  **Gitignored**: request bodies contain the vendor's own system prompt.
- `<vendor>-<version>-<date>/summary.json` — committed: request shape (fields, tool count, effort, output cap,
  instruction size, SHA-256 and section headings, whether our SYSTEM_PROMPT is the tail), message sizes and the event
  types seen. Written by `.venv/bin/python -m eval.audits.summarize eval/audits/<audit>`.

A probe's private config home (a copy of the CLI's sign-in) is never kept here: delete it after the probe.

| Audit | Adapter | Result |
|---|---|---|
| `muse-1.4.18-20261112` | `vendor_muse.py` | No `tools` field; effort `max`; 128,000 output cap; Muse's instructions 63,961 chars (12 sections), our SYSTEM_PROMPT their tail; developer message 28,088 chars. Same request shape and event types as 1.4.15. |
| `agy-1.5.7-20261112` | `vendor_agy.py` | Same as 1.5.3: events init / step_update / result, steps user_input / agent_response, same init and result fields (`permission_mode` request-review), 60 build tools advertised but the recorded request declares only `manage_task`; system instruction exactly ours; user turn matches the wrapper; field 19 `gemini-3.8-flash-n`. Identical with Agent Mode Auto (1.5.7's new default) and Default (pinned by the adapter). Written by `.venv/bin/python -m eval.audits.agy_audit`. |
