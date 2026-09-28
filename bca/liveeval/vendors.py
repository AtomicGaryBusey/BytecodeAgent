"""Subjects from other vendors' coding-agent CLIs, run as tool-less one-shot answerers.

A subject spec is `<vendor>:<model>` (`codex:gpt-6-luna`, `kimi:kimi-code/k3`); anything without
a known vendor prefix is a `claude -p` model (ClaudeSubject). Every vendor subject gets the same
isolation contract as ClaudeSubject:

- an empty temporary working directory outside the repository;
- the child environment from `child_env()` (an allowlist) plus the vendor's own switches;
- a private temporary config home holding only copies of the credential files (no user config,
  instruction files, memories, skills, plugins or MCP servers can reach the subject), deleted after
  the call. A token the CLI refreshed during the call is copied back to the real home (CredHome);
- no tools: each adapter switches every tool off it can, and fails CLOSED -- any tool call or tool
  result in the event stream makes the call an `isolation:` error, which aborts the run (see
  runner._FATAL_RE) instead of scoring an answer produced with help.

The CLIs below do not stream text deltas in their JSON modes: the answer arrives as one message,
which is handed to `on_text` in one piece (the early-abort check still runs on it).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from bca.liveeval.subject import (
    _VERSION_RE,
    CLI_DEFAULT_EFFORT,
    EFFORTS,
    SYSTEM_PROMPT,
    ClaudeSubject,
    OnText,
    Response,
    Subject,
    child_env,
)


class IsolationError(RuntimeError):
    """The subject could use (or used) a tool, or its isolated home could not be set up."""


# -- credentials: copied into a private home per call, refreshed tokens copied back ---------------


class CredHome:
    """A private config home for one call. `files` (relative to `real`) are copied in; after the call,
    `sync_back()` copies each one the CLI rewrote (a refreshed OAuth token) back to `real`, atomically
    and under an exclusive lock, unless `real` changed meanwhile (another call refreshed first: keep
    that one). Missing optional files are skipped; a missing required one is an IsolationError."""

    def __init__(self, real: Path, files: tuple[str, ...], required: tuple[str, ...], sync: tuple[str, ...]):
        self.real = Path(real)
        self.files, self.required, self.sync = files, required, sync
        self.path = Path(tempfile.mkdtemp(prefix="subj-home-", dir="/tmp"))
        os.chmod(self.path, 0o700)
        self._orig: dict[str, bytes] = {}
        with self._lock(fcntl.LOCK_SH):
            for rel in files:
                src = self.real / rel
                if not src.is_file():
                    if rel in required:
                        self.cleanup()
                        raise IsolationError(f"isolation: {src} not found (log in first)")
                    continue
                data = src.read_bytes()
                self._orig[rel] = data
                dst = self.path / rel
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                dst.write_bytes(data)
                os.chmod(dst, 0o600)

    def _lock(self, mode):
        real = self.real

        class _L:
            def __enter__(self_):
                real.mkdir(parents=True, exist_ok=True)
                self_.fh = open(real / ".bca-cred.lock", "a")
                fcntl.flock(self_.fh, mode)

            def __exit__(self_, *exc):
                fcntl.flock(self_.fh, fcntl.LOCK_UN)
                self_.fh.close()

        return _L()

    def sync_back(self) -> list[str]:
        synced = []
        with self._lock(fcntl.LOCK_EX):
            for rel in self.sync:
                tmp, real = self.path / rel, self.real / rel
                if rel not in self._orig or not tmp.is_file():
                    continue
                new = tmp.read_bytes()
                if new == self._orig[rel] or not new.strip():
                    continue
                if real.is_file() and real.read_bytes() != self._orig[rel]:
                    continue  # refreshed by a concurrent call already
                part = real.with_name(real.name + ".bca-new")
                part.write_bytes(new)
                os.chmod(part, 0o600)
                os.replace(part, real)
                synced.append(rel)
        return synced

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


# -- the generic runner: one process, JSONL on stdout, killable ------------------------------------


@dataclass
class Parsed:
    text: str = ""  # the answer (the last assistant message)
    model: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CliSubject(Subject):
    """Base for vendor CLIs. Subclasses define `vendor`, `binary_name`, `efforts`, `home_real`,
    `home_files` / `home_required` / `home_sync`, `argv(prompt, home)`, `extra_env(home)` and
    `on_event(ev, parsed) -> str | None` (returns answer text as soon as it is known, if any)."""

    vendor = "cli"
    binary_name = ""
    efforts: tuple[str, ...] = ()
    home_files: tuple[str, ...] = ()
    home_required: tuple[str, ...] = ()
    home_sync: tuple[str, ...] = ()

    def __init__(self, model: str, *, timeout: float = 180.0, effort: str | None = None, binary: str | None = None,
                 home_real: str | Path | None = None):
        if effort is not None and effort not in self.efforts:
            raise ValueError(f"{self.vendor}: unknown effort {effort!r}; have {', '.join(self.efforts)}")
        if not model:
            raise ValueError(f"{self.vendor}: a model is required ({self.vendor}:<model>)")
        self.model = model
        self.timeout = timeout
        self.effort = effort
        self.binary = shutil.which(binary or self.binary_name) or (binary or self.binary_name)
        self.home_real = Path(home_real).expanduser() if home_real else self.default_home()
        self.calls = 0
        self._lock = threading.Lock()
        self._version: str | None = None

    name = property(lambda self: f"{self.vendor}-cli")

    def default_home(self) -> Path:
        raise NotImplementedError

    def argv(self, prompt: str, home: Path) -> list[str]:
        raise NotImplementedError

    def extra_env(self, home: Path) -> dict[str, str]:
        return {}

    def prepare_home(self, home: Path) -> None:
        """Write the subject's own files (system prompt, agent profile) into the private home."""

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        raise NotImplementedError

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        """Last look at the parse after the process exited."""

    @property
    def cli_version(self) -> str:
        if self._version is None:
            try:
                out = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=30,
                                     env=child_env(), stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
                m = _VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
                self._version = m.group(0) if out.returncode == 0 and m else "unknown"
            except (OSError, subprocess.SubprocessError):
                self._version = "unknown"
        return self._version

    def describe(self) -> dict:
        return {"effort": self.effort or CLI_DEFAULT_EFFORT, "cli_version": f"{self.vendor} {self.cli_version}",
                "vendor": self.vendor}

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        with self._lock:
            self.calls += 1
        t0 = time.perf_counter()
        try:
            home = CredHome(self.home_real, self.home_files, self.home_required, self.home_sync)
        except IsolationError as e:
            return Response("", ok=False, error=str(e), model=self.model)
        workdir = tempfile.mkdtemp(prefix="subj-", dir="/tmp")
        p = Parsed(model=self.model)
        first, deltas, killed, timer_fired, proc = None, 0, False, threading.Event(), None
        stderr = ""
        raw = open(raw_path, "w") if raw_path else None
        try:
            self.prepare_home(home.path)
            env = {**child_env(), **self.extra_env(home.path)}
            proc = subprocess.Popen(self.argv(prompt, home.path), cwd=workdir, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)

            def kill() -> None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

            def on_timeout() -> None:
                timer_fired.set()
                kill()

            err_chunks: list[bytes] = []
            err_t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
            err_t.start()
            timer = threading.Timer(self.timeout if timeout is None else timeout, on_timeout)
            timer.start()
            try:
                for line in proc.stdout:
                    s = line.decode(errors="replace")
                    if raw:
                        raw.write(s)
                    try:
                        ev = json.loads(s)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    got = self.on_event(ev, p)
                    if p.violations:
                        kill()
                        break
                    if got:
                        if first is None:
                            first = time.perf_counter() - t0
                        deltas += 1
                        if on_text is not None and on_text(got) is False:
                            killed = True
                            kill()
                            break
            finally:
                timer.cancel()
                if killed or timer_fired.is_set() or p.violations:
                    kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    kill()
                    proc.wait()
                err_t.join(timeout=5)
                stderr = b"".join(err_chunks).decode(errors="replace")[-4000:]
                if raw:
                    raw.write(json.dumps({"bca_stderr": stderr[-2000:]}) + "\n")
            if not (killed or timer_fired.is_set()):  # our own SIGKILL is not the CLI failing
                self.finish(p, proc.returncode, stderr)
        finally:
            if raw:
                raw.close()
            try:
                home.sync_back()
            finally:
                home.cleanup()
                shutil.rmtree(workdir, ignore_errors=True)
        timed_out = timer_fired.is_set() and not killed
        error = p.error
        if p.violations:
            error = "isolation: the subject used a tool: " + "; ".join(p.violations)[:400]
        ok = not timed_out and not error and (killed or proc.returncode == 0)
        if not ok and not error:
            error = "timeout" if timed_out else f"exit status {proc.returncode}: {stderr.strip()[-500:]}"
        return Response(p.text, ok=ok, killed=killed, timed_out=timed_out, error=error, model=p.model,
                         wall_s=time.perf_counter() - t0, first_text_s=first, deltas=deltas, usage=p.usage,
                         raw_path=raw_path)


