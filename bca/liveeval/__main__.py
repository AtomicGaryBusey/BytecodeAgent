"""python -m bca.liveeval {run,summarize,compare,probe} -- see bca/liveeval/__init__.py."""

import argparse
import json
import sys
from pathlib import Path

from bca.liveeval.prompts import ISAS, PATHS
from bca.liveeval.registry import (
    BATTERY_TIMEOUTS,
    DEFAULT_TIMEOUT,
    MODEL_ALIASES,
    RegistryError,
    default_timeout,
    load_variants,
    model_for,
    safe_name,
    select,
    variant_max_attempts,
)
from bca.liveeval.runner import Budget, InfraGuard, RunDirBusy, RunLock, plan_cells, run_matrix, run_plan
from bca.liveeval.subject import CLI_DEFAULT_EFFORT, EFFORTS
from bca.liveeval.vendors import VENDORS, all_efforts, make_subject
from bca.liveeval.summarize import compare, summarize


def _split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _subjects(args) -> list[str]:
    if args.model and args.subjects:
        raise RegistryError("give --model or --subjects, not both")
    names = _split(args.subjects) or _split(args.model)
    for n in names:
        safe_name(n)
    if len(set(map(safe_name, names))) != len(names):
        raise RegistryError(f"duplicate subject in {names}")
    return names


def main(argv=None, *, subject_factory=None) -> int:
    """`subject_factory(model_arg) -> Subject` replaces the live `claude -p` subject (tests)."""
    ap = argparse.ArgumentParser(prog="python -m bca.liveeval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True, type=Path)
    r.add_argument("--tasks", default="round1",
                   help="round1 (default), round2, long, adversarial, all (every battery), or task names; "
                        "comma-separated, mixable")
    r.add_argument("--paths", default=",".join(PATHS))
    r.add_argument("--isas", default=",".join(ISAS))
    r.add_argument("--model", help=f"one subject: an alias ({', '.join(MODEL_ALIASES)}), a Claude model id, or "
                                  f"<vendor>:<model> for another vendor's CLI ({', '.join(VENDORS)})")
    r.add_argument("--subjects", help="several subjects, comma-separated (run one after the other into "
                                      "<run-dir>/<subject>/<variant>/)")
    r.add_argument("--variant", default="baseline",
                   help="prompt variants from bca.liveeval.adversarial.VARIANTS, comma-separated "
                        "(baseline = unchanged prompts; 'all' = every variant)")
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--max-calls", type=int, required=True,
                   help="hard cap on subject calls for this run dir, global across subjects and variants")
    r.add_argument("--timeout", type=float, default=None,
                   help=f"per-call subject timeout in seconds for every battery (default: {DEFAULT_TIMEOUT:.0f}, "
                        + ", ".join(f"{b} {t:.0f}" for b, t in BATTERY_TIMEOUTS.items()) + ")")
    r.add_argument("--effort", choices=all_efforts(),
                   help="reasoning effort for every subject (claude: " + "|".join(EFFORTS) + "; "
                        + "; ".join(f"{k}: {'|'.join(v.efforts)}" for k, v in VENDORS.items())
                        + ") (default: not passed; recorded as "
                        f"{CLI_DEFAULT_EFFORT!r}). Recommended for multi-subject runs.")
    r.add_argument("--max-attempts", type=int, default=4)
    r.add_argument("--max-infra-errors", type=int, default=5,
                   help="abort the run after this many consecutive infrastructure errors (process failure, "
                        "overload, rate limit); auth / unknown-model errors abort at once")
    r.add_argument("--infra-backoff", type=float, default=10.0,
                   help="seconds before the first retry after an infrastructure error (doubles each time, "
                        "at most 300)")
    r.add_argument("--dry-run", action="store_true",
                   help="print the cells, the worst-case and the expected call counts, make no calls")
    r.add_argument("--estimate-from", action="append", type=Path, default=[], metavar="RUN_DIR",
                   help="earlier run dirs whose observed attempts per cell drive the expected-calls estimate "
                        "(repeatable)")
    s = sub.add_parser("summarize")
    s.add_argument("run_dir", type=Path)
    c = sub.add_parser("compare", help="markdown comparison of several run dirs (model x battery, ...)")
    c.add_argument("run_dirs", type=Path, nargs="+")
    pr = sub.add_parser("probe", help="one tiny isolated call per model; prints the model id the stream reports")
    pr.add_argument("models", nargs="+")
    pr.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args(argv)
    try:
        return _dispatch(args, subject_factory)
    except (ValueError, FileNotFoundError, RunDirBusy) as e:  # RegistryError, a broken variant, a bad run dir
        print(f"error: {e}", file=sys.stderr)
        return 1


def _dispatch(args, subject_factory) -> int:
    if args.cmd == "summarize":
        print(summarize(args.run_dir))
        return 0
    if args.cmd == "compare":
        print(compare(args.run_dirs))
        return 0
    if args.cmd == "probe":
        if len(args.models) > 5:
            raise RegistryError("probe at most 5 models per invocation")
        for m in args.models:
            subj = subject_factory(model_for(m)) if subject_factory else make_subject(model_for(m),
                                                                                    timeout=args.timeout)
            resp = subj.complete("Reply with the single word OK.")
            print(json.dumps({"model_arg": m, "ok": resp.ok, "model_reported": resp.model, "text": resp.text[:40],
                              "error": resp.error[:300], "cost_usd": resp.cost_usd}))
        return 0
    return _run(args, subject_factory)


