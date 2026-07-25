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
        # The agent's working — its thinking, and the tools it reached for.
        # Kept apart from `stream_output` because callers render the two in
        # different places; merging them would put scratch work in the answer.
        # An executor with no visibility into either simply never calls it.
        stream_reasoning: Callable[[str], None] | None = None,
    ) -> ExecutionResult: ...


def enforces_read_only(executor: object) -> bool:
    """Whether this executor can honor ``Task.metadata["read_only"]``.

    Read via ``getattr`` rather than added to the Protocol above so that an
    executor written elsewhere keeps working — and, more importantly, so the
    default is the safe answer. An executor that has never heard of read-only
    reports False and gets refused the job, instead of silently accepting a
    promise it cannot keep.
    """
    check = getattr(executor, "enforces_read_only", None)
    return bool(check()) if callable(check) else False
