from __future__ import annotations

from fluxion.core.models.task import Task
from fluxion.executors.base import Executor


class TaskRouter:
    def __init__(self, executors: dict[str, Executor], default_executor: str) -> None:
        self._executors = executors
        self._default_executor = default_executor

    def select_executor_with_name(self, task: Task) -> tuple[str, Executor]:
        preferred = str(task.metadata.get("executor") or self._default_executor).strip()
        if preferred in self._executors and self._executors[preferred].supports(task):
            return preferred, self._executors[preferred]
        for name, executor in self._executors.items():
            if executor.supports(task):
                return name, executor
        raise RuntimeError("No executor can handle this task")

    def select_executor(self, task: Task) -> Executor:
        return self.select_executor_with_name(task)[1]
