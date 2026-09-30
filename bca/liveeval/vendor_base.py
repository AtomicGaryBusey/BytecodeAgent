"""Subjects from other vendors' coding-agent CLIs, run as tool-less one-shot answerers.

A subject spec is `<vendor>:<model>` (`codex:gpt-6-luna`, `kimi:kimi-code/k3`, `agy:gemini-3.8-flash-high`,
`grok:grok-4.7`, `copilot:mai-code-1.1-flash`); anything without a known vendor prefix is a `claude -p` model (ClaudeSubject). Every vendor subject gets
the same isolation contract as ClaudeSubject:

- an empty temporary working directory outside the repository;
- the child environment from `child_env()` (an allowlist) plus the vendor's own switches;
- a private temporary config home holding only copies of the credential files (no user config,
  instruction files, memories, skills, plugins or MCP servers can reach the subject), deleted after
  the call. A token the CLI refreshed during a successful call is copied back to the real home, only
  when it is complete, and one refresh at a time (CredHome);
- no tools: each adapter switches every tool off it can, verifies the request the CLI actually sent
  where the CLI records it, and fails CLOSED -- any tool call, tool result, unknown event, malformed
  event or unverifiable request makes the call an `isolation:` error, which aborts the run (see
  runner._FATAL_RE) instead of scoring an answer produced with help;
- the CLI version must be one the adapter was audited against (`audited_versions`): an upgraded CLI
  aborts the run until it is re-audited, so results stay comparable;
- a vendor safety block is a scored refusal, as for Claude (`Parsed.refusal`).

Codex and Kimi deliver the answer as one message; Antigravity and Grok stream deltas but write their
request records only at the end of the turn. None of them is killed on an early abort (`streams_text = False`):
the scorer stops reading, the CLI finishes, and its request is verified. A CLI that sets `streams_text`
is killed on an early abort and must be verifiable afterwards (finish() gets returncode None).
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bca.liveeval.subject import _VERSION_RE, CLI_DEFAULT_EFFORT, OnText, Response, Subject, child_env


# Claude-specific variables child_env() keeps for `claude -p`; another vendor's CLI never needs them (grok,
# for one, can read Claude config through CLAUDE_CONFIG_DIR).
VENDOR_ENV_DROP = frozenset({"CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"})


class IsolationError(RuntimeError):
    """The subject could use (or used) a tool, or its isolated home could not be set up: fatal."""


class SetupError(RuntimeError):
    """A transient failure before the model call (network, a helper command timing out): retried."""


# -- live processes and homes, cleaned up if the harness exits -------------------------------------

_LIVE: dict[int, tuple[int | None, Path | None]] = {}  # id -> (process group, private home)
_LIVE_LOCK = threading.Lock()


def _reap_live() -> None:
    with _LIVE_LOCK:
        items = list(_LIVE.values())
        _LIVE.clear()
    for pgid, home in items:
        if pgid:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if home:
            shutil.rmtree(home, ignore_errors=True)


atexit.register(_reap_live)


# -- credentials: copied into a private home per call, refreshed tokens copied back ---------------

ExpiryFn = Callable[[str, bytes], "float | None"]


def _string_leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _string_leaves(v, (*path, k))
    elif isinstance(obj, str):
        yield path, obj


def credentials_degraded(old: bytes, new: bytes) -> str:
    """Why `new` must not replace `old` ("" if it may): unparseable JSON, or a string field that was
    non-empty and is now empty or missing (a CLI that blanks its tokens after a failed refresh)."""
    if not new.strip():
        return "empty file"
    try:
        o = json.loads(old)
    except ValueError:
        return ""  # not JSON to begin with: only emptiness is checked
    try:
        n = json.loads(new)
    except ValueError:
        return "unparseable JSON"
    new_leaves = dict(_string_leaves(n))
    for path, val in _string_leaves(o):
        if val and not new_leaves.get(path):
            return f"field {'.'.join(map(str, path))} emptied"
    return ""


class CredHome:
    """A private config home for one call. `files` (relative to `real`) are copied in.

    Refresh safety: `expiry(rel, data)` gives a credential file's access-token expiry (epoch seconds) or
    None. If any copied credential expires within `horizon` seconds, the call becomes the refresh
    LEADER: it keeps an exclusive lock on the real home for the whole call, so no other call starts
    (and none refreshes concurrently from the same refresh token); calls already running hold tokens
    valid for longer than any call lasts. `sync_back(ok)` copies back each rewritten credential only if
    the call succeeded, the new file is not degraded (credentials_degraded) and the real file was not
    changed meanwhile. Missing optional files are skipped; a missing required one is an IsolationError.
    A home with no files never touches the real home (no lock file either)."""

    def __init__(self, real: Path, files: tuple[str, ...], required: tuple[str, ...], sync: tuple[str, ...],
                 *, expiry: ExpiryFn | None = None, horizon: float = 0.0):
        self.real = Path(real)
        self.files, self.required, self.sync = files, required, sync
        self.path = Path(tempfile.mkdtemp(prefix="subj-home-", dir="/tmp"))
        os.chmod(self.path, 0o700)
        self._orig: dict[str, bytes] = {}
        self._fh = None  # the held lock file (leader), else None
        self.leader = False
        try:
            fh = self._acquire(fcntl.LOCK_EX)
            try:
                for rel in files:
                    src = self.real / rel
                    if not src.is_file():
                        if rel in required:
                            raise IsolationError(f"isolation: {src} not found (log in first)")
                        continue
                    data = src.read_bytes()
                    self._orig[rel] = data
                    dst = self.path / rel
                    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    dst.write_bytes(data)
                    os.chmod(dst, 0o600)
                    if expiry is not None and rel in sync:
                        exp = expiry(rel, data)
                        if exp is not None and exp < time.time() + horizon:
                            self.leader = True
            finally:
                if self.leader:
                    self._fh = fh
                else:
                    self._release(fh)
        except BaseException:
            self.cleanup()
            raise

    def _acquire(self, mode):
        if not self.files:
            return None
        self.real.mkdir(parents=True, exist_ok=True)
        fh = open(self.real / ".bca-cred.lock", "a")
        fcntl.flock(fh, mode)
        return fh

    @staticmethod
    def _release(fh) -> None:
        if fh is not None:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def sync_back(self, ok: bool = True) -> list[str]:
        synced: list[str] = []
        fh = self._fh or self._acquire(fcntl.LOCK_EX)
        self._fh = None
        try:
            # `ok` is informational: a token refreshed during a failed call is still the only valid one if the
            # vendor rotates refresh tokens, so it is kept -- but never a blanked or broken file (a CLI that
            # wipes its tokens after invalid_grant), see credentials_degraded.
            del ok
            for rel in self.sync:
                tmp, real = self.path / rel, self.real / rel
                if rel not in self._orig or not tmp.is_file():
                    continue
                new = tmp.read_bytes()
                if new == self._orig[rel] or credentials_degraded(self._orig[rel], new):
                    continue
                if real.is_file() and real.read_bytes() != self._orig[rel]:
                    continue  # refreshed by another call already: keep that one
                part = real.with_name(real.name + ".bca-new")
                part.write_bytes(new)
                os.chmod(part, 0o600)
                os.replace(part, real)
                synced.append(rel)
            return synced
        finally:
            self._release(fh)

    def cleanup(self) -> None:
        if self._fh is not None:
            self._release(self._fh)
            self._fh = None
        shutil.rmtree(self.path, ignore_errors=True)


# -- the generic runner: one process, JSONL on stdout, killable ------------------------------------


@dataclass
class Parsed:
    text: str = ""  # the answer: every assistant message, in order, exactly as handed to on_text
    model: str = ""
    model_reported: bool = False  # True only when the CLI showed which model served the call
    usage: dict = field(default_factory=dict)
    error: str = ""
    violations: list[str] = field(default_factory=list)
    refusal: str = ""  # the vendor's safety system blocked the answer: a scored refusal
    notes: list[str] = field(default_factory=list)
    abort: bool = False  # the call is void (p.error says why, a transient error): stop the CLI now, score nothing


class CliSubject(Subject):
    """Base for vendor CLIs. Subclasses define `vendor`, `binary_name`, `efforts`, `audited_versions`,
    `streams_text`, `home_files` / `home_required` / `home_sync`, `default_home()`, `argv(prompt, home)`,
    `extra_env(home)`, `prepare_home(home)`, `on_event(ev, parsed) -> str | None` (answer text as soon as
    it is known), `finish(parsed, returncode, stderr)` (returncode None: the harness killed the process
    after an early abort -- not a CLI failure; verify the request anyway) and optionally
    `cred_expiry(rel, data)` for refresh serialisation. Setup code may raise IsolationError (fatal) or
    SetupError (transient)."""

    vendor = "cli"
    binary_name = ""
    efforts: tuple[str, ...] = ()
    audited_versions: tuple[str, ...] = ()  # empty: no version pin (tests)
    streams_text = False
    deliver_at_end = False  # True: on_event returns no text; the final answer (p.text, possibly replaced by
    # finish() with the CLI's own record of it) is handed to on_text once, after finish()
    home_files: tuple[str, ...] = ()
    home_required: tuple[str, ...] = ()
    home_sync: tuple[str, ...] = ()
    refresh_margin_s = 120.0  # extra headroom on top of the call timeout for the refresh leader check

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
        """Last look at the parse after the process exited (returncode None: killed by the harness)."""

    def cleanup(self, home: Path) -> None:
        """Called after EVERY started call, timeouts included, before the private home is deleted: remove
        anything the CLI wrote outside it. Must not raise."""

    def cred_expiry(self, rel: str, data: bytes) -> float | None:
        """Access-token expiry (epoch seconds) of a credential file, or None if unknown."""
        return None

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

    def _fail(self, error: str, t0: float) -> Response:
        return Response("", ok=False, error=error, model=self.model, model_source="requested",
                        wall_s=time.perf_counter() - t0)

    def complete(self, prompt: str, on_text: OnText | None = None, *, raw_path: str = "",
                 timeout: float | None = None) -> Response:
        with self._lock:
            self.calls += 1
        t0 = time.perf_counter()
        limit = self.timeout if timeout is None else timeout
        if self.audited_versions and self.cli_version not in self.audited_versions:
            return self._fail(f"isolation: {self.vendor} CLI {self.cli_version} is not an audited version "
                              f"({', '.join(self.audited_versions)}); re-audit the adapter before running it", t0)
        try:
            home = CredHome(self.home_real, self.home_files, self.home_required, self.home_sync,
                            expiry=self.cred_expiry, horizon=limit + self.refresh_margin_s)
        except IsolationError as e:
            return self._fail(str(e), t0)
        t0 = time.perf_counter()  # the call starts once the credential lock is held (no queueing in wall_s)
        token = object()
        with _LIVE_LOCK:
            _LIVE[id(token)] = (None, home.path)
        workdir = tempfile.mkdtemp(prefix="subj-", dir="/tmp")
        p = Parsed(model=self.model)
        first, deltas, killed, stopped, timer_fired, proc = None, 0, False, False, threading.Event(), None
        stderr = ""
        raw = open(raw_path, "w") if raw_path else None
        call_ok = False
        try:
            try:
                self.prepare_home(home.path)
                argv = self.argv(prompt, home.path)
            except IsolationError as e:  # fatal for the run, never a crash in a worker
                return self._fail(str(e), t0)
            except (SetupError, OSError, subprocess.SubprocessError) as e:  # transient: retried by the runner
                return self._fail(f"setup: {type(e).__name__}: {e}", t0)
            env = {k: v for k, v in child_env().items() if k not in VENDOR_ENV_DROP}
            env.update(self.extra_env(home.path))
            try:
                proc = subprocess.Popen(argv, cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=env, start_new_session=True)
            except OSError as e:
                return self._fail(f"setup: cannot start {self.vendor}: {e}", t0)
            with _LIVE_LOCK:
                _LIVE[id(token)] = (proc.pid, home.path)

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
            timer = threading.Timer(limit, on_timeout)
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
                    try:
                        got = self.on_event(ev, p)
                    except Exception as e:  # noqa: BLE001 - a malformed event: the CLI changed; fail closed
                        p.violations.append(f"malformed {self.vendor} event ({type(e).__name__}: {e}): {s[:200]}")
                        got = None
                    if p.violations or p.abort:
                        kill()
                        break
                    if got and not stopped:
                        if first is None:
                            first = time.perf_counter() - t0
                        deltas += 1
                        if on_text is not None and on_text(got) is False:
                            stopped = True  # the scorer has seen enough
                            if self.streams_text:
                                killed = True
                                kill()
                                break
                            # a whole-message CLI is done anyway: let it finish so its request is verified
            finally:
                timer.cancel()
                if killed or timer_fired.is_set() or p.violations or p.abort:
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
            if not timer_fired.is_set():
                try:  # verify the request even after an early abort (returncode None: we killed it)
                    self.finish(p, None if killed else proc.returncode, stderr)
                except Exception as e:  # noqa: BLE001 - an unverifiable call is not scored
                    p.violations.append(f"{self.vendor} request verification failed ({type(e).__name__}: {e})")
            if self.deliver_at_end and not timer_fired.is_set() and not p.violations and p.text:
                if first is None:
                    first = time.perf_counter() - t0
                deltas += 1
                if on_text is not None and on_text(p.text) is False:
                    stopped = True
            call_ok = (not timer_fired.is_set() and not p.violations and not p.error
                       and (killed or proc.returncode == 0))
        finally:
            if raw:
                raw.close()
            if proc is not None:
                try:
                    self.cleanup(home.path)
                except Exception as e:  # noqa: BLE001 - cleanup must never mask the call's outcome
                    p.notes.append(f"cleanup failed: {type(e).__name__}: {e}")
            try:
                home.sync_back(call_ok)
            finally:
                home.cleanup()
                shutil.rmtree(workdir, ignore_errors=True)
                with _LIVE_LOCK:
                    _LIVE.pop(id(token), None)
        timed_out = timer_fired.is_set() and not killed
        error = p.error
        if p.violations:
            error = "isolation: " + "; ".join(p.violations)[:600]
        ok = call_ok
        if not ok and not error:
            error = "timeout" if timed_out else f"exit status {proc.returncode}: {stderr.strip()[-500:]}"
        return Response(p.text, ok=ok, killed=killed or stopped, timed_out=timed_out, error=error, model=p.model,
                        wall_s=time.perf_counter() - t0, first_text_s=first, deltas=deltas, usage=p.usage,
                        raw_path=raw_path, refusal="" if p.violations else p.refusal,  # a violation is never a refusal
                        notes=list(p.notes),
                        model_source="reported" if p.model_reported else "requested")
