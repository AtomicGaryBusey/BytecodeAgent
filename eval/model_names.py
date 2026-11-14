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
    "copilot:kimi-k3": "Kimi K3 via Copilot",
    "vibe:mistral-large-4-0-3": "Mistral Large 4 (Vibe harness)", "vibe:mistral-medium-3.5": "Mistral Medium 3.5 (Vibe harness)",
    "muse:muse-spark-1.3-contributor": "Meta Muse Spark 1.3 (Muse Code harness)",
}

# Models that run on the user's own hardware through a vendor's route: local models in every table and in the
# dashboard. Apple's on-device model runs on the user's Mac (`apple:system`); Private Cloud Compute (`apple:pcc`)
# is Apple's cloud and stays a vendor model.
ON_DEVICE = {"apple:system"}


def is_local(label: str) -> bool:
    return label.startswith("local:") or label in ON_DEVICE


# Run directories (prefixes under eval/runs/) whose battery is not complete yet: unreported until it is.
IN_PROGRESS = (
    "agy-argon-20261107-142027",  # Gemini 4 Argon High: 122/224, paused on quota
    "claude-sonnet-4-6-20261111", "hex-claude-sonnet-4-6-20261111", "apps-claude-sonnet-4-6-20261111",
    "apps-hex-claude-sonnet-4-6-20261111",
    "claude-opus-4-6-20261111", "hex-claude-opus-4-6-20261111", "apps-claude-opus-4-6-20261111",
    "apps-hex-claude-opus-4-6-20261111",
    "claude-opus-4-5-20251101-20261111", "hex-claude-opus-4-5-20251101-20261111",
    "apps-claude-opus-4-5-20251101-20261111", "apps-hex-claude-opus-4-5-20251101-20261111",
    "apps-kimi-k3.1-pro-highspeed-20261111", "apps-hex-kimi-k3.1-pro-highspeed-20261111",  # 7-day cap at 19/96
    "kimi-kimi-for-coding-highspeed-20261111", "hex-kimi-kimi-for-coding-highspeed-20261111",
    "apps-kimi-kimi-for-coding-highspeed-20261111", "apps-hex-kimi-kimi-for-coding-highspeed-20261111",
    "copilot-kimi-k3-20261112", "hex-copilot-kimi-k3-20261112", "apps-copilot-kimi-k3-20261112",  # started 11-12
    "apps-hex-copilot-kimi-k3-20261112",
)


def in_progress(path: str) -> bool:
    """True for a file under an in-progress run directory (path as globbed: eval/runs/<dir>/...)."""
    parts = path.split("/")
    return len(parts) > 2 and parts[2] in IN_PROGRESS
