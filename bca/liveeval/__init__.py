"""Live-model end-to-end evaluation: a model writes machine code through the three pipelines
(streamed assembly, structured JSON, QBE IR) and the differential harness scores it.

    python -m bca.liveeval run --run-dir eval/runs/<stamp> --max-calls N
        [--tasks round1|round2|long|adversarial|all|names] [--paths ...] [--isas ...]
        [--model opus | --subjects haiku,sonnet,opus] [--variant baseline,<name>...]
        [--effort low|medium|high|xhigh|max] [--timeout S] [--max-infra-errors N] [--infra-backoff S]
        [--dry-run [--estimate-from eval/runs/<earlier> ...]]
    python -m bca.liveeval summarize eval/runs/<stamp>
    python -m bca.liveeval compare eval/runs/<a> eval/runs/<b> ...
    python -m bca.liveeval probe opus sonnet haiku          # one tiny call each

Subjects are Claude models (`claude -p`: an alias such as opus/sonnet/haiku/fable or a model id) or
another vendor's coding CLI as `<vendor>:<model>`: codex:gpt-6-luna, kimi:kimi-code/k3,
agy:gemini-3.8-flash-high, grok:grok-4.7 (bca.liveeval.vendors). Every subject runs tool-less with the
same prompts; see reports/cross-vendor-methodology.md. A subject whose plan quota is spent stops its
own cells only; the rest of the run continues.

Without --model/--subjects and with only the baseline variant a run dir has the flat round-1
layout; otherwise it is <run-dir>/<subject>/<variant>/ (runner.run_plan). --max-calls is one cap
for the whole run dir (calls.log at its top), across subjects and variants and across processes;
one live run per run dir (run.lock). The per-call timeout defaults to 180 s (long battery: 400 s).
Infrastructure errors (process failure, overload, auth, unknown model) are retried with back-off,
never scored, and abort the run (exit 3) after --max-infra-errors in a row; re-running resumes,
also inside a partially attempted cell.
"""

from bca.liveeval.pipelines import Attempt, Verdict, attempt, check
from bca.liveeval.prompts import ISAS, PATHS, build_prompt
from bca.liveeval.runner import Budget, run_cell, run_matrix
from bca.liveeval.subject import ClaudeSubject, FakeSubject, Response
from bca.liveeval.tasks import BY_NAME, TASKS, Task
from bca.liveeval.tasks2 import ALL_BY_NAME, ALL_TASKS, ROUND2, feasible_cells, select_tasks

__all__ = [
    "ALL_BY_NAME", "ALL_TASKS", "ROUND2", "feasible_cells", "select_tasks",
    "Attempt", "BY_NAME", "Budget", "ClaudeSubject", "FakeSubject", "ISAS", "PATHS", "Response",
    "TASKS", "Task", "Verdict", "attempt", "build_prompt", "check", "run_cell", "run_matrix",
]
