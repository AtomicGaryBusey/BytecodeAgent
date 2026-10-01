"""Build the editor from assembly (GNU as + ld, no libc) and run it ONLY inside a throwaway container: no
network, read-only root, its own scratch directory, an unprivileged user, CPU/memory/process limits."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from bca.image import AsmError, assemble_program
from demo.editor.scenarios import SCENARIOS

HERE = Path(__file__).parent
IMAGE = "python:3.12-slim"
LD = shutil.which("aarch64-linux-gnu-ld") or "ld"


class BuildError(Exception):
    pass


def build(source: str, out: Path) -> None:
    """Assemble with the eval's GNU as wrapper (its refusals: no file inclusion, no macros, the prompt's
    placement rules) and link a static, libc-free executable."""
    try:
        obj = assemble_program(source, "aarch64")
    except AsmError as e:
        raise BuildError(f"GNU as rejected the program:\n{str(e)[:1500]}") from None
    with tempfile.TemporaryDirectory(prefix="edit-build-") as tmp:
        o = Path(tmp) / "edit.o"
        o.write_bytes(obj)
        r = subprocess.run([LD, "-static", "-nostdlib", "-e", "_start", "-o", str(out), str(o)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise BuildError(f"The linker rejected the program:\n{(r.stderr or r.stdout).strip()[:1500]}")


def docker_command(work: Path | str, names: list[str] | None = None) -> list[str]:
    """The sandbox: no network, read-only root, an unprivileged user, no capabilities, CPU/memory/process limits;
    the only writable places are /tmp (tmpfs) and the scratch directory mounted at /work."""
    return ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
            "-v", f"{work}:/work", "-w", "/work", "--user", f"{os.getuid()}:{os.getgid()}",
            "--cpus", "1", "--memory", "256m", "--pids-limit", "64", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", IMAGE, "python3", "/work/driver.py", *(names or [])]


def run_scenarios(binary: Path, names: list[str] | None = None, timeout: float = 300) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="edit-sandbox-") as tmp:
        work = Path(tmp)
        shutil.copy(binary, work / "edit")
        os.chmod(work / "edit", 0o755)
        shutil.copy(HERE / "driver.py", work / "driver.py")
        (work / "scenarios.json").write_text(json.dumps(SCENARIOS))
        (work / "run").mkdir()
        os.chmod(work, 0o777)
        os.chmod(work / "run", 0o777)
        cmd = docker_command(work, names)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"sandbox failed (exit {r.returncode}): {r.stderr.strip()[-800:]}")
        results = json.loads(r.stdout)
    by_name = {s["name"]: s for s in SCENARIOS}
    for res in results:
        s = by_name[res["name"]]
        res["screen"] = base64.b64decode(res["screen"])
        res["files"] = {k: None if v is None else base64.b64decode(v).decode("latin-1")
                        for k, v in res["files"].items()}
        problems = []
        if res["timed_out"]:
            problems.append(f"did not exit within {5:.0f} s after the last key (killed)")
        elif res["status"] != 0:
            problems.append(f"exit status {res['status']} (expected 0)")
        for name, want in s["expect"].items():
            got = res["files"].get(name)
            if got != want:
                problems.append(f"{name}: expected {want!r}, " + ("but the file does not exist" if got is None else f"got {got!r}"))
        if not res["restored"] and not res["timed_out"]:
            problems.append("the terminal settings were not restored on exit")
        screen = res["screen"].decode("latin-1")
        for text in s.get("screen", []):
            if text not in screen:
                problems.append(f"the screen output never contained {text!r}")
        res["ok"], res["problems"] = not problems, problems
    return results
