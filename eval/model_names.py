"""Display names added on 2026-11-12 and the runs still in progress, shared by xv_tables, app_tables, hex_tables and
analytics (each keeps its own older names). A run directory listed in IN_PROGRESS is skipped by every table until
its battery is complete; drop it from the list then and regenerate."""

NEW_NAMES = {
    "codex:gpt-6.1-sol": "Codex GPT-6.1 Sol", "codex:gpt-6.1-terra": "Codex GPT-6.1 Terra",
    "codex:gpt-5.6-sol": "Codex GPT-5.6 Sol", "codex:gpt-5.6-terra": "Codex GPT-5.6 Terra",
    "codex:gpt-5.6-luna": "Codex GPT-5.6 Luna",
    "cerebras:gpt-oss-120b": "OpenAI gpt-oss-120b (Cerebras)", "fireworks:gpt-oss-120b": "OpenAI gpt-oss-120b (Fireworks)",
    "cerebras:qwen-3.8-27b": "Qwen 3.8 27B (Cerebras)",
    "kimi:kimi-code/k3.1-pro-highspeed": "Kimi K3.1 Pro Highspeed",
    "kimi:kimi-code/kimi-for-coding-highspeed": "Kimi K2.7 Code Highspeed",
    "claude-opus-5": "Claude Opus 5", "claude-fable-5": "Claude Fable 5", "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6", "claude-opus-4-5-20251101": "Claude Opus 4.5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "agy:gemini-4-argon-high": "Gemini 4 Argon High (Antigravity)",
    "agy:gemini-4-hydrogen-high": "Gemini 4 Hydrogen High (Antigravity)",
    "copilot:kimi-k3": "Kimi K3 via Copilot",
    "vibe:mistral-large-4-0-3": "Mistral Large 4 (Vibe harness)", "vibe:mistral-medium-3.5": "Mistral Medium 3.5 (Vibe harness)",
    "mistral:mistral-large-4": "Mistral Large 4",
    "muse:muse-spark-1.3-contributor": "Meta Muse Spark 1.3 (Muse Code harness)",
    "muse:muse-wonder-full-1.0-contributor": "Meta Muse Wonder Full 1.0 (Muse Code harness)",
    "muse:muse-wonder-1.0-contributor": "Meta Muse Wonder 1.0 (Muse Code harness)",
    "muse:muse-ember-1.0-contributor": "Meta Muse Ember 1.0 (Muse Code harness)",
}

# Models that run on the user's own hardware through a vendor's route: local models in every table and in the
# dashboard. Apple's on-device model runs on the user's Mac (`apple:system`); Private Cloud Compute (`apple:pcc`)
# is Apple's cloud and stays a vendor model.
ON_DEVICE = {"apple:system"}


def is_local(label: str) -> bool:
    return label.startswith("local:") or label in ON_DEVICE


# Run directories (prefixes under eval/runs/) whose battery is not complete yet: unreported until it is.
IN_PROGRESS = (
    # Mistral Large 4 through Mistral's API (tool-free), started 2026-11-15
    "mistral-large-4-20261115", "hex-mistral-large-4-20261115", "apps-mistral-large-4-20261115",
    "apps-hex-mistral-large-4-20261115",
    "claude-sonnet-4-6-20261111", "hex-claude-sonnet-4-6-20261111", "apps-claude-sonnet-4-6-20261111",
    "apps-hex-claude-sonnet-4-6-20261111",
    "claude-opus-4-6-20261111", "hex-claude-opus-4-6-20261111", "apps-claude-opus-4-6-20261111",
    "apps-hex-claude-opus-4-6-20261111",
    "claude-opus-4-5-20251101-20261111", "hex-claude-opus-4-5-20251101-20261111",
    "apps-claude-opus-4-5-20251101-20261111", "apps-hex-claude-opus-4-5-20251101-20261111",
    "kimi-kimi-for-coding-highspeed-20261111", "hex-kimi-kimi-for-coding-highspeed-20261111",
    "apps-kimi-kimi-for-coding-highspeed-20261111", "apps-hex-kimi-kimi-for-coding-highspeed-20261111",
    "copilot-kimi-k3-20261112", "hex-copilot-kimi-k3-20261112", "apps-copilot-kimi-k3-20261112",  # started 11-12
    "apps-hex-copilot-kimi-k3-20261112",
    # Apriel-1.6-15B-Thinker local baseline, started 11-13
    "apriel-budget-20261113", "hex-apriel-budget-20261113", "apps-apriel-budget-20261113",
    "apps-hex-apriel-budget-20261113",
    # Muse round 2: Ember waits for the weekly quota (Wonder and Wonder Full are complete and reported)
    "muse-ember-1.0-20261113", "hex-muse-ember-1.0-20261113", "apps-muse-ember-1.0-20261113",
    "apps-hex-muse-ember-1.0-20261113",
    # the paid-tier half of the Muse tier comparison (complete 11-16): a study pair in effort_tables.py, never a row
    "muse-tier-paid-spark-1.3-20261113", "apps-muse-tier-paid-spark-1.3-20261113",
    # Grok 4.7 misleading_hint / terse on the adversarial battery (user 11-16): a variant study, never a row
    "grok-4.7-adv-variants-20261116",
    # second long-battery samples (run-to-run variation; reported apart once written up, never a model's row)
    "long-s2-grok-4.7-build-fast-20261113", "long-s2-grok-4.7-20261113", "long-s2-grok-4.6-20261113",
)


def in_progress(path: str) -> bool:
    """True for a file under an in-progress run directory (path as globbed: eval/runs/<dir>/...)."""
    parts = path.split("/")
    return len(parts) > 2 and parts[2] in IN_PROGRESS

# Run directories that are incomplete on purpose and reported as they stand (eval/regenerate.py's guard skips them).
KNOWN_PARTIAL = {
    "apple-pcc-medium-20261110": "Apple PCC, shelved by the user 2026-11-10; excluded from every table",
    "apple-system-20261108-021847": "Apple on-device, round 1 reported at 32/48; shelved 2026-11-10",
    "kolibri-untuned-trial-r1-20261004-221032": "the untuned Kolibri wall-clock trial; not reported",
    "local-20260929-235149": "local Ollama models: several subjects were stopped as too slow; reported per subject",
    "local-r3-20260930-105307": "as above",
}
