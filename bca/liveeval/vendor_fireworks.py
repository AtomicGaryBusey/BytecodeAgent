"""Models served by Fireworks.ai, pay-per-token (serverless): `fireworks:<model id>`, e.g. `fireworks:glm-5p3`.

The same contract as vendor_foundry, which this subclasses; only setup, auth and prices differ:

- The request is ours, byte for byte: POST https://api.fireworks.ai/inference/v1/chat/completions, `stream: true`
  with usage, messages = [system: SYSTEM_PROMPT, user: prompt], `model` = accounts/fireworks/models/<id>. No tools,
  sampling fields or max_tokens. `--effort` sends `reasoning_effort` (Fireworks: low, medium, high, xhigh, max,
  none; support varies by model). Without it nothing is sent ("model-default").
- The served model is verified: every chunk that names a model must name the requested id. Reasoning arrives in
  `reasoning_content` deltas: recorded, never scored. A tool call fails closed; finish_reason `length` fails closed
  (Fireworks lowers max_tokens to fit the context window, and its default output limit is not documented, so the
  first one is looked at before deciding how to score it).
- Auth: FIREWORKS_API_KEY, or the first line of BCA_FIREWORKS_KEY_FILE (default ~/.config/bca/fireworks.key,
  which must not be readable by group or others). The key is never logged.
- Spend cap: the shared per-model ledger of vendor_foundry (~/.local/state/bca/foundry-spend.json), keyed
  `fireworks:<id>`, $25 per model by default (BCA_FOUNDRY_CAP_USD). Rejected requests are not charged.
- Only models with a price below are accepted (prices from each model's fireworks.ai page; see MODELS).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from bca.liveeval.vendor_foundry import WORST_PROMPT_TOKENS, FoundryError, FoundrySubject, Ledger

HOST = "api.fireworks.ai"
ROUTE = "/inference/v1/chat/completions"
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
KEY_FILE = Path(os.environ.get("BCA_FIREWORKS_KEY_FILE", "~/.config/bca/fireworks.key")).expanduser()

# Model id -> USD per 1M tokens (input, cached input, output) for serverless, from the model's page on
# fireworks.ai/models (read 2026-11-07), and an output cap assumed for the spend reserve.
MODELS = {
    "minimax-m3p1-flash": {"price": (0.15, 0.015, 0.60), "max_out": 128_000},
    "glm-5p3": {"price": (1.40, 0.26, 4.40), "max_out": 128_000},
}


def _price(model: str, usage: dict) -> float:
    pin, pcached, pout = MODELS[model]["price"]
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    out = int(usage.get("completion_tokens") or 0)
    return ((prompt - cached) * pin + cached * pcached + out * pout) / 1e6


class FireworksSubject(FoundrySubject):
    vendor = "fireworks"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, ledger: Ledger | None = None,
                 budget=None, key: str | None = None):
        if not model:
            raise ValueError("fireworks: name the model id, e.g. fireworks:glm-5p3")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"fireworks: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        model = model.removeprefix("accounts/fireworks/models/")
        super().__init__(model, timeout=timeout, effort=None, account="-", resource_group="-", ledger=ledger,
                         budget=budget)
        self.effort = effort
        self.model_id = model
        self.deployment = f"fireworks:{model}"  # the ledger key
        self.name = f"fireworks:{model}"
        self._key = key

    def setup(self) -> dict:
        if self._setup is None:
            if self.model_id not in MODELS:
                raise FoundryError(f"fireworks: model {self.model_id!r} has no price here (have {', '.join(MODELS)}); "
                                   "read its fireworks.ai page and add it first")
            full = f"accounts/fireworks/models/{self.model_id}"
            self._setup = {"host": HOST, "endpoint": f"https://{HOST}", "model": full, "route": ROUTE,
                           "price_per_1m": MODELS[self.model_id]["price"]}
        return self._setup

    def _bearer(self) -> str:
        if self._key:
            return self._key
        key = os.environ.get("FIREWORKS_API_KEY", "").strip()
        if not key and KEY_FILE.exists():
            if KEY_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise FoundryError(f"fireworks: {KEY_FILE} is readable by group or others; chmod 600 it")
            key = KEY_FILE.read_text().splitlines()[0].strip() if KEY_FILE.read_text().strip() else ""
        if not key:
            raise FoundryError(f"fireworks: no API key (set FIREWORKS_API_KEY or write it to {KEY_FILE})")
        self._key = key
        return key

    @property
    def cli_version(self) -> str:
        return "fireworks (direct API)"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or "model-default", "cli_version": "fireworks (direct API)", "vendor": "fireworks",
                "fireworks": {"model": s["model"], "route": ROUTE, "price_per_1m": s["price_per_1m"],
                              "cap_usd": self.ledger.cap}}

    def request_body(self, prompt: str) -> dict:
        body = super().request_body(prompt)
        body["model"] = self.setup()["model"]
        return body

    def _price(self, model: str, usage: dict) -> float:
        return _price(self.model_id, usage)

    def _worst(self, model: str) -> float:
        pin, _, pout = MODELS[self.model_id]["price"]
        return (WORST_PROMPT_TOKENS * pin + MODELS[self.model_id]["max_out"] * pout) / 1e6
