# Status and review of this run (local:qwen3.6:35b-a3b-q8_0, thinking off and on)

**Scope: round1 + round2 only (110 cells per subject), by user decision 2026-10-01.** The run was launched on all four
function batteries; after round1 it was stopped and resumed with `--tasks round1,round2` to free the GPU once it was
clear the local models score in single digits. The long and adversarial batteries and the app battery were not run.
Complete for that scope.

Subjects (Ollama 0.30.10 on bca-ollama2, :11436; Q8_0, digest 0218f872e86b; num_ctx 65536; Modelfile sampling):

- `local:qwen3.6:35b-a3b-q8_0@nothink` (Ollama `think: false`): 4/110 first attempt, 15/110 within four.
- `local:qwen3.6:35b-a3b-q8_0@think` (`think: true`): 9/110 first attempt, 15/110 within four.

## False-reject review (2026-10-01)

All 790 failed attempts were replayed through the current pipeline (recorded response text, current stream / encoder
/ QBE / checker code, which includes c1c1df7f, 79a275c1 and d7df9dda): no verdict changes, so no ADJ. The one
`crosscheck` reject (`sum8.stream.x86_64` attempt 1, `lea r9, [r13 - r13]`) is refused by GNU as too ("invalid use of
register"). Everything else is genuine: invalid QBE (144 compile failures with thinking off), stream lines GNU as also
rejects, structured schema misuse and malformed JSON (format), wrong results, ABI and runtime faults.

- **Thinking off** never returned reasoning (every call carries the note `think=false`; the adapter fails closed if
  reasoning comes back, and no call did). Two attempts timed out with partial text.
- **Thinking on**: 376 of 405 scored attempts hit the 180 s function limit, 374 of them before any answer text --
  the model was still reasoning. Calls that finished reasoned for about 12-14 K characters (recorded in the notes,
  never scored). Same limit as every subject.

Recorded wall times include waits for the shared GPU (calls to one server are serialised; the timeout counts only the
call's own model time), so latencies are not comparable with the vendor runs.
