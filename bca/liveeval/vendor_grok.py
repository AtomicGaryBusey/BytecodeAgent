"""xAI Grok Build CLI (`grok`) as a tool-less subject (`grok:<model>`); see bca.liveeval.vendor_base.

Run: `grok --agent <home>/bca-subject.md --output-format streaming-json --disable-web-search
--no-subagents --no-memory --no-auto-update --no-leader --verbatim -m MODEL [--effort E] --single=PROMPT`
with GROK_HOME = the private home (only auth.json copied in) and HOME / TMPDIR / XDG_* = directories
inside it: grok 1.0.41 reads ~/.claude (hooks, rules, CLAUDE.md, skills, MCP), ~/.cursor and codex
files from HOME and writes sessions, logs, caches and bundled skills under GROK_HOME; none of the
user's reach the subject (checked with strace: no access under the real home). The GROK_*_ENABLED
compat switches, GROK_WORKFLOWS, GROK_MEMORY, telemetry and the auto-updater are also turned off.
XAI_API_KEY never reaches the child (child_env's allowlist drops it). `--verbatim` sends the prompt
without the `<user_query>` wrapper. Retries are the harness's: GROK_MAX_RETRIES=1 (grok's default is
15, and it retries a 429 once) and GROK_TURN_TRANSIENT_RETRY=0.

Tools. The agent file (GROK_AGENT) runs in `promptMode: full` (its body IS the system prompt) with
`injectDefaultTools: false`; grok refuses an empty curated toolset, so it declares one harmless tool
(todo_write) and removes it again with `disallowedTools`, which also names the two hosted (server-side)
tools grok would otherwise attach to a Responses API request: web_search and x_search
(xai-grok-agent builder.rs: `hosted_tool_allowed`). `tools: []` is NOT an empty allowlist (it means
"inherit all"), and `--tools ""` means every tool. grok records each call in the private home:
sessions/<cwd>/<id>/{system_prompt.txt, tool_definitions.json, prompt_context.json, chat_history.jsonl,
signals.json, updates.jsonl, summary.json}. `finish` reads that record before the home is deleted and
fails CLOSED unless: the function tool list sent is `[]`; the system prompt is exactly SYSTEM_PROMPT
(prompt_context: promptMode full, no AGENTS.md, memory off); the conversation holds only the system
prompt, grok's empty first-user-message prefix, the harness prompt verbatim, reasoning items and
assistant messages without tool calls -- a hosted tool call (web_search_call, x_search, code
interpreter) is recorded as a `backend_tool_call` item and fails the call; signals.json counts no tool
call; updates.jsonl and events.jsonl hold only allowlisted kinds (GROK_UPDATES, GROK_SESSION_EVENTS: one
turn, one sampling loop, no tool / permission / MCP event or tool phase). Per call, the request is also
bounded (A1): exactly one main-loop model call (end.num_turns, which counts main-loop calls only, and
usage.json modelCalls), made by the served model only, and at most grok_input_bound(prompt) input tokens
with at most GROK_SERVER_PREFIX (+slack) of them cached -- a hidden tool round, a second model or injected
context fails closed. The stream itself is allowlisted (text, thought, usage, end, error,
available_commands with an empty tool list); anything else fails closed.
Gap (documented, not per-call): the hosted-tool declaration is not in grok's record. It was captured at
the wire (a loopback recording proxy, 2026-09-28, grok-4.7): the /v1/responses body has no `tools` key
and the response echoes `"tools": []`, `num_server_side_tools_used: 0`; per call only a hosted tool's
use is detectable (backend_tool_call).

Side requests. grok titles every new session with a second model call (grok-4.6, a `session_title`
function tool, the prompt in `<user_query>`; session/summary.rs). There is no switch: SummaryGenerator runs
on the first non-empty user chunk of every new session (it is skipped only for a resumed session that
already has a title), and the summary model always resolves to some id (config/mod.rs: CLI > env >
config.toml > remote settings > grok-4.6). GROK_SESSION_SUMMARY_MODEL (env beats remote settings) points
it at a model id that does not exist, so the endpoint answers 404 before any inference (captured:
`{"code":"not-found","error":"The model bca-disabled does not exist ..."}`); the prompt is still sent to
xAI in that rejected request. grok then stores the prompt's first ten words as the title, which `finish`
checks (summary.json generated_title == grok_title_fallback(prompt), else the side call was answered:
fail closed); a failed side call is not counted in num_turns / modelUsage. Title refresh, recaps, turn summaries, prompt
suggestions and workflow listings are switched off; config.toml ignores every skill path (grok installs
bundled skills into its home and lists them in a `<system-reminder>`, which `discoverSkills: false`
does not stop -- seen once live and failed closed).

Extras the model sees (A2), from the captured request: [system SYSTEM_PROMPT, user "", user prompt].
The empty user message is grok's first-user-message prefix, rendered empty by the agent's
`userMessageTemplate` instead of the default `<user_info>` block (OS, shell, cwd, date, rules); grok
trims the prompt's surrounding whitespace (the harness prompt's final newline). Unremovable: the xAI
endpoint prepends its own server-side context, 1152 cached input tokens on every grok-4.7 call
(a 40-token request is billed ~1320-1400 input tokens), not visible to the client.

Model and effort (A5, A6). The assistant record carries the Responses API response's `model`
(`grok-4.7-build` for `-m grok-4.7`, `grok-4.7-build-fast` for grok-4.7-build-fast), its system
fingerprint and the reasoning effort the server applied (`response.reasoning.effort`): the served
model is "reported". Every model in the 1.0.41 catalog defaults to effort `high` (seen live: `high`
served without --effort); `--effort` takes low / medium / high / xhigh (grok-4.5: no xhigh -- grok exits
with "--effort/--reasoning-effort: unknown effort level 'xhigh'; use one of: high, medium, low"). grok
soft-ignores an effort on a model without effort support, so an explicit effort served at another level
or not reported is an error ("grok effort mismatch: ...", fatal); without --effort the served effort is
noted as the CLI default. The served id must be the requested one or its documented alias
(GROK_SERVED_AS); anything else is "model rerouted: requested X, grok served Y" (fatal).

Errors (A7). grok exits 1 with `Error: <message>` on stderr and a `{"type":"error"}` line. Verified
with a fault-injecting proxy (no model call): free quota -> "You’ve reached your free Grok Build usage
limit for now. ..."; 429 without detail -> "Request failed (HTTP 429)."; 429 with a server detail -> the
detail verbatim (the SuperGrok plan text "You’ve hit the rate limit for your plan. Upgrade your account
or try again later." is grok's fallback for an OAuth rate limit); 401 -> grok refreshes the token and
retries 3 times, then "Internal error: {"message": "Auth recovery succeeded but N authenticated inference
requests were still rejected (401) ...", "http_status": 401}"; 403/400/500 -> "Internal error:
{"message": "API error (status 403 Forbidden): ...", "http_status": 403}". Safety blocks: a refused
turn ends with stopReason `refusal` (Parsed.refusal); a Responses `content_filter` stop is mapped to it;
and xAI's content-safety check answers HTTP 403 ("403 is content-safety, never auth", sampling/error.rs;
e.g. "Content violates usage guidelines. Failed check: SAFETY_CHECK_TYPE_DATA_LEAKAGE"): a 403 whose body
is not about the account (grok_safety_403: not a subscription / permission_denied / credits / ZDR /
sign-in text) is a scored refusal; the account 403s stay errors.

Credentials: auth.json maps "<issuer>::<client>" scopes to {key, refresh_token, expires_at (RFC 3339
string, ~6 h)}; grok refreshes when fewer than 300 s remain (GROK_AUTH_EARLY_INVALIDATION_SECS) or after a
401, rotating the refresh token, and rewrites auth.json: CredHome copies it back (cred_expiry).

Side effect outside the private home: grok 1.0.41 creates an empty `/tmp/sessions/<session id>`
directory (a hard-coded path, not TMPDIR; strace); `cleanup` (after every call, timeouts included)
removes it (and /tmp/sessions when empty and no other grok call of this process is running). It also runs `bash -lc` (reading /etc/profile and the
private HOME's absent .bashrc) to capture a shell environment, and `git branch` in the empty workdir.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

GROK_AGENT_NAME = "bca-subject"
# Hosted (server-side) tools grok attaches for backend-search models; removed by name.
GROK_HOSTED_TOOLS = ("web_search", "x_search")
GROK_AGENT = f"""---
name: {GROK_AGENT_NAME}
description: Tool-less answerer for the BytecodeAgent live evaluation.
promptMode: full
injectDefaultTools: false
toolConfig:
  tools:
    - id: GrokBuild:todo_write
