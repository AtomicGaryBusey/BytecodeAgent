"""Record a voided agy (or Muse) attempt as the scored attempt it is under the rules of 2026-11-15 (user decisions): when the
model calls agy's built-in `manage_task` tool, or its answer hits the output token limit, the attempt is one model
call scored on the text written up to that point -- not an infrastructure error followed by a fresh try.

For attempts made before the adapter applied those rules. The text is read from the kept raw stream
(attemptN.infraK.raw.jsonl.gz), up to the first tool step or error_message step, and graded by the same pipeline
(run_cell with a stub subject returning exactly that text). Scored attempts numbered N or later were made after a
do-over and are moved to <run>/superseded/<subject>/partial/<cell>/ first; unless the replayed attempt passes or is
the last allowed, the run then continues with attempt N+1 on its normal command.

An attempt whose request could not be verified (agy killed before it recorded the request, 2026-11-15 13:24) is
recorded only if it scores as a failure (an empty answer, or a wrong one): a pass on an unverified request is refused.

usage: .venv/bin/python eval/replay_voided.py <group dir (.../<subject>/baseline)> <tasks spec> <path> <cell> <attempt> <time>
  <time>: a substring of the infra record's time (e.g. 13:55) selecting which voided try of that attempt to score
"""
import gzip
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bca.liveeval.registry import select  # noqa: E402
from bca.liveeval.runner import MAX_ATTEMPTS, cell_id, plan_cells, run_cell  # noqa: E402
from bca.liveeval.subject import Response, Subject  # noqa: E402

gdir, spec, path, cid, want, when = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6]
cdir = gdir / "cells" / cid
infra = [json.loads(ln) for ln in (gdir / "infra.jsonl").read_text().splitlines()]
recs = [r for r in infra if r["cell"] == cid and r["attempt"] == want and when in r.get("time", "")]
assert len(recs) == 1, f"{len(recs)} infra records match"
rec = recs[0]
raw = cdir / f"attempt{want}.infra{rec['try']}.raw.jsonl.gz"
text, usage, stop, result_error = "", {}, None, ""
muse = rec.get("vendor") == "muse"
if muse:  # Muse Code's event log: the model step's deltas, up to Muse proposing its built-in search task
    with gzip.open(raw, "rt") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t, pl = ev.get("payload_type"), ev.get("payload") or {}
            if stop:
                continue
            kind = str((pl.get("event") or {}).get("task_kind") or "")
            if t == "task.lifecycle.proposed" and kind.startswith("tool.muse."):
                stop = {"step_type": "muse_tool", "kind": kind}
            elif t == "run.output.delta":
                text += pl.get("text") or ""
            elif t == "task.lifecycle.failed" and str((pl.get("event") or {}).get("reason", "")).startswith("stream guard"):
                text = ""
            elif t == "task.lifecycle.failed":
                raise SystemExit("a failed model task: not a search stop")
    assert stop, "no tool.muse.* proposal in the kept stream"
    assert f"non-model task was proposed: '{stop['kind']}'" in rec["error"], rec["error"][:120]
    why = (f"muse proposed its built-in {stop['kind']} task after the model step: the call was stopped there and the "
           "answer written before it is scored as it stands")
    mark = {"tool_stops": 1}
with (gzip.open(raw, "rt") if not muse else open("/dev/null")) as fh:
    for line in fh:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        b = ev.get("step_update") or {}
        if ev.get("event") == "result":
            result_error = str((ev.get("result") or {}).get("error") or "")
        if stop:
            continue
        if b.get("step_type") in ("tool", "error_message"):
            stop = b
            continue
        if b.get("step_type") == "agent_response":
            text += b.get("text_delta") or ""
            usage = b.get("usage") or usage
assert stop, "no tool or error_message step in the kept stream"
if muse:
    pass
elif stop["step_type"] == "tool":
    assert stop.get("tool_name") == "manage_task"
    why = (f"the model called agy's built-in manage_task tool ({json.dumps((stop.get('tool_info') or {}).get('parameters'))}): "
           "the call was stopped there and the answer written before it is scored as it stands")
    mark = {"tool_stops": 1}
else:
    assert "cut off because it exceeded the output token limit" in rec["error"] + result_error, rec["error"][:120]
    why = ("output token limit reached: agy's continuation was dropped; the answer written within the limit is scored "
           "as it stands")
    mark = {"output_cut": 1}
unverified = "cannot verify the request" in rec["error"]
# an unverified request with answer text is recorded only as a failure (checked after grading, below)

# scored attempts from `want` on followed a do-over: set them aside
run_dir = gdir.parent.parent
aside = run_dir / "superseded" / gdir.parent.name / "partial" / cid
later = [f for f in cdir.iterdir() if ".infra" not in f.name and f.name.startswith("attempt")
         and int(f.name[7:].split(".")[0]) >= want]
later += [f for f in (cdir / "cell.json", cdir / "final.disasm.txt") if f.exists()]
if later:
    aside.mkdir(parents=True, exist_ok=True)
    for f in later:
        shutil.move(str(f), str(aside / f.name))
for log in ("attempts", "results"):
    f = gdir / f"{log}.jsonl"
    if not f.exists():
        continue
    keep, drop = [], []
    for ln in f.read_text().splitlines(keepends=True):
        r = json.loads(ln)
        gone = r["cell"] == cid and (log == "results" or r["attempt"] >= want)
        (drop if gone else keep).append(ln)
    if drop:
        aside.mkdir(parents=True, exist_ok=True)
        with open(aside / f"set-aside.{log}.jsonl", "a") as out:  # a name the report globs do not read
            out.writelines(drop)
        f.write_text("".join(keep))
assert len(list(cdir.glob("attempt*.verdict.json"))) == want - 1, f"attempts 1..{want - 1} must be scored"

sample = rec.get("sample")
cells = [(t, b, p, i) for n in (None, sample and 1) for t, b, p, i in plan_cells(select(spec), [path], [rec["isa"]], n)
         if cell_id(t, p, i) == cid][:1]
assert len(cells) == 1, f"cannot find {cid} in the plan"
task, battery, p, isa = cells[0]


class Replay(Subject):
    name = rec["subject"]

    def complete(self, prompt, on_text=None, *, raw_path="", timeout=None):
        if on_text and text:
            on_text(text)
        return Response(text, ok=True, model=rec.get("model") or "", model_source="requested", wall_s=rec["wall_s"],
                        usage={**usage, **mark},
                        notes=[why, f"recorded by eval/replay_voided.py from {raw.name} (the attempt of {rec['time']}, "
                                    f"{rec['wall_s']:.0f} s, first logged as an infrastructure error"
                                    + ("; its request was not verifiable, the answer is empty" if unverified else "") + ")"])


meta = {k: rec[k] for k in ("subject", "variant", "battery", "effort", "cli_version", "vendor", "model_effort_variant")
        if k in rec}
out = run_cell(Replay(), task, p, isa, gdir, max_attempts=want, meta=meta, timeout=rec.get("timeout_s"))
assert not (unverified and text.strip() and out.get("pass_final")), "a PASS on an unverified request: restore and re-run"
print(cid, f"attempt {want}: {len(text)} chars ->", out.get("categories"), "moved aside:", len(later))
if not out.get("pass_final") and want < MAX_ATTEMPTS:  # the cell is not finished: the run goes on with the next attempt
    (cdir / "cell.json").unlink()
    res = gdir / "results.jsonl"
    res.write_text("".join(ln for ln in res.read_text().splitlines(keepends=True) if json.loads(ln)["cell"] != cid))
    print("  cell left open: the run continues with attempt", want + 1)
else:
    print("  cell complete")