def _run(args, subject_factory) -> int:
    from bca.liveeval.plan import Rates, estimate, render

    subjects = _subjects(args)
    vnames = _split(args.variant) or ["baseline"]
    variants = load_variants(vnames)
    selected = select(args.tasks)
    paths, isas = _split(args.paths), _split(args.isas)
    bad = [p for p in paths if p not in PATHS] + [i for i in isas if i not in ISAS]
    if bad:
        raise RegistryError(f"unknown path/ISA {bad}; have {', '.join(PATHS)} / {', '.join(ISAS)}")
    cells = plan_cells(selected, paths, isas)
    flat = not subjects and list(variants) == ["baseline"]  # the round-1/round-2 layout
    if len(subjects) > 1 and not args.effort:
        print(f"warning: {len(subjects)} subjects without --effort: each runs at its CLI default effort, which "
              f"may differ between models; pass an explicit --effort ({'|'.join(EFFORTS)}) for a like-for-like "
              "comparison", file=sys.stderr)
    subjects = subjects or ["default"]

    def timeout_for(battery: str) -> float:
        return args.timeout if args.timeout is not None else default_timeout(battery)

    if args.dry_run:
        if flat:
            for t, _b, p, i in cells:
                print(f"{t.name}.{p}.{i}")
        else:
            for sname in subjects:
                for v in variants:
                    for t, _b, p, i in cells:
                        print(f"{safe_name(sname)}/{safe_name(v)}/{t.name}.{p}.{i}")
        n = len(cells) * len(subjects) * len(variants)
        vmax = {v: variant_max_attempts(v, args.max_attempts) for v in variants}
        most = len(cells) * len(subjects) * sum(vmax.values())
        each = (f"{args.max_attempts} attempts each" if set(vmax.values()) == {args.max_attempts}
                else "attempts per variant: " + ", ".join(f"{v} {m}" for v, m in vmax.items()))
        print(f"{n} cells; at most {most} calls ({each}); --max-calls {args.max_calls}")
        bats = list(dict.fromkeys(b for _t, b, _p, _i in cells))
        print("per-call timeout: " + ", ".join(f"{b or 'default'} {timeout_for(b):.0f} s" for b in bats)
              + f"; effort: {args.effort or CLI_DEFAULT_EFFORT}")
        from bca.liveeval.runner import check_variants

        check_variants(variants, cells)
        used = Budget(args.run_dir, args.max_calls).used if args.run_dir.exists() else 0
        rows = estimate(args.run_dir if args.run_dir.exists() else None, [safe_name(x) for x in subjects],
                        [safe_name(v) for v in variants], cells, max_attempts=args.max_attempts,
                        rates=Rates.from_dirs(args.estimate_from), flat=flat,
                        variant_names={safe_name(v): v for v in variants})
        print()
        print(render(rows, max_calls=args.max_calls, used=used))
        return 0
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with RunLock(args.run_dir):
        return _live(args, subject_factory, subjects, variants, selected, paths, isas, flat, timeout_for)


def _live(args, subject_factory, subjects, variants, selected, paths, isas, flat, timeout_for) -> int:
    budget = Budget(args.run_dir, args.max_calls)
    guard = InfraGuard(max_consecutive=args.max_infra_errors, base_delay=args.infra_backoff)
    progress = lambda m: print(m, flush=True)  # noqa: E731

    def make(model_arg):
        if subject_factory:
            return subject_factory(model_arg)
        return make_subject(model_arg, timeout=args.timeout or DEFAULT_TIMEOUT, effort=args.effort)

    if flat:
        subj = make(None)
        recs = run_matrix(subj, selected, paths, isas, args.run_dir, jobs=min(args.jobs, 4), budget=budget,
                          max_attempts=args.max_attempts, progress=progress, timeout_for=timeout_for, guard=guard,
                          meta=subj.describe() if hasattr(subj, "describe") else None)
    else:
        subj = {safe_name(n): make(None if n == "default" else model_for(n)) for n in subjects}
        models = {safe_name(n): n for n in subjects}
        recs = run_plan(subj, {safe_name(k): v for k, v in variants.items()}, selected, paths, isas, args.run_dir,
                        jobs=min(args.jobs, 4), budget=budget, max_attempts=args.max_attempts, progress=progress,
                        models=models, timeout_for=timeout_for, guard=guard)
    print(f"calls used in this run dir: {budget.used}/{budget.max_calls}", flush=True)
    if guard.total:
        print(f"infrastructure errors (retried, not counted as attempts): {guard.total}", flush=True)
    if guard.aborted:
        left = sum(bool(r.get("incomplete")) for r in recs)
        print(f"error: run aborted: {guard.aborted}. {left} cells left incomplete (no cell.json); fix the cause "
              "and re-run the same command to resume them.", file=sys.stderr, flush=True)
        return 3
    return 0 if all(not r.get("incomplete") for r in recs) else 2


if __name__ == "__main__":
    sys.exit(main())
