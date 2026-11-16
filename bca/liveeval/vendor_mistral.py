"""Mistral models through Mistral's own API ("La Plateforme"), pay-per-token: `mistral:<model id>`, e.g.
`mistral:mistral-large-4`. The tool-free route to the models the Vibe agent-harness study measured inside Vibe.

The same contract as vendor_cerebras / vendor_fireworks (vendor_foundry beneath them); only the host, model ids,
auth, prices and the spend cap differ:

- The request is ours, byte for byte: POST https://api.mistral.ai/v1/chat/completions, `stream: true` with usage,
  messages = [system: SYSTEM_PROMPT, user: prompt], `model` = the id as Mistral lists it. No tools, sampling fields,
  max_tokens or effort.
- Checked live 2026-11-15 (one tiny call each, mistral-large-4 and mistral-medium-3-5): OpenAI-style SSE ending in
  `[DONE]`; every chunk names the model by exactly the requested id; the last chunk carries usage
  (completion_tokens includes completion_tokens_details.reasoning_tokens; prompt_tokens_details.cached_tokens).
  Large 4 streams its reasoning INSIDE `content`, as a list of {"type": "thinking"} chunks before the {"type":
  "text"} answer (`_delta` separates them: reasoning is recorded, never scored); Medium 3.5 sent plain strings and
  no reasoning on the test prompt. Rate limits (headers): 2,000 requests a minute; 15M tokens a minute for Large 4,
  1M for Medium 3.5. GET /v1/models lists mistral-large-4 (alias mistral-large-latest) and mistral-medium-3-5
  (= mistral-medium-2604, the model Vibe calls mistral-vibe-cli-latest).
- Auth: MISTRAL_API_KEY, or the first line of BCA_MISTRAL_KEY_FILE (default ~/.config/bca/mistral.key, which must
  not be readable by group or others). The key is never logged. This is the user's Mistral API key; their
  Ring-0-Judge agent draws on the same account's API allowance, so nothing runs without an agreed cap.
- Spend cap: the shared per-model ledger, keyed `mistral:<id>`. The default cap is $0 (BCA_MISTRAL_CAP_USD): every
  run must set the cap the user approved for it.
- Only models with a price below are accepted.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from bca.liveeval.vendor_fireworks import FireworksSubject
from bca.liveeval.vendor_foundry import WORST_PROMPT_TOKENS, FoundryError, Ledger

HOST = "api.mistral.ai"
ROUTE = "/v1/chat/completions"
EFFORTS: tuple[str, ...] = ()  # no effort parameter is sent: the model's default
KEY_FILE = Path(os.environ.get("BCA_MISTRAL_KEY_FILE", "~/.config/bca/mistral.key")).expanduser()
CAP_USD = float(os.environ.get("BCA_MISTRAL_CAP_USD", "0"))

# Model id -> USD per 1M tokens (input, cached input, output) and an output cap assumed for the spend reserve.
# Prices 2026-11-15: Mistral's pricing page lists Large 4 at 0.70 / 0.07 / 2.10, pricing aggregators at 1.36 / 0.14 /
# 4.18 (a launch promotion appears to have ended); the HIGHER figures are booked, so the ledger is at or above the
# real charge. Add a model only with a price read from docs.mistral.ai/inference/pricing and an id that GET
# /v1/models lists for the key.
MODELS = {
    "mistral-large-4": {"price": (1.36, 0.14, 4.18), "max_out": 65_536},
    # Medium 3.5 is not on Mistral's pricing page (2026-11-15); 1.50 / 7.50 is the aggregators' figure, no cached rate
    # Deprecated 2026-10-31 per its model card (replacement: Large 4); the API still serves it. Not run tool-free.
    "mistral-medium-3-5": {"price": (1.50, 1.50, 7.50), "max_out": 65_536},
}


def _price(model: str, usage: dict) -> float:
    pin, pcached, pout = MODELS[model]["price"]
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    out = int(usage.get("completion_tokens") or 0)
    return ((prompt - cached) * pin + cached * pcached + out * pout) / 1e6


class MistralSubject(FireworksSubject):
    vendor = "mistral"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, ledger: Ledger | None = None,
                 budget=None, key: str | None = None):
        if not model:
            raise ValueError("mistral: name the model id, e.g. mistral:gpt-oss-120b")
        if effort is not None:
            raise ValueError("mistral: no effort setting is sent; every Mistral row runs at the model's default")
        super().__init__(model, timeout=timeout, effort=None, ledger=ledger or Ledger(cap=CAP_USD), budget=budget,
                         key=key)
        self.effort = effort
        self.model_id = model
        self.deployment = f"mistral:{model}"  # the ledger key
        self.name = f"mistral:{model}"

    def setup(self) -> dict:
        if self._setup is None:
            if self.model_id not in MODELS:
                raise FoundryError(f"mistral: model {self.model_id!r} has no price here (have {', '.join(MODELS)}); "
                                   "read docs.mistral.ai/inference/pricing and add it first")
            self._setup = {"host": HOST, "endpoint": f"https://{HOST}", "model": self.model_id, "route": ROUTE,
                           "price_per_1m": MODELS[self.model_id]["price"], "served_as": ()}
        return self._setup

    def _bearer(self) -> str:
        if self._key:
            return self._key
        key = os.environ.get("MISTRAL_API_KEY", "").strip()
        if not key and KEY_FILE.exists():
            if KEY_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise FoundryError(f"mistral: {KEY_FILE} is readable by group or others; chmod 600 it")
            text = KEY_FILE.read_text()
            key = text.splitlines()[0].strip() if text.strip() else ""
        if not key:
            raise FoundryError(f"mistral: no API key (set MISTRAL_API_KEY or write it to {KEY_FILE})")
        self._key = key
        return key

    @property
    def cli_version(self) -> str:
        return "mistral (direct API)"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or "model-default", "cli_version": "mistral (direct API)", "vendor": "mistral",
                "mistral": {"model": s["model"], "route": ROUTE, "price_per_1m": s["price_per_1m"],
                             "cap_usd": self.ledger.cap}}

    def _delta(self, d: dict) -> dict:
        """Mistral streams a reasoning model's `content` as a list of typed chunks: {"type": "thinking", "thinking":
        [{"type": "text", "text": ...}]} for reasoning (recorded, never scored) and {"type": "text", "text": ...}
        for the answer. A plain string is the answer. Any other chunk type fails closed."""
        c = d.get("content")
        if not isinstance(c, list):
            return d
        text, think = [], []
        for item in c:
            t = item.get("type") if isinstance(item, dict) else None
            if t == "text" and isinstance(item.get("text"), str):
                text.append(item["text"])
            elif t == "thinking":
                for part in item.get("thinking") or []:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        think.append(part["text"])
                    else:
                        return {**d, "_isolation": f"unknown thinking chunk from Mistral: {str(part)[:120]}"}
            else:
                return {**d, "_isolation": f"unknown content chunk from Mistral: {str(item)[:120]}"}
        out = {k: v for k, v in d.items() if k != "content"}
        if text:
            out["content"] = "".join(text)
        if think:
            out["reasoning"] = "".join(think)
        return out

    def _price(self, model: str, usage: dict) -> float:
        return _price(self.model_id, usage)

    def _complete(self, *a, **kw):
        r = super()._complete(*a, **kw)
        r.notes = [n.replace("Fireworks' serving default", "Mistral's serving default") for n in r.notes]
        return r

    _unreported_note = "charged an estimate from the streamed deltas"

    def _unreported(self, model: str, prompt: str, usage: dict, worst: float) -> float:
        # As for Fireworks: about one token per delta, with a margin, and the prompt at a pessimistic 2 chars/token.
        out = int(usage.get("streamed_deltas") or 0) * 1.25 + 2048
        return _price(self.model_id, {"prompt_tokens": len(prompt) // 2 + 2000, "completion_tokens": int(out)})

    def _worst(self, model: str) -> float:
        pin, _, pout = MODELS[self.model_id]["price"]
        return (WORST_PROMPT_TOKENS * pin + MODELS[self.model_id]["max_out"] * pout) / 1e6
