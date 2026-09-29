"""Model subjects: a live `claude -p` process (streamed, killable) and a scripted fake.

A subject is called with a prompt and an `on_text(delta) -> bool` callback that receives the
model's text as it arrives; returning False asks the subject to stop NOW (the live subject
kills its process group). `complete()` returns a `Response` with the full text received.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field

SYSTEM_PROMPT = (
    "You are an expert low-level programmer. Reply with exactly the code that is asked for "
    "and nothing else: no explanations, no questions."
)

# Isolation: no tools, no MCP, no skills/slash commands, no settings files (so no user
# CLAUDE.md, memory, hooks or plugins), no session files, a neutral system prompt, and an
# empty temporary working directory outside the repository.
CLAUDE_FLAGS = (
    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
    "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
    "--no-session-persistence", "--setting-sources", "", "--safe-mode",
    # The requested model answers or refuses: never a silent stand-in. By default Claude Code re-runs a
    # call that a model's safety classifier stops on another model (2026-09-28: claude-fable-5-1 ->
    # claude-opus-4-8, category "cyber", on this harness's own system prompt).
    "--settings", '{"switchModelsOnFlag": false}',
    "--system-prompt", SYSTEM_PROMPT,
)  # fmt: skip

EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `claude --help`, --effort <level> (CLI 2.1.283)
CLI_DEFAULT_EFFORT = "cli-default"  # recorded when --effort is not passed

# The child's environment is built from an allowlist, never passed through: a parent Claude Code
# session exports CLAUDECODE, CLAUDE_CODE_* (session ids, entrypoint, messaging sockets) and
# CLAUDE_EFFORT, and a user environment may carry ANTHROPIC_MODEL / ANTHROPIC_DEFAULT_*_MODEL, all of
# which would change what the subject is. What `claude` needs to find its binary, config and
# credentials: PATH, HOME (~/.claude, ~/.claude.json, ~/.claude/.credentials.json), USER/LOGNAME,
# XDG_* (config/cache/data/state/runtime dirs; the runtime dir and D-Bus address for a keyring),
# CLAUDE_CONFIG_DIR (a relocated config dir), an API key or token if that is how it authenticates,
# and the proxy / CA settings a network may need.
ENV_ALLOW = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_DIRS", "XDG_DATA_DIRS", "DBUS_SESSION_BUS_ADDRESS",
    "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
})  # fmt: skip
ENV_ALLOW_PREFIXES = ("LC_",)


def child_env(environ: dict | None = None) -> dict[str, str]:
    """The environment of a `claude -p` subject process: `environ` (default os.environ) filtered
    through ENV_ALLOW / ENV_ALLOW_PREFIXES. CLAUDECODE, CLAUDE_CODE_*, CLAUDE_EFFORT, ANTHROPIC_MODEL
    and everything else not listed is dropped."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items() if k in ENV_ALLOW or k.startswith(ENV_ALLOW_PREFIXES)}


_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


@dataclass
class Response:
    text: str
    ok: bool = True  # False: the subject failed (timeout, process error)
    killed: bool = False  # stopped early by on_text returning False
    timed_out: bool = False
    error: str = ""
    model: str = ""
    wall_s: float = 0.0
    first_text_s: float | None = None  # seconds from start to the first text delta
    deltas: int = 0
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    raw_path: str = ""  # where the raw event stream was saved (live subject)
    refusal: str = ""  # the model (or its safety classifier) refused: "<category>: <explanation>"
    model_source: str = "reported"  # "requested": the CLI never said which model served the call

    def to_dict(self) -> dict:
        return asdict(self)


OnText = Callable[[str], bool]


def _refusal_text(details: dict) -> str:
    cat = details.get("category") or "unspecified"
    expl = (details.get("explanation") or "").strip()
    return f"{cat}: {expl[:200]}" if expl else cat


class Subject:
    name = "subject"
    calls = 0
    # A subject whose complete() also takes a `timeout=` keyword (seconds) gets the runner's
    # per-battery timeout for each call; other subjects are called without it.

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "") -> Response:
        raise NotImplementedError

    def describe(self) -> dict:
        """What to record about the subject in group.json and every cell record."""
        return {"effort": getattr(self, "effort", None) or CLI_DEFAULT_EFFORT,
                "cli_version": getattr(self, "cli_version", None) or self.name}


class FakeSubject(Subject):
    """Scripted responses, in order. Each script item is a string (sent as one delta), a list
    of chunks (streamed one by one), or a Response-like dict {"chunks"| "text", "timeout", "error"}
    ("error": the call fails like a process/API failure, with that message)."""

    name = "fake"

    def __init__(self, script: Sequence, model: str = "fake-model"):
        self.timeouts: list[float | None] = []  # the per-call timeout the runner asked for
        self.script = list(script)
        self.model = model
        self.calls = 0
        self.prompts: list[str] = []
        self.stopped_at: list[int | None] = []  # chunk index at which on_text said stop

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        self.timeouts.append(timeout)
        self.prompts.append(prompt)
        if not self.script:
            raise RuntimeError("FakeSubject script exhausted")
        item = self.script.pop(0)
        self.calls += 1
        timed_out = False
        if isinstance(item, dict):
            timed_out = bool(item.get("timeout"))
            error = item.get("error", "")
            if error:  # an infrastructure failure: {"error": "exit status 1: API Error: 529 ..."}
                return Response(item.get("text", ""), ok=False, error=error, model=self.model)
            item = item.get("chunks", item.get("text", ""))
        chunks = [item] if isinstance(item, str) else list(item)
        t0 = time.perf_counter()
        got, stop, first = [], None, None
        for i, chunk in enumerate(chunks):
            got.append(chunk)
            if first is None:
                first = time.perf_counter() - t0
            if on_text is not None and on_text(chunk) is False:
                stop = i
                break
        self.stopped_at.append(stop)
        return Response(
            "".join(got), ok=not timed_out, killed=stop is not None, timed_out=timed_out,
            error="timeout" if timed_out else "", model=self.model,
            wall_s=time.perf_counter() - t0, first_text_s=first, deltas=len(got),
        )  # fmt: skip


