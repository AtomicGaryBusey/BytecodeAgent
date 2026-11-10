"""Models served by Microsoft Foundry (Azure), pay-per-token: `foundry:<deployment name>`.

No agent CLI is involved: the adapter makes one HTTPS request per call, so the fairness contract holds
by construction and is checked per call, as in vendor_vllm:

- The request is ours, byte for byte: POST <endpoint><route>, `stream: true` (with usage), messages =
  [system: SYSTEM_PROMPT, user: prompt], `model` = the deployment name. No `tools`, `tool_choice`,
  `response_format`, sampling fields or `max_completion_tokens`: the model's own defaults apply (the
  analogue of a vendor CLI's defaults). MAI-Thinking-1's `reasoning_display` is not sent, so no
  encrypted reasoning comes back (it is opaque anyway and never scored).
- Effort: `--effort` sends `reasoning_effort` (Chat Completions). Without it nothing is sent
  ("model-default").
- The deployment is read from Azure before the first call (`az cognitiveservices account deployment
  show`): its model name, version, format, SKU and content-filter policy (raiPolicyName) go into
  describe(), so every cell records them. Only a GlobalStandard SKU is accepted, because the prices
  below are GlobalStandard prices.
- The served model is verified: every stream chunk that names a model must name the deployment's
  model (a router or a swapped deployment fails closed). A tool call in the stream fails closed.
- Content safety: Azure's filter is part of what the route serves. A prompt blocked before generation
  (HTTP 400, content_filter / SafetyBlockedError), a stream that ends in a SafetyBlockedError event, or
  finish_reason `content_filter` is a scored refusal, as for Copilot's filter.
- finish_reason `length` (the model's own output cap; 64K for MAI-Thinking-1) fails closed (`isolation:`)
  so the first one is looked at before deciding how to score it.
- Auth: a Microsoft Entra ID token from the signed-in Azure CLI (`az account get-access-token`), cached
  until five minutes before it expires. No API key is read or stored.
- Spend cap: every call is priced from its reported usage and added to a ledger per deployment
  (~/.local/state/bca/foundry-spend.json, file-locked, shared by all runs). A call starts only if the
  ledger's spend plus what is reserved for calls in flight plus this call's worst case (the model's
  output cap at the output price, plus the prompt) stays within the cap (BCA_FOUNDRY_CAP_USD, default
  25 per deployment). A call without a usage report is charged its worst case. A refused start
  says "foundry spend cap reached", which the runner treats as fatal.
- Routes: both MAI models (format "Microsoft") use /mai/v1/chat/completions on the resource's Foundry host
  (properties.endpoints, "Azure MAI Model Inference API v1.0"); the others /openai/v1/chat/completions on
  the same host ("AI Foundry API").
- Configuration: BCA_FOUNDRY_ACCOUNT (the Foundry resource name) and BCA_FOUNDRY_RG (its resource
  group). The endpoint comes from the resource itself.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from bca.liveeval.subject import SYSTEM_PROMPT, OnText, Response, Subject

MODEL_DEFAULT = "model-default"
EFFORTS = ("low", "medium", "high", "xhigh")
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
LEDGER = Path(os.environ.get("BCA_FOUNDRY_LEDGER", "~/.local/state/bca/foundry-spend.json")).expanduser()
CAP_USD = float(os.environ.get("BCA_FOUNDRY_CAP_USD", "25"))

# Underlying model name (lower case) -> chat completions route, USD per 1M tokens (input, cached input,
# output) at GlobalStandard, and the model's output cap in tokens. Prices: Azure Retail Prices API
# (prices.azure.com, product "MAI Models", meters "... glbl 1M Tokens", eastus2), read 2026-11-07.
MODELS = {
    "mai-thinking-1": {"route": "/mai/v1/chat/completions", "price": (2.00, 0.20, 8.00), "max_out": 64_000},
    "mai-code-1.1-flash": {"route": "/mai/v1/chat/completions", "price": (0.20, 0.02, 1.20), "max_out": 64_000},
    # product "Azure Deepseek Models", meters "V4 Pro Inp/cached/Outp glbl Tokens" (per 1K; westus3 and 39
    # other regions), read 2026-11-07. Output cap not stated in the catalog: 128K assumed for the reserve.
    "deepseek-v4-pro": {"route": "/openai/v1/chat/completions", "price": (1.74, 0.145, 3.48), "max_out": 128_000},
}
WORST_PROMPT_TOKENS = 40_000  # generous: the longest repair prompt is far below this


class FoundryError(RuntimeError):
    pass


def _az(*args: str, timeout: float = 60.0) -> dict:
    try:
        out = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise FoundryError("foundry: the Azure CLI (az) is not installed") from None
    except subprocess.TimeoutExpired:
        raise FoundryError(f"foundry: az {args[0]} timed out") from None
    if out.returncode != 0:
        msg = out.stderr.strip()[-400:]
        if "az login" in msg or "AADSTS" in msg or "not logged in" in msg.lower():
            raise FoundryError(f"foundry: not logged in to Azure (run `az login`): {msg}")
        raise FoundryError(f"foundry: az {' '.join(args[:3])} failed: {msg}")
    return json.loads(out.stdout or "{}")


def price_of(model: str, usage: dict) -> float:
    pin, pcached, pout = MODELS[model]["price"]
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    out = int(usage.get("completion_tokens") or 0)
    return ((prompt - cached) * pin + cached * pcached + out * pout) / 1e6


def worst_case(model: str) -> float:
    pin, _, pout = MODELS[model]["price"]
    return (WORST_PROMPT_TOKENS * pin + MODELS[model]["max_out"] * pout) / 1e6


class Ledger:
    """{deployment: {"spent": usd, "calls": n, "reserved": {token: usd}}} in one JSON file, under flock."""

    def __init__(self, path: Path = LEDGER, cap: float = CAP_USD):
        self.path, self.cap = path, cap

    def _edit(self, fn):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text()) if self.path.exists() else {}
                result = fn(data)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
                tmp.replace(self.path)
                return result
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def reserve(self, deployment: str, amount: float) -> str | None:
        """A reservation token, or None when the call would break the cap."""
        token = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"

        def fn(data):
            d = data.setdefault(deployment, {"spent": 0.0, "calls": 0, "reserved": {}})
            if d["spent"] + sum(d["reserved"].values()) + amount > self.cap:
                return None
            d["reserved"][token] = amount
            return token

        return self._edit(fn)

    def settle(self, deployment: str, token: str, cost: float) -> float:
        def fn(data):
            d = data[deployment]
            d["reserved"].pop(token, None)
            d["spent"] = round(d["spent"] + cost, 6)
            d["calls"] += 1
            return d["spent"]

        return self._edit(fn)

    def spent(self, deployment: str) -> float:
        return self._edit(lambda data: data.get(deployment, {}).get("spent", 0.0))


class FoundrySubject(Subject):
    vendor = "foundry"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, deployment: str, *, timeout: float = 180.0, effort: str | None = None,
                 account: str | None = None, resource_group: str | None = None, ledger: Ledger | None = None,
                 budget=None):
        if not deployment:
            raise ValueError("foundry: name the deployment, e.g. foundry:mai-thinking-1")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"foundry: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        self.deployment = deployment
        self.name = f"foundry:{deployment}"
        self.timeout = timeout
        self.effort = effort
        self.account = account or os.environ.get("BCA_FOUNDRY_ACCOUNT", "")
        self.resource_group = resource_group or os.environ.get("BCA_FOUNDRY_RG", "")
        self.ledger = ledger or Ledger()
        self.budget = budget
        self._setup: dict | None = None
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self.connection = http.client.HTTPSConnection  # tests swap in plain HTTP to a local fake

    # -- Azure metadata (no model call) ------------------------------------------------------------

    def setup(self) -> dict:
        if self._setup is None:
            if not (self.account and self.resource_group):
                raise FoundryError("foundry: set BCA_FOUNDRY_ACCOUNT and BCA_FOUNDRY_RG (the Foundry resource "
                                   "and its resource group)")
            acct = _az("cognitiveservices", "account", "show", "--name", self.account,
                       "--resource-group", self.resource_group)
            dep = _az("cognitiveservices", "account", "deployment", "show", "--name", self.account,
                      "--resource-group", self.resource_group, "--deployment-name", self.deployment)
            m = (dep.get("properties") or {}).get("model") or {}
            model = str(m.get("name", "")).lower()
            if model not in MODELS:
                raise FoundryError(f"foundry: deployment {self.deployment} serves {model!r}, which this adapter "
                                   f"has no route and price for (have {', '.join(MODELS)}); audit it first")
            sku = (dep.get("sku") or {}).get("name", "")
            if sku != "GlobalStandard":
                raise FoundryError(f"foundry: deployment {self.deployment} is {sku!r}; only GlobalStandard is "
                                   "priced here")
            props = acct.get("properties") or {}
            # the model routes live on the Foundry host (services.ai.azure.com), not on the resource's
            # default endpoint (cognitiveservices.azure.com)
            eps = props.get("endpoints") or {}
            endpoint = eps.get("Azure MAI Model Inference API v1.0" if MODELS[model]["route"].startswith("/mai/")
                               else "AI Foundry API") or eps.get("AI Foundry API") or props.get("endpoint", "")
            u = urlparse(endpoint)
            if u.scheme != "https" or not u.hostname:
                raise FoundryError(f"foundry: unusable endpoint {endpoint!r} on {self.account}")
            self._setup = {"host": u.netloc, "endpoint": endpoint, "model": model,
                           "model_version": m.get("version"), "model_format": m.get("format"), "sku": sku,
                           "content_filter": (dep.get("properties") or {}).get("raiPolicyName"),
                           "route": MODELS[model]["route"], "price_per_1m": MODELS[model]["price"]}
        return self._setup

    def _bearer(self) -> str:
        if self._token is None or self._token[1] - time.time() < 300:
            t = _az("account", "get-access-token", "--resource", TOKEN_RESOURCE)
            self._token = (t["accessToken"], float(t.get("expires_on") or time.time() + 1800))
        return self._token[0]

    @property
    def cli_version(self) -> str:
        return "foundry (direct API)"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or MODEL_DEFAULT, "cli_version": "foundry (direct API)", "vendor": "foundry",
                "foundry": {k: s[k] for k in ("model", "model_version", "model_format", "sku", "content_filter",
                                              "route", "price_per_1m")} | {"cap_usd": self.ledger.cap}}

    # pricing (a subclass with its own price table overrides these) ---------------------------------

    def _price(self, model: str, usage: dict) -> float:
        return price_of(model, usage)

    def _worst(self, model: str) -> float:
        return worst_case(model)

    # -- one call ----------------------------------------------------------------------------------

    def request_body(self, prompt: str) -> dict:
        body = {"model": self.deployment, "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
        if self.effort is not None:
            body["reasoning_effort"] = self.effort
        return body

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        if self.budget is not None and not self.budget():
            return Response("", ok=False, error="call budget exhausted")
        try:
            s = self.setup()
            bearer = self._bearer()
        except FoundryError as e:
            return Response("", ok=False, error=str(e), model=self.deployment, model_source="requested")
        worst = self._worst(s["model"])
        token = self.ledger.reserve(self.deployment, worst)
        if token is None:
            spent = self.ledger.spent(self.deployment)
            return Response("", ok=False, error=f"{self.vendor} spend cap reached: ${spent:.2f} spent of "
                            f"${self.ledger.cap:.2f} for {self.deployment} (next call reserves ${worst:.2f})")
        self.calls += 1
        r = None
        try:
            r = self._complete(prompt, on_text, raw_path, self.timeout if timeout is None else timeout, s, bearer)
            return r
        finally:
            usage = (r.usage if r else {}) or {}
            cost = (self._price(s["model"], usage) if usage.get("completion_tokens") is not None
                    else min(worst, self._unreported(s["model"], prompt, usage, worst)))
            total = self.ledger.settle(self.deployment, token, cost)
            if r is not None:
                r.cost_usd = round(cost, 6)
                r.notes.append(f"{self.vendor} spend: ${cost:.4f} this call, ${total:.2f} of ${self.ledger.cap:.2f} "
                               f"for {self.deployment}" + ("" if usage.get("completion_tokens") is not None
                                                           else f" (no usage reported: {self._unreported_note})"))

    _unreported_note = "charged worst case"

    def _unreported(self, model: str, prompt: str, usage: dict, worst: float) -> float:
        """What to book for a call that ended without a usage report (a timeout or a dropped stream)."""
        return worst

    def _complete(self, prompt: str, on_text: OnText | None, raw_path: str, limit: float, s: dict,
                  bearer: str) -> Response:
        body = json.dumps(self.request_body(prompt)).encode()
        conn = self.connection(s["host"], timeout=limit + 30)
        fired = threading.Event()
        sock = []  # the request's socket: http.client drops conn.sock when the server says Connection: close
                   # (fm serve does), and the response keeps reading from it, so shut that socket down

        def on_timeout() -> None:
            fired.set()
            for so in (*sock, conn.sock):
                try:
                    so and so.shutdown(2)
                except OSError:
                    pass

        t0 = time.perf_counter()
        timer = threading.Timer(limit, on_timeout)
        timer.start()
        text, reasoning, notes = [], [], []
        reasoning_deltas = 0
        first, deltas, killed, done, error, refusal, model, finish, usage = None, 0, False, False, "", "", "", None, {}
        raw = open(raw_path, "w") if raw_path else None

        def emit(chunk: str) -> bool:
            nonlocal first, deltas
            if not chunk:
                return True
            if first is None:
                first = time.perf_counter() - t0
            deltas += 1
            text.append(chunk)
            return not (on_text is not None and on_text(chunk) is False)

        try:
            conn.request("POST", s["route"], body=body, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {bearer}"})
            sock.append(conn.sock)
            if fired.is_set():  # the limit passed while the request was being sent
                on_timeout()
            resp = conn.getresponse()
            if resp.status != 200:
                data = resp.read()[:2000].decode(errors="replace")
                if raw:
                    raw.write(data)
                # nothing was generated: a rejected request (429 rate limit, 5xx, ...) is not billed, and a
                # prompt blocked by the input filter is billed for the prompt only (estimated: 3 bytes a token)
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "http_status": resp.status}
                if resp.status == 400 and ("content_filter" in data or "SafetyBlocked" in data
                                           or "ResponsibleAIPolicyViolation" in data):
                    refusal = f"content_filter: prompt blocked before generation: {data[:200]}"
                    usage["prompt_tokens"] = len(body) // 3
                else:
                    error = f"{self.vendor} HTTP {resp.status}: {data[:500]}"
            else:
                for line in resp:
                    if raw:
                        raw.write(line.decode(errors="replace"))
                    line = line.strip()
                    if not line or line.startswith(b":"):
                        continue
                    if line.startswith((b"event:", b"id:", b"retry:")):
                        continue
                    if not line.startswith(b"data:"):
                        error = f"isolation: unexpected stream line {line[:120]!r}"
                        break
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        done = True
                        break
                    try:
                        ev = json.loads(data)
                    except ValueError:
                        error = f"isolation: unparseable stream chunk {data[:120]!r}"
                        break
                    if ev.get("error"):
                        err = ev["error"]
                        kind = str(err.get("type") or err.get("code") or "") if isinstance(err, dict) else ""
                        if "SafetyBlocked" in kind or "content_filter" in kind:
                            refusal = f"content_filter: {kind}: {str(err)[:200]}"
                        else:
                            error = f"{self.vendor} stream error: {str(err)[:400]}"
                        continue  # [DONE] follows
                    if ev.get("model"):
                        model = str(ev["model"])
                        if not model.lower().startswith(s["model"]) and model not in s.get("served_as", ()):
                            error = (f"isolation: model rerouted: deployment {self.deployment} serves {s['model']}, "
                                     f"stream reported {model}")
                            break
                    if ev.get("usage"):
                        usage = ev["usage"]
                    for ch in ev.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("tool_calls") or d.get("function_call"):
                            error = "isolation: the model emitted a tool call"
                            break
                        for k in ("reasoning_content", "reasoning"):
                            if isinstance(d.get(k), str) and d[k]:
                                reasoning.append(d[k])
                                reasoning_deltas += 1
                        if isinstance(d.get("refusal"), str) and d["refusal"]:  # OpenAI-style refusal field
                            refusal = f"refusal: {d['refusal'][:200]}"
                        if d.get("content") and not emit(d["content"]):
                            killed = True
                            break
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                    if error or killed:
                        break
        except OSError as e:
            if not fired.is_set():
                error = f"{self.vendor} unreachable at {s['host']}: {e}"
        finally:
            timer.cancel()
            conn.close()  # on an early abort this ends the generation
            if raw:
                raw.close()
        wall = time.perf_counter() - t0
        timed_out = fired.is_set() and not killed
        if finish == "content_filter" and not refusal:
            refusal = "content_filter: finish_reason content_filter"
        if timed_out:
            error = "timeout"
        elif refusal:
            error = ""
        elif not error and not killed and not done:
            error = f"{self.vendor} closed the stream before [DONE]"
        elif not error and not killed and finish == "length":
            error = f"isolation: the answer reached the model's output cap (finish_reason length; usage {usage})"
        usage = dict(usage or {})
        usage["finish_reason"] = finish
        usage["streamed_deltas"] = deltas + reasoning_deltas  # what arrived, for a call that reports no usage
        if reasoning:
            notes.append(f"reasoning not scored ({len(''.join(reasoning))} chars): {''.join(reasoning)[:200]}")
        if self.effort is not None:
            notes.append(f"reasoning_effort={self.effort}")
        if refusal:
            return Response("".join(text), ok=False, refusal=refusal[:300], model=model or s["model"],
                            model_source="reported" if model else "requested", wall_s=wall, first_text_s=first,
                            deltas=deltas, usage=usage, raw_path=raw_path, notes=notes)
        ok = not error or (killed and not error.startswith("isolation:"))
        return Response("".join(text), ok=ok and not timed_out, killed=killed, timed_out=timed_out,
                        error="" if ok and not timed_out else error, model=model or s["model"],
                        model_source="reported" if model else "requested", wall_s=wall, first_text_s=first,
                        deltas=deltas, usage=usage, cost_usd=None, raw_path=raw_path, notes=notes)