tools: []
disallowedTools: [todo_write, {", ".join(GROK_HOSTED_TOOLS)}]
skills: []
discoverSkills: false
inheritSkills: false
agentsMd: false
mcpInheritance: none
mcpServers: []
userMessageTemplate:
  custom: ""
---
{SYSTEM_PROMPT}
"""
GROK_FLAGS = ("--output-format", "streaming-json", "--disable-web-search", "--no-subagents", "--no-memory",
              "--no-auto-update", "--no-leader", "--verbatim")
_COMPAT = [f"GROK_{v}_{s}_ENABLED" for v in ("CLAUDE", "CURSOR", "CODEX")
           for s in ("SKILLS", "RULES", "AGENTS", "MCPS", "HOOKS", "SESSIONS")]
# Side calls grok makes besides the subject's request: the session title is generated by another model
# from the prompt on every new session (session/summary.rs, no switch); pointing it at a model id that does
# not exist makes the proxy reject it before any inference. Title refresh, recaps, turn summaries and
# prompt suggestions are switched off.
GROK_NO_MODEL = "bca-disabled"
GROK_ENV = {"GROK_TELEMETRY_ENABLED": "0", "GROK_DISABLE_AUTOUPDATER": "1", "GROK_MAX_RETRIES": "1",
            "GROK_TURN_TRANSIENT_RETRY": "0", "GROK_WORKFLOWS": "0", "GROK_MEMORY": "0",
            "GROK_SESSION_SUMMARY_MODEL": GROK_NO_MODEL, "GROK_TITLE_REFRESH": "0", "GROK_SESSION_RECAP": "0",
            "GROK_TURN_SUMMARY": "0", "GROK_PROMPT_SUGGESTIONS": "0", "GROK_SUGGESTIONS": "0",
            **dict.fromkeys(_COMPAT, "0")}
# config.toml in the private home: every skill path is ignored (grok installs bundled skills into its home
# and lists them to the model in a <system-reminder>; `discoverSkills: false` does not stop that).
GROK_CONFIG = '[skills]\nignore = ["/"]\n'
GROK_EARLY_INVALIDATION_S = 300.0  # xai-grok-login model.rs DEFAULT_EARLY_INVALIDATION_SECS
GROK_DEFAULT_EFFORT = "high"  # every model in the 1.0.41 catalog (models_cache.json reasoning_effort)
# streaming-json line types (xai-grok-pager headless/reducer/acp.rs) that carry no tool: anything else fails closed.
GROK_EVENTS = frozenset({"text", "thought", "usage", "end", "error", "available_commands"})
GROK_REFUSAL_STOPS = frozenset({"refusal", "content_filter"})
# chat_history.jsonl item types a tool-less turn records; a tool / hosted tool adds others.
GROK_ITEMS = frozenset({"system", "user", "reasoning", "assistant"})
_ASSISTANT_KEYS = frozenset({"type", "content", "model_id", "model_fingerprint", "reasoning_effort"})
# updates.jsonl (method, sessionUpdate) pairs a tool-less turn writes (every live and fault-injected call, 1.0.41):
# the prompt echo, answer and reasoning chunks, grok's retry notice and the turn end. Anything else
# (tool_call, tool_call_update, plan, available_commands_update, ...) fails closed.
GROK_UPDATES = frozenset({("session/update", "user_message_chunk"), ("session/update", "agent_message_chunk"),
                          ("session/update", "agent_thought_chunk"), ("_x.ai/session/update", "turn_completed"),
                          ("_x.ai/session/update", "retry_state")})
# events.jsonl (xai-grok-session-events types.rs `Event`, tag "type") of a single sampling loop; tool_started /
# tool_completed / permission_* / mcp_* / goal_* and the tool_execution / permission_prompt phases fail closed.
GROK_SESSION_EVENTS = frozenset({"turn_started", "loop_started", "phase_changed", "first_token", "turn_ended"})
GROK_PHASES = frozenset({"waiting_for_model", "streaming_reasoning", "streaming_text"})
# The model the endpoint serves for a requested id (the Responses API `model`, recorded as model_id), seen live
# 2026-09-28: `-m grok-4.7` -> grok-4.7-build, `-m grok-4.7-build-fast` -> grok-4.7-build-fast. Any other served
# id is a substitution ("model rerouted", fatal). A model not listed here must be served under its own id.
GROK_SERVED_AS = {"grok-4.7": "grok-4.7-build", "grok-4.7-build-fast": "grok-4.7-build-fast"}
# Input-token guard (A1: a hidden tool / hosted-tool declaration / injected context would inflate the request).
# Every grok-4.7 call billed 1152 cached input tokens (the endpoint's own server-side prefix), or 0 cached on a
# cold cache, and never more -- also for an identical prompt sent twice (sum_to, 1266 chars: 488 + 1152 both
# times). Total input (uncached + cached) is bounded like Codex's: 2/3 token per character of SYSTEM_PROMPT +
# prompt (real prompts run ~0.3), plus 800, plus that prefix. Seen: 30-char prompt 1289 (bound 2062), sum_to
# 1640 (bound 2886), the longest harness prompt so far (33862 chars) bounds at ~24.6k.
GROK_SERVER_PREFIX = 1152
GROK_CACHE_SLACK = 128
_PERMISSION_403 = re.compile(  # a 403 about the account, not the content (xai-grok-shell sampling/error.rs)
    r"subscription|permission[_ ]?denied|not signed in|sign in|log ?in|logout|auth(?:entic|ori[sz])|credential|"
    r"api[ _-]?key|"
    r"credits?\b|billing|spend|quota|usage limit|\bzdr\b|data retention|remote[ _-]?settings|\bteam\b|"
    r"organi[sz]ation|region|country|suspend|banned|blocked user", re.I)
SESSIONS_TMP = Path("/tmp/sessions")  # grok 1.0.41 creates /tmp/sessions/<id> (module docstring)


def grok_safety_403(message: str) -> str:
    """The xAI content-safety explanation if `message` (a grok error) is an HTTP 403 that is not about the
    account, else "". grok 1.0.41 surfaces every 403 as `Internal error: {"message": "API error (status 403
    Forbidden): <body>", "http_status": 403}`; xai-grok-shell sampling/error.rs: "403 is content-safety, never
    auth" -- its example body is "Content violates usage guidelines. Failed check: SAFETY_CHECK_TYPE_DATA_LEAKAGE".
    The account 403s (".. requires a Grok subscription", "permission_denied: ...", "run out of credits", ZDR /
    remote-settings blocks) stay errors (fatal in the runner)."""
    if not re.search(r'"http_status"\s*:\s*403\b|\(status 403\b', message or ""):
        return ""
    inner = message
    m = re.search(r"\{.*\}", message, re.S)
    if m:
        try:
            inner = str(json.loads(m.group(0)).get("message") or message)
        except (ValueError, AttributeError):
            pass
    body = re.sub(r"^.*?\(status 403[^)]*\):\s*", "", inner, flags=re.S).strip()
    if not body or _PERMISSION_403.search(body):
        return ""
    return body


def grok_input_bound(prompt: str) -> int:
    """Most input tokens (uncached + cached) a tool-less grok request for `prompt` may bill (GROK_SERVER_PREFIX)."""
    return GROK_SERVER_PREFIX + (len(SYSTEM_PROMPT) + len(prompt)) * 2 // 3 + 800


def grok_title_fallback(prompt: str) -> str:
    """The title grok stores when its title side call fails: the prompt's first ten words (session_summary.rs
    title_fallback_from_user_text; a harness prompt has no system-reminder or skill markup to strip)."""
    return " ".join(prompt.split()[:10]) or "New session"


def _rfc3339(s: str) -> float:
    """Epoch seconds of an RFC 3339 timestamp with up to nanosecond precision."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", s.strip())
    if not m:
        raise ValueError(f"not an RFC 3339 timestamp: {s!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = "+00:00" if m.group(3) == "Z" else m.group(3)
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}").timestamp()


