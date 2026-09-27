import argparse
import sys
from pathlib import Path

from bca.liveeval.prompts import ISAS, PATHS
from bca.liveeval.runner import Budget, run_matrix
from bca.liveeval.subject import ClaudeSubject
from bca.liveeval.summarize import summarize
from bca.liveeval.tasks2 import feasible_cells, select_tasks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bca.liveeval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True, type=Path)
    r.add_argument("--tasks", default="round1",
                   help="round1 (default), round2, all, or comma-separated task names")
    r.add_argument("--paths", default=",".join(PATHS))
    r.add_argument("--isas", default=",".join(ISAS))
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--max-calls", type=int, required=True, help="hard cap on subject calls for this run dir")
    r.add_argument("--timeout", type=float, default=180.0)
    r.add_argument("--max-attempts", type=int, default=4)
    r.add_argument("--dry-run", action="store_true",
                   help="print the cells and the worst-case call count, make no calls")
    s = sub.add_parser("summarize")
    s.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "summarize":
        print(summarize(args.run_dir))
        return 0
    tasks = select_tasks(args.tasks)
    cells = feasible_cells(tasks, args.paths.split(","), args.isas.split(","))
    if args.dry_run:
        for t, p, i in cells:
            print(f"{t.name}.{p}.{i}")
        print(f"{len(cells)} cells; at most {len(cells) * args.max_attempts} calls "
              f"({args.max_attempts} attempts each); --max-calls {args.max_calls}")
        return 0
    args.run_dir.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.run_dir, args.max_calls)
    subject = ClaudeSubject(timeout=args.timeout)
    recs = run_matrix(subject, tasks, args.paths.split(","), args.isas.split(","), args.run_dir,
                      jobs=min(args.jobs, 4), budget=budget, max_attempts=args.max_attempts,
                      progress=lambda m: print(m, flush=True))
    print(f"calls used in this run dir: {budget.used}/{budget.max_calls}", flush=True)
    return 0 if all(not r.get("incomplete") for r in recs) else 2


if __name__ == "__main__":
    sys.exit(main())
