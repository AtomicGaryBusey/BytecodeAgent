"""`python -m bca.liveeval models`: per vendor, the installed CLI against the audited versions, the sign-in
state, the account's model catalog, and which models no run directory has benchmarked yet.

No model is called, so no plan quota is spent: each vendor's catalog comes from its own listing command
(`codex debug models`, `agy models`, `grok models`), its login-time config (Kimi's config.toml, refreshed
only by `kimi login`), or the Copilot SDK's listModels (copilot_driver.mjs, list mode). Claude Code has no
catalog command: its row shows only the version and the aliases, and a model id is checked with `probe`.

Every listing runs with that CLI's self-updater off (agy in a throwaway HOME whose update stamp is fresh,
since `agy models` in the real home starts agy's background updater: that is how 1.2.12 became 1.5.3 on
2026-11-07). `--latest` also asks npm for the newest published Codex and Copilot packages (network).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from bca.liveeval.registry import MODEL_ALIASES
from bca.liveeval.subject import child_env

RUNS = Path("eval/runs")


@dataclass
class Model:
    id: str
    name: str = ""
    efforts: list[str] = field(default_factory=list)
    note: str = ""  # hidden, not enabled, default, ...


@dataclass
class VendorReport:
    vendor: str
    version: str = "?"
    audited: tuple[str, ...] = ()
    latest: str = ""  # newest published version (--latest), "" when not asked or not knowable
    signed_in: str = "?"  # "yes", "no", or what the CLI said
    models: list[Model] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def version_ok(self) -> bool:
        return not self.audited or self.version in self.audited


def _run(argv: list[str], *, env: dict | None = None, timeout: float = 90.0, cwd: str | None = None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          env={**child_env(), **(env or {})}, cwd=cwd or tempfile.gettempdir())


# -- parsers (pure: unit-tested against captured output) ---------------------------------------------


def parse_codex(out: str) -> list[Model]:
    d = json.loads(out)
    models = d.get("models", d) if isinstance(d, dict) else d
    res = []
    for m in models:
        if not isinstance(m, dict) or not m.get("slug"):
            continue
        efforts = [x.get("effort") for x in m.get("supported_reasoning_levels") or [] if isinstance(x, dict)]
        res.append(Model(m["slug"], m.get("display_name") or "", efforts,
                         "hidden" if m.get("visibility") == "hide" else ""))
    return res


def parse_agy(out: str) -> list[Model]:
    res = []
    for line in out.splitlines():
        if "\t" in line:
            mid, name = line.split("\t", 1)
            if mid.strip() and " " not in mid.strip():
                res.append(Model(mid.strip(), name.strip()))
    return res


_GROK_LINE = re.compile(r"^\s*[*-]\s+(\S+)(?:\s+\((.*)\))?\s*$")


def parse_grok(out: str) -> tuple[list[Model], str]:
    signed = "no" if "not authenticated" in out.lower() else "yes" if "logged in" in out.lower() else "?"
    res = []
    for line in out.splitlines():
        m = _GROK_LINE.match(line)
        if m:
            res.append(Model(m.group(1), note=m.group(2) or ""))
    return res, signed


def parse_kimi(text: str) -> list[Model]:
    cfg = tomllib.loads(text)
    res = []
    for key, m in (cfg.get("models") or {}).items():
        if isinstance(m, dict):
            note = f"default effort {m['default_effort']}" if m.get("default_effort") else ""
            res.append(Model(key, m.get("display_name") or "", list(m.get("support_efforts") or []), note))
    return res


def parse_copilot(models: list) -> list[Model]:
    res = []
    for m in models:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        state = (m.get("policy") or {}).get("state")
        note = "" if state == "enabled" else "router" if m["id"] == "auto" else f"policy {state or 'not set'}"
        res.append(Model(m["id"], m.get("name") or "", list(m.get("supportedReasoningEfforts") or []), note))
    return res


# -- collectors (one per vendor; each fills a VendorReport and never raises) ---------------------------


def _version(cls) -> str:
    try:
        # an effort the class accepts: the Codex adapter refuses an unpinned model without one
        return cls("x", timeout=30, effort=(cls.efforts or (None,))[0]).cli_version
    except Exception as e:  # noqa: BLE001 -- a missing CLI is a row, not a crash
        return f"unavailable ({e})"


def collect_claude() -> VendorReport:
    r = VendorReport("claude")
    try:
        out = _run(["claude", "--version"], timeout=30)
        m = re.search(r"\d+\.\d+\.\d+", out.stdout)
        r.version = m.group(0) if m else "unknown"
    except (OSError, subprocess.SubprocessError) as e:
        r.version, r.error = "unavailable", str(e)
    r.signed_in = "not checked"
    r.notes.append("Claude Code has no catalog command: check a model id with `python -m bca.liveeval probe <id>`")
    r.notes.append("aliases: " + ", ".join(f"{a} = {m}" for a, m in MODEL_ALIASES.items()))
    return r


def collect_codex() -> VendorReport:
    from bca.liveeval.vendor_codex import CodexSubject

    r = VendorReport("codex", _version(CodexSubject), CodexSubject.audited_versions)
    try:
        out = _run(["codex", "debug", "models"], timeout=90)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[-300:] or f"exit {out.returncode}")
        r.models = parse_codex(out.stdout)
        r.signed_in = "yes" if (Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
                                / "auth.json").exists() else "no"
        r.notes.append("the server filters this list by CLI version (gpt-6.1-terra needs >= 0.165.0)")
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        r.error = f"codex debug models: {e}"
    return r


def collect_agy() -> VendorReport:
    from bca.liveeval.vendor_agy import AgySubject

    r = VendorReport("agy", _version(AgySubject), AgySubject.audited_versions)
    with tempfile.TemporaryDirectory(prefix="bca-agy-models-") as tmp:
        app = Path(tmp) / ".gemini" / "antigravity-cli"
        app.mkdir(parents=True)
        (app / "last_check.timestamp").touch()  # the background updater skips a check < 15 minutes old
        env = {"HOME": tmp, "TMPDIR": tmp, "XDG_CONFIG_HOME": f"{tmp}/.config", "XDG_CACHE_HOME": f"{tmp}/.cache",
               "XDG_DATA_HOME": f"{tmp}/.local/share", "XDG_STATE_HOME": f"{tmp}/.local/state"}
        try:
            out = _run(["agy", "models"], env=env, timeout=90, cwd=tmp)
            text = out.stdout + out.stderr
            if "sign in" in text.lower():
                r.signed_in = "no"
                r.error = "agy: signed out (run `agy` once interactively)"
            else:
                r.models = parse_agy(out.stdout)
                r.signed_in = "yes" if r.models else "?"
        except (OSError, subprocess.SubprocessError) as e:
            r.error = f"agy models: {e}"
    return r


def collect_grok() -> VendorReport:
    from bca.liveeval.vendor_grok import GROK_ENV, GrokSubject

    r = VendorReport("grok", _version(GrokSubject), GrokSubject.audited_versions)
    try:
        out = _run(["grok", "models"], env={k: v for k, v in GROK_ENV.items() if "UPDATE" in k}, timeout=90)
        r.models, r.signed_in = parse_grok(out.stdout + out.stderr)
        if r.signed_in == "no":
            r.notes.append("signed out: the list is grok's fallback, not the account's (`grok login`)")
    except (OSError, subprocess.SubprocessError) as e:
        r.error = f"grok models: {e}"
    return r


def collect_kimi() -> VendorReport:
    from bca.liveeval.vendor_kimi import KimiSubject

    r = VendorReport("kimi", _version(KimiSubject), KimiSubject.audited_versions)
    home = Path(os.environ.get("KIMI_CODE_HOME") or "~/.kimi-code").expanduser()
    try:
        cfg = home / "config.toml"
        r.models = parse_kimi(cfg.read_text())
        r.notes.append(f"catalog as of the last `kimi login` (config.toml {_mtime(cfg)}); re-login to refresh")
        creds = sorted((home / "credentials").glob("*.json"))
        r.signed_in = "yes (refresh token on file)" if creds else "no"
    except (OSError, tomllib.TOMLDecodeError) as e:
        r.error = f"kimi config: {e}"
    return r


def collect_copilot() -> VendorReport:
    from bca.liveeval.vendor_copilot import CopilotSubject

    try:
        s = CopilotSubject("x", timeout=30)
    except Exception as e:  # noqa: BLE001
        return VendorReport("copilot", "unavailable", error=f"copilot: {e}")
    r = VendorReport("copilot", s.cli_version, CopilotSubject.audited_versions)
    try:
        models, auth = s.list_models()
        r.models = parse_copilot(models)
        r.signed_in = f"yes ({auth.get('statusMessage')})" if auth.get("isAuthenticated") else "no (`gh auth login`)"
        r.notes.append("models with a policy other than enabled need enabling in GitHub's Copilot settings")
    except Exception as e:  # noqa: BLE001
        r.error = f"copilot listModels: {e}"
    return r


COLLECTORS = {"claude": collect_claude, "codex": collect_codex, "agy": collect_agy, "grok": collect_grok,
              "kimi": collect_kimi, "copilot": collect_copilot}
NPM_PACKAGES = {"codex": ["@openai/codex"], "copilot": ["@github/copilot", "@github/copilot-sdk"]}


def _mtime(p: Path) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))


def latest_versions(vendor: str) -> str:
    vals = []
    for pkg in NPM_PACKAGES.get(vendor, []):
        try:
            out = _run(["npm", "view", pkg, "version"], timeout=60)
            vals.append(f"{pkg.split('/')[-1]} {out.stdout.strip() or '?'}")
        except (OSError, subprocess.SubprocessError):
            vals.append(f"{pkg} ?")
    return ", ".join(vals)


# -- what has been run ------------------------------------------------------------------------------


def benchmarked(runs: Path = RUNS) -> dict[str, set[str]]:
    """vendor -> the model ids some run directory holds a group for (group.json model_arg; Claude aliases
    resolved to the id they stood for)."""
    out: dict[str, set[str]] = {}
    for g in runs.glob("*/*/*/group.json"):
        try:
            arg = json.loads(g.read_text()).get("model_arg") or ""
        except (OSError, ValueError):
            continue
        vendor, sep, model = arg.partition(":")
        if not sep:
            vendor, model = "claude", MODEL_ALIASES.get(arg, arg)
        if model:
            out.setdefault(vendor, set()).add(model)
    return out


def render(reports: list[VendorReport], done: dict[str, set[str]]) -> str:
    lines = []
    for r in reports:
        ran = done.get(r.vendor, set())
        ver = r.version + ("" if r.version_ok else f"  NOT AUDITED (audited: {', '.join(r.audited)})")
        lines.append(f"== {r.vendor}  cli {ver}" + (f"  latest: {r.latest}" if r.latest else "")
                     + f"  signed in: {r.signed_in}")
        if r.error:
            lines.append(f"   error: {r.error}")
        new = []
        for m in r.models:
            mark = "ran" if m.id in ran else "NEW"  # Kimi keys are kimi-code/<m>, as runs name them
            if mark == "NEW" and m.note not in ("router", "hidden"):  # hidden: internal (auto-review, ...)
                new.append(m.id)
            extra = "  ".join(x for x in (m.name, ("efforts " + "/".join(m.efforts)) if m.efforts else "", m.note) if x)
            lines.append(f"   [{mark:>3}] {m.id}" + (f"  {extra}" if extra else ""))
        if r.vendor == "claude":
            claude_ran = sorted(ran)
            lines.append("   benchmarked: " + (", ".join(claude_ran) or "none"))
        elif r.models:
            lines.append(f"   not yet benchmarked: {', '.join(new) if new else 'none'}")
        for n in r.notes:
            lines.append(f"   note: {n}")
    return "\n".join(lines)


def main(vendors: list[str] | None = None, *, latest: bool = False, as_json: bool = False) -> str:
    names = vendors or list(COLLECTORS)
    unknown = [v for v in names if v not in COLLECTORS]
    if unknown:
        raise ValueError(f"unknown vendor {unknown}; have {', '.join(COLLECTORS)}")
    reports = []
    for v in names:
        r = COLLECTORS[v]()
        if latest:
            r.latest = latest_versions(v)
        reports.append(r)
    done = benchmarked()
    if as_json:
        from dataclasses import asdict

        return json.dumps({"vendors": [asdict(r) for r in reports],
                           "benchmarked": {k: sorted(v) for k, v in done.items()}}, indent=2)
    return render(reports, done)