# -- OpenAI Codex CLI -------------------------------------------------------------------------------

# Every tool codex can offer, switched off (codex-rs 0.157: features/src/lib.rs, core/src/tools/
# spec_plan.rs). apply_patch has no switch: it is registered whenever the model's metadata names a
# patch format. It stays contained: read-only sandbox, approvals never granted, an empty workdir, and
# a file_change item fails the call closed.
CODEX_OFF = (
    'approval_policy="never"', 'web_search="disabled"', "project_doc_max_bytes=0",
    'history.persistence="none"', "check_for_update_on_startup=false", "analytics.enabled=false",
    "features.shell_tool=false", "features.unified_exec=false", "features.view_image=false",
    "features.multi_agent=false", "features.image_generation=false", "features.apps=false",
    "features.plugins=false", "features.memories=false", "tools.experimental_request_user_input.enabled=false",
    "include_environment_context=false", "include_permissions_instructions=false",
    "include_apps_instructions=false",
)  # fmt: skip
CODEX_TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "collab_tool_call", "web_search"}


class CodexSubject(CliSubject):
    vendor = "codex"
    binary_name = "codex"
    efforts = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
    home_files = ("auth.json",)
    home_required = ("auth.json",)
    home_sync = ("auth.json",)

    def default_home(self) -> Path:
        return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()

    def prepare_home(self, home: Path) -> None:
        (home / "bca-system.md").write_text(SYSTEM_PROMPT + "\n")

    def argv(self, prompt: str, home: Path) -> list[str]:
        argv = [self.binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--ignore-rules", "--sandbox", "read-only", "-m", self.model,
                "-c", f'model_instructions_file="{home / "bca-system.md"}"']
        for kv in CODEX_OFF:
            argv += ["-c", kv]
        if self.effort:
            argv += ["-c", f'model_reasoning_effort="{self.effort}"']
        return argv + ["--", prompt]

    def extra_env(self, home: Path) -> dict[str, str]:
        return {"CODEX_HOME": str(home)}

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        typ = ev.get("type")
        if typ in ("item.started", "item.updated", "item.completed"):
            item = ev.get("item") or {}
            it = item.get("type")
            if it in CODEX_TOOL_ITEMS:
                p.violations.append(f"{it}: {json.dumps(item)[:200]}")
                return None
            if typ != "item.completed":
                return None
            if it == "agent_message":
                p.text = item.get("text") or ""
                return p.text
            if it == "error":
                msg = item.get("message") or ""
                p.notes.append(msg)
                if msg.startswith("model rerouted:") and "->" in msg:
                    p.model = msg.split("->", 1)[1].split("(")[0].strip()
        elif typ == "turn.completed":
            p.usage = ev.get("usage") or {}
        elif typ == "turn.failed":
            p.error = "turn failed: " + ((ev.get("error") or {}).get("message") or "")
        elif typ == "error":
            p.notes.append(ev.get("message") or "")
        return None

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if returncode not in (0, None) and not p.error:
            p.error = f"exit status {returncode}: " + (p.notes[-1] if p.notes else stderr.strip()[-500:])


