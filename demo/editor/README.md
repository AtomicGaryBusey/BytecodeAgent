# `edit`: a terminal text editor written by a model in AArch64 machine code

`edit` is a small full-screen text editor (kilo-style) for Linux on AArch64. All of its code was written by
**Codex GPT-6 Astra** as one assembly file, `edit.s`, from the specification in [`SPEC.md`](SPEC.md).
It was produced on the first attempt (round 1 of up to 6) and passed every test scenario in the sandbox.

- **No compiler, no libc, no runtime:** GNU as turned Astra's assembly into machine code and `ld` linked it
  into a static executable.
- **What it uses:** its own `_start`, raw system calls, and a `.bss` buffer.

| | |
|---|---|
| Binary | `edit`, static ELF 64-bit AArch64, **5,128 bytes**, no dynamic section |
| Code | **477 instructions** in `.text` (598 lines of assembly, 9,615 bytes of source) |
| sha256 | `b7bc9d41ec59cc423782a1cc5d4db271af7f2af3de2c74f310ee8eca9c22dccc` |
| System calls | `openat`, `close`, `read`, `write`, `ioctl`, `exit_group`: 12 `svc #0` sites, all visible in `edit.s` |
| Produced | 2026-10-01; one call (143 s; 2,884 input / 4,643 output tokens) through the eval's isolated Codex adapter (zero tools, CLI default effort) |

## The pipeline

**spec → model-written AArch64 assembly → GNU as object → `ld` static ELF**

There's no C, no Brainfuck, no other intermediate language, and no compiler anywhere:
- **The model's part:** Astra wrote the assembly text directly.
- **GNU as:** it encoded that text instruction by instruction into machine code.
- **`ld`:** it only placed the sections at their addresses and resolved label references (no libraries, no
  start files).

Every byte of `.text` comes from a line of `edit.s`. `artifacts/03-object/edit.lst` shows which line
produced which bytes.

## Artifacts: every phase, for examination

[`artifacts/`](artifacts/) is regenerated from the recorded run by `make_artifacts.py`, with the same
toolchain. The script checks that the relinked executable is byte-for-byte identical to the committed
`edit` (sha256 above).

| Directory | Contents |
|---|---|
| `00-spec/` | `SPEC.md`; `prompt.txt`, the exact prompt sent; `system-prompt.txt`, the harness system prompt |
| `01-model-output/` | `response.txt`, Astra's answer exactly as returned; `raw.jsonl`, the Codex CLI's recorded event stream (checked for secrets: none; it names the call's private temporary home); `usage.json`, token counts |
| `02-assembly/` | `edit.s`, exactly as assembled; `strip.diff`, the difference from the raw response (none: the answer was the source as is, with no Markdown fences or prose) |
| `03-object/` | `edit.o` from GNU as; `edit.lst`, the assembler listing mapping each source line to its address and bytes; `readelf-a.txt`; `objdump-dr.txt`, disassembly with relocations; `nm.txt` |
| `04-executable/` | `edit`, the static ELF; `readelf-a.txt`; `objdump-d.txt`; `edit.map`, the linker map; `edit.xxd`, a full hex dump; `sha256-size.txt`; `commands.txt`, the exact `as`/`ld` command lines and binutils 2.42 versions |
| `05-tests/` | `test-log.txt`, each scenario's keystrokes, expected and actual file, exit status, terminal restore, and a screen-bytes excerpt; `screens/*.bin`, the full terminal output per scenario; `docker-command.txt`, the sandbox command |

The model's reasoning isn't available. The Codex CLI returned no reasoning text or summary for this model at
its default effort, only the count: 160 reasoning tokens. `usage.json` records this.

There was **only one round**: the first answer passed, so `runs/20261001-163109/` holds a single
prompt, response and verdict.

## Run it

On an AArch64 Linux machine, such as the DGX Spark this was built on:

```sh
./demo/editor/edit notes.txt
```

**Keys:**
- **Move:** arrow keys, Home/End, PgUp/PgDn.
- **Edit:** type to insert; Enter splits the line; Backspace and Delete remove characters and join lines.
- **Save:** Ctrl-S.
- **Quit:** Ctrl-Q. If there are unsaved changes, press it twice.

The status bar shows the file name, `line:column` and `[modified]`.

**Limits:**
- Files up to the spec's limits: 1 MiB, 65,536 lines, lines up to 4,096 bytes.
- No tabs or UTF-8 handling: control bytes show as `?`.

**Caution:** it is model-written native code. Before running it outside a sandbox, read `edit.s` (it is short).
- The only system calls are the six above.
- The only file it opens is the one named on the command line: read-only to load it, and
  `O_WRONLY|O_CREAT|O_TRUNC` with mode 0644 to save it.

## How it was made and tested

| File | Role |
|---|---|
| `prompt.py` | The prompt: the app battery's output-format rules plus the editor's system calls and the spec |
| `sandbox.py` | Assembles with the eval's GNU as wrapper (no file inclusion, no macros, the prompt's layout rules), links with `ld -static -nostdlib -e _start`, and runs the binary **only** in a throwaway container: `--network none`, read-only root, unprivileged user, `--cap-drop ALL`, 1 CPU / 256 MiB / 64 processes |
| `driver.py` | Runs inside that container. Gives the editor a 24x80 pseudo-terminal, sends each scenario's keystrokes, then checks the saved file, exit status 0, that the terminal settings were restored, and that expected text reached the screen |
| `scenarios.py` | 12 scenarios: new file, editing a middle line, Backspace and Delete line joins, Enter split, arrow wrapping and column clamping, page scrolling through 60 lines, quit with unsaved changes, a cancelled quit warning, a 200-byte line with horizontal scrolling, an empty file, a file without a final newline |
| `test_scenarios.py` | Checks every scenario's expected file against a Python model of the spec |
| `reference/edit_ref.c` | A C implementation of the spec, used only to validate the tests (it passes all 12). It is not the demo |
| `make_artifacts.py` | Regenerates `artifacts/` from a recorded round and checks it reproduces `edit` byte for byte |
| `run_astra.py` | Builds the prompt, calls the model, builds and tests the result, and feeds the first failure back (up to 6 rounds). Every prompt, response and verdict is in `runs/20261001-163109/` |

The binary passed all 12 scenarios in each of 6 separate sandbox runs. It also exits 1 with `usage: edit FILE` when given
no argument, and exits 1 with `edit: not a terminal` when its input is not a terminal.

To rebuild from source (the result should be byte-identical with the same binutils):

```sh
PYTHONPATH=. .venv/bin/python -c "from pathlib import Path; from demo.editor.sandbox import build; \
  build(Path('demo/editor/edit.s').read_text(), Path('/tmp/edit'))"
```

To run the sandbox tests (needs docker and the local `python:3.12-slim` image):

```sh
.venv/bin/python -m pytest -q demo/editor
```
