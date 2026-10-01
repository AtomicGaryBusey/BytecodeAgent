"""Have a model write the editor: prompt -> assembly -> GNU as + ld -> sandboxed keystroke tests, with the
first failure fed back for up to N rounds. Every prompt, response and verdict is kept under runs/<ts>/.

    PYTHONPATH=. .venv/bin/python -m demo.editor.run_astra [--model codex:gpt-6-astra] [--rounds 6]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from bca.liveeval.apps import escape
from bca.liveeval.pipelines import strip_fences
from bca.liveeval.vendors import make_subject
from demo.editor.prompt import build_prompt
from demo.editor.sandbox import BuildError, build, run_scenarios
from demo.editor.scenarios import SCENARIOS

HERE = Path(__file__).parent
QUOTA_RE = re.compile(r"usage limit|rate limit|quota|try again at", re.I)
KEYNAMES = {"\x1b[A": "Up", "\x1b[B": "Down", "\x1b[C": "Right", "\x1b[D": "Left", "\x1b[H": "Home",
            "\x1b[F": "End", "\x1b[3~": "Delete", "\x1b[5~": "PgUp", "\x1b[6~": "PgDn", "\r": "Enter",
            "\x7f": "Backspace", "\x13": "Ctrl-S", "\x11": "Ctrl-Q"}


def describe_keys(keys: list[str]) -> str:
    out, text = [], ""
    for k in keys:
        if k in KEYNAMES:
            if text:
                out.append(repr(text))
                text = ""
            out.append(KEYNAMES[k])
        else:
            text += k
    if text:
        out.append(repr(text))
    return ", ".join(out)


def failure_message(results: list[dict]) -> str:
    bad = [r for r in results if not r["ok"]]
    s = {x["name"]: x for x in SCENARIOS}[bad[0]["name"]]
    r = bad[0]
    files = "; ".join(f"{k} = {escape(v.encode('latin-1'))}" for k, v in s["files"].items()) or "none"
    screen = r["screen"]
    tail = escape(screen[-600:]) if screen else "(nothing)"
    return (
        f"{len(bad)} of {len(results)} test scenarios failed. The first: `{s['name']}` ({s['doc']}).\n"
        f"Terminal: 24 rows x 80 columns. Files before: {files}. Command: edit {s['arg']}\n"
        f"Keys sent (each key as one write; quoted text = one write per character): {describe_keys(s['keys'])}\n"
        "Problems:\n- " + "\n- ".join(r["problems"]) + "\n"
        f"Last bytes the program wrote to the terminal ({len(screen)} in total): {tail}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="codex:gpt-6-astra")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    run = HERE / "runs" / time.strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    subject = make_subject(args.model, timeout=args.timeout)
    (run / "subject.json").write_text(json.dumps({"model": args.model, **subject.describe()}, indent=1))
    previous = None
    for rnd in range(1, args.rounds + 1):
        prompt = build_prompt(previous)
        (run / f"round{rnd}.prompt.txt").write_text(prompt)
        t0 = time.time()
        resp = subject.complete(prompt, raw_path=str(run / f"round{rnd}.raw.jsonl"), timeout=args.timeout)
        verdict = {"round": rnd, "wall_s": round(time.time() - t0, 1), "ok": False, "model": resp.model,
                   "usage": resp.usage, "response_chars": len(resp.text or "")}
        (run / f"round{rnd}.response.txt").write_text(resp.text or "")
        if not resp.ok or resp.refusal:
            verdict["error"] = resp.refusal or resp.error
            (run / f"round{rnd}.verdict.json").write_text(json.dumps(verdict, indent=1))
            print(f"round {rnd}: call failed: {verdict['error'][:300]}")
            if QUOTA_RE.search(verdict["error"] or ""):
                print("plan quota reached: stopping")
            return
        src = strip_fences(resp.text)
        binary = run / f"round{rnd}.edit"
        try:
            build(src, binary)
        except BuildError as e:
            verdict["stage"], failure = "build", str(e)
        else:
            results = run_scenarios(binary)
            verdict["stage"] = "test"
            verdict["scenarios"] = {r["name"]: r["problems"] or "ok" for r in results}
            if all(r["ok"] for r in results):
                verdict["ok"] = True
                (run / f"round{rnd}.verdict.json").write_text(json.dumps(verdict, indent=1))
                shutil.copy(binary, HERE / "edit")
                (HERE / "edit.s").write_text(src if src.endswith("\n") else src + "\n")
                digest = hashlib.sha256((HERE / "edit").read_bytes()).hexdigest()
                print(f"round {rnd}: ALL {len(results)} SCENARIOS PASS; edit sha256 {digest}")
                return
            failure = failure_message(results)
        verdict["failure"] = failure
        (run / f"round{rnd}.verdict.json").write_text(json.dumps(verdict, indent=1, default=str))
        print(f"round {rnd}: {verdict['stage']} failed: {failure.splitlines()[0][:200]}")
        previous = (src, failure)
    print(f"no passing editor in {args.rounds} rounds")


if __name__ == "__main__":
    main()
