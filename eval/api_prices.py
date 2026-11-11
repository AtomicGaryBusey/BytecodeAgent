"""What each recorded attempt cost, or would have cost at the vendor's published API price.

Routes differ in what they can tell us, so every figure carries its basis:

- real: what was charged (Fireworks, Azure Foundry: priced from the call's usage, kept in the spend ledger).
- cli: the CLI's own API-equivalent figure for a flat-rate plan (Claude Code `total_cost_usd`, Grok Build
  `total_cost_usd`). Nothing was charged per call.
- credits: GitHub Copilot's reported AI credits at $0.01 each (GitHub's published rate since 2026-06-01).
- list: our estimate for a flat-rate plan that reports tokens but no price (Codex, Kimi Code, Antigravity): the
  call's tokens at the API list prices below. Cache writes are priced as input.
- free: Apple Foundation Models (on-device, or PCC on the iCloud+ quota).
- none: no price (local models; models with no published API price).

The list prices are third-party aggregator figures (the vendors' own pages could not be fetched), checked
2026-11-11; where sources disagreed the note says so. Tokenisers differ, so cross-vendor dollars are approximate.
"""

from __future__ import annotations

CHECKED = "2026-11-11"
CREDIT_USD = 0.01  # one GitHub Copilot AI credit

# label -> (input, cached input, output) USD per 1M tokens, and a note on the source
LIST_PRICES: dict[str, tuple[tuple[float, float, float], str]] = {
    "codex:gpt-6-astra": ((10.00, 1.00, 50.00), "MorphLLM table verified 2026-10-29; Lindy, aitokenprice agree"),
    "codex:gpt-6-sol": ((2.00, 0.20, 10.00), "aitokenprice; GPT-6.1 Sol replaced it at the same price (cached $0.10)"),
    "codex:gpt-6-luna": ((0.10, 0.01, 0.50), "MorphLLM, aitokenprice; one source lists $0.20 / $1.20"),
    "codex:gpt-5.5": ((5.00, 0.50, 30.00), "several sources agree (standard tier)"),
    "kimi:kimi-code/k3.1-pro": ((2.00, 0.20, 10.00), "BenchLM (Moonshot pricing page), Puter agree"),
    "kimi:kimi-code/k3": ((3.00, 0.30, 15.00), "BenchLM, Maximem, MorphLLM agree"),
    "agy:gemini-3.8-flash-high": ((0.75, 0.075, 3.75), "introductory rate through 2026-12-31 (doubles 2027-01-01)"),
}
# models with no published API price for the id we ran
NO_LIST_PRICE = {
    "codex:gpt-daybreak-blue-latest": "Daybreak Blue is a ChatGPT-plan model with no API listing",
    "kimi:kimi-code/kimi-for-coding": "K2.8 Preview has no API listing (the K2 series closed to new users 2026-10-31)",
}


def _n(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def vendor_of(label: str) -> str:
    if label.startswith("claude-") or ":" not in label:
        return "claude"
    return label.split(":", 1)[0]


def attempt_cost(label: str, a: dict) -> tuple[float | None, str]:
    """(USD or None, basis) for one attempt record of model `label` (an eval/analytics.py label)."""
    v, u = vendor_of(label), a.get("usage") or {}
    if v in ("fireworks", "foundry"):
        return (a.get("cost_usd"), "real") if isinstance(a.get("cost_usd"), (int, float)) else (None, "real")
    if v == "claude":
        return (a.get("cost_usd"), "cli") if isinstance(a.get("cost_usd"), (int, float)) else (None, "cli")
    if v == "grok":
        return (_n(u["total_cost_usd"]), "cli") if "total_cost_usd" in u else (None, "cli")
    if v == "copilot":
        return (_n(u["ai_credits"]) * CREDIT_USD, "credits") if "ai_credits" in u else (None, "credits")
    if v == "apple":
        return 0.0, "free"
    if label in LIST_PRICES:
        if not u or not (_n(u.get("input_tokens")) or _n(u.get("output_tokens"))):
            return None, "list"
        (pin, pcached, pout), _ = LIST_PRICES[label]
        inp, out = _n(u.get("input_tokens")), _n(u.get("output_tokens"))
        if v == "codex":  # OpenAI style: input_tokens includes the cached ones
            cached = _n(u.get("cached_input_tokens"))
            fresh = max(0.0, inp - cached) + _n(u.get("cache_write_input_tokens"))
        elif v == "kimi":  # Anthropic style: cache reads and writes are counted apart from input_tokens
            cached = _n(u.get("cache_read_input_tokens"))
            fresh = inp + _n(u.get("cache_creation_input_tokens"))
        else:  # agy: never cached in these runs
            cached = _n(u.get("cache_read_tokens"))
            fresh = max(0.0, inp - cached)
        return (fresh * pin + cached * pcached + out * pout) / 1e6, "list"
    return None, "none"
