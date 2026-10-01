"""Regenerate every phase of the editor's pipeline from one recorded round, for examination, and check that
it reproduces the committed binary byte for byte:

    spec -> prompt -> model output -> assembly -> GNU as object -> ld static ELF -> sandbox tests

    PYTHONPATH=. .venv/bin/python -m demo.editor.make_artifacts demo/editor/runs/<ts> <round>
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bca.image import assemble_program
from bca.liveeval.apps import escape
from bca.liveeval.pipelines import strip_fences
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.stream import _gas_tool
from demo.editor.run_astra import describe_keys
from demo.editor.sandbox import LD, docker_command, run_scenarios
from demo.editor.scenarios import COLS, ROWS, SCENARIOS

HERE = Path(__file__).parent
OUT = HERE / "artifacts"
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|Bearer\s+\S{20,}|"
                       r"gh[pousr]_[A-Za-z0-9]{20,}|access_token|refresh_token|api[_-]?key)", re.I)


def sh(cmd: list[str], **kw) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)
    return r.stdout


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_bytes if isinstance(text, bytes) else path.write_text)(text)


def main(run: Path, rnd: int) -> None:
    gas, objdump, readelf, nm = _gas_tool("aarch64"), "objdump", "readelf", "nm"
    if OUT.exists():
        shutil.rmtree(OUT)
    verdict = json.loads((run / f"round{rnd}.verdict.json").read_text())
    assert verdict["ok"], "only a passing round is captured"

    # 00 -- the specification and the exact prompt
    d = OUT / "00-spec"
    shutil.copy(HERE / "SPEC.md", d / "SPEC.md") if d.mkdir(parents=True) is None else None
    shutil.copy(run / f"round{rnd}.prompt.txt", d / "prompt.txt")
    write(d / "system-prompt.txt", SYSTEM_PROMPT + "\n")
    assert (HERE / "SPEC.md").read_text().strip() in (d / "prompt.txt").read_text()

    # 01 -- the model's output exactly as returned, and the CLI's event stream
    d = OUT / "01-model-output"
    response = (run / f"round{rnd}.response.txt").read_text()
    write(d / "response.txt", response)
    raw = (run / f"round{rnd}.raw.jsonl").read_text()
    hits = SECRET_RE.findall(raw)
    assert not hits, f"possible secrets in the raw stream: {hits[:3]}"
    write(d / "raw.jsonl", raw)
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    reasoning_items = [e for e in events if "reasoning" in json.dumps(e.get("item", {})).lower()]
    write(d / "usage.json", json.dumps({
        "model": verdict["model"], "wall_s": verdict["wall_s"], "usage": verdict["usage"],
        "response_chars": len(response),
        "reasoning_text": "none returned: the Codex CLI's event stream has no reasoning item or summary for this "
                          "model at its default effort; only the count usage.reasoning_output_tokens is reported",
        "reasoning_items_in_stream": len(reasoning_items),
        "events": [e.get("type") or next(iter(e)) for e in events],
    }, indent=1) + "\n")

    # 02 -- the assembly exactly as assembled
    d = OUT / "02-assembly"
    src = strip_fences(response)
    committed = (HERE / "edit.s").read_text()
    assert committed.rstrip("\n") == src.rstrip("\n"), "edit.s differs from the stripped response"
    write(d / "edit.s", committed)
    diff = "".join(difflib.unified_diff(response.splitlines(True), committed.splitlines(True),
                                        "01-model-output/response.txt", "02-assembly/edit.s"))
    write(d / "strip.diff", diff or "(no difference: the response was the assembly source as is; nothing was "
                                    "stripped)\n")

    # 03 -- the object file from GNU as, with a listing
    d = OUT / "03-object"
    d.mkdir(parents=True)
    obj = assemble_program(committed, "aarch64")  # the eval's wrapper: refusals, limits, then GNU as
    with tempfile.TemporaryDirectory() as tmp:
        text = committed + "\n"  # bca.image.assemble_program feeds the source plus a newline on stdin
        sh([gas, "-al=" + str(d / "edit.lst"), "-o", f"{tmp}/edit.o", "--"], input=text, cwd=tmp)
        relisted = Path(f"{tmp}/edit.o").read_bytes()
    assert relisted == obj, "the listing run produced a different object"
    write(d / "edit.o", obj)
    write(d / "readelf-a.txt", sh([readelf, "-aW", "edit.o"], cwd=d))
    write(d / "objdump-dr.txt", sh([objdump, "-dr", "edit.o"], cwd=d))
    write(d / "nm.txt", sh([nm, "-n", "edit.o"], cwd=d))

    # 04 -- the static executable
    d = OUT / "04-executable"
    d.mkdir(parents=True)
    ld_cmd = [LD, "-static", "-nostdlib", "-e", "_start", "-Map=edit.map", "-o", "edit", "../03-object/edit.o"]
    sh(ld_cmd, cwd=d)
    exe = (d / "edit").read_bytes()
    committed_exe = (HERE / "edit").read_bytes()
    assert exe == committed_exe, "the relinked executable differs from demo/editor/edit"
    digest = hashlib.sha256(exe).hexdigest()
    write(d / "readelf-a.txt", sh([readelf, "-aW", "edit"], cwd=d))
    write(d / "objdump-d.txt", sh([objdump, "-d", "edit"], cwd=d))
    write(d / "edit.xxd", sh(["xxd", "edit"], cwd=d))
    insns = sum(1 for line in sh([objdump, "-d", "-j", ".text", "edit"], cwd=d).splitlines()
                if re.match(r"^ +[0-9a-f]+:", line))
    write(d / "sha256-size.txt", f"sha256 {digest}\nsize {len(exe)} bytes\ninstructions in .text {insns}\n"
                                 "identical to demo/editor/edit: yes\n")
    versions = "\n".join(sh([t, "--version"]).splitlines()[0] for t in (gas, LD, objdump, readelf))
    write(d / "commands.txt",
          "# 1. Assemble (bca.image.assemble_program: refuse file inclusion, macros and the prompt's forbidden\n"
          "#    directives, then GNU as on stdin under resource limits; AArch64 gets no header)\n"
          f"{gas} -o edit.o -- < edit.s   # plus -al=edit.lst for the listing in 03-object\n\n"
          "# 2. Link: static, no libc, no start files, entry _start (run in 04-executable/)\n"
          + " ".join(ld_cmd) + "\n\n# Tool versions\n" + versions + "\n")

    # 05 -- the sandbox tests
    d = OUT / "05-tests"
    results = run_scenarios(HERE / "edit")
    write(d / "docker-command.txt", " ".join(docker_command("<scratch dir with edit, driver.py, scenarios.json>"))
          + f"\n\n# terminal size set on the pseudo-terminal: {ROWS} rows x {COLS} columns\n")
    by_name = {s["name"]: s for s in SCENARIOS}
    log = [f"Sandbox test log: {sum(r['ok'] for r in results)} of {len(results)} scenarios pass "
           f"(binary sha256 {digest}).\n"]
    for r in results:
        s = by_name[r["name"]]
        log.append(f"== {r['name']}: {'PASS' if r['ok'] else 'FAIL'} -- {s['doc']}")
        log.append(f"files before: " + ("; ".join(f"{k} = {escape(v.encode('latin-1'))}" for k, v in s["files"].items()) or "none"))
        log.append(f"command: edit {s['arg']}")
        log.append(f"keys: {describe_keys(s['keys'])}")
        for name, want in s["expect"].items():
            got = r["files"].get(name)
            log.append(f"expected {name}: {escape(want.encode('latin-1'))}")
            log.append(f"actual   {name}: " + ("(missing)" if got is None else escape(got.encode('latin-1'))))
        log.append(f"exit status {r['status']}; timed out: {r['timed_out']}; terminal restored: {r['restored']}")
        if s.get("screen"):
            log.append(f"screen must contain: {', '.join(map(repr, s['screen']))}")
        log.append(f"screen bytes: {len(r['screen'])} in total; last 300: {escape(r['screen'][-300:])}")
        log.append("problems: " + ("none" if r["ok"] else "; ".join(r["problems"])) + "\n")
        write(d / "screens" / f"{r['name']}.bin", r["screen"])
    write(d / "test-log.txt", "\n".join(log))
    assert all(r["ok"] for r in results), "a scenario failed"
    print(f"artifacts written to {OUT}: sha256 {digest}, {len(exe)} bytes, {insns} instructions")


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
