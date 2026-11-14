"""Pinned vendor CLI binaries: the eval runs a frozen copy, so a CLI that updates itself cannot change mid-run.

Three CLIs updated themselves during the 2026-11 runs (Muse 1.4.15 -> 1.4.18 -> 1.4.20, agy 1.5.3 -> 1.5.7, Claude
Code 2.1.322 -> 2.1.323 -> 2.1.325); each update stopped runs for a re-audit, or went unnoticed until one. A pin is a
copy of the audited binary outside the vendor's install path, in a read-only directory (an in-place self-update
cannot replace it), recorded in `eval/pins.json` with its version and SHA-256. `eval/pin.py` makes and lists pins.

Resolution order for a subject's binary: an explicit `binary=` argument (tests), then `BCA_<NAME>_BIN`, then the pin,
then PATH. A pin whose file is missing or whose hash differs fails closed (`isolation:`): the run must not silently
fall back to whatever the vendor installed since. `BCA_NO_PINS=1` ignores the pins (PATH is used).
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import cache
from pathlib import Path

PINS_FILE = Path(__file__).resolve().parents[2] / "eval" / "pins.json"
PIN_ROOT = Path(os.environ.get("BCA_PIN_ROOT") or "~/.local/share/bca-pins").expanduser()


class PinError(RuntimeError):
    pass


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(pins_file: Path | None = None) -> dict:
    try:
        return json.loads((pins_file or PINS_FILE).read_text())
    except FileNotFoundError:
        return {}


@cache
def _verified(path: str, want: str, mtime_ns: int) -> bool:  # hashed once per process and file state
    return sha256(path) == want


def pinned(name: str, pins_file: Path | None = None) -> dict | None:
    """The verified pin for CLI `name` ({"path", "version", "sha256", "env"}), or None when there is none.
    Raises PinError (an `isolation:` error) when a recorded pin cannot be used as recorded."""
    if os.environ.get("BCA_NO_PINS") == "1":
        return None
    pin = load(pins_file).get(name)
    if not pin:
        return None
    path = os.path.expanduser(pin["path"])
    if not os.path.isfile(path):
        raise PinError(f"isolation: the pinned {name} {pin['version']} is missing at {path}; re-pin with eval/pin.py")
    if not _verified(path, pin["sha256"], os.stat(path).st_mtime_ns):
        raise PinError(f"isolation: the pinned {name} at {path} is not the binary that was pinned "
                       f"({pin['version']}, sha256 {pin['sha256'][:12]}...); re-audit and re-pin with eval/pin.py")
    return {**pin, "path": path, "env": dict(pin.get("env") or {})}


def resolve(name: str, explicit: str | None = None) -> tuple[str | None, dict[str, str]]:
    """(binary path or None for PATH lookup, extra environment the pin needs)."""
    if explicit:
        return explicit, {}
    env = os.environ.get(f"BCA_{name.upper()}_BIN")
    if env:
        return env, {}
    pin = pinned(name)
    return (pin["path"], pin["env"]) if pin else (None, {})
