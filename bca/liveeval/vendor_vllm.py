"""Open-weight models served by vLLM's OpenAI-compatible server: `vllm:<served model name>`.

The counterpart of vendor_local (Ollama) for models Ollama cannot run (e.g. Aleph Alpha's Kolibri-1, which
needs vLLM plus Aleph Alpha's plugin). No agent CLI is involved, so the fairness contract holds by
construction and is checked per call:

- The request is ours, byte for byte: POST /v1/chat/completions, `stream: true` (with usage), messages =
  [system: SYSTEM_PROMPT, user: prompt], no `tools`, no `tool_choice`, no `response_format`, no sampling
  fields. Sampling is the model's own defaults (vLLM applies the checkpoint's generation_config.json),
  recorded at setup; the analogue of a vendor CLI's default settings.
- Effort: `--effort` sends `chat_template_kwargs.reasoning_effort` (Kolibri's levels: none, low, medium,
  high; `none` also sends `enable_thinking: false`). Without --effort nothing is sent and the template's
  default applies ("model-default"; high for Kolibri).
- The context window: the server's `max_model_len` (from /v1/models) bounds prompt + answer. No
  `max_tokens` is sent, so the answer may use the whole window; a call that ends with finish_reason
  "length" hit the window and fails closed (`isolation:`), as vendor_local does at num_ctx.
- The served model is verified: /v1/models must list it, and every stream chunk must name it. The server
  version must be an audited one (/version).
- Reasoning: the server's reasoning parser returns it in `reasoning_content` / `reasoning` deltas, and a
  leading <think>...</think> block in the content is held back too (vendor_local._ThinkFilter). Either
  is recorded in notes, never scored. A tool call in the stream fails closed.
- Server: BCA_VLLM_URL (default http://127.0.0.1:8000). Calls are serialised per server through a
  cross-process file lock (concurrent requests would slow each other down), and the per-call clock
  starts once the lock is held.
- Server setup (docs: reports/cross-vendor-methodology.md, "Local models"): container bca-vllm-kolibri,
  127.0.0.1:8000, weights from the bca-hf-models volume, HF_HUB_OFFLINE=1, `--max-model-len 65536`,
  a reasoning parser and no tool parser.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import os
import threading
import time
from urllib.parse import urlparse

from bca.liveeval.subject import SYSTEM_PROMPT, OnText, Response, Subject
from bca.liveeval.vendor_local import _ThinkFilter

VLLM_URL = os.environ.get("BCA_VLLM_URL", "http://127.0.0.1:8000")
AUDITED_VERSIONS = ("0.29.0",)
MODEL_DEFAULT = "model-default"
EFFORTS = ("none", "low", "medium", "high")


class VllmError(RuntimeError):
    pass


class VllmSubject(Subject):
    vendor = "vllm"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, url: str | None = None,
                 budget=None):
        if not model:
            raise ValueError("vllm: name the served model, e.g. vllm:Aleph-Alpha/Kolibri-1")
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"vllm: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        self.model = model
        self.name = f"vllm:{model}"
        self.timeout = timeout
        self.effort = effort
        self.budget = budget
        u = urlparse(url or VLLM_URL)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 8000
        self._lock = threading.Lock()
        self._lockfile = f"/tmp/bca-vllm-{self.host}-{self.port}.lock"
        self._setup: dict | None = None

    # -- server metadata (no model call) -----------------------------------------------------------

    def _http(self, method: str, path: str, timeout: float = 60.0):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path)
            r = conn.getresponse()
            data = r.read()
        except OSError as e:
            raise VllmError(f"vllm server unreachable at {self.host}:{self.port}: {e}") from None
        finally:
            conn.close()
        try:
            obj = json.loads(data or b"{}")
        except ValueError:
            obj = {"error": data[:300].decode(errors="replace")}
        if r.status != 200:
            raise VllmError(f"vllm server {path} HTTP {r.status}: {obj}")
        return obj

    def setup(self) -> dict:
        """Server version, the served model's entry and context window; cached."""
        if self._setup is None:
            version = self._http("GET", "/version").get("version", "")
            if version not in AUDITED_VERSIONS:
                raise VllmError(f"isolation: vLLM {version!r} is not an audited version "
                                f"({', '.join(AUDITED_VERSIONS)}); re-audit before running")
            models = {m.get("id"): m for m in self._http("GET", "/v1/models").get("data", [])}
            if self.model not in models:
                raise VllmError(f"vllm model {self.model!r} not served (have {', '.join(sorted(map(str, models)))})")
            m = models[self.model]
            ctx = m.get("max_model_len")
            if not isinstance(ctx, int):
                raise VllmError(f"isolation: cannot read max_model_len of {self.model}")
            self._setup = {"version": version, "max_model_len": ctx, "root": m.get("root")}
        return self._setup

    @property
    def cli_version(self) -> str:
        try:
            return f"vllm {self.setup()['version']}"
        except VllmError:
            return "vllm unknown"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.effort or MODEL_DEFAULT, "cli_version": f"vllm {s['version']}", "vendor": "vllm",
                "vllm": {"max_model_len": s["max_model_len"], "root": s["root"],
                         "sampling": "generation_config.json defaults", "server": f"{self.host}:{self.port}"}}

    # -- one call ----------------------------------------------------------------------------------

    def request_body(self, prompt: str) -> dict:
        body = {"model": self.model, "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
        if self.effort is not None:
            kw = {"reasoning_effort": self.effort}
            if self.effort == "none":
                kw["enable_thinking"] = False
            body["chat_template_kwargs"] = kw
        return body

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        if self.budget is not None and not self.budget():
            return Response("", ok=False, error="call budget exhausted")
        with self._lock, open(self._lockfile, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                return self._complete(prompt, on_text, raw_path, self.timeout if timeout is None else timeout)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _complete(self, prompt: str, on_text: OnText | None, raw_path: str, limit: float) -> Response:
        try:
            s = self.setup()
        except VllmError as e:
            return Response("", ok=False, error=str(e), model=self.model, model_source="requested")
        self.calls += 1
        body = json.dumps(self.request_body(prompt)).encode()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=limit + 30)
        fired = threading.Event()

        def on_timeout() -> None:
            fired.set()
            try:
                conn.sock and conn.sock.shutdown(2)
            except OSError:
                pass

        t0 = time.perf_counter()
        timer = threading.Timer(limit, on_timeout)
        timer.start()
        text, reasoning, notes = [], [], []
        filt = _ThinkFilter()
        first, deltas, killed, done, error, model, finish, usage = None, 0, False, False, "", "", None, {}
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
            conn.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            if r.status != 200:
                error = f"vllm server /v1/chat/completions HTTP {r.status}: {r.read()[:500].decode(errors='replace')}"
            else:
                for line in r:
                    if raw:
                        raw.write(line.decode(errors="replace"))
                    line = line.strip()
                    if not line or line.startswith(b":"):
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
                        error = f"vllm server error: {ev['error']}"
                        break
                    if ev.get("model"):
                        model = ev["model"]
                        if model != self.model:
                            error = f"isolation: model rerouted: requested {self.model}, stream reported {model}"
                            break
                    if ev.get("usage"):
                        usage = ev["usage"]
                    for ch in ev.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("tool_calls"):
                            error = "isolation: the model emitted a tool call"
                            break
                        for k in ("reasoning_content", "reasoning"):
                            if d.get(k):
                                reasoning.append(d[k])
                        if d.get("content") and not emit(filt.feed(d["content"])):
                            killed = True
                            break
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                    if error or killed:
                        break
                if not killed and not error and done and not emit(filt.flush()):
                    killed = True
        except OSError as e:
            if not fired.is_set():
                error = f"vllm server unreachable at {self.host}:{self.port}: {e}"
        finally:
            timer.cancel()
            conn.close()  # on an early abort this cancels the generation server-side
            if raw:
                raw.close()
        wall = time.perf_counter() - t0
        timed_out = fired.is_set() and not killed
        if timed_out:
            error = "timeout"
        elif not error and not killed and not done:
            error = "vllm server closed the stream before [DONE]"
        if not error and not killed and finish == "length":
            error = (f"isolation: the answer reached the {s['max_model_len']}-token context window "
                     f"(finish_reason length; usage {usage})")
        usage = dict(usage or {})
        usage["finish_reason"] = finish
        thinking = "".join(reasoning) + filt.thinking
        if thinking:
            notes.append(f"reasoning not scored ({len(thinking)} chars): {thinking[:200]}")
        if self.effort == "none" and thinking and not error:
            error, killed = (f"isolation: reasoning was off (effort none) but the model returned {len(thinking)} "
                             "chars of reasoning: the server did not honour the request"), False
        if self.effort is not None:
            notes.append(f"reasoning_effort={self.effort}")
        ok = not error or (killed and not error.startswith("isolation:"))
        return Response("".join(text), ok=ok and not timed_out, killed=killed, timed_out=timed_out,
                        error="" if ok and not timed_out else error, model=model or self.model,
                        model_source="reported" if model else "requested", wall_s=wall, first_text_s=first,
                        deltas=deltas, usage=usage, cost_usd=None, raw_path=raw_path, notes=notes)
