"""GitHub Copilot CLI as a tool-less subject (`copilot:<model>`); see bca.liveeval.vendor_base.

Run: `node copilot_driver.mjs <config>`: the Copilot SDK (@github/copilot-sdk 1.0.15, which pins CLI 1.0.89)
spawns the audited CLI runtime (`copilot --headless --stdio --no-auto-update --disable-builtin-mcps
--log-level all --log-dir <home>/logs`) and runs one session in SDK "empty" mode with:
`systemMessage: {mode: "replace", content: SYSTEM_PROMPT}`, `availableTools: []`, `excludedTools:
["builtin:*", "mcp:*", "custom:*"]`, no MCP server (the built-in GitHub MCP server is disabled), no custom
instructions, agents, skills, memory, compaction or co-author trailer, and a permission handler that rejects
everything. Why the SDK and not `copilot -p`: the CLI's prompt mode always sends Copilot's own ~17-28k-char
system prompt (a custom agent's body is only appended as subordinate `<agent_instructions>`), `--available-tools`
with an empty value restricts nothing (18 tools were sent), a custom agent's `tools: []` still leaves the
built-in `skill` and `sql` tools, and `--max-ai-credits` injects a `<session_limits_status>` user message
(all seen live, 2026-09-30). The runtime also prefixes the prompt with a `<current_datetime>` block; the
driver's userPromptTransformed hook sends the prompt as given instead.

Private home: COPILOT_HOME, HOME, TMPDIR and XDG_* are directories in the per-call CredHome; no file of the
user's Copilot config is copied (none is needed). Credentials: in headless mode the runtime does not read
the Copilot CLI's own keyring login ("Stored authentication discovery" does not run; `copilot login` stores a
token in the GNOME keyring), so it authenticates through the GitHub CLI: it runs `gh auth token`, and gh
(GH_CONFIG_DIR = the user's gh config dir, read-only in practice; the token is in the keyring, reached over
the D-Bus session bus that child_env passes) prints the user's OAuth token to the runtime. The harness never
reads the token. It gives the runtime the gh account's scopes (repo, workflow, ...), which only the runtime's
own GitHub calls could use: the model has no tools. Copilot plan and model availability follow that account
(Copilot Pro for AtomicGaryBusey).

Per-call verification (fail CLOSED, finish()):
- the runtime's log records the exact wire request (`[rust:model_wire] Wire request: {...}`): exactly one
  request, for the requested model; its body keys are allowlisted (COPILOT_WIRE_KEYS: no `tools`,
  `tool_choice`, `functions`, `parallel_tool_calls`); the system prompt is SYSTEM_PROMPT exactly (Responses
  `instructions`, Anthropic `system` text blocks or a chat `system` message) and the conversation is the
  prompt verbatim, one user message (the Anthropic path trims its trailing whitespace, seen live: allowed and
  noted; nothing else); `copilotToolsFingerprint` (Responses) lists no tool; every `turn tool
  surface resolved` line has tool_count 0; the requested effort is the one sent (Responses
  `reasoning.effort`, chat `reasoning_effort`, Anthropic `output_config.effort`);
- the event stream is allowlisted (COPILOT_EVENTS): any tool, permission, subagent, skill, MCP start or model
  change event fails the call; `model.model_call_success` must report toolCount 0 and a request capture with no
  tools and one system segment (`agent_replacement`); `session.usage_info` 0 tool-definition tokens; the
  user/system messages echoed by the runtime must be the prompt and SYSTEM_PROMPT; exactly one model call
  (model.call_start, assistant.usage) and one assistant message, without tool requests;
- the served model (assistant.usage / assistant.message / model.call_start `model`) must be the requested
  one, not Auto or BYOK; a `session.model_change` (the runtime's rate-limit auto switch or its "refusal
  fallback" to another model) is an error ("model rerouted", fatal). The served effort
  (model_call_success.reasoningEffort) must match a requested effort ("copilot effort mismatch", fatal);
- input tokens are bounded (copilot_input_bound): grok models bill a server-side prefix the client never
  sends (1563 input tokens for a 45-token request, seen live), so their bound includes COPILOT_SERVER_PREFIX.

Refusals: a response finished with `content_filter` (Anthropic `refusal` is normalised to it) or
`contentFilterTriggered`, or an OpenAI-style `refusal` in the recorded message, is a scored refusal. Errors: a
`session.error` becomes `copilot <errorType>[/<errorCode>] (HTTP n): <message>`; the runner treats
authentication, authorization and quota errors (and a weekly rate limit) as fatal, other rate limits as
transient. Usage: token counts, the nano-AIU the runtime billed (1e9 nano-AIU = 1 AI credit; Copilot Pro has
1500 a month) and the plan's remaining AI credits.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from bca.liveeval.subject import _VERSION_RE, SYSTEM_PROMPT, child_env
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

COPILOT_CLI_VERSION = "1.0.89"
COPILOT_SDK_VERSION = "1.0.15"
DRIVER = Path(__file__).with_name("copilot_driver.mjs")
# The runtime's flags besides the SDK's own (--headless --no-auto-update --stdio --log-level all).
COPILOT_CLI_ARGS = ("--disable-builtin-mcps",)
COPILOT_ENV = {"COPILOT_AUTO_UPDATE": "false", "GH_NO_UPDATE_NOTIFIER": "1", "GH_PROMPT_DISABLED": "1",
               "NO_COLOR": "1"}
# Session events a tool-less turn produces (every live probe, 2026-09-30, SDK 1.0.15 / CLI 1.0.89); anything
# else (tool.*, permission.*, subagent.*, skill.*, external_tool.*, session.model_change, compaction, MCP
# server start, ...) fails closed. session.error / model.call_failure / bca.error are errors, not violations.
COPILOT_EVENTS = frozenset({
    "bca.auth", "bca.session", "bca.prompt_transformed", "bca.error", "bca.stop_error",
    "pending_messages.modified", "session.mcp_servers_loaded", "session.tools_updated", "hook.start", "hook.end",
    "user.message", "system.message", "model.turn_started", "assistant.turn_start", "session.usage_info",
    "model.call_start", "model.model_call_started", "assistant.message_start", "assistant.message_delta",
    "assistant.streaming_delta", "assistant.reasoning", "assistant.reasoning_delta",
    "model.captured_assignment_context", "assistant.usage", "model.model_call_success", "model.call_finished",
    "model.message", "assistant.message", "model.response", "model.turn_ended", "assistant.turn_end",
    "model.messages_snapshot", "session.usage_checkpoint", "assistant.idle", "session.idle", "session.shutdown",
    "session.error", "model.call_failure", "session.warning", "session.info",
})  # fmt: skip
# SDK hooks the runtime runs around a turn (only userPromptTransformed has a handler: the driver's).
COPILOT_HOOKS = frozenset({"userPromptSubmitted", "userPromptTransformed", "sessionStart", "agentStop", "sessionEnd"})
# Wire request body keys seen for tool-less requests on the three endpoint families (Responses:
# instructions/input; Anthropic /v1/messages: system/messages; chat/completions: messages).
COPILOT_WIRE_KEYS = frozenset({
    "model", "instructions", "input", "reasoning", "store", "stream", "include", "copilotToolsFingerprint", "type",
    "headers", "agent_task_id", "initiator", "messages", "system", "max_tokens", "output_config", "temperature",
    "thinking", "reasoning_effort", "snippy", "stream_options",
})  # fmt: skip
COPILOT_TOOL_KEYS = ("tools", "tool_choice", "functions", "function_call", "parallel_tool_calls", "mcp_servers",
                     "web_search_options")
COPILOT_REFUSAL_FINISH = "content_filter"
COPILOT_SERVER_PREFIX = {"grok": 2400}  # model-id prefix -> tokens the endpoint adds server-side (grok: 1519 seen)
_WIRE_RE = re.compile(r"\[rust:model_wire\] Wire request: ")
_LOG_LINE_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
_TOOL_COUNT_RE = re.compile(r'turn tool surface resolved \{.*?"tool_count":(\d+)')


def copilot_input_bound(prompt: str, model: str) -> int:
    """Most input tokens a tool-less request of `prompt` may bill (the system prompt and the prompt at 2/3 token
    per character -- real prompts run ~0.3 -- plus 800, plus the endpoint's own server-side prefix)."""
    prefix = next((n for p, n in COPILOT_SERVER_PREFIX.items() if model.startswith(p)), 0)
    return (len(SYSTEM_PROMPT) + len(prompt)) * 2 // 3 + 800 + prefix


def wire_requests(log_text: str) -> list:
    """The JSON bodies the runtime logged after `Wire request:` (pretty-printed over several lines, up to the
    next timestamped log line); an unparseable body is returned as a string."""
    lines = log_text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = _WIRE_RE.search(lines[i])
        if not m:
            i += 1
            continue
        buf = [lines[i][m.end():]]
        i += 1
        while i < len(lines) and not _LOG_LINE_RE.match(lines[i]):
            buf.append(lines[i])
            i += 1
        text = "\n".join(buf)
        try:
            out.append(json.loads(text))
        except ValueError:
            out.append(text)
    return out


def _texts(content) -> str | None:
    """A message content (a string, or text blocks) as one string; None if it holds anything but text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(c, dict) and c.get("type") in ("text", "input_text")
                                         and isinstance(c.get("text"), str) for c in content):
        return "".join(c["text"] for c in content)
    return None


def _native_cli(binary: str) -> str:
    """The native CLI executable behind the npm `copilot` launcher (…/@github/copilot/npm-loader.js ->
    …/@github/copilot-linux-<arch>/copilot), or BCA_COPILOT_CLI."""
    if os.environ.get("BCA_COPILOT_CLI"):
        return os.environ["BCA_COPILOT_CLI"]
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower(), "x64")
    loader = Path(os.path.realpath(binary))
    return str(loader.parent.parent / f"copilot-linux-{arch}" / "copilot")


def _sdk_entry() -> Path:
    return Path(os.environ.get("BCA_COPILOT_SDK") or
                f"~/.local/opt/copilot/sdk-{COPILOT_SDK_VERSION}/node_modules/@github/copilot-sdk").expanduser()


def _continuation(d) -> str:
    """The reason of a runtime continuation telemetry event ('' if it is not one)."""
    t = d.get("telemetry") if isinstance(d, dict) else None
    if isinstance(t, dict) and t.get("event") == "model_turn_continuation":
        props = t.get("properties") if isinstance(t.get("properties"), dict) else {}
        return str(props.get("source") or "continuation")
    return ""


def _reasoning_item(d: dict) -> bool:
    """An assistant.message that only carries the model's encrypted reasoning: the Responses API returns
    it as a separate output item of the same model call (gpt-6-luna, seen 2026-09-29), with empty
    content and no tool requests. It is neither an answer nor a second model call."""
    return (d.get("content") == "" and not d.get("toolRequests")
            and bool(d.get("reasoningOpaque") or d.get("reasoningText")))


class CopilotSubject(CliSubject):
    vendor = "copilot"
    binary_name = "copilot"
    # the union of the catalog's reasoning efforts (per model: listModels supportedReasoningEfforts)
    efforts = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    audited_versions = (COPILOT_CLI_VERSION,)
    deliver_at_end = True  # the answer is the recorded assistant message, handed over after verification
    streams_text = False
    home_files: tuple[str, ...] = ()  # nothing of the user's is copied: auth goes through gh (module docstring)

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        self.native = _native_cli(self.binary)
        self.node = shutil.which("node") or "node"
        self._call = threading.local()

    def default_home(self) -> Path:
        return Path(os.environ.get("COPILOT_HOME") or "~/.copilot").expanduser()

    @property
    def cli_version(self) -> str:
        """The native runtime's version, and the SDK's (which must pin the same CLI): "1.0.89", or a mismatch
        string that is not an audited version."""
        if self._version is None:
            try:
                out = subprocess.run([self.native, "--version"], capture_output=True, text=True, timeout=30,
                                     env=child_env(), stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
                m = _VERSION_RE.search((out.stdout or "") + (out.stderr or ""))
                version = m.group(0) if out.returncode == 0 and m else "unknown"
            except (OSError, subprocess.SubprocessError):
                version = "unknown"
            sdk = self._sdk_version()
            if sdk != (COPILOT_SDK_VERSION, COPILOT_CLI_VERSION):
                version = f"{version} (sdk {sdk[0]} pinning cli {sdk[1]})"
            self._version = version
        return self._version

    @staticmethod
    def _sdk_version() -> tuple[str, str]:
        root = _sdk_entry()
        try:
            ver = json.loads((root / "package.json").read_text()).get("version", "?")
            m = re.search(r'COPILOT_CLI_VERSION = "([^"]+)"', (root / "dist/cliVersion.js").read_text())
            return ver, m.group(1) if m else "?"
        except (OSError, ValueError):
            return "missing", "?"

    def describe(self) -> dict:
        d = super().describe()
        d["sdk_version"] = self._sdk_version()[0]
        d["auth"] = "gh-cli (headless runtime)"
        return d

    def list_models(self, timeout: float = 120.0) -> tuple[list[dict], dict]:
        """The account's model catalog (SDK listModels) and auth status, for `python -m bca.liveeval models`.
        No session and no model call; runs in a throwaway private home exactly like a call's."""
        if not DRIVER.is_file() or not (_sdk_entry() / "dist/index.js").is_file():
            raise IsolationError(f"isolation: copilot driver or SDK missing ({DRIVER}, {_sdk_entry()})")
        with tempfile.TemporaryDirectory(prefix="bca-copilot-models-") as tmp:
            home = Path(tmp)
            for d in ("copilot", "home/.config", "home/.cache", "home/.local/share", "home/.local/state", "tmp"):
                (home / d).mkdir(parents=True, mode=0o700)
            cfg = home / "bca-list.json"
            cfg.write_text(json.dumps({"sdk": str(_sdk_entry() / "dist/index.js"), "cli": self.native,
                                       "home": str(home / "copilot"), "listModels": True,
                                       "cliArgs": [*COPILOT_CLI_ARGS, "--log-dir", str(home / "copilot/logs")]}))
            out = subprocess.run([self.node, str(DRIVER), str(cfg)], capture_output=True, text=True,
                                 timeout=timeout, env={**child_env(), **self.extra_env(home)},
                                 stdin=subprocess.DEVNULL, cwd=tmp)
        models, auth, err = None, {}, ""
        for line in out.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "bca.models":
                models = ev.get("data")
            elif ev.get("type") == "bca.auth":
                auth = ev.get("data") or {}
            elif ev.get("type") == "bca.error":
                err = (ev.get("data") or {}).get("message", "")
        if not isinstance(models, list):
            raise RuntimeError(f"copilot listModels failed: {err or out.stderr.strip()[-300:] or 'no output'}")
        return models, auth

    # -- the call -----------------------------------------------------------------------------------

    def prepare_home(self, home: Path) -> None:
        try:
            for d in ("copilot", "home/.config", "home/.cache", "home/.local/share", "home/.local/state", "tmp"):
                (home / d).mkdir(parents=True, mode=0o700)
        except OSError as e:
            raise IsolationError(f"isolation: cannot set up the copilot home {home}: {e}") from e
        self._call.home = home
        self._call.events = []
        self._call.partial = []  # streamed answer text, scored only if the output cap stops the call

    def argv(self, prompt: str, home: Path) -> list[str]:
        if not DRIVER.is_file() or not (_sdk_entry() / "dist/index.js").is_file():
            raise IsolationError(f"isolation: copilot driver or SDK missing ({DRIVER}, {_sdk_entry()})")
        self._call.prompt = prompt
        cfg = {"sdk": str(_sdk_entry() / "dist/index.js"), "cli": self.native, "home": str(home / "copilot"),
               "model": self.model, "system": SYSTEM_PROMPT,
               "prompt": prompt, "cliArgs": [*COPILOT_CLI_ARGS, "--log-dir", str(home / "copilot/logs")]}
        if self.effort:
            cfg["effort"] = self.effort
        path = home / "bca-call.json"
        path.write_text(json.dumps(cfg))
        os.chmod(path, 0o600)
        return [self.node, str(DRIVER), str(path)]

    def extra_env(self, home: Path) -> dict[str, str]:
        h = home / "home"
        gh_config = os.environ.get("GH_CONFIG_DIR") or str(Path("~/.config/gh").expanduser())
        return {"HOME": str(h), "TMPDIR": str(home / "tmp"), "COPILOT_HOME": str(home / "copilot"),
                "XDG_CONFIG_HOME": str(h / ".config"), "XDG_CACHE_HOME": str(h / ".cache"),
                "XDG_DATA_HOME": str(h / ".local/share"), "XDG_STATE_HOME": str(h / ".local/state"),
                "GH_CONFIG_DIR": gh_config, **COPILOT_ENV}

    # -- the stream ---------------------------------------------------------------------------------

    @staticmethod
    def _refuse(p: Parsed, why: str) -> None:
        if not p.refusal:
            p.refusal = why[:300]

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        typ, d = ev.get("type"), ev.get("data")
        bad = p.violations.append
        if typ not in COPILOT_EVENTS:
            if typ == "bca.permission_request":
                bad(f"permission requested: {json.dumps(d)[:160]}")
            elif typ == "model.telemetry" and _continuation(d):
                # the call hit the output cap (seen: claude-sonnet-5.5, 32000 tokens, source
                # thinking-exhausted-continuation) and the runtime would nudge the model into a second, injected
                # turn. Stop here and score what the model wrote within the cap, as `claude -p` would score a
                # max-token answer: no continuation, and no do-over either.
                p.text = "".join(getattr(self._call, "partial", []))
                p.notes.append(f"output cap reached ({_continuation(d)}): the runtime's continuation was stopped; "
                               "the answer written within the cap is scored as it stands")
                p.abort = True
            elif typ in ("model.turn_retry", "model.model_call_failure"):
                # the runtime is about to call the model a second time (seen: reason streaming_error, a broken
                # stream): one attempt must be one model call, so the attempt is void -- a transient error the
                # runner retries -- never a second call inside it and never an isolation failure
                why = d.get("reason") if isinstance(d, dict) and d.get("reason") else \
                    str((d.get("modelCall") or {}).get("error") if isinstance(d, dict) else d)[:200]
                p.error = p.error or f"copilot {typ.split('.')[-1].replace('_', ' ')} ({why}); the attempt is void"
                p.abort = True
            elif typ == "session.model_change":
                p.error = p.error or f"model rerouted: copilot switched models ({json.dumps(d)[:200]})"
                p.refusal = ""
            else:
                bad(f"unexpected copilot event {typ!r}: {json.dumps(ev)[:160]}")
            return None
        d = d if isinstance(d, dict) else {}
        self._call.events.append((typ, d))
        if typ == "bca.auth" and not d.get("isAuthenticated"):
            p.error = p.error or f"copilot authentication: not authenticated ({d.get('statusMessage')}); run `gh auth login`"
        elif typ == "session.error":
            code = f"/{d['errorCode']}" if d.get("errorCode") else ""
            http = f" (HTTP {d['statusCode']})" if d.get("statusCode") else ""
            p.error = p.error or f"copilot {d.get('errorType')}{code}{http}: {d.get('message')}"
        elif typ == "bca.error":
            p.error = p.error or f"copilot: {d.get('message')}"
        elif typ == "model.call_failure":  # the runtime retries next: void the attempt now (transient)
            p.notes.append(f"model call failed: {d.get('failureKind')} {d.get('errorCode') or ''} "
                           f"{str(d.get('errorMessage') or '')[:200]}")
            p.error = p.error or f"copilot model call failed ({d.get('failureKind')}); the attempt is void"
            p.abort = True
        elif typ == "hook.start" and d.get("hookType") not in COPILOT_HOOKS:
            bad(f"hook {d.get('hookType')!r} ran")
        elif typ == "session.mcp_servers_loaded":
            live = [s for s in d.get("servers") or [] if not isinstance(s, dict) or s.get("status") != "disabled"]
            if live:
                bad(f"MCP servers loaded: {json.dumps(live)[:160]}")
        elif typ == "session.tools_updated" and set(d) - {"model"}:
            bad(f"tools updated: {json.dumps(d)[:160]}")
        elif typ == "bca.prompt_transformed" and d.get("changed"):
            p.notes.append("the runtime's prompt prefix was removed (userPromptTransformed)")
        elif typ == "assistant.message_delta" and isinstance(d.get("deltaContent"), str):
            self._call.partial.append(d["deltaContent"])
        elif typ == "assistant.message":
            if d.get("toolRequests"):
                bad(f"tool requests: {json.dumps(d.get('toolRequests'))[:160]}")
            content = d.get("content")
            if not isinstance(content, str):
                bad(f"malformed assistant message {json.dumps(d)[:160]}")
            elif not _reasoning_item(d):
                p.text = content if not p.text else p.text + "\n" + content
        return None

    # -- after the call: the runtime's own record ---------------------------------------------------

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if returncode not in (0, None) and not p.error and not p.refusal:
            lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
            p.error = f"exit status {returncode}: " + ("\n".join(lines[-6:]) or "no stderr")[-500:]
        home = getattr(self._call, "home", None)
        if home is None:
            p.violations.append("cannot verify the request: private home unknown")
        else:
            answered = any(t == "assistant.message" and not _reasoning_item(d)
                           for t, d in getattr(self._call, "events", []))
            self.audit_events(p, answered)
            self.audit_wire(home, getattr(self._call, "prompt", ""), p, required=answered or not p.error)
        if p.violations:
            p.refusal = ""  # an isolation failure is never scored, not even as a refusal

    def audit_events(self, p: Parsed, answered: bool) -> None:
        bad = p.violations.append
        evs = getattr(self._call, "events", [])
        prompt = getattr(self._call, "prompt", "")
        count = {k: sum(1 for t, d in evs if t == k and not (t == "assistant.message" and _reasoning_item(d)))
                 for k in ("model.call_start", "assistant.usage", "assistant.message", "model.model_call_success",
                           "user.message", "system.message")}
        for t, d in evs:
            if t == "user.message" and (d.get("content") != prompt or d.get("transformedContent", prompt) != prompt):
                bad(f"the user message is not the prompt verbatim: {str(d.get('transformedContent'))[:100]!r}")
            elif t == "system.message" and d.get("content") != SYSTEM_PROMPT:
                bad(f"system message is not SYSTEM_PROMPT: {str(d.get('content'))[:80]!r}")
            elif t == "session.usage_info" and d.get("toolDefinitionsTokens"):
                bad(f"{d.get('toolDefinitionsTokens')} tool-definition tokens in the context")
            elif t == "model.model_call_success":
                cap = d.get("requestCapture") if isinstance(d.get("requestCapture"), dict) else {}
                segs = [s.get("segment") for s in cap.get("system_segments") or [] if isinstance(s, dict)]
                if d.get("toolCount") or d.get("toolTokenCount") or cap.get("tools") or segs != ["agent_replacement"]:
                    bad(f"request capture: toolCount {d.get('toolCount')}, tools {json.dumps(cap.get('tools'))[:80]}, "
                        f"system segments {segs}")
                eff = d.get("reasoningEffort")
                if isinstance(eff, str):
                    p.usage["reasoning_effort"] = eff
            elif t == "assistant.usage":
                self._usage(p, d)
            elif t == "model.message":
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                if msg.get("refusal"):
                    self._refuse(p, f"refusal: {str(msg['refusal'])[:200]}")
            elif t == "session.shutdown" and isinstance(d.get("totalNanoAiu"), (int, float)):
                p.usage["nano_aiu"] = d["totalNanoAiu"]
                p.usage["ai_credits"] = round(d["totalNanoAiu"] / 1e9, 6)
        if answered or not p.error:
            for k in ("model.call_start", "assistant.usage", "assistant.message", "model.model_call_success"):
                if count[k] != 1 and (answered or count[k]):
                    bad(f"{count[k]} {k} events (one model call expected)")
            if count["system.message"] != 1 or count["user.message"] != 1:
                bad(f"{count['system.message']} system / {count['user.message']} user messages")
        served = {d.get("model") for t, d in evs if t in ("model.call_start", "assistant.usage", "assistant.message")}
        served.discard(None)
        if served:
            if served != {self.model}:
                p.error = f"model rerouted: requested {self.model}, copilot served {', '.join(sorted(map(str, served)))}"
                p.refusal = ""  # a substitute's refusal is not the requested model's
            else:
                p.model, p.model_reported = self.model, True
        eff = p.usage.get("reasoning_effort")
        if self.effort and answered and eff != self.effort:
            p.error = p.error or (f"copilot effort mismatch: requested {self.effort}, served {eff or 'none'} "
                                  f"(model {self.model})")
            p.refusal = ""
        elif not self.effort and answered:
            p.notes.append(f"served at effort {eff or 'unreported'} (CLI default)")

    def _usage(self, p: Parsed, d: dict) -> None:
        if d.get("isAuto") or d.get("isByok"):
            p.violations.append(f"auto / BYOK routing (isAuto {d.get('isAuto')}, isByok {d.get('isByok')})")
        if d.get("finishReason") == COPILOT_REFUSAL_FINISH or d.get("contentFilterTriggered"):
            self._refuse(p, f"refusal: copilot finish reason {d.get('finishReason')} (content filter)")
        elif d.get("finishReason") == "length":
            p.notes.append("stopped at the output-token limit")
        for src, dst in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
                         ("cacheReadTokens", "cache_read_input_tokens"), ("cacheWriteTokens", "cache_write_input_tokens"),
                         ("reasoningTokens", "reasoning_tokens")):
            if isinstance(d.get(src), int):
                p.usage[dst] = d[src]
        prem = ((d.get("quotaSnapshots") or {}).get("premium_interactions") or {})
        if isinstance(prem.get("remainingPercentage"), (int, float)):
            p.usage["plan_remaining_pct"] = prem["remainingPercentage"]

    def audit_wire(self, home: Path, prompt: str, p: Parsed, *, required: bool) -> None:
        bad = p.violations.append
        text = ""
        for f in sorted((home / "copilot/logs").glob("*.log")):
            text += f.read_text(errors="replace") + "\n"
        reqs = wire_requests(text)
        counts = [int(n) for n in _TOOL_COUNT_RE.findall(text)]
        if any(counts):
            bad(f"tool surface resolved with {max(counts)} tools")
        if not reqs:
            if required:
                bad("cannot verify the request: no wire request in the copilot log")
            return
        if len(reqs) != 1:
            bad(f"{len(reqs)} wire requests (one model call expected)")
        for body in reqs:
            if not isinstance(body, dict):
                bad(f"unparseable wire request: {str(body)[:120]!r}")
                continue
            tool_keys = [k for k in COPILOT_TOOL_KEYS if body.get(k) not in (None, [], {})]
            if tool_keys:
                bad(f"tools in the request: {tool_keys}: {json.dumps({k: body[k] for k in tool_keys})[:160]}")
            extra = sorted(set(body) - COPILOT_WIRE_KEYS - set(COPILOT_TOOL_KEYS))
            if extra:
                bad(f"unexpected request keys {extra}")
            fp = body.get("copilotToolsFingerprint")
            if fp is not None and (not isinstance(fp, dict) or fp.get("count") or fp.get("tools")):
                bad(f"tool fingerprint {json.dumps(fp)[:160]}")
            if body.get("model") != self.model:
                bad(f"request for model {body.get('model')!r}, not {self.model}")
            system, user = self._conversation(body, p)
            if system != SYSTEM_PROMPT:
                bad(f"the request's system prompt is not SYSTEM_PROMPT: {str(system)[:80]!r}")
            if user != prompt:
                if isinstance(user, str) and user == prompt.rstrip():  # /v1/messages: the runtime trims the end
                    p.notes.append("the runtime trimmed the prompt's trailing whitespace")
                else:
                    bad(f"the request's conversation is not the prompt verbatim: {str(user)[:100]!r}")
            sent = self._sent_effort(body)
            if sent is not None:
                p.usage.setdefault("reasoning_effort", sent)
            if self.effort and sent != self.effort:
                bad(f"requested effort {self.effort}, the request carried {sent!r}")
        bound = copilot_input_bound(prompt, self.model)
        # assistant.usage inputTokens is the whole prompt, cached tokens included (grok-4.7 sum_to: 1915 input of which
        # 1408 cache reads = its server prefix + the request)
        total = p.usage.get("input_tokens", 0)
        if total > bound:
            bad(f"{total} input tokens billed, more than a tool-less request of this prompt can have ({bound})")

    @staticmethod
    def _conversation(body: dict, p: Parsed) -> tuple[str | None, str | None]:
        """(system prompt, the one user message) of a Responses, Anthropic or chat-completions body."""
        bad = p.violations.append
        system = None
        if "instructions" in body:
            system = body["instructions"] if isinstance(body["instructions"], str) else None
        if "system" in body:
            system = _texts(body["system"]) if system is None else None
        items = body.get("input") if "input" in body else body.get("messages")
        if not isinstance(items, list):
            bad(f"no conversation in the request ({sorted(body)})")
            return system, None
        users = []
        for it in items:
            if not isinstance(it, dict) or it.get("type", "message") != "message":
                bad(f"conversation item {json.dumps(it)[:120]}")
                continue
            role, content = it.get("role"), _texts(it.get("content"))
            if role == "system" and system is None and not users:
                system = content
            elif role == "user" and content is not None:
                users.append(content)
            else:
                bad(f"conversation item {role!r}: {json.dumps(it)[:120]}")
        if len(users) != 1:
            bad(f"{len(users)} user messages in the request")
        return system, users[0] if len(users) == 1 else None

    @staticmethod
    def _sent_effort(body: dict) -> str | None:
        r = body.get("reasoning")
        if isinstance(r, dict) and isinstance(r.get("effort"), str):
            return r["effort"]
        if isinstance(body.get("reasoning_effort"), str):
            return body["reasoning_effort"]
        oc = body.get("output_config")
        if isinstance(oc, dict) and isinstance(oc.get("effort"), str):
            return oc["effort"]
        return None
