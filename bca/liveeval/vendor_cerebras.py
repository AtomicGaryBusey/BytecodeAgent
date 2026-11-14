"""Models served by Cerebras Inference, pay-per-token: `cerebras:<model id>`, e.g. `cerebras:gpt-oss-120b`.

The same contract as vendor_fireworks (and vendor_foundry beneath it), which this subclasses; only the host, the
model ids, auth, prices and the default spend cap differ:

- The request is ours, byte for byte: POST https://api.cerebras.ai/v1/chat/completions, `stream: true` with usage,
  messages = [system: SYSTEM_PROMPT, user: prompt], `model` = the id as Cerebras lists it (GET /v1/models). No
  tools, sampling fields or max_tokens. `--effort` sends `reasoning_effort` (low, medium, high); without it nothing
  is sent ("model-default").
- Checked live 2026-11-11 (one tiny call per model): the stream is OpenAI-style SSE; every chunk names the model
  by exactly the requested id; reasoning arrives in `reasoning` deltas (recorded, never scored); the last chunk
  carries usage (completion_tokens includes reasoning_tokens; prompt_tokens_details.cached_tokens) and
  `time_info`; then `[DONE]`. Rate limits (response headers): gpt-oss-120b 1,000 requests and 500K tokens a
  minute, qwen-3.8-27b 450 requests and 150K tokens a minute; a 429 is a transient infrastructure error, retried.
- finish_reason `length` (Cerebras' own output cap; we send no max_tokens) is scored as a timeout with a note,
  as for Fireworks.
- Auth: CEREBRAS_API_KEY, or the first line of BCA_CEREBRAS_KEY_FILE (default ~/.config/bca/cerebras.key, which
  must not be readable by group or others). The key is never logged.
- Spend cap: the shared per-model ledger (~/.local/state/bca/foundry-spend.json), keyed `cerebras:<id>`, $7.50
  per model by default (BCA_CEREBRAS_CAP_USD) so the two models fit the account's $15 of credit; the ledger books
  at or above the real charge.
- Only models with a price below are accepted.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from bca.liveeval.vendor_fireworks import FireworksSubject
from bca.liveeval.vendor_foundry import WORST_PROMPT_TOKENS, FoundryError, Ledger

HOST = "api.cerebras.ai"
ROUTE = "/v1/chat/completions"
EFFORTS = ("low", "medium", "high")
KEY_FILE = Path(os.environ.get("BCA_CEREBRAS_KEY_FILE", "~/.config/bca/cerebras.key")).expanduser()
CAP_USD = float(os.environ.get("BCA_CEREBRAS_CAP_USD", "7.5"))

# Model id (as GET /v1/models lists it, 2026-11-11) -> USD per 1M tokens (input, cached input, output) from
# cerebras.ai/pricing (given by the user 2026-11-11; no cached-input rate is listed, so cached input is priced as
# input), and an output cap assumed for the spend reserve.
MODELS = {
    "gpt-oss-120b": {"price": (0.35, 0.35, 0.75), "max_out": 65_536},
    "qwen-3.8-27b": {"price": (0.99, 0.99, 1.49), "max_out": 65_536},
}


def _price(model: str, usage: dict) -> float:
    pin, pcached, pout = MODELS[model]["price"]
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    out = int(usage.get("completion_tokens") or 0)
    return ((prompt - cached) * pin + cached * pcached + out * pout) / 1e6


class CerebrasSubject(FireworksSubject):
    vendor = "cerebras"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, ledger: Ledger | None = None,
                 budget=None, key: str | None = None):
        if not model:
            raise ValueError("cerebras: name the model id, e.g. cerebras:gpt-oss-120b")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"cerebras: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        super().__init__(model, timeout=timeout, effort=None, ledger=ledger or Ledger(cap=CAP_USD), budget=budget,
                         key=key)
        self.effort = effort
        self.model_id = model
        self.deployment = f"cerebras:{model}"  # the ledger key
        self.name = f"cerebras:{model}"

    def setup(self) -> dict:
        if self._setup is None:
            if self.model_id not in MODELS:
                raise FoundryError(f"cerebras: model {self.model_id!r} has no price here (have {', '.join(MODELS)}); "
                                   "read cerebras.ai/pricing and add it first")
            self._setup = {"host": HOST, "endpoint": f"https://{HOST}", "model": self.model_id, "route": ROUTE,
                           "price_per_1m": MODELS[self.model_id]["price"], "served_as": ()}
        return self._setup

    def _bearer(self) -> str:
        if self._key:
            return self._key
        key = os.environ.get("CEREBRAS_API_KEY", "").strip()
        if not key and KEY_FILE.exists():
            if KEY_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise FoundryError(f"cerebras: {KEY_FILE} is readable by group or others; chmod 600 it")
            text = KEY_FILE.read_text()
            key = text.splitlines()[0].strip() if text.strip() else ""
        if not key:
            raise FoundryError(f"cerebras: no API key (set CEREBRAS_API_KEY or write it to {KEY_FILE})")
        self._key = key
        return key

    @property
    def cli_version(self) -> str:
        return "cerebras (direct API)"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or "model-default", "cli_version": "cerebras (direct API)", "vendor": "cerebras",
                "cerebras": {"model": s["model"], "route": ROUTE, "price_per_1m": s["price_per_1m"],
                             "cap_usd": self.ledger.cap}}

    def _price(self, model: str, usage: dict) -> float:
        return _price(self.model_id, usage)

    def _complete(self, *a, **kw):
        r = super()._complete(*a, **kw)
        r.notes = [n.replace("Fireworks' serving default", "Cerebras' serving default") for n in r.notes]
        return r

    _unreported_note = "charged an estimate from the streamed deltas"

    def _unreported(self, model: str, prompt: str, usage: dict, worst: float) -> float:
        # As for Fireworks: about one token per delta, with a margin, and the prompt at a pessimistic 2 chars/token.
        out = int(usage.get("streamed_deltas") or 0) * 1.25 + 2048
        return _price(self.model_id, {"prompt_tokens": len(prompt) // 2 + 2000, "completion_tokens": int(out)})

    def _worst(self, model: str) -> float:
        pin, _, pout = MODELS[self.model_id]["price"]
        return (WORST_PROMPT_TOKENS * pin + MODELS[self.model_id]["max_out"] * pout) / 1e6
