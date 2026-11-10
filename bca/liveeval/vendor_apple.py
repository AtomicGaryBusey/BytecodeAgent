"""Apple Foundation Models on the user's Mac: `apple:system` (the on-device model) or `apple:pcc`
(Private Cloud Compute).

No CLI and no API key. The Mac runs Apple's `fm serve` (OpenAI Chat Completions over a Unix socket; for
the on-device model, behind the Mac session's own governor, `fm_serve` in fm-mcp), and the harness reaches
it through an SSH local forward from this machine (`ssh -N -L 127.0.0.1:18976:<socket> <user>@<mac>`).
The endpoint is BCA_APPLE_ENDPOINT (default http://127.0.0.1:18976). The contract, as for vendor_foundry
(which this subclasses for the request and stream handling):

- The request is ours, byte for byte: messages = [system: SYSTEM_PROMPT, user: prompt], `stream: true`
  with usage, `model` = the Apple model id. No tools, no sampling fields (so sampling is the framework's
  random default and answers are not deterministic, as for every other subject) and no max_tokens. The
  Mac session checked (2026-11-08) that `fm serve` builds a session with exactly our instructions and
  prompt: the framework transcript holds those two entries and the response, and prompt_tokens equals
  the framework's own count. The model's chat template adds about 56 tokens of its own either way.
- Served model: every chunk that names a model must name the requested id.
- Refusals: `message.refusal` / a refusal delta, finish_reason content_filter, or an HTTP 500 whose text
  names a guardrail or unsafe content is a scored refusal. (The guardrail shape over `fm serve` was not
  observed before the first runs; any other HTTP 500 is recorded verbatim as an error for review.)
- Context window (on-device: 8,192 tokens, prompt and answer together): an HTTP 500 "exceeded the model's
  context size" is a scored `context_limit` attempt, not a retried infrastructure error.
- finish_reason is "stop" even when an output cap cuts an answer (measured), so it is not trusted for
  truncation; we send no cap, so only the context window can cut an answer, and that fails as above.
- The Mac's governor stops a call at 120 s (stricter than the harness's 180 s round-1 limit; the Mac session's
  safety limit, not ours to change): an HTTP 503 or, once streaming, a final SSE error event saying "did not
  answer within" is a scored timeout. Every other 503 or 429 (memory pressure, circuit breaker, rate limit) and
  an unreachable tunnel are transient infrastructure errors: backed off and retried, never scored. The one
  exception is the governor's memory pause ("fm is paused to protect this Mac ... Do not retry automatically"):
  that stops the run at once (fatal), as the Mac asks; resume later with the same command.
- PCC's quota error (`insufficient_quota`, "Your quota has been reached") also stops the run (fatal): /health's
  quota flags were seen to stay false after the quota ran out, so they are not a reliable stop signal.
- Reasoning (PCC only, opt-in with `--effort low|medium|high`): sends `reasoning_effort`, the only accepted
  values (anything else is a 400). Without it nothing is sent, as in PCC round 1. PCC reports
  `reasoning_tokens` 0 even when it reasons; the hidden reasoning is counted in `completion_tokens`, so a
  setting is verified by completion tokens far above the answer length against a default control.
- Cost: none (on-device; PCC is quota-limited on the Mac's account, not billed). Run with `--jobs 1`: the
  Mac has one model slot and a 30 calls/minute governor. PCC is served by Apple's raw `fm serve` with no
  governor (no rate limit, queue or timeout), so the subject paces itself to one call start every
  BCA_APPLE_MIN_INTERVAL_S (default 2 s), and the harness's own time limits apply.
"""

from __future__ import annotations

import http.client
import os
import time
from urllib.parse import urlparse

from bca.liveeval.subject import OnText, Response
from bca.liveeval.vendor_foundry import FoundryError, FoundrySubject

ENDPOINT = os.environ.get("BCA_APPLE_ENDPOINT", "http://127.0.0.1:18976")
ROUTE = "/v1/chat/completions"
# Minimum gap between call starts (30 calls/minute), whatever the endpoint enforces itself.
MIN_INTERVAL_S = float(os.environ.get("BCA_APPLE_MIN_INTERVAL_S", "2.0"))
# Apple model id -> what the Mac reported for it (2026-11-08: macOS 27.2, FoundationModels 2.1.14)
MODELS = {
    "system": {"name": "AFM 3 Core Advanced (on-device)", "context": 8192},
    "pcc": {"name": "Apple Private Cloud Compute", "context": None},
}
_CONTEXT = "exceeded the model's context size"
_GUARDRAIL = ("guardrail", "unsafe", "sensitive", "safety")


