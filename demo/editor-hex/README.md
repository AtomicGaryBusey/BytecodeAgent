# `edit`, written by a model directly as AArch64 machine code

`edit` is the same kilo-style terminal text editor as [`../editor/`](../editor/README.md), from the same
specification and the same tests, but this time **Codex GPT-6 Astra wrote every byte of the program itself**:
372 lines of hexadecimal, 354 AArch64 instruction words plus 233 bytes of strings and tables.

**There is no assembler, no linker and no compiler anywhere in this program's making.** The harness parsed
Astra's hex into bytes, checked that each word decodes as an ARMv8.0-A instruction (reading only, nothing is
re-encoded), and copied the bytes unchanged into an ELF container: an ELF header and four program headers
(code at 0x400000, data at 0x500000, a zeroed work area at 0x600000, a non-executable stack). Every jump
distance, every address and every immediate is Astra's own arithmetic; there are no labels.

| | |
|---|---|
| Binary | `edit`, a static AArch64 ELF of 12,288 bytes (mostly page padding), sha256 `abe6de7b75b5a20cba87328e9b8644248ec60d15c0d5a66022a673f85675dcb4` |
| Model's output | `edit.hex`, exactly as returned (3,897 bytes of text; nothing was stripped) |
| Code | 1,416 bytes = 354 instruction words at 0x400000; 233 data bytes at 0x500000 |
| System calls | `openat`, `close`, `read`, `write`, `ioctl`, `exit_group` only |
| Tests | all 12 sandboxed keystroke scenarios pass (`artifacts/04-tests/test-log.txt`) |
| Rounds | 4 of 6 allowed (`runs/20261001-175331/`), zero tools, the Codex CLI's default effort |

## How it got there

Each failed round sent the first failing scenario back, plus a capstone disassembly of Astra's own bytes so it
could see what its words decode to.

1. **Round 1:** the editor ran and saved files, but text went in at the wrong place (cursor bookkeeping):
   8 of 12 scenarios failed.
2. **Round 2:** the insert position was fixed; some edits now left the program looping (5 hangs).
3. **Round 3:** 10 scenarios died on SIGILL: hand-computed branch offsets pointing at the wrong place
   (several backward branches that should have gone forward).
4. **Round 4:** Astra corrected the branch words (e.g. `17ffff44` -> `14000040`, `b 0x400...` back to forward
   targets), a `tbnz` offset and a few constants; all 12 scenarios pass.

## Run it

```
./demo/editor-hex/edit notes.txt
```

Arrows, Home/End and PgUp/PgDn move; type to insert; Enter splits a line; Backspace/Delete join lines; Ctrl-S
saves; Ctrl-Q quits (twice when there are unsaved changes). It is model-written native code: read
`artifacts/02-machine-code/listing.txt` first, or run it the way the tests do, in a throwaway container
(`artifacts/04-tests/docker-command.txt`).

## Every phase, for examination (`artifacts/`)

| Directory | Contents |
|---|---|
| `00-spec/` | `SPEC.md`, the exact prompt (`prompt.txt`), the system prompt |
| `01-model-output/` | Astra's response exactly as returned, the CLI's event stream (`raw.jsonl`, checked for secrets), token usage (the CLI returns no reasoning text, only a count) |
| `02-machine-code/` | `edit.hex` as parsed (`strip.diff`: empty, the answer was the program as is), `code.bin` and `data.bin` (the bytes), `listing.txt` (every word at its address with capstone's disassembly, and a hex dump of the data) |
| `03-executable/` | `edit`, `readelf -lh`, a full hex dump, sha256 and sizes, `how-it-was-built.txt` |
| `04-tests/` | the sandbox test log (keys, expected and actual files, exit status, terminal restored) and screen captures |

`PYTHONPATH=. .venv/bin/python -m demo.editor.make_artifacts_hex demo/editor-hex/runs/20261001-175331 4`
regenerates all of it from the recorded round and checks that the rebuilt `edit` is byte-identical to the
committed one. The code that ran the rounds is `demo/editor/run_astra_hex.py` (prompt: `prompt_hex.py`; the
layout and the ELF writer: `bca/liveeval/hexpath.py`).
