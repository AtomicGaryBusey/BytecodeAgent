"""Make, list and remove pinned vendor CLI binaries (see bca/liveeval/pins.py for why and how they are used).

  .venv/bin/python eval/pin.py list                       pins, and what the vendor has installed now (drift)
  .venv/bin/python eval/pin.py pin <name> [--from PATH]   copy the installed binary (or PATH) into the pin store
  .venv/bin/python eval/pin.py unpin <name>               drop the manifest entry (the copy is left in the store)

Pin only a version the adapter has been audited against: a pin freezes it for every later run. To move to a new
version: audit it (eval/audits/), add it to the adapter's audited versions, then `pin` again.
Store: ~/.local/share/bca-pins/<name>/<version>/<name>, read-only file in a read-only directory. Manifest:
eval/pins.json (in git; the binaries are not).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bca.liveeval import pins  # noqa: E402

# name -> (how to find the real installed binary, extra env the binary needs when run without its launcher)
MUSE_DIR = Path("~/.local/bin").expanduser()


def installed(name: str) -> Path | None:
    if name == "muse":  # the launcher script execs muse-bin-<version>; the launcher is what self-updates
        try:
            ver = (MUSE_DIR / ".muse-version").read_text().strip()
        except OSError:
            return None
        p = MUSE_DIR / f"muse-bin-{ver}"
        return p if p.is_file() else None
    w = shutil.which(name)
    return Path(w).resolve() if w else None


def pin_env(name: str) -> dict[str, str]:
    if name == "muse":  # what the launcher exports for the binary, plus the launcher-level switch for good measure
        info = MUSE_DIR / ".muse-release-info.json"
        return {"MUSE_RELEASE_INFO": info.read_text().strip(), "MUSE_NO_AUTO_UPDATE": "1"} if info.is_file() else {}
    if name == "claude":
        return {"DISABLE_AUTOUPDATER": "1"}
    return {}


def version_of(path: Path, env: dict[str, str]) -> str:
    out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=60,
                         env={**os.environ, **env}, cwd="/tmp", stdin=subprocess.DEVNULL)
    m = re.search(r"\d+\.\d+\.\d+", out.stdout + out.stderr)
    if out.returncode != 0 or not m:
        raise SystemExit(f"cannot read a version from {path} --version: {(out.stdout + out.stderr)[:200]}")
    return m.group(0)


def save(d: dict) -> None:
    pins.PINS_FILE.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")


def cmd_pin(name: str, src: Path | None) -> None:
    src = src or installed(name)
    if not src or not src.is_file():
        raise SystemExit(f"no installed {name} binary found; pass --from PATH")
    if src.read_bytes()[:2] == b"#!":
        raise SystemExit(f"{src} is a script, not a self-contained binary: {name} cannot be pinned by copying one file")
    env = pin_env(name)
    ver = version_of(src, env)
    dest_dir = pins.PIN_ROOT / name / ver
    dest = dest_dir / name
    if not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(dest_dir, 0o755)
        shutil.copy2(src, dest)
    os.chmod(dest, 0o555)
    os.chmod(dest_dir, 0o555)  # nothing can replace or add a file here without a chmod first
    digest = pins.sha256(dest)
    if digest != pins.sha256(src):
        raise SystemExit(f"{dest} differs from {src}: an earlier pin of {ver} holds different bytes; inspect it")
    if version_of(dest, env) != ver:
        raise SystemExit(f"the copy at {dest} does not report {ver}")
    d = pins.load()
    d[name] = {"version": ver, "path": str(dest).replace(str(Path.home()), "~", 1), "sha256": digest, "env": env,
               "pinned_from": str(src).replace(str(Path.home()), "~", 1)}
    save(d)
    print(f"pinned {name} {ver}: {dest} (sha256 {digest[:16]}...)")


def cmd_list() -> None:
    d = pins.load()
    for name in sorted(set(d) | {"claude", "agy", "muse"}):
        pin = d.get(name)
        cur = installed(name)
        try:
            cur_v = version_of(cur, pin_env(name)) if cur else "not installed"
        except SystemExit:
            cur_v = "unreadable"
        if not pin:
            print(f"{name}: NOT pinned (installed: {cur_v})")
            continue
        try:
            pins.pinned(name)
            state = "ok"
        except pins.PinError as e:
            state = str(e)
        drift = "" if cur_v == pin["version"] else f"; the vendor install has moved to {cur_v}"
        print(f"{name}: pinned {pin['version']} ({state}){drift}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["list"]:
        cmd_list()
    elif a[:1] == ["pin"] and len(a) >= 2:
        cmd_pin(a[1], Path(a[a.index("--from") + 1]).expanduser() if "--from" in a else None)
    elif a[:1] == ["unpin"] and len(a) == 2:
        d = pins.load()
        d.pop(a[1], None)
        save(d)
        print(f"unpinned {a[1]}")
    else:
        raise SystemExit(__doc__)