class AppleSubject(FoundrySubject):
    vendor = "apple"
    efforts: tuple[str, ...] = ("low", "medium", "high")  # PCC only; the on-device model has no reasoning

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, budget=None,
                 endpoint: str | None = None):
        if not model:
            raise ValueError("apple: name the model, e.g. apple:system (on-device) or apple:pcc")
        if effort is not None and model != "pcc":
            raise ValueError("apple: only PCC takes a reasoning effort; the on-device model has no effort setting")
        if effort is not None and effort not in self.efforts:
            raise ValueError(f"apple: unknown effort {effort!r}; PCC accepts {', '.join(self.efforts)}")
        super().__init__(model, timeout=timeout, effort=effort, account="-", resource_group="-", budget=budget)
        self.model_id = model
        self.deployment = model
        self.name = f"apple:{model}"
        self.endpoint = endpoint or ENDPOINT
        self.connection = http.client.HTTPConnection  # plain HTTP to the local end of the SSH forward
        self._last_call = float("-inf")

    def setup(self) -> dict:
        if self._setup is None:
            if self.model_id not in MODELS:
                raise FoundryError(f"apple: unknown model {self.model_id!r} (have {', '.join(MODELS)})")
            u = urlparse(self.endpoint)
            if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise FoundryError(f"apple: {self.endpoint!r} must be the local end of an SSH forward "
                                   "(http://127.0.0.1:<port>)")
            self._setup = {"host": u.netloc, "endpoint": self.endpoint, "model": self.model_id, "route": ROUTE,
                           **MODELS[self.model_id]}
        return self._setup

    def _bearer(self) -> str:
        return "none"  # the socket is reached only through SSH; fm serve takes no key

    @property
    def cli_version(self) -> str:
        return "apple fm serve (direct API)"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or "model-default", "cli_version": self.cli_version, "vendor": "apple",
                "apple": {"model": s["model"], "name": s["name"], "context_tokens": s["context"],
                          "route": ROUTE, "transport": "SSH local forward to fm serve's Unix socket"}}

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        if self.budget is not None and not self.budget():
            return Response("", ok=False, error="call budget exhausted")
        try:
            s = self.setup()
        except FoundryError as e:
            return Response("", ok=False, error=str(e), model=self.model_id, model_source="requested")
        # Pace calls ourselves: the PCC endpoint is Apple's raw `fm serve` with no governor in front of it.
        wait = self._last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        self.calls += 1
        r = self._complete(prompt, on_text, raw_path, self.timeout if timeout is None else timeout, s, "none")
        r.cost_usd = 0.0
        err = r.error or ""
        if "insufficient_quota" in err or "quota has been reached" in err.lower():
            # PCC's quota on the Mac's account (2026-11-08: reached after about 140 calls while /health still said
            # limit_reached false). Retrying only polls Apple; stop the run and resume later with the same command.
            r.error = f"apple: PCC quota reached; stop and resume later: {err[:200]}"
        elif "did not answer within" in err:  # the Mac's governor time limit (120 s): a timeout, like ours
            r.timed_out, r.ok = True, False
            r.notes.append(f"apple governor timeout: {err[:200]}")
            r.error = "timeout"
        elif err.startswith(("apple HTTP 500", "apple stream error")):
            # The same 500 arrives either as the HTTP status or, once streaming has begun, as a final SSE error
            # event (seen 2026-11-09 on PCC: "The model's safety guardrails were triggered." after a long
            # repetitive answer). Either way it is the model's outcome, not an infrastructure fault.
            low = err.lower()
            if _CONTEXT in low:
                r.error = f"context_limit: the prompt and answer exceed the model's context window: {err[:300]}"
            elif any(w in low for w in _GUARDRAIL):
                r.refusal, r.error, r.ok = f"guardrail: {err.split(':', 1)[-1].strip()[:300]}", "", False
        return r
