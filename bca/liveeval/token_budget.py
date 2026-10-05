"""Token budgets: the per-call limit for local subjects in token-budget mode (`vllm:<model>@budget`).

The vendor models run under per-battery wall-clock limits (registry.BATTERY_TIMEOUTS). Served at 100-300
tokens/s, those limits never constrain how long they reason; on a single desktop GPU at ~30 tokens/s the
same limits would cut a local model off long before it had used as many tokens as the vendor models
routinely do. In token-budget mode the limit is instead the number of output tokens (reasoning included),
and each budget is the largest output any vendor model produced for one call in that battery (measured
over every committed vendor attempt on 2026-10-05), rounded up:

    battery      most any vendor model used   budget
    round1       27,661                        32,768
    round2       39,297                        49,152
    long         44,729                        49,152
    adversarial  22,159                        24,576
    apps         76,794                        81,920

So a local model gets at least the thinking room the most verbose vendor model ever used. A call that
reaches its budget is a timeout, recorded and scored exactly like a wall-clock timeout (its error text
is the same "timeout", so repair prompts stay identical); the budget detail goes into the call's notes.
The wall clock still has a generous ceiling (TOKEN_BUDGET_MIN_RATE) against a hung server.
"""

from __future__ import annotations

import contextvars

BATTERY_TOKEN_BUDGETS = {"round1": 32768, "round2": 49152, "long": 49152, "adversarial": 24576, "apps": 81920}
DEFAULT_TOKEN_BUDGET = 32768
TOKEN_BUDGET_MIN_RATE = 8.0  # tokens/s: the wall-clock ceiling is budget / this rate, plus slack
TOKEN_BUDGET_SLACK_S = 300.0

# The battery of the cell being run (set by runner.run_cell for the calls it makes).
CURRENT_BATTERY: contextvars.ContextVar[str | None] = contextvars.ContextVar("bca_current_battery", default=None)


def token_budget(battery: str | None) -> int:
    return BATTERY_TOKEN_BUDGETS.get(battery or "", DEFAULT_TOKEN_BUDGET)


def wall_ceiling(budget: int) -> float:
    return budget / TOKEN_BUDGET_MIN_RATE + TOKEN_BUDGET_SLACK_S
