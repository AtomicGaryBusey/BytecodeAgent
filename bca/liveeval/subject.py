"""Model subjects: a live `claude -p` process (streamed, killable) and a scripted fake.

A subject is called with a prompt and an `on_text(delta) -> bool` callback that receives the
model's text as it arrives; returning False asks the subject to stop NOW (the live subject
kills its process group). `complete()` returns a `Response` with the full text received.
"""

from __future__ import annotations

import json
import os
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
    "--system-prompt", SYSTEM_PROMPT,
)  # fmt: skip


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

    def to_dict(self) -> dict:
        return asdict(self)


OnText = Callable[[str], bool]


class Subject:
    name = "subject"
    calls = 0

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "") -> Response:
        raise NotImplementedError


class FakeSubject(Subject):
    """Scripted responses, in order. Each script item is a string (sent as one delta), a list
    of chunks (streamed one by one), or a Response-like dict {"chunks"| "text", "timeout"}."""

    name = "fake"

    def __init__(self, script: Sequence, model: str = "fake-model"):
        self.script = list(script)
        self.model = model
        self.calls = 0
        self.prompts: list[str] = []
        self.stopped_at: list[int | None] = []  # chunk index at which on_text said stop

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "") -> Response:
        self.prompts.append(prompt)
        if not self.script:
            raise RuntimeError("FakeSubject script exhausted")
        item = self.script.pop(0)
        self.calls += 1
        timed_out = False
        if isinstance(item, dict):
            timed_out = bool(item.get("timeout"))
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
                 budget: Callable[[], bool] | None = None):
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self.model = model
        self.budget = budget  # called before each call; returns False to refuse
        self.calls = 0
        self._lock = threading.Lock()

    def argv(self, prompt: str) -> list[str]:
        argv = [self.binary, *CLAUDE_FLAGS]
        if self.model:
            argv += ["--model", self.model]
        return argv + ["--", prompt]

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "") -> Response:
        if self.budget is not None and not self.budget():
            return Response("", ok=False, error="call budget exhausted")
        with self._lock:
            self.calls += 1
        workdir = tempfile.mkdtemp(prefix="subj-", dir="/tmp")  # neutral, empty, outside repo
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_SESSION",))}
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

        timer = threading.Timer(self.timeout, on_timeout)
        timer.start()
        text, model, usage, cost, error = [], "", {}, None, ""
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
                        model = ev.get("message", {}).get("model", model)
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
                elif typ == "result":
                    usage = msg.get("usage", {}) or {}
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
            cost_usd=cost, raw_path=raw_path,
        )  # fmt: skip
