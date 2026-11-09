"""Models served by Fireworks.ai, pay-per-token (serverless): `fireworks:<model id>`, e.g. `fireworks:glm-5p3`.

The same contract as vendor_foundry, which this subclasses; only setup, auth and prices differ:

- The request is ours, byte for byte: POST https://api.fireworks.ai/inference/v1/chat/completions, `stream: true`
  with usage, messages = [system: SYSTEM_PROMPT, user: prompt], `model` = accounts/fireworks/models/<id>. No tools,
  sampling fields or max_tokens. `--effort` sends `reasoning_effort` (Fireworks: low, medium, high, xhigh, max,
  none; support varies by model). Without it nothing is sent ("model-default").
- The served model is verified: every chunk that names a model must name the requested id, or exactly the
  display name Fireworks' model API gives it (MODELS[...]['served_as']; Qwen 3.8 Max streams its display name). Reasoning arrives in
  `reasoning_content` deltas: recorded, never scored. A tool call fails closed. finish_reason `length` means the model
  used up Fireworks' default output cap for it (undocumented; 65,536 tokens for MiniMax M3 and 32,768 for Nemotron
  3 Ultra, seen 2026-11-08, both spent entirely on reasoning): scored as a timeout with a note, as vendor_vllm
  scores its token budget, never raised per model.
- Auth: FIREWORKS_API_KEY, or the first line of BCA_FIREWORKS_KEY_FILE (default ~/.config/bca/fireworks.key,
  which must not be readable by group or others). The key is never logged.
- A call that ends without a usage report (a timeout) is booked from what it streamed: 1.25 tokens per delta
  plus 2,048, never more than the worst case. Foundry still books the worst case.
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
    # from fireworks.ai/pricing (Serverless Standard, read 2026-11-08)
    # its stream names the model by display name; this exact name is what Fireworks' model API
    # (GET /v1/accounts/fireworks/models/qwen3p8-max, displayName) reports for it (checked 2026-11-08)
    "qwen3p8-max": {"price": (2.00, 0.25, 6.00), "max_out": 128_000, "served_as": ("Qwen 3.8 Max",)},
    "deepseek-v4p1-flash": {"price": (0.30, 0.006, 1.20), "max_out": 128_000},
    "minimax-m3": {"price": (0.30, 0.06, 1.20), "max_out": 128_000},
    "nemotron-3-ultra-nvfp4": {"price": (0.60, 0.12, 2.40), "max_out": 128_000},
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
                           "price_per_1m": MODELS[self.model_id]["price"],
                           "served_as": MODELS[self.model_id].get("served_as", ())}
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

    def _complete(self, *a, **kw):
        r = super()._complete(*a, **kw)
        if r.error.startswith("isolation: the answer reached the model's output cap"):
            # Fireworks' own default output cap for the model (we send no max_tokens; 65,536 for MiniMax M3,
            # 32,768 for Nemotron 3 Ultra, measured 2026-11-08). The model used up its output budget without
            # finishing: scored as a timeout, as vendor_vllm scores its token budget.
            out = (r.usage or {}).get("completion_tokens")
            r.notes.append(f"output cap reached: {out} output tokens (Fireworks' serving default; finish_reason length)")
            r.timed_out, r.ok, r.error = True, False, "timeout"
        return r

    _unreported_note = "charged an estimate from the streamed deltas"

    def _unreported(self, model: str, prompt: str, usage: dict, worst: float) -> float:
        # Fireworks streams about one token per delta (a GLM-5.3 call: 624 deltas, 621 completion tokens),
        # and closing the connection ends the generation. Book what arrived with a margin for what was generated
        # but not yet sent, and the prompt at a pessimistic 2 chars/token plus the system prompt.
        out = int(usage.get("streamed_deltas") or 0) * 1.25 + 2048
        return _price(self.model_id, {"prompt_tokens": len(prompt) // 2 + 2000, "completion_tokens": int(out)})

    def _worst(self, model: str) -> float:
        pin, _, pout = MODELS[self.model_id]["price"]
        return (WORST_PROMPT_TOKENS * pin + MODELS[self.model_id]["max_out"] * pout) / 1e6
