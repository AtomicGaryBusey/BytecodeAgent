"""Stop eval processes by PID, never by pattern-matching a shell command line.

`pkill -f <pattern>` matches the shell that runs it (the pattern is in its own command line): it killed the calling
shell twice in this project (2026-11-12, 2026-11-13). This selects processes by exact argv fields, never selects
itself or its ancestors, prints what it will stop, and signals each PID.

  eval/stop.py --run <run-dir name or path> ...   `bca.liveeval run --run-dir <that>` (and the subject CLIs it started)
  eval/stop.py --script <script path or basename> a chain / resumer / guard script. Its children are left running
                                                  (a run it started carries on) unless --tree or --run names them.
  eval/stop.py --watch                            every eval/watch.py
  --tree      also stop every descendant of the selected processes
  --dry-run   print the selection, signal nothing
  --kill      SIGKILL instead of SIGTERM (only after a SIGTERM did not work)
Exit status: 0 if every selected process is gone afterwards (or nothing matched), 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import status  # noqa: E402


def ancestors() -> set[int]:
    out, p = set(), os.getpid()
    while p > 1:
        out.add(p)
        p = status.parent(p)
    return out


def norm_run(name: str) -> str:
    return os.path.basename(name.rstrip("/"))


def select(a, ps) -> dict[int, list[str]]:
    mine, sel = ancestors(), {}
    runs = {norm_run(r) for r in a.run}
    for pid, argv in ps:
        if pid in mine:
            continue
        if runs and "bca.liveeval" in argv and "--run-dir" in argv and \
                norm_run(argv[argv.index("--run-dir") + 1]) in runs:
            sel[pid] = argv
        for s in a.script:
            # the script as an argv field of an interpreter (bash x.sh / python x.py), compared by path or basename
            if any(f == s or (os.sep not in s and os.path.basename(f) == s) for f in argv[:3]) and \
                    os.path.basename(argv[0]) in ("bash", "sh", "python", "python3") or argv[0] == s:
                sel[pid] = argv
        if a.watch and any(f.endswith("eval/watch.py") for f in argv[:3]):
            sel[pid] = argv
    if a.tree or runs:  # a run's subject CLI children always go with it
        for root in [p for p in sel if a.tree or "bca.liveeval" in sel[p]]:
            for p in status.family(root, ps) - mine:
                sel.setdefault(p, next(av for q, av in ps if q == p))
    return sel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="append", default=[])
    ap.add_argument("--script", action="append", default=[])
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--kill", action="store_true")
    a = ap.parse_args()
    if not (a.run or a.script or a.watch):
        ap.error("name what to stop: --run, --script or --watch")
    sel = select(a, status.procs())
    for pid, argv in sorted(sel.items()):
        print(f"{'would stop' if a.dry_run else 'stopping'} {pid}: {' '.join(argv)[:140]}")
    if not sel:
        print("nothing matched")
        return 0
    if a.dry_run:
        return 0
    for pid in sel:
        try:
            os.kill(pid, signal.SIGKILL if a.kill else signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):
        left = [p for p in sel if os.path.exists(f"/proc/{p}")]
        if not left:
            return 0
        time.sleep(0.5)
    print(f"still alive after 10 s: {left} (re-run with --kill)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