# -- Moonshot Kimi Code CLI -------------------------------------------------------------------------

# The agent profile IS the whole system prompt (no ${base_prompt} / ${agents_md}), and `tools: []`
# is an empty allowlist: every builtin and MCP tool fails the policy check (kimi-code 2.1,
# agent-core-v2 toolPolicy/evaluate.ts). Without an agent file `-p` runs with every tool on auto.
KIMI_AGENT = f"""---
name: bca-subject
description: Tool-less answerer for the BytecodeAgent live evaluation.
tools: []
subagents: []
---
{SYSTEM_PROMPT}
"""


class KimiSubject(CliSubject):
    vendor = "kimi"
    binary_name = "kimi"
    efforts = ("low", "high", "max")
    home_files = ("credentials/kimi-code.json", "config.toml", "region")
    home_required = ("credentials/kimi-code.json",)
    home_sync = ("credentials/kimi-code.json",)

    def default_home(self) -> Path:
        return Path(os.environ.get("KIMI_CODE_HOME") or "~/.kimi-code").expanduser()

    def prepare_home(self, home: Path) -> None:
        (home / "bca-subject.md").write_text(KIMI_AGENT)

    def argv(self, prompt: str, home: Path) -> list[str]:
        if prompt.lstrip().startswith("/"):
            prompt = " " + prompt  # a leading slash selects a command (/goal ...)
        return [self.binary, "--output-format", "stream-json", "--agent-file", str(home / "bca-subject.md"),
                "-m", self.model, f"--prompt={prompt}"]

    def extra_env(self, home: Path) -> dict[str, str]:
        env = {"KIMI_CODE_HOME": str(home), "KIMI_DISABLE_TELEMETRY": "1", "KIMI_CODE_NO_AUTO_UPDATE": "1"}
        if self.effort:
            env["KIMI_MODEL_THINKING_EFFORT"] = self.effort
        return env

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        role = ev.get("role")
        if role == "assistant":
            if ev.get("tool_calls"):
                names = [((c.get("function") or {}).get("name") or "?") for c in ev["tool_calls"]]
                p.violations.append("tool_calls " + ",".join(names))
                return None
            content = ev.get("content")
            if isinstance(content, str) and content:
                p.text = content
                return content
        elif role == "tool":
            p.violations.append(f"tool result {str(ev.get('content'))[:120]}")
        elif role == "meta" and ev.get("type") == "turn.step.retrying":
            p.notes.append(f"retry {ev.get('error_name')}: {ev.get('error_message')}")
        return None

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if returncode not in (0, None) and not p.error:
            lines = [ln for ln in stderr.strip().splitlines() if ln.strip() and not ln.startswith("See log:")]
            p.error = f"exit status {returncode}: " + ("\n".join(lines[-6:]) or "no stderr")[-500:]


# -- registry ---------------------------------------------------------------------------------------

VENDORS: dict[str, type[CliSubject]] = {"codex": CodexSubject, "kimi": KimiSubject}


def split_spec(spec: str) -> tuple[str | None, str]:
    """`codex:gpt-6-luna` -> ("codex", "gpt-6-luna"); a Claude alias or id -> (None, spec)."""
    vendor, sep, model = spec.partition(":")
    if sep and vendor in VENDORS:
        return vendor, model
    return None, spec


def all_efforts() -> tuple[str, ...]:
    return tuple(dict.fromkeys([*EFFORTS, *(e for v in VENDORS.values() for e in v.efforts)]))


def make_subject(spec: str | None, *, timeout: float, effort: str | None = None) -> Subject:
    vendor, model = split_spec(spec or "")
    if vendor is None:
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"claude: unknown effort {effort!r}; have {', '.join(EFFORTS)}")
        return ClaudeSubject(timeout=timeout, model=spec or None, effort=effort)
    return VENDORS[vendor](model, timeout=timeout, effort=effort)


__all__ = ["CliSubject", "CodexSubject", "CredHome", "IsolationError", "KimiSubject", "VENDORS", "all_efforts",
           "make_subject", "split_spec"]
