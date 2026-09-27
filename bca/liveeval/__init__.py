"""Live-model end-to-end evaluation: a model writes machine code through the three pipelines
(streamed assembly, structured JSON, QBE IR) and the differential harness scores it.

    python -m bca.liveeval run --run-dir eval/runs/<stamp> [--tasks ...] [--paths ...] [--isas ...]
    python -m bca.liveeval summarize eval/runs/<stamp>
"""

from bca.liveeval.pipelines import Attempt, Verdict, attempt, check
from bca.liveeval.prompts import ISAS, PATHS, build_prompt
from bca.liveeval.runner import Budget, run_cell, run_matrix
from bca.liveeval.subject import ClaudeSubject, FakeSubject, Response
from bca.liveeval.tasks import BY_NAME, TASKS, Task

__all__ = [
    "Attempt", "BY_NAME", "Budget", "ClaudeSubject", "FakeSubject", "ISAS", "PATHS", "Response",
    "TASKS", "Task", "Verdict", "attempt", "build_prompt", "check", "run_cell", "run_matrix",
]
