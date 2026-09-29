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

from bca.liveeval.subject import EFFORTS, ClaudeSubject, Subject
from bca.liveeval.vendor_base import CliSubject, CredHome, IsolationError, Parsed
from bca.liveeval.vendor_agy import AgySubject
from bca.liveeval.vendor_codex import CodexSubject
from bca.liveeval.vendor_grok import GrokSubject
from bca.liveeval.vendor_kimi import KimiSubject

VENDORS: dict[str, type[CliSubject]] = {"codex": CodexSubject, "kimi": KimiSubject, "agy": AgySubject, "grok": GrokSubject}


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


__all__ = ["AgySubject", "CliSubject", "CodexSubject", "GrokSubject", "CredHome", "IsolationError", "KimiSubject", "Parsed", "VENDORS",
           "all_efforts", "make_subject", "split_spec"]
