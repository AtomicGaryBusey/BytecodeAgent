"""Moonshot Kimi Code CLI as a tool-less subject (`kimi:<model>`); see bca.liveeval.vendor_base.

Run: `kimi --output-format stream-json --agent-file <home>/bca-subject.md -m MODEL --prompt=...` with
KIMI_CODE_HOME = the private home. That home holds copies of credentials/*.json and `region`, the
agent profile, and a config.toml cut down to the [providers.*] and [models.*] tables of the user's
(`kimi_config`), plus `default_model` = the subject's model: no hooks, MCP servers, permission rules, [thinking] preferences, services
(moonshot_search / moonshot_fetch) or any other user setting reach the subject. A refreshed OAuth
token (kimi-code 2.1 writes credentials/<key>.json by rename) is copied back by CredHome.

Tools. The agent profile's `tools: []` is an empty allowlist (agent-core-v2 toolPolicy) and its body
is the whole system prompt (no ${base_prompt}/${agents_md}). The stream-json output does not show the
request, but kimi records it in the private home: sessions/*/*/agents/main/wire.jsonl (profile.bind,
llm.tools_snapshot, llm.request with sha256 hashes of the system prompt and of the tool list,
context.append_message, usage.record, step.end). `finish` reads that record before the home is
deleted and fails CLOSED unless: the bound profile has no tools/subagents/AGENTS.md and exactly
SYSTEM_PROMPT; every tools snapshot is empty; every llm.request carries toolsHash == sha256("[]") and
systemPromptHash == sha256(SYSTEM_PROMPT) (kimi 2.1.1: systemPromptChars == len(SYSTEM_PROMPT), so
no cwd/date/skills are appended to it); the user turn is the prompt verbatim; nothing but the known
injections below was added to the context; no message carries tool calls or a tool role.

Unremovable extras (A2), seen in the recorded context as extra user messages (messageCount 3):
- date reminder (origin injection/date_change, 227 chars): "<system-reminder>\\nToday's date is
  YYYY-MM-DD. The current date is restated in a reminder whenever it changes; rely on the latest such
  reminder for the current date. DO NOT mention this to the user explicitly.\\n</system-reminder>";
- permission-mode reminder (injection/permission_mode, 508 chars): "<system-reminder>\\nAuto
  permission mode is active. Tool approvals will be handled automatically ..." -- print mode forces
  `auto` unconditionally (apps/kimi-code/src/cli/v2/run-v2-print.ts forceAuto).

Model and effort: the wire record names the model id sent (llm.request model, e.g. `k3`) and the
effort applied (profile.bind / llm.request thinkingEffort: `high` by default for kimi-code/k3, the
catalog's default_effort). The API response's served model is NOT recorded anywhere: model_source
stays "requested". Token usage comes from the wire record's step.end usage.

Safety blocks (Parsed.refusal): a response the provider filtered (finish reason `filtered`, e.g. an
OpenAI-style `content_filter`) ends the turn with error code `provider.filtered`, which print mode
reports as "Provider safety policy blocked the response." (apps/kimi-code/src/cli/v2/run-v2-print.ts
formatNativeTurnFailure; packages/agent-core-v2/src/agent/loop/loopService.ts) and exits non-zero;
kimi never retries it (packages/kosong/src/errors.ts isRetryableGenerateError). The wire record shows
it too (step.end finishReason "filtered", turn.ended error.code "provider.filtered"). Either is a
scored refusal, and the request is still verified.

kimi-code 2.3.1 (re-audited 2026-10-23, two live calls, K3 and K3.1 Pro): the same profile binding, request
hashes, context (the prompt plus the same two injections) and stream-json events as 2.1.1. One new wire record,
`changeNotifier.snapshot` (agentsMdHash, agentsMdFiles, skillsHash, subagentNames), is bookkeeping never sent to the
model; it fails closed if it reports an AGENTS.md. Model catalog refreshed by a fresh `kimi login`: k3.1-pro and
k3.1-pro-highspeed appear, both with default_effort `max` (k3: `high`).

Credentials: kimi refreshes its OAuth token when fewer than max(300 s, expires_in / 2) remain before
`expires_at` (packages/oauth/src/oauth-manager.ts defaultRefreshThreshold); cred_expiry reports that
moment so CredHome serialises refreshes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import tomllib
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

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

_SHA = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
EMPTY_TOOLS_HASH = _SHA("[]")  # 4f53cda1...
SYSTEM_PROMPT_HASH = _SHA(SYSTEM_PROMPT)
# Context injections kimi 2.1.1 adds in print mode (module docstring), with a size cap.
KIMI_INJECTIONS = {"date_change": ("<system-reminder>\nToday's date is", 400),
                   "permission_mode": ("<system-reminder>\nAuto permission mode is active.", 600)}
# stream-json lines (apps/kimi-code/src/cli/prompt-render.ts PromptJsonWriter): anything else fails closed.
KIMI_META = frozenset({"system.version", "session.resume_hint", "turn.step.retrying"})
# wire.jsonl record types seen in a tool-less print run; an unknown type naming tools/skills/MCP/hooks/
# subagents fails closed, any other unknown type is tolerated (the checks below still apply).
_WIRE_SUSPECT = re.compile(r"tool|mcp|skill|hook|subagent|plugin", re.I)
_WIRE_KNOWN = frozenset({"metadata", "runtime.set_binding", "profile.bind", "permission.set_mode", "agent.message.appended",
                         "agent.turn.started", "turn.prompt", "context.append_message", "plugin.session_start",
                         "context.append_loop_event", "llm.tools_snapshot", "llm.request", "usage.record",
                         "token_counting.measured", "agent.turn.ended", "turn.ended", "token_counting.turn_recorded",
                         "prompt.completed", "changeNotifier.snapshot"})
# config.toml tables the subject needs: the provider (base_url, OAuth key) and the model catalog.
KIMI_CONFIG_TABLES = ("providers", "models")
KIMI_REFUSAL = "Provider safety policy blocked the response"  # print mode's text for provider.filtered
KIMI_FILTERED = "provider.filtered"
KIMI_MIN_REFRESH_S = 300.0
_HEADER_RE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def kimi_config(text: str, model: str) -> str:
    """The user's config.toml cut down to KIMI_CONFIG_TABLES (checked by re-parsing: IsolationError if
    the cut lost or added anything in those tables), with `default_model` = the subject's model (kimi
    2.1.1 refuses to start without one, even with -m)."""
    try:
        full = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise IsolationError(f"isolation: kimi config.toml does not parse ({e})") from None
    out, keep = [f"default_model = {json.dumps(model)}", ""], False
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            keep = m.group(1).strip().strip('"').split(".")[0] in KIMI_CONFIG_TABLES
        if keep:
            out.append(line)
    cut = "\n".join(out) + "\n"
    try:
        got = tomllib.loads(cut)
    except tomllib.TOMLDecodeError as e:
        raise IsolationError(f"isolation: cannot cut kimi config.toml down ({e})") from None
    want = {"default_model": model, **{k: full[k] for k in KIMI_CONFIG_TABLES if k in full}}
    if got != want:
        raise IsolationError("isolation: cannot cut kimi config.toml down to its providers/models tables")
    return cut


class KimiSubject(CliSubject):
    vendor = "kimi"
    binary_name = "kimi"
    efforts = ("low", "high", "max")
    audited_versions = ("2.1.1", "2.3.1")  # 2.3.1 re-audited 2026-10-23 (module docstring)
    streams_text = False
    # The OAuth token file is credentials/<key>.json, where config.toml names the key
    # (kimi-code-env-<hash> for a CLI login); copy whatever credential files there are.
    home_files = ("config.toml", "region")

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        creds = tuple(sorted(f"credentials/{p.name}" for p in (self.home_real / "credentials").glob("*.json")))
        self.home_required = creds or ("credentials/<none>.json",)  # none: CredHome fails "log in first"
        self.home_files = (*self.home_required, *type(self).home_files)
        self.home_sync = creds
        self._call = threading.local()  # the private home and prompt of the call running in this thread

    def default_home(self) -> Path:
        return Path(os.environ.get("KIMI_CODE_HOME") or "~/.kimi-code").expanduser()

    def prepare_home(self, home: Path) -> None:
        (home / "bca-subject.md").write_text(KIMI_AGENT)
        cfg = home / "config.toml"
        if cfg.is_file():
            cfg.write_text(kimi_config(cfg.read_text(), self.model))

    def argv(self, prompt: str, home: Path) -> list[str]:
        if prompt.lstrip().startswith("/"):
            prompt = " " + prompt  # a leading slash selects a command (/goal ...)
        self._call.home, self._call.prompt = home, prompt
        return [self.binary, "--output-format", "stream-json", "--agent-file", str(home / "bca-subject.md"),
                "-m", self.model, f"--prompt={prompt}"]

    def extra_env(self, home: Path) -> dict[str, str]:
        env = {"KIMI_CODE_HOME": str(home), "KIMI_DISABLE_TELEMETRY": "1", "KIMI_CODE_NO_AUTO_UPDATE": "1",
               "KIMI_CODE_BUILTIN_PRODUCT_SKILLS": "0"}
        if self.effort:
            env["KIMI_MODEL_THINKING_EFFORT"] = self.effort
        return env

    def cred_expiry(self, rel: str, data: bytes) -> float | None:
        """When kimi will refresh this OAuth token file (module docstring), or None (not a token file,
        `expires_at` 0 = never). Token values are never read beyond these two numbers."""
        if not rel.startswith("credentials/"):
            return None
        try:
            cred = json.loads(data)
        except ValueError:
            return None
        exp = cred.get("expires_at") if isinstance(cred, dict) else None
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= 0:
            return None
        life = cred.get("expires_in")
        life = life if isinstance(life, (int, float)) and not isinstance(life, bool) and life > 0 else 0
        return float(exp) - max(KIMI_MIN_REFRESH_S, life * 0.5)

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        role = ev.get("role")
        if role == "assistant":
            calls = ev.get("tool_calls")
            if calls not in (None, []):
                if not isinstance(calls, list):
                    p.violations.append(f"malformed tool_calls {json.dumps(calls)[:160]}")
                    return None
                names = [str(((c.get("function") or {}) if isinstance(c, dict) else {}).get("name", c))
                         if isinstance(c, dict) else str(c) for c in calls]
                p.violations.append("tool_calls " + ",".join(names)[:200])
                return None
            if set(ev) - {"role", "content", "tool_calls"}:
                p.violations.append(f"unexpected assistant fields {sorted(set(ev) - {'role', 'content', 'tool_calls'})}")
                return None
            content = ev.get("content")
            if content is not None and not isinstance(content, str):
                p.violations.append(f"malformed assistant content {json.dumps(content)[:160]}")
                return None
            if content:
                p.text += content
                return content
        elif role == "tool":
            p.violations.append(f"tool result {str(ev.get('content'))[:120]}")
        elif role == "meta" and ev.get("type") in KIMI_META:
            if ev.get("type") == "turn.step.retrying":
                p.notes.append(f"retry {ev.get('failed_attempt')}/{ev.get('max_attempts')} "
                               f"{ev.get('error_name')} ({ev.get('status_code')}): {ev.get('error_message')}")
                if ev.get("error_name") == KIMI_FILTERED or KIMI_REFUSAL in str(ev.get("error_message")):
                    self._refuse(p, f"{KIMI_FILTERED}: {ev.get('error_message')}")
        else:
            p.violations.append(f"unexpected stream-json event {json.dumps(ev)[:160]}")
        return None

    @staticmethod
    def _refuse(p: Parsed, why: str) -> None:
        if not p.refusal:  # the first block wins: nothing after it is scored
            p.refusal = why[:300]

    # -- after the call: the CLI's own record of the request -----------------------------------------

    def audit_wire(self, home: Path, prompt: str, p: Parsed, *, required: bool = True) -> None:
        """Check kimi's record of the call (module docstring). `required`: the call produced something
        that is scored (an answer or a refusal), so the record must exist and hold a model request; a
        failed call (quota, network) may have none, but whatever it recorded is still checked."""
        wires = sorted(home.glob("sessions/*/*/agents/*/wire.jsonl"))
        if not wires:
            if required:
                p.violations.append("no wire.jsonl request record in the private home (kimi layout changed?)")
            return
        st = {"requests": 0, "messages": 0, "effort": "", "model": ""}
        usage: dict = {}
        for wire in wires:
            if wire.parent.name != "main":
                p.violations.append(f"a second agent ran ({wire.parent.name})")
            st["messages"] = 0  # the context of this agent
            for n, line in enumerate(wire.read_text(errors="replace").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    r = None
                if not isinstance(r, dict):
                    p.violations.append(f"unparseable wire record {wire.parent.name}:{n}: {line[:120]!r}")
                    continue
                t = self._audit_record(r, prompt, p, st)
                if t == "step.end":
                    ev = r["event"]
                    if ev.get("finishReason") == "filtered":
                        self._refuse(p, f"{KIMI_FILTERED}: {KIMI_REFUSAL} (finish reason filtered)")
                    u = ev.get("usage")
                    if u is not None and not isinstance(u, dict):
                        p.violations.append(f"malformed step.end usage {json.dumps(u)[:120]}")
                    for k, v in (u or {}).items():
                        if isinstance(v, int):
                            usage[k] = usage.get(k, 0) + v
        if required and not st["requests"]:
            p.violations.append("the wire record holds no llm.request")
        if usage:
            p.usage = {"input_tokens": usage.get("inputOther", 0), "output_tokens": usage.get("output", 0),
                       "cache_read_input_tokens": usage.get("inputCacheRead", 0),
                       "cache_creation_input_tokens": usage.get("inputCacheCreation", 0)}
        if st["requests"]:
            p.usage.update({"wire_model": st["model"], "thinking_effort": st["effort"], "llm_requests": st["requests"]})

    def _audit_record(self, r: dict, prompt: str, p: Parsed, st: dict) -> str:
        t = r.get("type") or ""
        bad = p.violations.append
        if not isinstance(t, str):
            bad(f"malformed wire record type {json.dumps(r)[:120]}")
            return ""
        if t == "profile.bind":
            if r.get("systemPrompt") != SYSTEM_PROMPT:
                bad(f"system prompt is not SYSTEM_PROMPT: {str(r.get('systemPrompt'))[:80]!r}")
            for k in ("activeToolNames", "agentsMdPaths", "subagents"):
                if r.get(k):
                    bad(f"profile {k} {r.get(k)}")
            if r.get("modelAlias") not in (None, self.model):
                p.error = p.error or f"model rerouted: requested {self.model}, kimi bound {r.get('modelAlias')}"
        elif t == "llm.tools_snapshot":
            tools = r.get("tools")
            if tools not in (None, []):
                names = [str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in tools] \
                    if isinstance(tools, list) else [f"malformed {json.dumps(tools)[:80]}"]
                bad("tools sent: " + ",".join(names)[:200])
        elif t == "llm.request":
            st["requests"] += 1
            st["effort"] = r.get("thinkingEffort") or st["effort"]
            st["model"] = r.get("model") or st["model"]
            if r.get("toolsHash") != EMPTY_TOOLS_HASH:
                bad(f"llm.request toolsHash {r.get('toolsHash')} is not the empty list's")
            if r.get("systemPromptHash") != SYSTEM_PROMPT_HASH:
                bad("llm.request system prompt differs from SYSTEM_PROMPT")
            if r.get("messageCount") != st["messages"]:
                bad(f"llm.request carried {r.get('messageCount')} messages, the record shows {st['messages']} "
                    f"(context not recorded as checked)")
            if r.get("modelAlias") not in (None, self.model):
                p.error = p.error or f"model rerouted: requested {self.model}, kimi sent {r.get('modelAlias')}"
        elif t == "context.append_message":
            st["messages"] += 1
            msg = r.get("message")
            if not isinstance(msg, dict):
                bad(f"malformed context message {json.dumps(r)[:120]}")
                return t
            content = msg.get("content") or []
            if not isinstance(content, list):
                bad(f"malformed context message content {json.dumps(content)[:120]}")
                return t
            if msg.get("toolCalls") or msg.get("role") not in ("user", "assistant"):
                bad(f"context message role={msg.get('role')} toolCalls={bool(msg.get('toolCalls'))}")
            elif msg.get("role") == "user":
                if not all(isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
                           for c in content):
                    bad(f"user context message with non-text content {json.dumps(content)[:120]}")
                    return t
                text = "".join(c["text"] for c in content)
                origin = msg.get("origin") if isinstance(msg.get("origin"), dict) else {}
                if origin.get("kind") == "user":
                    if text.strip() != prompt.strip():
                        bad("the user turn is not the prompt verbatim")
                elif origin.get("kind") == "injection" and origin.get("variant") in KIMI_INJECTIONS:
                    head, cap = KIMI_INJECTIONS[origin["variant"]]
                    if not text.startswith(head) or len(text) > cap:
                        bad(f"injection {origin['variant']} changed: {text[:100]!r}")
                else:
                    bad(f"unexpected context injection {origin.get('kind')}/{origin.get('variant')}: {text[:100]!r}")
        elif t == "context.append_loop_event":
            ev = r.get("event")
            if not isinstance(ev, dict):
                bad(f"malformed loop event {json.dumps(r)[:120]}")
                return t
            part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
            if ev.get("type") == "content.part" and part.get("type") not in ("think", "text"):
                bad(f"content part {part.get('type')}")
            if ev.get("type") == "step.end":
                return "step.end"
            if _WIRE_SUSPECT.search(str(ev.get("type"))):
                bad(f"loop event {ev.get('type')}")
        elif t == "turn.ended":
            err = r.get("error")
            if isinstance(err, dict) and err.get("code") == KIMI_FILTERED:
                self._refuse(p, f"{KIMI_FILTERED}: {err.get('message') or KIMI_REFUSAL}")
        elif t == "changeNotifier.snapshot":
            # kimi-code 2.3.1 bookkeeping, not sent to the model: the CLI's installed subagents and a hash of its
            # bundled skills (neither usable: the request's tool list is empty). An AGENTS.md it loaded is not ok.
            if r.get("agentsMdHash") is not None or r.get("agentsMdFiles"):
                bad(f"AGENTS.md loaded: {str(r.get('agentsMdFiles'))[:100]!r}")
        elif t == "plugin.session_start":
            if r.get("content"):
                bad(f"plugin session_start content {str(r.get('content'))[:100]!r}")
        elif t not in _WIRE_KNOWN and _WIRE_SUSPECT.search(t):
            bad(f"wire record {t}")
        return t

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if KIMI_REFUSAL in stderr:
            self._refuse(p, f"{KIMI_FILTERED}: {KIMI_REFUSAL}.")
        home = getattr(self._call, "home", None)
        if home is None:
            p.violations.append("cannot verify the request: private home unknown")
        else:  # every call, failed ones too: a request with tools is fatal whatever the outcome
            self.audit_wire(home, getattr(self._call, "prompt", ""), p,
                            required=returncode in (0, None) or bool(p.refusal))
        if returncode not in (0, None) and not p.error and not p.refusal:
            lines = [ln for ln in stderr.strip().splitlines() if ln.strip() and not ln.startswith("See log:")]
            p.error = f"exit status {returncode}: " + ("\n".join(lines[-6:]) or "no stderr")[-500:]
            if p.notes:  # the provider error behind the retries (quota / rate limit), if stderr lost it
                p.error += f" [last {p.notes[-1][:300]}]"
        if p.violations:
            p.refusal = ""  # an isolation failure is never scored, not even as a refusal
