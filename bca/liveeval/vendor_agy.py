"""Google Antigravity CLI (`agy`) as a tool-less subject (`agy:<model>`); see bca.liveeval.vendor_base.

Credentials. `agy` keeps its Google OAuth token in the Secret Service keyring (zalando/go-keyring over
D-Bus; the log says `ChainedAuth: authenticated via keyring`), not in a file under ~/.gemini. So the
private home holds no credential copies at all: the keyring is reached through the session bus
(DBUS_SESSION_BUS_ADDRESS / XDG_RUNTIME_DIR, both in child_env's allowlist) and a refreshed token is
written back to the keyring by agy itself. Everything else agy reads or writes (settings, agents,
rules, skills, MCP config, conversations, brain/, history, summaries, logs, the updater stamp, its
TMPDIR feature-flag cache) lives under HOME, which is the per-call private home.

Tools. The subject runs a custom agent (`--agent bca-subject`, in the private home's global config
dir) whose frontmatter excludes every default component and inherits no customizations; its body is
SYSTEM_PROMPT. `agy` 1.2.12 still declares one tool, `manage_task` (list/kill/status/send_input for
background tasks; bug #1015): with no other tool nothing can ever start a background task, so it can
reach no file, command, network or model -- and any call of it fails the run closed (a tool step).
The stream-json `init.tools` list is NOT what the model gets (it lists ~57 build tools even for this
agent), and an unknown `--agent` silently falls back to the full default agent. So the request itself
is checked after every call: agy records each model request in the private home's conversation
database (conversations/<id>.db, table gen_metadata, protobuf), and `finish` decodes it and fails
closed unless the declared tools are within AGY_ALLOWED_TOOLS, the system instruction is exactly
`<System Prompt>\\n{SYSTEM_PROMPT}\\n</System Prompt>` and every user turn is the harness prompt in
agy's fixed wrapper (AGY_USER_RE). agy streams deltas, but it writes the request record only when the
turn ends, so it is NOT killed on an early abort (`streams_text = False`): it finishes and is checked.

Model identity (A5). The record's model field (request field 19) is written on the REQUEST side: it
is the base id agy asked for (`gemini-3.8-flash` for `gemini-3.8-flash-high`), never a name the API
sent back, and neither the stream-json events nor the record hold a response-side model name
(checked on agy 1.2.12). So the model is "requested, not reported"; a field 19 that is neither the
requested id nor its base is noted and recorded as the model agy actually asked for.

Safety blocks (Parsed.refusal). agy 1.2.12 surfaces a content-filter stop (its changelog: "the CLI now
surfaces a clear content-filter stop reason"; the binary's enums STOP_REASON_CONTENT_FILTER,
FINISH_REASON_SAFETY/RECITATION/PROHIBITED_CONTENT, Gemini block reasons SAFETY, BLOCKLIST,
PROHIBITED_CONTENT, SPII, IMAGE_SAFETY) in a failed result or an AGY_ERROR line; the exact wording was
not captured, so AGY_SAFETY_RE matches those names and plain "content filter" / "safety" wording in
error, status and stop-reason fields only (never the answer text). Any of them is a scored refusal.

Unremovable extras the model sees (A2), from the recorded request: the system prompt is wrapped in
`<System Prompt>` tags; the user turn is wrapped as `<USER_REQUEST>\\n{prompt}\\n</USER_REQUEST>`
followed by `<ADDITIONAL_METADATA>` (the local time) and a `<USER_SETTINGS_CHANGE>` note naming the
model; and the manage_task declaration (~540 tokens by agy's own count).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

# Safety-block markers (module docstring), matched against error / status / stop-reason fields.
AGY_SAFETY_RE = re.compile(
    r"CONTENT_FILTER|content[ _-]?filter|FINISH_REASON_(?:SAFETY|RECITATION|PROHIBITED_CONTENT|BLOCKLIST|SPII)|"
    r"\b(?:IMAGE_)?(?:SAFETY|RECITATION|PROHIBITED_CONTENT|BLOCKLIST|SPII)\b|safety (?:filter|polic|reason|system)|"
    r"blocked (?:by|due to) (?:the )?(?:content |safety )|prohibited content",
    re.I,
)
# The event fields that can carry a stop reason or an error (never the answer text).
_AGY_REASON_KEYS = ("error", "status", "stop_reason", "finish_reason", "block_reason", "short_error", "reason",
                    "status_detail")

AGY_AGENT_NAME = "bca-subject"
AGY_AGENT = f"""---
name: {AGY_AGENT_NAME}
description: Tool-less answerer for the BytecodeAgent live evaluation.
mainAgent: true
excludeDefaultComponents: true
inheritCustomizations: false
inheritMcp: false
tools: []
agents: []
skills: []
rules: []
---
{SYSTEM_PROMPT}
"""
# `useG1Credits` false (the default, stated): never spend paid credits once the plan quota is gone.
AGY_SETTINGS = {"useG1Credits": False}
# Declared by agy 1.2.12 even with excludeDefaultComponents (see the module docstring).
AGY_ALLOWED_TOOLS = frozenset({"manage_task"})
AGY_SYSTEM = f"<System Prompt>\n{SYSTEM_PROMPT}\n</System Prompt>"
AGY_STEP_TYPES = frozenset({"user_input", "agent_response"})
AGY_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-max")


def agy_user_re(prompt: str) -> re.Pattern:
    """agy's fixed wrapper around the harness prompt (1.2.12): anything else in the user turn fails closed."""
    return re.compile(
        r"<USER_REQUEST>\n\s*" + re.escape(prompt.strip()) + r"\s*\n</USER_REQUEST>\n"
        r"<ADDITIONAL_METADATA>\nThe current local time is: [^\n<]*\n</ADDITIONAL_METADATA>"
        r"(?:\n<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from [^\n<]*\n</USER_SETTINGS_CHANGE>)?",
    )