class ClaudeSubject(Subject):
    """`claude -p` with stream-json output; text deltas are forwarded as they arrive."""

    name = "claude-cli"

    def __init__(self, *, timeout: float = 180.0, binary: str = "claude", model: str | None = None,
                 budget: Callable[[], bool] | None = None, effort: str | None = None):
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self.model = model
        self.effort = effort  # None: --effort is not passed (the CLI's default applies)
        self.budget = budget  # called before each call; returns False to refuse
        self.calls = 0
        self._lock = threading.Lock()
        self._version: str | None = None

    def argv(self, prompt: str) -> list[str]:
        argv = [self.binary, *CLAUDE_FLAGS]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        return argv + ["--", prompt]

    def env(self) -> dict[str, str]:
        return child_env()

    @property
    def cli_version(self) -> str:
        """`claude --version` (no model call), cached; "unknown" if it cannot be run."""
        if self._version is None:
            try:
                out = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30,
                                     env=self.env(), stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
                m = _VERSION_RE.search(out.stdout or "")
                self._version = m.group(0) if out.returncode == 0 and m else "unknown"
            except (OSError, subprocess.SubprocessError):
                self._version = "unknown"
        return self._version

    def describe(self) -> dict:
        return {"effort": self.effort or CLI_DEFAULT_EFFORT, "cli_version": self.cli_version}

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        if self.budget is not None and not self.budget():
            return Response("", ok=False, error="call budget exhausted")
        with self._lock:
            self.calls += 1
        workdir = tempfile.mkdtemp(prefix="subj-", dir="/tmp")  # neutral, empty, outside repo
        env = self.env()
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            self.argv(prompt), cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, start_new_session=True,
        )  # fmt: skip
        timer_fired = threading.Event()

        def kill() -> None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        def on_timeout() -> None:
            timer_fired.set()
            kill()

        timer = threading.Timer(self.timeout if timeout is None else timeout, on_timeout)
        timer.start()
        text, model, usage, cost, error, refusal = [], "", {}, None, "", ""
        first, deltas, killed, result_text = None, 0, False, None
        raw = open(raw_path, "w") if raw_path else None
        try:
            for line in proc.stdout:
                if raw:
                    raw.write(line.decode(errors="replace"))
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                typ = msg.get("type")
                if typ == "system" and msg.get("subtype") == "init":
                    model = msg.get("model", model)
                elif typ == "stream_event":
                    ev = msg.get("event", {})
                    if ev.get("type") == "message_start":
                        m = ev.get("message", {}).get("model")
                        model = m if m and m != "<synthetic>" else model
                    elif ev.get("type") == "message_delta" and (ev.get("delta") or {}).get("stop_reason") == "refusal":
                        refusal = refusal or _refusal_text((ev["delta"].get("stop_details") or {}))
                    elif ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        delta = ev["delta"].get("text", "")
                        if not delta:
                            continue
                        if first is None:
                            first = time.perf_counter() - t0
                        deltas += 1
                        text.append(delta)
                        if on_text is not None and on_text(delta) is False:
                            killed = True
                            kill()
                            break
                elif typ == "system" and msg.get("subtype") in ("model_refusal_fallback", "model_refusal_no_fallback"):
                    refusal = refusal or _refusal_text({"category": msg.get("api_refusal_category"),
                                                        "explanation": msg.get("api_refusal_explanation")})
                    if msg.get("fallback_model"):  # switchModelsOnFlag is off; should never happen
                        refusal += f" (answered by fallback model {msg['fallback_model']})"
                elif typ == "result":
                    usage = msg.get("usage", {}) or {}
                    for it in usage.get("iterations") or []:  # a server-side substitution (fallback_message)
                        if isinstance(it, dict) and it.get("type") not in (None, "message") and it.get("model"):
                            refusal = refusal or f"served by fallback model {it['model']} ({it.get('type')})"
                            model = it["model"]
                    cost = msg.get("total_cost_usd")
                    result_text = msg.get("result")
                    if msg.get("is_error"):
                        error = f"result error: {msg.get('subtype')} {msg.get('api_error_status')}"
        finally:
            timer.cancel()
            if killed or timer_fired.is_set():
                kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill()
                proc.wait()
            stderr = proc.stderr.read().decode(errors="replace")[-2000:]
            if raw:
                raw.close()
            shutil.rmtree(workdir, ignore_errors=True)
        body = "".join(text)
        if not body and result_text and not killed:
            body = result_text  # no partial messages seen (should not happen)
        timed_out = timer_fired.is_set() and not killed
        ok = not timed_out and not error and (killed or proc.returncode == 0)
        if not ok and not error:
            error = "timeout" if timed_out else f"exit status {proc.returncode}: {stderr.strip()[-500:]}"
        return Response(
            body, ok=ok, killed=killed, timed_out=timed_out, error=error, model=model,
            wall_s=time.perf_counter() - t0, first_text_s=first, deltas=deltas, usage=usage,
            cost_usd=cost, raw_path=raw_path, refusal=refusal,
        )  # fmt: skip
