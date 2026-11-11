"""Gzip every raw stream (`*.raw.jsonl` -> `*.raw.jsonl.gz`) under eval/runs, in place.

The runner gzips each attempt's raw stream when the call returns (since 2026-11-11); this compresses the ones
recorded before that. Nothing scores from a raw stream: it is the audit record of what the vendor sent, and
`zcat` reads it back unchanged. Skipped: a run directory whose run.lock is held by a live run, and any file
written in the last 15 minutes. Run from the repo root: .venv/bin/python eval/compress_raw.py [--dry-run]
"""
import fcntl
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bca.liveeval.runner import compress_raw  # noqa: E402

RECENT_S = 15 * 60


def live_run_dirs(root: Path) -> set[Path]:
    """Run directories whose run.lock some process holds right now."""
    live = set()
    for lock in root.rglob("run.lock"):
        try:
            with open(lock, "a") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
        except BlockingIOError:
            live.add(lock.parent)
        except OSError:
            pass
    return live


def main(dry: bool) -> None:
    root = Path("eval/runs")
    live = live_run_dirs(root)
    now = time.time()
    n = before = after = skipped = 0
    for f in sorted(root.rglob("*.raw.jsonl")):
        if any(d in f.parents for d in live) or now - f.stat().st_mtime < RECENT_S:
            skipped += 1
            continue
        size = f.stat().st_size
        before += size
        n += 1
        if not dry:
            after += compress_raw(f).stat().st_size
    print(f"{'would compress' if dry else 'compressed'} {n} raw streams, {before / 1e6:,.0f} MB"
          + ("" if dry else f" -> {after / 1e6:,.0f} MB") + f"; skipped {skipped} (live run or written in the last "
          f"{RECENT_S // 60} min); live runs: {', '.join(str(d) for d in sorted(live)) or 'none'}")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