# -- the recorded model request: a minimal protobuf wire decoder -----------------------------------


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def pb_fields(b: bytes) -> list[tuple[int, int, object]] | None:
    """(field, wire type, value) of one protobuf message, or None if `b` is not a well-formed one."""
    out, i = [], 0
    try:
        while i < len(b):
            key, i = _varint(b, i)
            f, wt = key >> 3, key & 7
            if f == 0:
                return None
            if wt == 0:
                v, i = _varint(b, i)
            elif wt == 1:
                v, i = b[i:i + 8], i + 8
            elif wt == 5:
                v, i = b[i:i + 4], i + 4
            elif wt == 2:
                n, i = _varint(b, i)
                v = b[i:i + n]
                if len(v) != n:
                    return None
                i += n
            else:
                return None
            out.append((f, wt, v))
    except IndexError:
        return None
    return out


def _sub(fields, num: int) -> list[bytes]:
    return [v for f, wt, v in fields if f == num and wt == 2]


def _str(fields, num: int) -> str:
    vals = _sub(fields, num)
    return vals[0].decode("utf-8", "replace") if vals else ""


def read_requests(db: Path) -> list[dict]:
    """The model requests agy recorded for one conversation: system prompt, tool names, chat messages
    (role, text) and the model id agy asked for (field 19: request side). Raises ValueError when the layout is not the
    one this adapter was written against (a new agy release): the caller fails closed."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in con.execute("SELECT data FROM gen_metadata ORDER BY idx")]
    finally:
        con.close()
    reqs = []
    for data in rows:
        if not isinstance(data, bytes):
            raise ValueError(f"gen_metadata row is {type(data).__name__}, not a protobuf blob")
        top = pb_fields(data)
        req = pb_fields(_sub(top, 1)[0]) if top and _sub(top, 1) else None
        if not req or not _sub(req, 1):
            raise ValueError("gen_metadata row without a request record")
        msgs = []
        for m in _sub(req, 2):
            mf = pb_fields(m)
            if mf is None:
                raise ValueError("unparseable chat message")
            role = next((v for f, wt, v in mf if f == 2 and wt == 0), 0)
            msgs.append((role, _str(mf, 3)))
        tools = []
        for t in _sub(req, 8):
            tf = pb_fields(t)
            if tf is None or not _str(tf, 1):
                raise ValueError("unparseable tool declaration")
            tools.append(_str(tf, 1))
        reqs.append({"system": _str(req, 1), "tools": tools, "messages": msgs, "model": _str(req, 19)})
    if not reqs:
        raise ValueError("no model request recorded")
    return reqs


class AgySubject(CliSubject):
    vendor = "agy"
    binary_name = "agy"
    efforts = ("low", "medium", "high", "max")  # `--effort`; `agy models` ids carry it as a suffix
    audited_versions = ("1.2.12",)
    # Not killed on an early abort: agy writes its request record (conversations/*.db) only when the turn
    # ends, so a killed call could never be verified (seen live, 2026-09-28). The rest of the answer is
    # read but never scored: the scorer already stopped.
    streams_text = False
    # No credential files: the token lives in the keyring (module docstring).
    home_files = ()
    home_required = ()
    home_sync = ()

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        self._call = threading.local()  # the private home and prompt of the call running in this thread

    def default_home(self) -> Path:
        # Only CredHome's lock file lands here: agy's state is never read from the real home.
        return Path("~/.gemini/antigravity-cli").expanduser()

    def prepare_home(self, home: Path) -> None:
        try:
            agents = home / ".gemini" / "config" / "agents"
            agents.mkdir(parents=True, mode=0o700)
            (agents / f"{AGY_AGENT_NAME}.md").write_text(AGY_AGENT)
            app = home / ".gemini" / "antigravity-cli"
            app.mkdir(parents=True, mode=0o700)
            (app / "settings.json").write_text(json.dumps(AGY_SETTINGS) + "\n")
            # The background self-updater skips its check when this stamp is < 15 minutes old.
            (app / "last_check.timestamp").touch()
            (home / "tmp").mkdir(mode=0o700)
        except OSError as e:
            raise IsolationError(f"isolation: cannot set up the agy home {home}: {e}") from e
        self._call.home = home

    def argv(self, prompt: str, home: Path) -> list[str]:
        self._call.prompt = prompt
        argv = [self.binary, "--output-format", "stream-json", "--agent", AGY_AGENT_NAME,
                "--disable-slash-commands", "--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        return argv + [f"--print={prompt}"]  # `=`: a prompt starting with '-' is not a flag

    def extra_env(self, home: Path) -> dict[str, str]:
        return {"HOME": str(home), "TMPDIR": str(home / "tmp"), "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"), "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_STATE_HOME": str(home / ".local/state")}

    def describe(self) -> dict:
        d = super().describe()
        variant = next((s[1:] for s in AGY_EFFORT_SUFFIXES if self.model.endswith(s)), "")
        if variant:
            d["model_effort_variant"] = variant
        return d

    @staticmethod
    def _safety(obj, p: Parsed, where: str) -> None:
        """A safety block named in an event's reason / error fields (or a stderr line) is a refusal; the
        first one wins (nothing after it is scored)."""
        vals = [obj] if isinstance(obj, str) else \
            [str(obj.get(k)) for k in _AGY_REASON_KEYS if isinstance(obj, dict) and obj.get(k) not in (None, "")]
        for v in vals:
            if AGY_SAFETY_RE.search(v) and not p.refusal:
                p.refusal = f"safety: {where}: {v.strip()[:300]}"

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        typ = ev.get("event")
        body = ev.get(typ) if isinstance(typ, str) else None
        if typ in ("init", "step_update", "result") and not isinstance(body, dict):
            p.violations.append(f"malformed {typ} event: {json.dumps(ev)[:200]}")
            return None
        if typ == "init":
            if body.get("agent") != AGY_AGENT_NAME:
                p.violations.append(f"agent {body.get('agent')!r} instead of {AGY_AGENT_NAME}")
            tools = body.get("tools")
            if tools is not None and not isinstance(tools, list):
                p.violations.append(f"malformed init tools {json.dumps(tools)[:120]}")
                return None
            # init.tools lists the build's tools, not the agent's (the request check in finish decides)
            p.notes.append(f"init: {len(tools or [])} build tools advertised")
        elif typ == "step_update":
            st = body.get("step_type")
            if st not in AGY_STEP_TYPES:
                p.violations.append(f"step {st!r}: {json.dumps(body)[:200]}")
                return None
            self._safety(body, p, f"step {st}")
            if st == "agent_response":
                if body.get("usage"):
                    if not isinstance(body["usage"], dict):
                        p.violations.append(f"malformed step usage {json.dumps(body['usage'])[:120]}")
                        return None
                    p.usage = body["usage"]
                delta = body.get("text_delta")
                if delta is not None and not isinstance(delta, str):
                    p.violations.append(f"malformed text_delta {json.dumps(delta)[:120]}")
                    return None
                if delta:
                    p.text += delta
                    return delta
        elif typ == "result":
            if body.get("usage"):
                if not isinstance(body["usage"], dict):
                    p.violations.append(f"malformed result usage {json.dumps(body['usage'])[:120]}")
                    return None
                p.usage = body["usage"]
            self._safety(body, p, "result")
            if body.get("status") != "SUCCESS":
                p.error = f"agy {body.get('status') or 'no status'}: {body.get('error') or ''}".strip()
                return None
            final = body.get("response")
            if final is not None and not isinstance(final, str):
                p.violations.append(f"malformed result response {json.dumps(final)[:120]}")
                return None
            if isinstance(final, str) and final != p.text:
                if final.startswith(p.text):  # deltas missed the tail: deliver it
                    rest = final[len(p.text):]
                    p.text = final
                    return rest
                p.notes.append("result.response differs from the streamed text")
        else:
            p.violations.append(f"unrecognised event {typ!r}")
        return None

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        agy_err = [ln.split(":", 1)[1].strip() for ln in stderr.splitlines() if ln.startswith("AGY_ERROR:")]
        for line in agy_err:
            self._safety(line, p, "AGY_ERROR")
        failed = returncode not in (0, None) or bool(p.error)
        if failed:
            if agy_err:
                p.error = f"exit status {returncode}: AGY_ERROR {agy_err[-1]}" + (f" ({p.error})" if p.error else "")
            elif not p.error:
                lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
                p.error = f"exit status {returncode}: " + ("\n".join(lines[-6:]) or "no stderr")[-500:]
        # A scored call -- an answer, an early-aborted answer (returncode None: killed by the harness)
        # or a refusal -- must show a clean request; a failed call has nothing to score.
        if not failed or p.refusal:
            self.verify_request(p)
        if p.violations:
            p.refusal = ""  # an isolation failure is never scored, not even as a refusal

    def verify_request(self, p: Parsed) -> None:
        """Fail closed unless the recorded request carried only allowed tools, our system prompt and
        the harness prompt (the only evidence of what the model got: see the module docstring)."""
        home, prompt = getattr(self._call, "home", None), getattr(self._call, "prompt", None)
        dbs = sorted((home / ".gemini" / "antigravity-cli" / "conversations").glob("*.db")) if home else []
        if len(dbs) != 1:
            p.violations.append(f"cannot verify the request: {len(dbs)} conversation databases")
            return
        try:
            reqs = read_requests(dbs[0])
        except (sqlite3.Error, ValueError) as e:
            p.violations.append(f"cannot verify the request: {e}")
            return
        user_re = agy_user_re(prompt or "")
        for r in reqs:
            extra = sorted(set(r["tools"]) - AGY_ALLOWED_TOOLS)
            if extra:
                p.violations.append("request declares tools " + ",".join(extra))
            if r["system"] != AGY_SYSTEM:
                p.violations.append(f"system prompt is not SYSTEM_PROMPT: {r['system'][:160]!r}")
            for role, text in r["messages"]:
                if role == 1 and not user_re.fullmatch(text):
                    p.violations.append(f"user turn is not the harness prompt: {text[:160]!r}")
                elif role not in (1, 2):
                    p.violations.append(f"chat message with role {role}: {text[:120]!r}")
        # Field 19 is what agy asked for (request side), not what served the call: model_reported stays
        # False. It names the base model; the requested id may carry the effort variant as a suffix.
        asked = reqs[-1]["model"]
        base = next((self.model[: -len(s)] for s in AGY_EFFORT_SUFFIXES if self.model.endswith(s)), self.model)
        if asked and asked not in (self.model, base):
            p.model = asked
            p.notes.append(f"agy requested {asked} for {self.model}")
