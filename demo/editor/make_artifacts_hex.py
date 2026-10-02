"""Regenerate every phase of the raw-machine-code editor from one recorded round, for examination, and check
that it reproduces the committed binary byte for byte:

    spec -> prompt -> model output (hex) -> parsed bytes -> ELF container (no assembler, no linker) -> tests

    PYTHONPATH=. .venv/bin/python -m demo.editor.make_artifacts_hex demo/editor-hex/runs/<ts> <round>
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from bca.liveeval import hexpath
from bca.liveeval.apps import escape
from bca.liveeval.pipelines import strip_fences
from bca.liveeval.subject import SYSTEM_PROMPT
from demo.editor.make_artifacts import SECRET_RE, write
from demo.editor.prompt_hex import WORK_AREA
from demo.editor.run_astra import describe_keys
from demo.editor.run_astra_hex import OUT as HEXDIR
from demo.editor.sandbox import docker_command, run_scenarios
from demo.editor.scenarios import COLS, ROWS, SCENARIOS

SPEC = Path(__file__).parent / "SPEC.md"
OUT = HEXDIR / "artifacts"


def main(run: Path, rnd: int) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    verdict = json.loads((run / f"round{rnd}.verdict.json").read_text())
    assert verdict["ok"], "only a passing round is captured"

    # 00 -- the specification and the exact prompt
    d = OUT / "00-spec"
    write(d / "SPEC.md", SPEC.read_text())
    shutil.copy(run / f"round{rnd}.prompt.txt", d / "prompt.txt")
    write(d / "system-prompt.txt", SYSTEM_PROMPT + "\n")

    # 01 -- the model's output exactly as returned, and the CLI's event stream
    d = OUT / "01-model-output"
    response = (run / f"round{rnd}.response.txt").read_text()
    write(d / "response.txt", response)
    raw = (run / f"round{rnd}.raw.jsonl").read_text()
    hits = SECRET_RE.findall(raw)
    assert not hits, f"possible secrets in the raw stream: {hits[:3]}"
    write(d / "raw.jsonl", raw)
    write(d / "usage.json", json.dumps({
        "model": verdict["model"], "wall_s": verdict["wall_s"], "usage": verdict["usage"],
        "response_chars": len(response),
        "reasoning_text": "none returned: the Codex CLI reports only the count usage.reasoning_output_tokens",
    }, indent=1) + "\n")

    # 02 -- the machine code as the model wrote it, and the bytes it parses to (nothing re-encoded)
    d = OUT / "02-machine-code"
    src = strip_fences(response)
    src = src if src.endswith("\n") else src + "\n"
    write(d / "edit.hex", src)
    write(d / "strip.diff", "".join(difflib.unified_diff(response.splitlines(True), src.splitlines(True),
                                                         "response.txt", "edit.hex")))
    prog = hexpath.parse_program("aarch64", src)
    hexpath.check("aarch64", prog.lines, hexpath.HEX_TEXT_BASE)
    write(d / "code.bin", prog.text)
    write(d / "data.bin", prog.data)
    write(d / "listing.txt",
          "Every instruction word as the model wrote it, at its address, with capstone's disassembly (for reading "
          "only; the bytes are executed exactly as written)\n\n"
          + hexpath.program_listing("aarch64", prog, limit=10**6) + "\n\nData bytes at 0x500000:\n"
          + subprocess.run(["xxd", "-o", str(hexpath.HEX_DATA_BASE)], input=prog.data, capture_output=True).stdout.decode())

    # 03 -- the executable: the same bytes in an ELF container (bca.liveeval.hexpath.write_elf)
    d = OUT / "03-executable"
    exe = hexpath.write_elf(prog, WORK_AREA)
    committed = (HEXDIR / "edit").read_bytes()
    assert exe == committed, "the rebuilt executable differs from the committed one"
    assert exe[0x1000:0x1000 + len(prog.text)] == prog.text and exe[0x2000:0x2000 + len(prog.data)] == prog.data
    digest = hashlib.sha256(exe).hexdigest()
    write(d / "edit", exe)
    (d / "edit").chmod(0o755)
    write(d / "readelf-lh.txt", subprocess.run(["readelf", "-lh", "-W", str(d / "edit")], capture_output=True,
                                               text=True).stdout)
    write(d / "hexdump.txt", subprocess.run(["xxd", str(d / "edit")], capture_output=True, text=True).stdout)
    write(d / "sha256.txt", f"{digest}  edit\n{len(exe)} bytes; {len(prog.text)} code bytes "
          f"({len(prog.lines)} instructions) at 0x400000, {len(prog.data)} data bytes at 0x500000, a "
          f"{WORK_AREA >> 20} MiB zeroed area at 0x600000\n")
    write(d / "how-it-was-built.txt",
          "No assembler, no linker, no compiler. bca.liveeval.hexpath.parse_program read the model's hex into "
          "bytes; hexpath.check only decoded them (capstone) and refused instructions outside ARMv8.0-A; "
          "hexpath.write_elf wrote an ELF header and program headers (PT_LOAD r-x at 0x400000 with the code, "
          "PT_LOAD rw- at 0x500000 with the data, PT_LOAD rw- at 0x600000 with no file bytes, PT_GNU_STACK) "
          "around the bytes, unchanged. Rebuild: PYTHONPATH=. .venv/bin/python -m demo.editor.make_artifacts_hex "
          f"{run} {rnd}\n")

    # 04 -- the sandbox tests
    d = OUT / "04-tests"
    results = run_scenarios(HEXDIR / "edit")
    write(d / "docker-command.txt", " ".join(docker_command("<scratch dir with edit, driver.py, scenarios.json>"))
          + f"\n\n# terminal size set on the pseudo-terminal: {ROWS} rows x {COLS} columns\n")
    by_name = {s["name"]: s for s in SCENARIOS}
    log = [f"Sandbox test log: {sum(r['ok'] for r in results)} of {len(results)} scenarios pass "
           f"(binary sha256 {digest}).\n"]
    for r in results:
        s = by_name[r["name"]]
        log.append(f"== {r['name']}: {'PASS' if r['ok'] else 'FAIL'} -- {s['doc']}")
        log.append("files before: " + ("; ".join(f"{k} = {escape(v.encode('latin-1'))}" for k, v in s["files"].items())
                                       or "none"))
        log.append(f"command: edit {s['arg']}")
        log.append(f"keys: {describe_keys(s['keys'])}")
        for name, want in s["expect"].items():
            got = r["files"].get(name)
            log.append(f"expected {name}: {escape(want.encode('latin-1'))}")
            log.append(f"actual   {name}: " + ("(missing)" if got is None else escape(got.encode('latin-1'))))
        log.append(f"exit status {r['status']}; timed out: {r['timed_out']}; terminal restored: {r['restored']}")
        log.append(f"screen bytes: {len(r['screen'])} in total; last 300: {escape(r['screen'][-300:])}")
        log.append("problems: " + ("none" if r["ok"] else "; ".join(r["problems"])) + "\n")
        write(d / "screens" / f"{r['name']}.bin", r["screen"])
    write(d / "test-log.txt", "\n".join(log))
    assert all(r["ok"] for r in results), "a scenario failed"
    print(f"artifacts written to {OUT}: sha256 {digest}, {len(exe)} bytes, {len(prog.lines)} instructions")


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