class GrokSubject(CliSubject):
    vendor = "grok"
    binary_name = "grok"
    efforts = ("low", "medium", "high", "xhigh")  # `--effort` (models_cache.json reasoning_efforts)
    audited_versions = ("1.0.41",)
    # Not killed on an early abort: the assistant turn (and any hosted tool call) is recorded only when the
    # response completes, so a killed call could not be verified. The scorer stops reading; grok finishes.
    streams_text = False
    home_files = ("auth.json",)
    home_required = ("auth.json",)
    home_sync = ("auth.json",)
    _inflight = 0
    _inflight_lock = threading.Lock()

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        self._call = threading.local()  # the private home, prompt and session id of this thread's call
        self._catalog: dict = {}

    def default_home(self) -> Path:
        return Path(os.environ.get("GROK_HOME") or "~/.grok").expanduser()

    def prepare_home(self, home: Path) -> None:
        try:
            (home / GROK_AGENT_NAME).with_suffix(".md").write_text(GROK_AGENT)
            (home / "config.toml").write_text(GROK_CONFIG)
            for d in ("home", "tmp", "home/.config", "home/.cache", "home/.local/share", "home/.local/state"):
                (home / d).mkdir(parents=True, mode=0o700)
        except OSError as e:
            raise IsolationError(f"isolation: cannot set up the grok home {home}: {e}") from e
        self._call.home = home
        self._call.session = None
        self._call.end = None
        self._call.blocked = False  # an HTTP 403 content-safety block (grok_safety_403)

    def argv(self, prompt: str, home: Path) -> list[str]:
        if prompt.startswith("/"):
            prompt = " " + prompt  # a leading slash would select a command (/compact, /goal ...)
        self._call.prompt = prompt
        argv = [self.binary, "--agent", str(home / f"{GROK_AGENT_NAME}.md"), *GROK_FLAGS, "-m", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        return argv + [f"--single={prompt}"]  # `=`: a prompt starting with '-' is not a flag

    def extra_env(self, home: Path) -> dict[str, str]:
        h = home / "home"
        return {"GROK_HOME": str(home), "HOME": str(h), "TMPDIR": str(home / "tmp"),
                "XDG_CONFIG_HOME": str(h / ".config"), "XDG_CACHE_HOME": str(h / ".cache"),
                "XDG_DATA_HOME": str(h / ".local/share"), "XDG_STATE_HOME": str(h / ".local/state"), **GROK_ENV}

    def cred_expiry(self, rel: str, data: bytes) -> float | None:
        """When grok will refresh (the earliest `expires_at` of auth.json's scopes minus 300 s), or None.
        Token values are never read."""
        try:
            auth = json.loads(data)
        except ValueError:
            return None
        exps = []
        for scope in (auth.values() if isinstance(auth, dict) else ()):
            exp = scope.get("expires_at") if isinstance(scope, dict) else None
            if isinstance(exp, str):
                try:
                    exps.append(_rfc3339(exp))
                except ValueError:
                    continue
        return min(exps) - GROK_EARLY_INVALIDATION_S if exps else None

    def describe(self) -> dict:
        d = super().describe()
        d["cli_default_effort"] = self._catalog.get("default_effort") or f"{GROK_DEFAULT_EFFORT} (1.0.41 catalog)"
        if self._catalog.get("efforts"):
            d["model_efforts"] = self._catalog["efforts"]
        return d

    # -- the stream -------------------------------------------------------------------------------

    @staticmethod
    def _refuse(p: Parsed, why: str) -> None:
        if not p.refusal:
            p.refusal = why[:300]

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        typ = ev.get("type")
        if typ not in GROK_EVENTS:
            p.violations.append(f"unexpected streaming-json event {json.dumps(ev)[:160]}")
            return None
        if typ == "available_commands":
            if ev.get("tools") != []:
                p.violations.append(f"tools advertised: {json.dumps(ev.get('tools'))[:160]}")
        elif typ in ("text", "thought"):
            data = ev.get("data")
            if not isinstance(data, str):
                p.violations.append(f"malformed {typ} event {json.dumps(ev)[:160]}")
                return None
            if typ == "text" and data:
                p.text += data
                return data
        elif typ == "usage":
            if ev.get("stopReason") in GROK_REFUSAL_STOPS:
                self._refuse(p, f"refusal: grok stop reason {ev['stopReason']}")
        elif typ == "end":
            stop = ev.get("stopReason")
            self._call.session = ev.get("sessionId")
            self._call.end = ev
            if stop in GROK_REFUSAL_STOPS:
                self._refuse(p, f"refusal: grok stop reason {stop}")
            elif stop == "max_tokens":
                p.notes.append("stopped at max_tokens")
            elif stop != "end_turn":
                p.error = p.error or f"grok turn ended with stopReason {stop!r}"
            usage = ev.get("usage")
            if usage is not None and not isinstance(usage, dict):
                p.violations.append(f"malformed end usage {json.dumps(usage)[:120]}")
                return None
            p.usage = {k: v for k, v in (usage or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if isinstance(ev.get("total_cost_usd"), (int, float)):
                p.usage["total_cost_usd"] = ev["total_cost_usd"]
            if isinstance(ev.get("modelUsage"), dict):
                p.usage["model_usage"] = sorted(ev["modelUsage"])
        elif typ == "error":
            msg = str(ev.get("message"))
            safety = grok_safety_403(msg)
            if safety:  # a scored refusal, as for Claude; the neutral error keeps the 403 text out of _FATAL_RE
                self._refuse(p, f"refusal: xAI content-safety block: {safety}")
                self._call.blocked = True
                p.error = p.error or "grok: the answer was blocked by xAI content safety (see refusal)"
            else:
                p.error = p.error or f"grok: {msg}"
        return None

    # -- after the call: grok's own record of the request -----------------------------------------

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if returncode not in (0, None) and not p.error and not p.refusal:
            lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
            p.error = f"exit status {returncode}: " + ("\n".join(lines[-6:]) or "no stderr")[-500:]
        home = getattr(self._call, "home", None)
        if home is None:
            p.violations.append("cannot verify the request: private home unknown")
        else:  # every call, failed ones too: a request with tools is fatal whatever the outcome
            # a safety 403 answered nothing: its record is checked when present (grok 1.0.41 writes one before
            # sending), but a missing one does not turn a content block into a run abort
            self.audit_session(home, getattr(self._call, "prompt", ""), p,
                               required=(returncode in (0, None) and not p.error)
                               or (bool(p.refusal) and not getattr(self._call, "blocked", False)))
        if p.violations:
            p.refusal = ""  # an isolation failure is never scored, not even as a refusal

    def audit_session(self, home: Path, prompt: str, p: Parsed, *, required: bool = True) -> None:
        """Check grok's record of the call (module docstring). `required`: the call produced something
        scored, so the record must exist and hold an answer; a failed call is checked for whatever it
        recorded."""
        self._read_catalog(home)
        dirs = sorted(d for d in home.glob("sessions/*/*") if d.is_dir() and (d / "tool_definitions.json").exists())
        if len(dirs) != 1:
            if dirs or required:
                p.violations.append(f"cannot verify the request: {len(dirs)} grok session records")
            return
        d = dirs[0]
        self._call.session = self._call.session or d.name
        bad = p.violations.append
        try:
            tools = json.loads((d / "tool_definitions.json").read_text())
        except ValueError as e:
            bad(f"unparseable tool_definitions.json ({e})")
            tools = None
        if tools != [] and tools is not None:
            names = [str(t.get("name", t)) if isinstance(t, dict) else str(t) for t in tools] \
                if isinstance(tools, list) else [f"malformed {json.dumps(tools)[:80]}"]
            bad("tools sent: " + ",".join(names)[:200])
        sp = d / "system_prompt.txt"
        if not sp.exists() or sp.read_text() != SYSTEM_PROMPT:
            bad(f"system prompt is not SYSTEM_PROMPT: {sp.read_text()[:80] if sp.exists() else 'missing'!r}")
        self._audit_context(d / "prompt_context.json", p)
        self._audit_signals(d / "signals.json", p)
        self._audit_updates(d / "updates.jsonl", p)
        self._audit_events(d / "events.jsonl", p, required)
        summ = self._json(d / "summary.json", p)
        if isinstance(summ, dict) and summ.get("agent_name") not in (None, GROK_AGENT_NAME):
            bad(f"agent {summ.get('agent_name')!r} ran instead of {GROK_AGENT_NAME}")
        title = summ.get("generated_title") if isinstance(summ, dict) else None
        if title is not None and title != grok_title_fallback(prompt):
            # the title side call (GROK_SESSION_SUMMARY_MODEL=bca-disabled) must have failed (404): grok then
            # stores the prompt's first ten words; any other title means a model answered it
            bad(f"the session-title side call was answered (title {str(title)[:80]!r})")
        last = self._audit_history(d / "chat_history.jsonl", prompt, p, required)
        self._audit_usage(d / "usage.json", prompt, p, last, required)

    @staticmethod
    def _json(path: Path, p: Parsed):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except ValueError as e:
            p.violations.append(f"unparseable {path.name} ({e})")
            return None

    def _audit_context(self, path: Path, p: Parsed) -> None:
        ctx = self._json(path, p)
        if ctx is None:
            return
        if not isinstance(ctx, dict):
            p.violations.append(f"malformed prompt_context.json {json.dumps(ctx)[:120]}")
            return
        if ctx.get("prompt_mode") != "full" or ctx.get("prompt_body") != SYSTEM_PROMPT:
            p.violations.append(f"prompt context mode {ctx.get('prompt_mode')!r} / body not SYSTEM_PROMPT")
        for k in ("agents_md_files", "persona_summaries"):
            if ctx.get(k):
                p.violations.append(f"prompt context {k}: {json.dumps(ctx.get(k))[:120]}")
        if ctx.get("memory_enabled") or ctx.get("memory_v2_enabled"):
            p.violations.append("memory enabled in the prompt context")

    def _audit_signals(self, path: Path, p: Parsed) -> None:
        sig = self._json(path, p)
        if sig is None:
            return
        if not isinstance(sig, dict):
            p.violations.append(f"malformed signals.json {json.dumps(sig)[:120]}")
        elif sig.get("toolCallCount") or sig.get("toolsUsed") or sig.get("toolFailureCount"):
            p.violations.append(f"grok counted tool calls: {sig.get('toolCallCount')} {sig.get('toolsUsed')}")

    def _audit_updates(self, path: Path, p: Parsed) -> None:
        if not path.exists():
            return
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                kind = (r.get("method"), ((r.get("params") or {}).get("update") or {}).get("sessionUpdate"))
            except (ValueError, AttributeError):
                p.violations.append(f"unparseable updates.jsonl:{n}: {line[:120]!r}")
                continue
            if kind not in GROK_UPDATES:
                p.violations.append(f"session update {kind[1]} ({kind[0]})")

    def _audit_events(self, path: Path, p: Parsed, required: bool) -> None:
        """events.jsonl: one turn, one sampling loop, no tool / permission / MCP / goal event (allowlist)."""
        bad = p.violations.append
        if not path.exists():
            if required:
                bad("no events.jsonl in the grok session record")
            return
        turns = loops = 0
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                typ = ev.get("type")
            except (ValueError, AttributeError):
                bad(f"unparseable events.jsonl:{n}: {line[:120]!r}")
                continue
            if typ not in GROK_SESSION_EVENTS:
                bad(f"session event {typ!r}: {line[:160]}")
            elif typ == "turn_started":
                turns += 1
                if ev.get("session_relationship") != "primary" or ev.get("redirect_kind") is not None:
                    bad(f"session event {line[:160]}")
            elif typ == "loop_started":
                loops += 1
                if ev.get("loop_index") != 0:
                    bad(f"sampling loop {ev.get('loop_index')!r} (a second model round)")
            elif typ == "phase_changed" and ev.get("phase") not in GROK_PHASES:
                bad(f"session phase {ev.get('phase')!r}")
        if turns > 1 or loops > 1:
            bad(f"{turns} turns / {loops} sampling loops recorded")

    def _audit_history(self, path: Path, prompt: str, p: Parsed, required: bool) -> None:
        bad = p.violations.append
        if not path.exists():
            if required:
                bad("no chat_history.jsonl in the grok session record")
            return
        systems = prompts = 0
        assistants: list[dict] = []
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                it = json.loads(line)
            except ValueError:
                it = None
            if not isinstance(it, dict):
                bad(f"unparseable chat_history.jsonl:{n}: {line[:120]!r}")
                continue
            t = it.get("type")
            if t not in GROK_ITEMS:
                bad(f"conversation item {t!r}: {line[:160]}")
            elif t == "system":
                systems += 1
                if it.get("content") != SYSTEM_PROMPT:
                    bad(f"system item is not SYSTEM_PROMPT: {str(it.get('content'))[:80]!r}")
            elif t == "user":
                content = it.get("content")
                if not (isinstance(content, list) and all(isinstance(c, dict) and c.get("type") == "text"
                                                          and isinstance(c.get("text"), str) for c in content)):
                    bad(f"user item with non-text content {json.dumps(content)[:120]}")
                    continue
                text = "".join(c["text"] for c in content)
                extra = set(it) - {"type", "content", "prompt_index"}
                if "prompt_index" in it:
                    prompts += 1
                    if text != prompt.strip() or extra:  # grok trims the prompt's surrounding whitespace
                        bad(f"the user turn is not the prompt verbatim: {text[:100]!r} {sorted(extra)}")
                elif text or extra:  # grok's first-user-message prefix, rendered empty by the agent file
                    bad(f"unexpected context injection {sorted(extra)}: {text[:160]!r}")
            elif t == "assistant":
                if it.get("tool_calls") or set(it) - _ASSISTANT_KEYS:
                    bad(f"assistant item with {sorted(set(it) - _ASSISTANT_KEYS) or 'tool_calls'}: {line[:160]}")
                assistants.append(it)
        if systems != 1 and (required or systems):
            bad(f"{systems} system items in the conversation")
        if prompts != 1 and (required or prompts):
            bad(f"{prompts} prompt turns in the conversation")
        if not assistants:
            if required and not p.refusal:
                bad("the conversation records no assistant response")
            return None
        answer = "\n".join(str(a.get("content") or "") for a in assistants)
        if answer != p.text:
            p.notes.append("recorded assistant text differs from the streamed text")
        if len(assistants) > 1:
            bad(f"{len(assistants)} assistant messages (one model call expected)")
        last = assistants[-1]
        served, effort = last.get("model_id"), last.get("reasoning_effort")
        if isinstance(last.get("model_fingerprint"), str):
            p.usage["model_fingerprint"] = last["model_fingerprint"]
        if isinstance(served, str) and served:
            p.model, p.model_reported = served, True
            if served != self.model:
                p.notes.append(f"grok served {self.model} as {served}")
            if served not in self._served_ids():
                p.error = f"model rerouted: requested {self.model}, grok served {served}"
                p.refusal = ""  # a substitute model's refusal is not the requested model's
        else:
            p.notes.append("grok recorded no served model id")
        if isinstance(effort, str) and effort:
            p.usage["reasoning_effort"] = effort
        if self.effort and effort != self.effort:
            # grok soft-ignores an effort the model does not take: served at another effort (or none reported)
            p.error = p.error or (f"grok effort mismatch: requested {self.effort}, grok served {effort or 'none'} "
                                  f"(model {self.model} not supported at this effort)")
            p.refusal = ""
        elif not self.effort:
            p.notes.append(f"served at effort {effort or 'unreported'} (CLI default)")
        return last

    def _served_ids(self) -> set[str]:
        return {self.model, GROK_SERVED_AS.get(self.model, self.model)}

    def _audit_usage(self, path: Path, prompt: str, p: Parsed, last: dict | None, required: bool) -> None:
        """One model call, by the served model only, of a size a tool-less request can have (A1): the stream's
        end record (num_turns = main-loop model calls; side calls are not counted) and usage.json."""
        bad = p.violations.append
        answered = last is not None
        served = p.model if answered and p.model_reported else None
        allowed = {served} if served else self._served_ids()
        calls: list[int] = []
        totals: list[int] = []
        cached: list[int] = []

        def check_models(where: str, mu) -> None:
            if mu is None:
                return
            if not isinstance(mu, dict):
                bad(f"malformed {where} modelUsage {json.dumps(mu)[:120]}")
                return
            if set(mu) - allowed:
                bad(f"{where}: model calls by {sorted(set(mu) - allowed)} (only {sorted(allowed)} expected)")
            if not all(isinstance(v, dict) and isinstance(v.get("modelCalls"), int) for v in mu.values()):
                bad(f"{where}: modelUsage without modelCalls {json.dumps(mu)[:120]}")
                return
            calls.append(sum(v["modelCalls"] for v in mu.values()))

        end = getattr(self._call, "end", None)
        if isinstance(end, dict):
            nt = end.get("num_turns")
            if not isinstance(nt, int) or nt > 1 or (answered and nt != 1):
                bad(f"end record: num_turns {nt!r} (one model call expected)")
            check_models("end record", end.get("modelUsage"))
            u = end.get("usage") if isinstance(end.get("usage"), dict) else {}
            inp, cr = u.get("input_tokens"), u.get("cache_read_input_tokens") or 0
            if isinstance(inp, int) and isinstance(cr, int):
                totals.append(inp + cr)
                cached.append(cr)
        elif answered and required:
            bad("no end record in the stream")
        uj = self._json(path, p)
        if uj is None:
            if answered and required:
                bad("no usage.json in the grok session record")
        elif not isinstance(uj, dict) or not isinstance(uj.get("session"), dict):
            bad(f"malformed usage.json {json.dumps(uj)[:120]}")
        else:
            sess = uj["session"]
            mc = sess.get("modelCalls")
            if not isinstance(mc, int):
                bad(f"usage.json: modelCalls {mc!r}")
            else:
                calls.append(mc)
            check_models("usage.json", sess.get("modelUsage"))
            if isinstance(sess.get("inputTokens"), int):
                totals.append(sess["inputTokens"])
            if isinstance(sess.get("cachedReadTokens"), int):
                cached.append(sess["cachedReadTokens"])
            if len(uj.get("turns") or []) > 1:
                bad(f"usage.json: {len(uj['turns'])} turns")
        for n in calls:
            if n > 1 or (answered and n != 1):
                bad(f"{n} model calls recorded (one expected)")
                break
        bound = grok_input_bound(prompt)
        if totals and max(totals) > bound:
            bad(f"{max(totals)} input tokens billed, more than a tool-less request of this prompt can have ({bound})")
        if cached and max(cached) > GROK_SERVER_PREFIX + GROK_CACHE_SLACK:
            bad(f"{max(cached)} cached input tokens (the server prefix is {GROK_SERVER_PREFIX})")
        if answered and required and not totals:
            bad("no input-token count recorded")

    def _read_catalog(self, home: Path) -> None:
        """The model's catalog entry (default effort, efforts), from grok's models_cache.json."""
        try:
            entry = json.loads((home / "models_cache.json").read_text())["models"][self.model]["info"]
        except (OSError, ValueError, KeyError, TypeError):
            return
        if isinstance(entry, dict):
            self._catalog = {"default_effort": entry.get("reasoning_effort"),
                             "efforts": [e.get("id") for e in entry.get("reasoning_efforts") or [] if isinstance(e, dict)]}

    def cleanup(self, home: Path) -> None:
        """After every started call, timeouts included (vendor_base): remove the empty /tmp/sessions/<id> grok
        made for this call (ids from the stream and from the private home's record; only safe names), and
        /tmp/sessions itself when empty and no other grok call of this process is running."""
        sids = {getattr(self._call, "session", None)} | {d.name for d in home.glob("sessions/*/*") if d.is_dir()}
        for sid in sids:
            if not isinstance(sid, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z-]*", sid):
                continue
            try:
                (SESSIONS_TMP / sid).rmdir()  # only if empty: grok never wrote into it in a tool-less call
            except OSError:
                pass  # absent, or not empty (then not ours to delete)
        with GrokSubject._inflight_lock:
            if GrokSubject._inflight <= 1:
                try:
                    SESSIONS_TMP.rmdir()
                except OSError:
                    pass

    def complete(self, prompt, on_text=None, **kw):
        with GrokSubject._inflight_lock:
            GrokSubject._inflight += 1
        try:
            return super().complete(prompt, on_text, **kw)
        finally:
            with GrokSubject._inflight_lock:
                GrokSubject._inflight -= 1
