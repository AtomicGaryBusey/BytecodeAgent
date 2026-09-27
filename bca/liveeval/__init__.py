"""Live-model end-to-end evaluation: a model writes machine code through the three pipelines
(streamed assembly, structured JSON, QBE IR) and the differential harness scores it.

    python -m bca.liveeval run --run-dir eval/runs/<stamp> --max-calls N [--tasks round1|round2|all|names]
                               [--paths ...] [--isas ...] [--dry-run]
    python -m bca.liveeval summarize eval/runs/<stamp>
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
