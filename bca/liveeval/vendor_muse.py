"""Meta Muse Spark through Muse Code, Meta's coding-agent CLI: `muse:<model>`, e.g. `muse:muse-spark-1.3-contributor`.
An AGENT-HARNESS STUDY, reported apart from every other subject (like vendor_vibe).

Audited 2026-11-11 on Muse Code 1.4.15 (request captured at a local recorder in front of api.meta.ai via --base-url):
- With `--preset empty` the Responses API request carries NO `tools` field at all.
- The system context cannot be replaced: `instructions` is Muse's own ~60,500-character agent prompt (identity, tone,
  repository work, tool-use sections) and a ~27,000-character developer message of workspace reminders comes first in
  `input`; there is no setting for it (`-c system_prompt` is refused). TBH_EVAL_APPEND_SYSTEM_PROMPT_FILE APPENDS our
  SYSTEM_PROMPT to `instructions` (verified: it is the tail). So these rows answer "how does the model do inside its
  vendor's agent harness", not the tool-free question, and are never ranked with the other rows.
- Sampling as Muse sends it: `reasoning: {"effort": "max"}` for muse-spark-1.3 and its contributor variant (the help says
  high), max_output_tokens 128000. `--effort` overrides it.

The protocol: a private XDG_CONFIG_HOME holding only a copy of muse/auth.json (refreshed tokens are copied back by
CredHome), private data/state homes, the empty working directory; `muse exec --json --prompt-file`, the `empty` preset,
one model step, shell / writes / web tools / reminders / user-input requests / foreign personal context all disabled,
no session log. The answer is Muse's own final text (run.terminal.completed), delivered once at the end, and it must
match the output deltas of the model task that completed. Fail closed: any task other than `model.meta.response`, any
event naming tools / MCP / skills / subagents / plugins, a model other than the one asked for, a user turn other than the
prompt, or more than one completed model task is an isolation error.

Muse's stream guard (seen 2026-11-11, ~1 call in 30): when a streamed answer contains characters of another script, Muse
fails that model task (`task.lifecycle.failed`, reason "stream guard resample (foreign-script)") and samples again, inside
the one step. That is the harness under study, so it is allowed, counted (usage["guard_resamples"]) and noted; the
discarded sample's deltas are dropped. Any other failed model task is an error.

Re-audited 2026-11-12 on 1.4.18 (the launcher self-updated after a reboot, mid-study, and removed 1.4.15): the same
request shape (no `tools`, effort max, max_output_tokens 128000, our SYSTEM_PROMPT the tail of `instructions`) and the
same event types; Muse's own text grew (instructions 60,598 -> 63,961 characters, same twelve sections; developer
message 26,912 -> 28,088). The study's last 6 app cells and its hex programs ran on 1.4.18 (each attempt records
cli_version). MUSE_NO_AUTO_UPDATE=1 is now set on every call.

Billing: the account has no Muse subscription allowance (`muse usage`: no_allowance); the `-contributor` model runs in
Meta's contributor tier (sessions shared with Meta). The usage record carries tokens, no price.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

MUSE_FLAGS = ("--preset", "empty", "--max-model-steps", "1", "--disable-shell", "--disable-write", "--disable-web-tools",
              "--disable-reminders", "--disable-request-user-input", "--no-foreign-personal-context", "--no-session-log")
MODEL_TASK = "model.meta.response"
_SUSPECT = re.compile(r"tool|mcp|skill|subagent|plugin|approval|shell|workspace\.write", re.I)
_KNOWN = frozenset({"runtime.command.accepted", "session.run.linked", "run.model.configured", "turn.input.user",
                    "run.lifecycle.started", "task.stream.linked", "task.lifecycle.proposed", "task.lifecycle.accepted",
                    "task.lifecycle.scheduled", "task.lifecycle.side_effect_intent", "task.lifecycle.started",
                    "task.lifecycle.status", "run.output.delta", "task.lifecycle.completed", "run.usage",
                    "run.terminal.completed"})


class MuseSubject(CliSubject):
    vendor = "muse"
    binary_name = "muse"
    efforts = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
    audited_versions = ("1.4.15", "1.4.18", "1.4.20")
    streams_text = False  # verified from the whole event log; let it finish
    deliver_at_end = True  # a guard resample replaces the streamed text: hand over only the final answer
    prompt_in_argv = False  # --prompt-file
    home_files = ("muse/auth.json",)
    home_required = ("muse/auth.json",)
    home_sync = ("muse/auth.json",)

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        self._call = threading.local()

    def default_home(self) -> Path:
        return Path("~/.config").expanduser()

    def prepare_home(self, home: Path) -> None:
        try:
            (home / "system.txt").write_text(SYSTEM_PROMPT)
            for d in ("data", "state", "cache", "tmp"):
                (home / d).mkdir(mode=0o700)
        except OSError as e:
            raise IsolationError(f"isolation: cannot set up the muse home {home}: {e}") from e

    def argv(self, prompt: str, home: Path) -> list[str]:
        (home / "prompt.txt").write_text(prompt)
        self._call.prompt, self._call.st = prompt, {"terminal": None, "final": None, "model_tasks": 0,
                                                      "completed": 0, "sample": ""}
        argv = [self.binary, "exec", "--json", "--model", self.model, "--prompt-file", str(home / "prompt.txt"),
                *MUSE_FLAGS]
        if self.effort:
            argv += ["--reasoning-effort", self.effort]
        return argv

    version_env = {"MUSE_NO_AUTO_UPDATE": "1"}  # the version probe too (it updated Muse to 1.4.20 on 11-13)

    def extra_env(self, home: Path) -> dict[str, str]:
        return {"XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "XDG_STATE_HOME": str(home / "state"),
                "XDG_CACHE_HOME": str(home / "cache"), "TMPDIR": str(home / "tmp"),
                "TBH_EVAL_APPEND_SYSTEM_PROMPT_FILE": str(home / "system.txt"),
                "MUSE_NO_AUTO_UPDATE": "1"}  # the launcher self-updates hourly otherwise (it did, 11-12, mid-study)

    def describe(self) -> dict:
        d = super().describe()
        d["effort"] = self.effort or "model-default (max, as audited)"
        d["muse"] = {"preset": "empty", "max_model_steps": 1,
                     "study": "agent harness (no tools sent; Muse's own instructions present); reported separately"}
        return d

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        t, pl = ev.get("payload_type"), ev.get("payload") or {}
        st = self._call.st
        if t == "run.model.configured":
            p.model, p.model_reported = str(pl.get("model_id")), True
            if pl.get("model_id") != self.model:
                p.violations.append(f"model {pl.get('model_id')!r} instead of {self.model!r}")
        elif t == "turn.input.user":
            if str(pl.get("prompt", "")).strip() != self._call.prompt.strip():
                p.violations.append("the user turn is not the task prompt as sent")
        elif t == "task.lifecycle.proposed":
            kind = (pl.get("event") or {}).get("task_kind")
            if kind != MODEL_TASK:
                p.violations.append(f"a non-model task was proposed: {kind!r}")
            st["model_tasks"] += 1
        elif t == "run.output.delta":
            st["sample"] += pl.get("text") or ""
        elif t == "task.lifecycle.failed":
            reason = str((pl.get("event") or {}).get("reason") or "")
            if reason.startswith("stream guard resample"):
                p.usage["guard_resamples"] = p.usage.get("guard_resamples", 0) + 1
                p.notes.append(f"muse discarded a sample and resampled: {reason}")
                st["sample"] = ""
            else:
                p.error = f"muse model task failed: {reason}"[:500]
        elif t == "task.lifecycle.completed":
            st["completed"] += 1
        elif t == "run.usage":
            for m in pl.get("models") or []:
                for k in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
                    if isinstance(m.get(k), int):
                        p.usage[k] = p.usage.get(k, 0) + m[k]
            p.usage["calls"] = pl.get("calls")
        elif t == "run.terminal.completed":
            st["terminal"], st["final"] = pl.get("terminal"), pl.get("text")
        elif t not in _KNOWN and _SUSPECT.search(str(t)):
            p.violations.append(f"muse event {t}")
        return None

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        st = self._call.st
        if returncode not in (0, None):
            lines = [ln for ln in stderr.strip().splitlines() if ln.strip() and not ln.startswith("muse: workspace root")]
            p.error = (lines[-1] if lines else f"exit status {returncode}")[:500]
            return
        if st["terminal"] != "completed":
            p.error = f"muse run ended {st['terminal']!r}"
            return
        if p.error:
            return
        if st["final"] is None or st["final"].strip() != st["sample"].strip():
            p.violations.append("the final answer differs from the completed sample's output")
        if st["completed"] != 1 or st["model_tasks"] != 1 + p.usage.get("guard_resamples", 0):
            p.violations.append(f"{st['model_tasks']} model tasks, {st['completed']} completed (one step expected)")
        p.text = st["final"] or ""
