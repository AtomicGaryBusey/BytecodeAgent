"""Have a model write the editor as raw AArch64 machine code: prompt -> the model's hex -> the bytes copied
unchanged into an ELF container (bca.liveeval.hexpath.write_elf; no assembler, no linker) -> the same
sandboxed keystroke tests as the assembly editor, with the first failure fed back for up to N rounds. Every
prompt, response and verdict is kept under demo/editor-hex/runs/<ts>/; a passing program becomes
demo/editor-hex/edit (binary) and demo/editor-hex/edit.hex (the model's exact output).

    PYTHONPATH=. .venv/bin/python -m demo.editor.run_astra_hex [--model codex:gpt-6-astra] [--rounds 6]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from bca.liveeval import hexpath
from bca.liveeval.pipelines import strip_fences
from bca.liveeval.vendors import make_subject
from demo.editor.prompt_hex import WORK_AREA, build_prompt
from demo.editor.run_astra import QUOTA_RE, failure_message
from demo.editor.sandbox import run_scenarios

OUT = Path(__file__).resolve().parent.parent / "editor-hex"
LISTING = ("\n\nYour machine code, as written, decodes as (address, your line, the hex, the disassembly; the "
           "entry point is the first line):\n")


class BuildError(Exception):
    pass


def build(text: str, out: Path) -> hexpath.HexProgram:
    """The model's bytes into an ELF container, unchanged. Raises BuildError (with the listing when the
    bytes parse but an instruction is rejected)."""
    try:
        prog = hexpath.parse_program("aarch64", text)
    except hexpath.HexError as e:
        raise BuildError(f"The machine code is malformed: {e}") from None
    try:
        hexpath.check("aarch64", prog.lines, hexpath.HEX_TEXT_BASE)
    except hexpath.HexError as e:
        raise BuildError(f"The machine code was rejected before running: {e}"
                         + LISTING + hexpath.program_listing("aarch64", prog)) from None
    elf = hexpath.write_elf(prog, WORK_AREA)
    assert elf[0x1000:0x1000 + len(prog.text)] == prog.text  # the executed code is the model's bytes
    out.write_bytes(elf)
    os.chmod(out, 0o755)
    return prog


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="codex:gpt-6-astra")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    run = OUT / "runs" / time.strftime("%Y%m%d-%H%M%S")
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
            prog = build(src, binary)
        except BuildError as e:
            verdict["stage"], failure = "build", str(e)
        else:
            verdict["code_bytes"], verdict["data_bytes"] = len(prog.text), len(prog.data)
            results = run_scenarios(binary)
            verdict["stage"] = "test"
            verdict["scenarios"] = {r["name"]: r["problems"] or "ok" for r in results}
            if all(r["ok"] for r in results):
                verdict["ok"] = True
                (run / f"round{rnd}.verdict.json").write_text(json.dumps(verdict, indent=1))
                shutil.copy(binary, OUT / "edit")
                (OUT / "edit.hex").write_text(src if src.endswith("\n") else src + "\n")
                digest = hashlib.sha256((OUT / "edit").read_bytes()).hexdigest()
                print(f"round {rnd}: ALL {len(results)} SCENARIOS PASS; edit sha256 {digest}")
                return
            failure = failure_message(results) + LISTING + hexpath.program_listing("aarch64", prog)
        verdict["failure"] = failure
        (run / f"round{rnd}.verdict.json").write_text(json.dumps(verdict, indent=1, default=str))
        print(f"round {rnd}: {verdict['stage']} failed: {failure.splitlines()[0][:200]}")
        previous = (src, failure)
    print(f"no passing editor in {args.rounds} rounds")


if __name__ == "__main__":
    main()
