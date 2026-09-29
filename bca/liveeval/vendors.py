"""Subject registry: `<vendor>:<model>` picks a vendor CLI adapter, anything else is a Claude model.

    codex:<model>  OpenAI Codex CLI           (vendor_codex.CodexSubject)
    kimi:<model>   Moonshot Kimi Code CLI     (vendor_kimi.KimiSubject)
    agy:<model>    Google Antigravity CLI     (vendor_agy.AgySubject)
    grok:<model>   xAI Grok Build CLI         (vendor_grok.GrokSubject)
    <other>        `claude -p --model <other>` (subject.ClaudeSubject)

The isolation contract every vendor adapter meets is described in vendor_base; the method and its
known limits in reports/cross-vendor-methodology.md.
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
