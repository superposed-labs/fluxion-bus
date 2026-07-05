from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task


@runtime_checkable
class Executor(Protocol):
    def name(self) -> str: ...

    def supports(self, task: Task) -> bool: ...

    def execute(
        self,
        task: Task,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: Callable[[str], None] | None = None,
    ) -> ExecutionResult: ...
