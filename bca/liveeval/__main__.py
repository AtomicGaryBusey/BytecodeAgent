import argparse
import sys
from pathlib import Path

from bca.liveeval.prompts import ISAS, PATHS
from bca.liveeval.runner import Budget, run_matrix
from bca.liveeval.subject import ClaudeSubject
from bca.liveeval.summarize import summarize
from bca.liveeval.tasks import BY_NAME, TASKS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bca.liveeval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True, type=Path)
    r.add_argument("--tasks", default=",".join(t.name for t in TASKS))
    r.add_argument("--paths", default=",".join(PATHS))
    r.add_argument("--isas", default=",".join(ISAS))
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--max-calls", type=int, required=True, help="hard cap on subject calls for this run dir")
    r.add_argument("--timeout", type=float, default=180.0)
    r.add_argument("--max-attempts", type=int, default=4)
    s = sub.add_parser("summarize")
    s.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "summarize":
        print(summarize(args.run_dir))
        return 0
    args.run_dir.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.run_dir, args.max_calls)
    subject = ClaudeSubject(timeout=args.timeout)
    tasks = [BY_NAME[n] for n in args.tasks.split(",")]
    recs = run_matrix(subject, tasks, args.paths.split(","), args.isas.split(","), args.run_dir,
                      jobs=min(args.jobs, 4), budget=budget, max_attempts=args.max_attempts,
                      progress=lambda m: print(m, flush=True))
    print(f"calls used in this run dir: {budget.used}/{budget.max_calls}", flush=True)
    return 0 if all(not r.get("incomplete") for r in recs) else 2


if __name__ == "__main__":
    sys.exit(main())
