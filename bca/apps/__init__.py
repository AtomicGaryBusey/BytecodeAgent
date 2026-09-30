"""The app battery: whole static programs (``_start``, raw system calls, no libc).

Each :class:`AppTask` carries a precise, ISA-neutral specification (the prompt builder adds the ISA and
system-call facts), a pure-Python reference that defines the truth, and a deterministic test generator.
"""

from bca.apps.tasks import APP_TASKS, AppTask, AppTest, task_by_name

__all__ = ["APP_TASKS", "AppTask", "AppTest", "task_by_name"]
