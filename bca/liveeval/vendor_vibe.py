"""Mistral models through Mistral Vibe, Mistral's own coding-agent CLI: `vibe:<alias>`, e.g.
`vibe:mistral-large-4-0-3`. An AGENT-HARNESS STUDY, reported apart from every other subject.

Why apart (audited 2026-11-11 on vibe 3.1.2, request captured at a local recorder in front of api.mistral.ai):
Vibe 3.x always runs its "Unified Harness". Every request carries 7 tool definitions (bash, read_file,
write_file, edit, run_typescript, search_tool_functions, skill) and a ~6,800-character agent system prompt with
our SYSTEM_PROMPT embedded in it (`--system-prompt-file` replaces only an inner section; the outer text is compiled
into the harness binary); no setting removes them (`--enabled-tools`, `enabled_tools`, `disabled_tools = ["*"]`,
the MCP / connector / subagent / shell switches). So these results answer "how does the model do inside its own
vendor's agent harness", not the tool-free question the other rows answer, and they are never ranked with them.

The protocol, which keeps everything else equal:
- Tools are offered by the harness but none can run: `disabled_tools = ["*"]` maps every builtin to `deny`, and
  the `ask` agent cannot be approved in headless mode. A denied attempt arrives as an `effect` event with
  `state.error.code == "tool_denied"`; it is counted (usage["tool_attempts"]) and noted, never fatal. An effect
  that is NOT a denial means a tool ran: an isolation failure (fatal), as for every CLI.
- A private VIBE_HOME per call: config.toml switches off project context, the commit signature, MCP, connectors,
  subagents, managed shell tools, background processes, telemetry, OTel, update checks, auto-update, experiments
  and the experimental connectors; the empty temporary working directory holds nothing. Authentication is the
  user's own Vibe sign-in in the OS keyring (nothing is copied).
- The model catalog is pinned here (MODELS), copied from the account's catalog (Vibe Pro, 2026-11-11: the server
  sends it through the experiments service, which this config switches off): each model's name, thinking level and
  temperature are fixed and recorded rather than following a server-side experiment. `--effort` overrides the
  thinking level, within the model's catalog levels.
- The same task prompt (`--prompt-file`) and SYSTEM_PROMPT (`--system-prompt-file`), headless, `--max-turns 4`
  (room to answer after a denied tool call), the harness's own per-call time limit; the answer is the LAST
  assistant message (Vibe's streaming output is one JSON line per finished entry, not token deltas).
- Usage and cost come from the CLI's own export (`--output-dir`: export.json). The pinned catalog entries carry no
  prices, so Vibe reports $0.00: the calls are billed to the user's Vibe Pro plan. The served model is not
  reported by Vibe (model_source "requested").
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

MAX_TURNS = 4
# Account catalog (experiment_held_eval_cache.json, vibe_cli_model_catalog / extra models, read 2026-11-11) plus
# Vibe's built-in default model (vibe_schema.DEFAULT_ACTIVE_MODEL_CONFIG).
MODELS = {
    "mistral-large-4-0-3": {"name": "mistral-large-4-0", "display_name": "Mistral Large 4", "thinking": "high",
                            "temperature": 1.0, "thinking_levels": ["low", "medium", "high"]},
    "glm-5-3": {"name": "zai-glm-5-3", "display_name": "GLM-5.3 (Mistral Hosted)", "thinking": "high",
                "temperature": 1.0, "thinking_levels": ["low", "high", "max"]},
    "zai-glm-5-3-flash": {"name": "zai-glm-5-3-flash", "display_name": "GLM-5.3 Flash (Mistral Hosted)",
                          "thinking": "high", "temperature": 1.0, "thinking_levels": ["low", "high", "max"],
                          "max_context_length": 1048576},
    "mistral-medium-3.5": {"name": "mistral-vibe-cli-latest", "display_name": "Mistral Medium 3.5", "thinking": "high",
                           "temperature": 1.0, "thinking_levels": ["off", "high"], "max_context_length": 262144},
}
CONFIG_BASE = """\
enable_mcp = false
enable_connectors = false
enable_subagents = false
include_project_context = false
include_commit_signature = false
enable_telemetry = false
enable_update_checks = false
enable_auto_update = false
managed_shell_tools_enabled = false
enable_background_processes = false
enable_otel = false
enable_pr_monitoring = false
experimental_enable_registry_skills = false
experimental_enable_document_library_connector = false
experimental_enable_mistral_mcp_connector = false
experimental_enable_local_projects = false
disabled_tools = ["*"]
"""
EXPORT_OUTCOMES = ("finished",)
# Harness-internal lookups that run even with every tool denied (seen 2026-11-11: Mistral Large 4 calls
# search_tool_functions at the start of some turns; its output is a count of tool functions, nothing from outside).
HARNESS_LOOKUPS = ("search_tool_functions",)


def model_block(alias: str, thinking: str) -> str:
    m = MODELS[alias]
    lines = ["[[models]]", f'name = "{m["name"]}"', 'provider = "mistral"', f'alias = "{alias}"',
             f'display_name = "{m["display_name"]}"', f'thinking = "{thinking}"', f'temperature = {m["temperature"]}',
             f"thinking_levels = {json.dumps(m['thinking_levels'])}"]
    if m.get("max_context_length"):
        lines.append(f'max_context_length = {m["max_context_length"]}')
    return "\n".join(lines) + "\n"


class VibeSubject(CliSubject):
    vendor = "vibe"
    binary_name = "vibe"
    efforts = ("off", "low", "medium", "high", "max")
    audited_versions = ("3.1.2",)
    streams_text = False
    deliver_at_end = True
    home_files = ()  # authentication is the keyring; nothing of the user's home is copied
    prompt_in_argv = False  # --prompt-file

    def __init__(self, model: str, **kw):
        super().__init__(model, **kw)
        self._call = threading.local()  # this thread's call: its private home and prompt
        if model not in MODELS:
            raise ValueError(f"vibe: unknown model alias {model!r}; pinned: {', '.join(MODELS)}")
        if self.effort is not None and self.effort not in MODELS[model]["thinking_levels"]:
            raise ValueError(f"vibe: {model} has thinking levels {MODELS[model]['thinking_levels']}, not {self.effort!r}")

    def default_home(self) -> Path:
        return Path("~/.vibe").expanduser()

    @property
    def thinking(self) -> str:
        return self.effort or MODELS[self.model]["thinking"]

    def prepare_home(self, home: Path) -> None:
        try:
            (home / "config.toml").write_text(CONFIG_BASE + "\n" + model_block(self.model, self.thinking)
                                              + "\n[experiments]\nenable = false\n")
            (home / "system.txt").write_text(SYSTEM_PROMPT)
            (home / "tmp").mkdir(mode=0o700)
        except OSError as e:
            raise IsolationError(f"isolation: cannot set up the vibe home {home}: {e}") from e

    def argv(self, prompt: str, home: Path) -> list[str]:
        (home / "prompt.txt").write_text(prompt)
        self._call.home, self._call.prompt = home, prompt
        return [self.binary, "--prompt-file", str(home / "prompt.txt"), "--output", "streaming",
                "--output-dir", str(home / "out"), "--max-turns", str(MAX_TURNS),
                "--system-prompt-file", str(home / "system.txt"), "--agent", "ask", "--model", self.model]

    def extra_env(self, home: Path) -> dict[str, str]:
        return {"VIBE_HOME": str(home), "TMPDIR": str(home / "tmp")}

    def describe(self) -> dict:
        d = super().describe()
        d["effort"] = self.thinking + ("" if self.effort else " (catalog default)")
        d["vibe"] = {"model": MODELS[self.model]["name"], "thinking": self.thinking,
                     "temperature": MODELS[self.model]["temperature"], "max_turns": MAX_TURNS,
                     "study": "agent harness (tools offered, all denied); reported separately"}
        return d

    def on_event(self, ev: dict, p: Parsed) -> str | None:
        typ = ev.get("type")
        if typ == "message" and ev.get("role") == "user":
            text = "".join(c.get("text", "") for c in ev.get("content") or [] if isinstance(c, dict))
            if text.strip() != self._call.prompt.strip():  # vibe drops the prompt's final newline
                p.violations.append("the user turn is not the task prompt as sent")
            return None
        if typ == "reasoning":
            p.usage["reasoning_chars"] = p.usage.get("reasoning_chars", 0) + len(ev.get("text") or "")
            return None
        if typ == "effect":
            state = ev.get("state") or {}
            tool = ((ev.get("detail") or {}).get("toolName")) or ev.get("title") or "?"
            if (state.get("error") or {}).get("code") == "tool_denied" and state.get("status") == "failed":
                p.usage["tool_attempts"] = p.usage.get("tool_attempts", 0) + 1
                tools = p.usage.setdefault("tools_attempted", [])
                if tool not in tools:
                    tools.append(tool)
                return None
            if tool in HARNESS_LOOKUPS and state.get("status") == "completed":
                # The harness's own catalog lookup: it returns counts of the (all denied) tool functions, e.g.
                # {"mode": "best_match", "toolCount": 5}; no file, command or network. Recorded, not fatal.
                p.usage["harness_lookups"] = p.usage.get("harness_lookups", 0) + 1
                return None
            p.violations.append(f"a tool ran: {tool} (status {state.get('status')!r})")
            return None
        if typ == "message" and ev.get("role") == "assistant":
            text = "".join(c.get("text", "") for c in ev.get("content") or [] if isinstance(c, dict)
                           and c.get("type") == "text")
            p.text = text  # the last assistant message is the answer
            return None
        p.violations.append(f"unknown vibe entry type {typ!r}")
        return None

    def complete(self, prompt, on_text=None, *, raw_path: str = "", timeout=None):
        r = super().complete(prompt, on_text, raw_path=raw_path, timeout=timeout)
        if "turn_limit" in (r.error or ""):
            # Vibe stopped the turn at --max-turns before a final answer (seen 2026-11-11: Mistral Large 4 spending its
            # turns on denied tools). The model's own outcome, like running out of time: a scored timeout, never
            # retried (retrying would give it extra draws).
            r.notes.append(f"vibe stopped the turn at the {MAX_TURNS}-turn limit without a final answer")
            r.timed_out, r.ok, r.error = True, False, "timeout"
        return r

    def finish(self, p: Parsed, returncode: int | None, stderr: str) -> None:
        if returncode not in (0, None):
            p.error = stderr.strip().splitlines()[-1][:500] if stderr.strip() else f"exit status {returncode}"
            return
        export = Path(self._call.home) / "out" / "export.json"
        if not export.is_file():
            p.violations.append("no export.json: the call cannot be verified")
            return
        e = json.loads(export.read_text())
        if e.get("outcome") not in EXPORT_OUTCOMES:
            p.error = f"vibe outcome {e.get('outcome')!r}: {e.get('error') or e.get('stop_reason')}"
            return
        cfg = e.get("config") or {}
        # Vibe adds ask_user_question and cron to the deny list in headless mode; "*" must be there.
        if ("*" not in (cfg.get("disabled_tools") or []) or cfg.get("bypass_tool_permissions")
                or cfg.get("include_project_context") or cfg.get("enable_mcp") or cfg.get("enable_connectors")
                or cfg.get("enable_subagents") or cfg.get("active_model") != self.model):
            p.violations.append("the effective config is not the audited one (tools / approvals / context / MCP / "
                                f"model: {cfg.get('active_model')!r})")
        u = e.get("usage") or {}
        p.usage.update({"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                        "cached_input_tokens": u.get("cached_input_tokens"), "steps": e.get("steps"),
                        "vibe_cost_usd": e.get("cost_usd"), "thinking": self.thinking})
        p.model = MODELS[self.model]["name"]
        if p.usage.get("tool_attempts"):
            p.notes.append(f"vibe: {p.usage['tool_attempts']} tool call(s) attempted and denied: "
                           + ", ".join(p.usage.get("tools_attempted", [])))
