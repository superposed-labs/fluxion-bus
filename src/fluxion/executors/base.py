from __future__ import annotations

from collections.abc import Callable, Iterable
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


def supports_native_images(executor: object) -> bool:
    """Whether an executor can attach ``Task.image_attachments`` natively.

    Unknown third-party executors safely fall back to workspace file paths.
    """
    check = getattr(executor, "supports_native_images", None)
    return bool(check()) if callable(check) else False


def native_image_media_types(executor: object) -> frozenset[str] | None:
    """Native image media types accepted by an executor.

    ``None`` preserves compatibility with third-party executors that only
    expose the older boolean capability and therefore claim all validated
    image types. An empty set means no native image interface.
    """
    declared = getattr(executor, "native_image_media_types", None)
    if callable(declared):
        values = declared()
        if isinstance(values, str):
            return frozenset({values.lower()})
        if isinstance(values, Iterable):
            return frozenset(str(value).lower() for value in values)
        return frozenset()
    return None if supports_native_images(executor) else frozenset()


def accepts_native_image(executor: object, media_type: str) -> bool:
    """Whether ``media_type`` should be sent through the native image API."""
    accepted = native_image_media_types(executor)
    return accepted is None or media_type.lower() in accepted
