"""Local open-weight models served by Ollama on this machine: `local:<ollama model>`.

There is no agent CLI in the way, so the fairness contract holds by construction and is checked per call:

- The request is ours, byte for byte: POST /api/chat, `stream: true`, messages = [system: SYSTEM_PROMPT,
  user: prompt], no `tools`, no `format`, options = {num_ctx} only. Sampling is the model's own Modelfile
  defaults (the analogue of a vendor CLI's default settings), read from /api/show and recorded.
- The context window is set explicitly (Ollama's default would silently truncate a long prompt):
  min(the model's native context, LOCAL_MAX_CTX). A call whose prompt plus answer reached the window
  (prompt_eval_count + eval_count >= num_ctx: Ollama would have truncated or shifted the context) fails
  closed.
- The served model is verified: the stream must name the requested model and, after the call, /api/ps
  must show it loaded under the digest recorded at setup, with our context length. Anything else is an
  `isolation:` error (fatal).
- Reasoning: a `thinking` field or a leading <think>...</think> block is recorded in notes, never scored.
  Thinking on/off is chosen per subject with a spec suffix: `local:<model>@think` / `local:<model>@nothink`
  sends Ollama's `think: true|false` (only for a model whose /api/show capabilities include `thinking`);
  without a suffix the field is not sent and the model's template default applies ("model-default").
  With @nothink, any reasoning in the answer (the `thinking` field or a <think> block) fails closed: the
  server did not honour the request. With @think, a call that returned no reasoning is noted. Ollama's
  eval_count counts the reasoning tokens too, so the context-window check below includes them.
- Server: BCA_OLLAMA_URL (default http://127.0.0.1:11435) picks the Ollama server for the whole run; the
  second server for downloaded models (`bca-ollama2`, writable volume) is http://127.0.0.1:11436.
- Timing. The server runs one request at a time (OLLAMA_NUM_PARALLEL=1), so calls are serialised per
  server through a cross-process file lock, and the per-call clock starts only once the lock is held and
  the model is resident: queueing behind other calls and loading the weights (35-50 s) never count
  against a cell. A model that is not loaded is warmed up (an empty /api/generate) before the clock starts.
- Server setup (docs: reports/cross-vendor-methodology.md, "Local models"): the `bca-ollama` container,
  127.0.0.1:11435, model volume mounted read-only, OLLAMA_NO_CLOUD=1 (no request can be forwarded to
  ollama.com), OLLAMA_NUM_PARALLEL=1. The server version must be an audited one.
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

LOCAL_URL = os.environ.get("BCA_OLLAMA_URL", "http://127.0.0.1:11435")
LOCAL_MAX_CTX = 65536  # enough for prompt + the longest answer the 900 s app timeout allows at ~50 tok/s
AUDITED_VERSIONS = ("0.30.10",)
KEEP_ALIVE = "60m"
MODEL_DEFAULT_SAMPLING = "model-default"  # recorded as the effort: no effort knob, Modelfile sampling
THINK_SUFFIXES = {"think": True, "nothink": False}  # local:<model>@think | @nothink


class LocalError(RuntimeError):
    pass


class _ThinkFilter:
    """Pass answer text through; hold back a leading <think>...</think> block (recorded, not scored)."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.state = "start"  # start -> (think ->) answer
        self.buf = ""
        self.thinking = ""

    def feed(self, s: str) -> str:
        if self.state == "answer":
            return s
        self.buf += s
        if self.state == "start":
            lead = self.buf.lstrip()
            if not lead:
                return ""
            if lead.startswith(self.OPEN):
                self.state, self.buf = "think", lead[len(self.OPEN):]
            elif self.OPEN.startswith(lead):
                return ""  # could still become <think>
            else:
                out, self.buf, self.state = self.buf, "", "answer"
                return out
        if self.state == "think":
            i = self.buf.find(self.CLOSE)
            if i < 0:
                return ""
            self.thinking += self.buf[:i]
            out, self.buf, self.state = self.buf[i + len(self.CLOSE):].lstrip("\n"), "", "answer"
            return out
        return ""

    def flush(self) -> str:
        if self.state == "think":  # never closed: all of it was reasoning
            self.thinking += self.buf
            self.buf = ""
            return ""
        out, self.buf = self.buf, ""
        return out


