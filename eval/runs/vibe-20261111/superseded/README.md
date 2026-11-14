Mistral Large 4 cells superseded on 2026-11-11 and re-run from scratch. The adapter had treated two of the model's own
outcomes as transient infrastructure errors and retried them, which gave the model extra draws: Vibe's
'Stopped: turn_limit' (the 4-turn budget spent without a final answer; now a scored timeout) and a completed
search_tool_functions catalog lookup (now a recorded harness lookup). Every cell with any infrastructure entry was
moved here, attempts, results and infra records included.
