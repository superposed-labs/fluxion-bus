"""The model a run actually goes out with must be visible and not silently changed.

An agent CLI routes to a quota pool by model, so a run that quietly gets a
different model than requested bills a different subscription.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from fluxion.core.engine import GatewayCore
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage
from fluxion.subagent import SubagentRunHandle


def _handle(**kwargs) -> SubagentRunHandle:
    base = dict(
        run_id="r1",
        task_id="r1",
        agent="antigravity",
        project="",
        workspace="/repo",
        thread="t",
        task_name="",
        parent_path="/root",
        agent_path="/root/t",
        conversation_key="local:/repo:t",
        accepted=True,
        summary="Task accepted.",
        adapter=None,
    )
    base.update(kwargs)
    return SubagentRunHandle(**base)  # type: ignore[arg-type]


def test_the_run_payload_states_which_model_it_will_use() -> None:
    payload = _handle(
        requested_model="gemini-3.6-flash-high", effective_model="gemini-3.6-flash-high"
    ).to_payload()

    assert payload["requested_model"] == "gemini-3.6-flash-high"
    assert payload["effective_model"] == "gemini-3.6-flash-high"
    assert "model_binding_warning" not in payload


def test_no_model_named_is_reported_as_the_executor_default() -> None:
    payload = _handle().to_payload()
    assert payload["effective_model"] == "(executor default)"


def test_resuming_a_conversation_built_on_another_model_is_flagged() -> None:
    payload = _handle(
        requested_model="gemini-3.6-flash-high",
        effective_model="gemini-3.6-flash-high",
        resumed_session_model="claude-opus-4-6-thinking",
    ).to_payload()

    warning = payload["model_binding_warning"]
    assert "claude-opus-4-6-thinking" in warning
    assert "gemini-3.6-flash-high" in warning
    assert "session_policy='new'" in warning


class _Recorder:
    """Captures the model the task carried when the executor was invoked."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.done = threading.Event()

    def name(self) -> str:
        return "antigravity"

    def supports(self, task) -> bool:
        return True

    def enforces_read_only(self) -> bool:
        return False

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.seen.append(str(task.metadata.get("model") or ""))
        self.done.set()
        return ExecutionResult(success=True, summary="ok", stdout="", stderr="", exit_code=0)


class _Sink:
    def send_status(self, *args, **kwargs) -> None:
        pass

    def send_result(self, *args, **kwargs) -> None:
        pass

    def send_typing(self, *args, **kwargs) -> None:
        pass

    def send_output_delta(self, *args, **kwargs) -> None:
        pass


def test_a_conversation_default_never_overrides_an_explicitly_named_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = JsonlStorage(Path(tmp))
        sessions = SessionManager(storage=storage)
        executor = _Recorder()
        gateway = GatewayCore(
            router=TaskRouter(executors={"antigravity": executor}, default_executor="antigravity"),
            storage=storage,
            sessions=sessions,
            artifact_max_files=10,
            worker_count=1,
            max_pending_per_user=5,
            max_retries=0,
            retry_backoff_sec=1,
            typing_heartbeat_sec=0,
            running_update_sec=0,
            reconcile_on_start=False,
        )
        gateway.start()
        try:
            sessions.set_model_override(
                conversation_key="local:/repo:t",
                channel="local",
                user_id="local",
                executor_name="antigravity",
                model="claude-opus-4-6-thinking",
            )
            task = Task.create(
                channel="local",
                user_id="local",
                text="go",
                workspace=Path(tmp),
                metadata={
                    "executor": "antigravity",
                    "conversation_key": "local:/repo:t",
                    "model": "gemini-3.6-flash-high",
                },
            )
            gateway.submit_task(task=task, channel_adapter=_Sink(), channel_context={})
            assert executor.done.wait(timeout=10)

            # The per-run model wins: a sticky conversation default must not
            # reroute an explicit choice into another quota pool.
            assert executor.seen == ["gemini-3.6-flash-high"]
        finally:
            gateway.stop()


def test_a_conversation_default_still_applies_when_no_model_is_named() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = JsonlStorage(Path(tmp))
        sessions = SessionManager(storage=storage)
        executor = _Recorder()
        gateway = GatewayCore(
            router=TaskRouter(executors={"antigravity": executor}, default_executor="antigravity"),
            storage=storage,
            sessions=sessions,
            artifact_max_files=10,
            worker_count=1,
            max_pending_per_user=5,
            max_retries=0,
            retry_backoff_sec=1,
            typing_heartbeat_sec=0,
            running_update_sec=0,
            reconcile_on_start=False,
        )
        gateway.start()
        try:
            sessions.set_model_override(
                conversation_key="local:/repo:t",
                channel="local",
                user_id="local",
                executor_name="antigravity",
                model="claude-opus-4-6-thinking",
            )
            task = Task.create(
                channel="local",
                user_id="local",
                text="go",
                workspace=Path(tmp),
                metadata={
                    "executor": "antigravity",
                    "conversation_key": "local:/repo:t",
                    "model": "",
                },
            )
            gateway.submit_task(task=task, channel_adapter=_Sink(), channel_context={})
            assert executor.done.wait(timeout=10)
            assert executor.seen == ["claude-opus-4-6-thinking"]
        finally:
            gateway.stop()