class LocalSubject(Subject):
    vendor = "local"
    efforts: tuple[str, ...] = ()

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, url: str | None = None,
                 budget=None):
        if effort is not None:
            raise ValueError("local: no effort levels (the model's own sampling defaults are used); omit --effort")
        if not model:
            raise ValueError("local: name the Ollama model, e.g. local:qwen2.5:14b")
        self.think: bool | None = None
        base, sep, suffix = model.rpartition("@")
        if sep:
            if suffix not in THINK_SUFFIXES or not base:
                raise ValueError(f"local: unknown suffix @{suffix}; use local:<model>@think or local:<model>@nothink")
            model, self.think = base, THINK_SUFFIXES[suffix]
        self.model = model
        self.name = f"local:{model}" + ("" if self.think is None else "@" + ("think" if self.think else "nothink"))
        self.timeout = timeout
        self.effort = None
        self.budget = budget
        u = urlparse(url or LOCAL_URL)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 11434
        self._lock = threading.Lock()  # one call at a time from this process ...
        self._lockfile = f"/tmp/bca-local-{self.host}-{self.port}.lock"  # ... and across processes
        self._setup: dict | None = None

    # -- server metadata (no model call) -----------------------------------------------------------

    def _http(self, method: str, path: str, body: dict | None = None, timeout: float = 60.0):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            data = r.read()
        except OSError as e:
            raise LocalError(f"local server unreachable at {self.host}:{self.port}: {e}") from None
        finally:
            conn.close()
        try:
            obj = json.loads(data or b"{}")
        except ValueError:
            obj = {"error": data[:300].decode(errors="replace")}
        if r.status != 200:
            raise LocalError(f"local server {path} HTTP {r.status}: {obj.get('error', obj)}")
        return obj

    def setup(self) -> dict:
        """Version, digest, native context, Modelfile sampling; cached. Raises LocalError / isolation errors."""
        if self._setup is None:
            version = self._http("GET", "/api/version").get("version", "")
            if version not in AUDITED_VERSIONS:
                raise LocalError(f"isolation: Ollama {version!r} is not an audited version "
                                 f"({', '.join(AUDITED_VERSIONS)}); re-audit before running")
            tags = {m["name"]: m for m in self._http("GET", "/api/tags").get("models", [])}
            if self.model not in tags:
                raise LocalError(f"local model {self.model!r} not found on the server (have {', '.join(sorted(tags))})")
            show = self._http("POST", "/api/show", {"model": self.model})
            native = next((v for k, v in (show.get("model_info") or {}).items() if k.endswith(".context_length")), None)
            if not isinstance(native, int):
                raise LocalError(f"isolation: cannot read the native context length of {self.model}")
            d = tags[self.model].get("details") or {}
            caps = show.get("capabilities")
            if self.think is not None and not (isinstance(caps, list) and "thinking" in caps):
                raise LocalError(f"isolation: {self.model} does not report the `thinking` capability ({caps}); "
                                 "omit @think/@nothink")
            self._setup = {
                "version": version, "digest": tags[self.model]["digest"], "native_ctx": native,
                "num_ctx": min(native, LOCAL_MAX_CTX), "sampling": (show.get("parameters") or "").strip(),
                "modelfile_system": bool(show.get("system")), "quantization": d.get("quantization_level"),
                "parameter_size": d.get("parameter_size"), "family": d.get("family"),
                "capabilities": caps if isinstance(caps, list) else None,
            }
        return self._setup

    @property
    def cli_version(self) -> str:
        try:
            return f"ollama {self.setup()['version']}"
        except LocalError:
            return "ollama unknown"

    def describe(self) -> dict:
        s = self.setup()
        return {"effort": self.think_label, "cli_version": f"ollama {s['version']}", "vendor": "local",
                "local": {"digest": s["digest"], "num_ctx": s["num_ctx"], "native_ctx": s["native_ctx"],
                          "quantization": s["quantization"], "parameter_size": s["parameter_size"],
                          "sampling": s["sampling"] or "ollama defaults", "think": self.think,
                          "server": f"{self.host}:{self.port}"}}

    @property
    def think_label(self) -> str:
        return MODEL_DEFAULT_SAMPLING if self.think is None else ("think" if self.think else "no-think")

    def _resident(self) -> dict | None:
        for m in self._http("GET", "/api/ps").get("models", []):
            if m.get("name") == self.model:
                return m
        return None

    def _warm(self, s: dict) -> None:
        """Load the model with our context length (outside the timed call)."""
        m = self._resident()
        if m and m.get("context_length") == s["num_ctx"]:
            return
        self._http("POST", "/api/generate", {"model": self.model, "prompt": "", "keep_alive": KEEP_ALIVE,
                                             "options": {"num_ctx": s["num_ctx"]}}, timeout=600)

    # -- one call ----------------------------------------------------------------------------------

    def request_body(self, prompt: str) -> dict:
        s = self.setup()
        body = {"model": self.model, "stream": True, "keep_alive": KEEP_ALIVE,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                "options": {"num_ctx": s["num_ctx"]}}
        if self.think is not None:
            body["think"] = self.think
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
            self._warm(s)
        except LocalError as e:
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
        text, thinking_field, notes = [], [], []
        filt = _ThinkFilter()
        first, deltas, killed, final, error, model = None, 0, False, None, "", ""
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
            conn.request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            if r.status != 200:
                data = r.read()[:500].decode(errors="replace")
                try:
                    data = json.loads(data).get("error", data)
                except ValueError:
                    pass
                error = f"local server /api/chat HTTP {r.status}: {data}"
            else:
                for line in r:
                    if raw:
                        raw.write(line.decode(errors="replace"))
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        error = f"isolation: unparseable stream line {line[:120]!r}"
                        break
                    if ev.get("error"):
                        error = f"local server error: {ev['error']}"
                        break
                    model = ev.get("model", model)
                    msg = ev.get("message") or {}
                    if msg.get("tool_calls"):
                        error = "isolation: the model emitted a tool call"
                        break
                    if msg.get("thinking"):
                        thinking_field.append(msg["thinking"])
                    if msg.get("content") and not emit(filt.feed(msg["content"])):
                        killed = True
                        break
                    if ev.get("done"):
                        final = ev
                        break
                if not killed and not error and final is not None and not emit(filt.flush()):
                    killed = True
        except OSError as e:
            if not fired.is_set():
                error = f"local server unreachable at {self.host}:{self.port}: {e}"
        finally:
            timer.cancel()
            conn.close()  # on an early abort this cancels the generation server-side
            if raw:
                raw.close()
        wall = time.perf_counter() - t0
        timed_out = fired.is_set() and not killed
        if timed_out:
            error = "timeout"
        elif not error and not killed and final is None:
            error = "local server closed the stream before `done`"
        usage = {}
        if final is not None:
            usage = {k: final.get(k) for k in ("prompt_eval_count", "eval_count", "prompt_eval_duration",
                                               "eval_duration", "load_duration", "total_duration")}
            usage["done_reason"] = final.get("done_reason")
            p, e = final.get("prompt_eval_count") or 0, final.get("eval_count") or 0
            if p + e >= s["num_ctx"]:
                error = (f"isolation: prompt ({p}) + answer ({e}) tokens reached the {s['num_ctx']}-token "
                         "context window: Ollama may have truncated or shifted it")
        if not error or killed or timed_out:  # verify what served the call (also after an abort/timeout)
            try:
                v = self._verify(s, model)
            except LocalError as ex:
                v = str(ex)
            if v:
                error, killed = v, False
        thinking = "".join(thinking_field) + filt.thinking
        if thinking:
            notes.append(f"reasoning not scored ({len(thinking)} chars): {thinking[:200]}")
        if self.think is False and thinking and not error:
            error, killed = (f"isolation: thinking was off (think=false) but the model returned "
                             f"{len(thinking)} chars of reasoning: the server did not honour the request"), False
        elif self.think is True and not thinking and (final is not None or killed):
            notes.append("think=true: no reasoning was returned")
        if self.think is not None:
            notes.append(f"think={'true' if self.think else 'false'}")
        ok = not error or (killed and not error.startswith("isolation:"))
        return Response("".join(text), ok=ok and not timed_out, killed=killed, timed_out=timed_out,
                        error="" if ok and not timed_out else error, model=model or self.model,
                        model_source="reported" if model else "requested", wall_s=wall, first_text_s=first,
                        deltas=deltas, usage=usage, cost_usd=None, raw_path=raw_path, notes=notes)

    def _verify(self, s: dict, served: str) -> str:
        if served and served != self.model:
            return f"isolation: model rerouted: requested {self.model}, stream reported {served}"
        m = self._resident()
        if m is None:
            return f"isolation: {self.model} is not resident after the call (cannot verify the digest)"
        if m.get("digest") != s["digest"]:
            return f"isolation: {self.model} digest changed ({m.get('digest')} != {s['digest']})"
        if m.get("context_length") not in (None, s["num_ctx"]):
            return f"isolation: {self.model} served with context {m.get('context_length')}, not {s['num_ctx']}"
        return ""
