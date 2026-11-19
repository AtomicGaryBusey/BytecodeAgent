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
  is recorded in notes, never scored. A model in FINAL_MARKERS (Apriel) writes its reasoning as plain text and
  its answer after a marker: everything before the marker is held back the same way (_FinalMarkerFilter). A tool call in the stream fails closed.
- Token-budget mode (`vllm:<model>@budget`, bca.liveeval.token_budget): the call's limit is a number of
  output tokens per battery (the most any vendor model used for one call there), sent as `max_tokens`,
  instead of the battery's wall-clock limit (a generous wall-clock ceiling remains). Reaching it is a
  timeout, recorded and scored as one, with the same error text; the budget goes into the notes. A
  separate subject (its own directory and table row).
- Server: BCA_VLLM_URL (default http://127.0.0.1:8000; https is supported). BCA_VLLM_KEY_FILE names a file holding a
  bearer key for a gateway in front of the server (e.g. a LiteLLM route): it must exist, be non-empty and be readable
  by its owner only (mode 0600), or every call fails closed; the key goes in the Authorization header and is never
  logged or recorded. A gateway hides vLLM's /version and /v1/models' max_model_len: BCA_VLLM_DECLARED_VERSION and
  BCA_VLLM_DECLARED_MAX_MODEL_LEN then carry the operator's stated values (recorded as declared, not observed). Calls are serialised per server through a
  cross-process file lock (concurrent requests would slow each other down), and the per-call clock
  starts once the lock is held. BCA_VLLM_SLOTS=N allows N calls at once (N locks): on a memory-bandwidth-bound
  server batch decoding costs each call almost nothing (Apriel on the GB10, 2026-11-14: 7.9 tok/s alone, 7.8 each
  at five at once), so a token-budget run may use it; the slot count is recorded in describe().
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
from bca.liveeval.token_budget import CURRENT_BATTERY, token_budget, wall_ceiling
from bca.liveeval.vendor_local import _ThinkFilter

VLLM_URL = os.environ.get("BCA_VLLM_URL", "http://127.0.0.1:8000")
AUDITED_VERSIONS = ("0.29.0", "0.31.0+88641915.nv26.10")  # 0.31.0: NVIDIA 26.10 build behind the Aurora LiteLLM gateway, audited 2026-11-16
MODEL_DEFAULT = "model-default"
EFFORTS = ("none", "low", "medium", "high")


class VllmError(RuntimeError):
    pass


# Models whose answer follows a plain-text marker instead of a <think> block, and that vLLM 0.29.0 has no reasoning
# parser for. Apriel-1.6-15B-Thinker (ServiceNow, MIT): its chat template asks for step-by-step reasoning, "then
# provide the final response after the marker [BEGIN FINAL RESPONSE]"; the model card's own example takes the text
# between that marker and <|end|>. (The card's vLLM route is an unmerged parser in a third-party image: not used.)
FINAL_MARKERS = {"apriel": "[BEGIN FINAL RESPONSE]"}
_FINAL_TAIL = ("<|end|>", "[END FINAL RESPONSE]")


def final_marker(model: str) -> str | None:
    m = model.lower()
    return next((mark for key, mark in FINAL_MARKERS.items() if key in m), None)


class _FinalMarkerFilter:
    """Hold back everything before the model's final-response marker (recorded as reasoning, never scored); the
    answer after it is released whole at the end, without the surrounding blank lines or a trailing end tag (a
    split stream could otherwise leak part of either). No marker at all: the whole output was reasoning, so the
    answer is empty and scored as such (the model never gave one)."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.buf = ""
        self.thinking = ""

    def feed(self, s: str) -> str:
        self.buf += s
        return ""

    def flush(self) -> str:
        i = self.buf.find(self.marker)
        if i < 0:
            self.thinking, self.buf = self.buf, ""
            return ""
        self.thinking, out, self.buf = self.buf[:i], self.buf[i + len(self.marker):].strip(), ""
        for t in _FINAL_TAIL:
            if out.endswith(t):
                out = out[: -len(t)].rstrip()
        return out


class VllmSubject(Subject):
    vendor = "vllm"
    efforts: tuple[str, ...] = EFFORTS

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, url: str | None = None,
                 budget=None):
        if not model:
            raise ValueError("vllm: name the served model, e.g. vllm:Aleph-Alpha/Kolibri-1")
        base, sep, suffix = model.rpartition("@")
        self.budget_mode = bool(sep) and suffix == "budget" and bool(base)
        if sep and not self.budget_mode:
            raise ValueError(f"vllm: unknown suffix @{suffix}; use vllm:<model>@budget for token-budget mode")
        if self.budget_mode:
            model = base
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"vllm: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        self.model = model
        self.name = f"vllm:{model}" + ("@budget" if self.budget_mode else "")
        self.timeout = timeout
        self.effort = effort
        self.budget = budget
        u = urlparse(url or VLLM_URL)
        self.https = u.scheme == "https"
        self.host, self.port = u.hostname or "127.0.0.1", u.port or (443 if self.https else 8000)
        self.base = (u.path or "").rstrip("/").removesuffix("/v1")  # a gateway may serve under a path prefix
        self.key_file = os.environ.get("BCA_VLLM_KEY_FILE", "")
        self.slots = max(1, int(os.environ.get("BCA_VLLM_SLOTS", "1")))
        self._lock = threading.BoundedSemaphore(self.slots)
        self._lockfile = f"/tmp/bca-vllm-{self.host}-{self.port}.lock"  # slot k > 0: <lockfile>.<k>
        self._setup: dict | None = None

    # -- server metadata (no model call) -----------------------------------------------------------

    def _conn(self, timeout: float):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        """Authorization for a gateway (BCA_VLLM_KEY_FILE); fails closed on a missing, empty or exposed key file."""
        if not self.key_file:
            return {}
        path = os.path.expanduser(self.key_file)
        try:
            st = os.stat(path)
            key = open(path).read().strip()
        except OSError as e:
            raise VllmError(f"vllm key file {self.key_file} unreadable: {type(e).__name__}") from None
        if st.st_mode & 0o077:
            raise VllmError(f"vllm key file {self.key_file} is readable by others (mode {st.st_mode & 0o777:o}); chmod 600")
        if not key:
            raise VllmError(f"vllm key file {self.key_file} is empty")
        return {"Authorization": f"Bearer {key}"}

    def _http(self, method: str, path: str, timeout: float = 60.0):
        headers = self._headers()
        conn = self._conn(timeout)
        try:
            conn.request(method, self.base + path, headers=headers)
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
            declared = os.environ.get("BCA_VLLM_DECLARED_VERSION", "")
            if declared:  # behind a gateway (LiteLLM) that hides vLLM's /version: the operator's stated version
                version = declared
            else:
                version = self._http("GET", "/version").get("version", "")
            if version not in AUDITED_VERSIONS:
                raise VllmError(f"isolation: vLLM {version!r} is not an audited version "
                                f"({', '.join(AUDITED_VERSIONS)}); re-audit before running")
            models = {m.get("id"): m for m in self._http("GET", "/v1/models").get("data", [])}
            if self.model not in models:
                raise VllmError(f"vllm model {self.model!r} not served (have {', '.join(sorted(map(str, models)))})")
            m = models[self.model]
            ctx = m.get("max_model_len")
            if ctx is None and os.environ.get("BCA_VLLM_DECLARED_MAX_MODEL_LEN"):  # a gateway's /v1/models omits it
                ctx = int(os.environ["BCA_VLLM_DECLARED_MAX_MODEL_LEN"])
            if not isinstance(ctx, int):
                raise VllmError(f"isolation: cannot read max_model_len of {self.model}")
            self._setup = {"version": version, "max_model_len": ctx, "root": m.get("root"),
                           **({"declared_by_operator": True} if declared else {})}
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
                         "sampling": "generation_config.json defaults", "server": "private gateway (BCA_VLLM_URL, not recorded)" if self.key_file else f"{self.host}:{self.port}",
                         "limit": "token budget per battery (bca.liveeval.token_budget)" if self.budget_mode
                         else "wall clock per battery",
                         "concurrent_calls": self.slots}}

    # -- one call ----------------------------------------------------------------------------------

    def request_body(self, prompt: str, max_tokens: int | None = None) -> dict:
        body = {"model": self.model, "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
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
        with self._lock:
            lf = self._acquire_slot()
            try:
                budget = token_budget(CURRENT_BATTERY.get()) if self.budget_mode else None
                limit = wall_ceiling(budget) if budget else (self.timeout if timeout is None else timeout)
                return self._complete(prompt, on_text, raw_path, limit, budget)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
                lf.close()

    def _acquire_slot(self):
        """One of the server's `slots` cross-process locks (BCA_VLLM_SLOTS, default 1: calls serialised)."""
        names = [self._lockfile] + [f"{self._lockfile}.{k}" for k in range(1, self.slots)]
        if self.slots == 1:
            lf = open(names[0], "a")
            fcntl.flock(lf, fcntl.LOCK_EX)
            return lf
        while True:
            for name in names:
                lf = open(name, "a")
                try:
                    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return lf
                except BlockingIOError:
                    lf.close()
            time.sleep(0.5)

    def _complete(self, prompt: str, on_text: OnText | None, raw_path: str, limit: float,
                  budget: int | None = None) -> Response:
        try:
            s = self.setup()
        except VllmError as e:
            return Response("", ok=False, error=str(e), model=self.model, model_source="requested")
        self.calls += 1
        body = json.dumps(self.request_body(prompt, budget)).encode()
        try:
            headers = {"Content-Type": "application/json", **self._headers()}
        except VllmError as e:
            return Response("", ok=False, error=str(e), model=self.model, model_source="requested")
        conn = self._conn(limit + 30)
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
        mark = final_marker(self.model)
        filt = _FinalMarkerFilter(mark) if mark else _ThinkFilter()
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

        cut = None  # an unparseable chunk: a cut-off stream if nothing follows it, else a protocol break
        try:
            conn.request("POST", self.base + "/v1/chat/completions", body=body, headers=headers)
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
                    if cut is not None:
                        error = f"isolation: unparseable stream chunk {cut[:120]!r}"
                        break
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
                        cut = data  # judged by what follows (above) or by the stream ending (below)
                        continue
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
            error = "vllm server closed the stream before [DONE]" + (" (cut off mid-chunk)" if cut is not None else "")
        out_tokens = (usage or {}).get("completion_tokens")
        if not error and not killed and finish == "length" and budget and isinstance(out_tokens, int) \
                and out_tokens >= budget:
            timed_out, error = True, "timeout"  # the token budget: scored as a timeout, same error text
            notes.append(f"token budget reached: {out_tokens} of {budget} output tokens")
        elif not error and not killed and finish == "length":
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
        if budget:
            notes.append(f"token budget {budget} (battery {CURRENT_BATTERY.get()})")
        ok = not error or (killed and not error.startswith("isolation:"))
        return Response("".join(text), ok=ok and not timed_out, killed=killed, timed_out=timed_out,
                        error="" if ok and not timed_out else error, model=model or self.model,
                        model_source="reported" if model else "requested", wall_s=wall, first_text_s=first,
                        deltas=deltas, usage=usage, cost_usd=None, raw_path=raw_path, notes=notes)
