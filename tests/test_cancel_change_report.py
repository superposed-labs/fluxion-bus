"""What a canceled run reports about the files it already changed.

Cancel used to replace the executor's result with an empty shell, so a run that
had written twenty files was reported with `changed_files: []` and nothing for
revert_subagent_run to undo. The run's own record is the only place that
information exists, so losing it there loses it for good.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fluxion.core.engine import GatewayCore
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage


class _Adapter:
    def __init__(self) -> None:
        self.results: list[ExecutionResult] = []
        self.statuses: list[tuple[str, str]] = []
        self.done = threading.Event()

    def send_status(self, task_id, status, context, detail=None) -> None:
        self.statuses.append((status, detail or ""))

    def send_result(self, task_id, result, context) -> None:
        self.results.append(result)
        self.done.set()

    def send_typing(self, context) -> None:
        pass

    def send_output_delta(self, task_id, text, context) -> None:
        pass


class _CancelingExecutor:
    """Writes a file, asks for its own cancellation, then returns like a
    terminated CLI does: unsuccessful, but carrying what it got done."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.gateway: GatewayCore | None = None

    def name(self) -> str:
        return "codex"

    def supports(self, task) -> bool:
        return True

    def enforces_read_only(self) -> bool:
        return False

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        (self.workspace / "new.txt").write_text("written before cancel", encoding="utf-8")
        assert self.gateway is not None
        self.gateway.cancel_task(task.id)
        return ExecutionResult(
            success=False,
            summary="Task canceled by user request.",
            stdout="partial work",
            stderr="",
            exit_code=130,
            log_file="/logs/task-x.log",
            executor_session_id="session-abc",
            file_operations=[
                {"op": "create", "path": "new.txt", "content": "written before cancel"}
            ],
        )


@pytest.fixture
def cleanup():
    gateways: list[GatewayCore] = []
    yield gateways
    for gateway in gateways:
        gateway.stop()


def _run_until_canceled(tmp_path: Path, cleanup) -> tuple[ExecutionResult, JsonlStorage]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _CancelingExecutor(workspace)
    storage = JsonlStorage(tmp_path / "data")
    gateway = GatewayCore(
        router=TaskRouter(executors={"codex": executor}, default_executor="codex"),
        storage=storage,
        sessions=SessionManager(storage=storage),
        artifact_max_files=10,
        worker_count=1,
        max_pending_per_user=10,
        max_retries=0,
        retry_backoff_sec=1,
        typing_heartbeat_sec=0,
        running_update_sec=0,
        reconcile_on_start=False,
    )
    cleanup.append(gateway)
    executor.gateway = gateway
    gateway.start()

    adapter = _Adapter()
    task = Task.create(
        channel="local",
        user_id="local",
        text="write a file",
        workspace=workspace,
        metadata={"executor": "codex", "subagent": {"mode": "workspace-write", "agent": "codex"}},
    )
    accepted, _ = gateway.submit_task(task=task, channel_adapter=adapter, channel_context={})
    assert accepted
    assert adapter.done.wait(timeout=30)
    return adapter.results[-1], storage


def test_canceled_run_reports_the_files_it_already_changed(tmp_path, cleanup) -> None:
    result, _storage = _run_until_canceled(tmp_path, cleanup)

    assert result.success is False
    assert result.exit_code == 130
    assert result.summary == "Task canceled by request."
    # The point of the fix: the work done before the cancel is still reported.
    assert result.changed_files == ["new.txt"]
    assert result.change_set_file  # revert_subagent_run has something to undo


def test_canceled_run_keeps_the_executor_identity(tmp_path, cleanup) -> None:
    result, _storage = _run_until_canceled(tmp_path, cleanup)

    # Without these the run cannot be traced back to its executor session or its
    # log, and for Antigravity the session id is what changed_files is read from.
    assert result.executor_session_id == "session-abc"
    assert result.log_file == "/logs/task-x.log"
    assert result.stdout == "partial work"
    # Nothing is left waiting on a process that is being torn down.
    assert result.pending_finalization is False


def test_canceled_run_record_carries_the_change_report(tmp_path, cleanup) -> None:
    _result, storage = _run_until_canceled(tmp_path, cleanup)

    events = storage.list_recent_task_events(limit=50)
    canceled = [event for event in events if event.get("status") == "CANCELED"]
    assert canceled, "no CANCELED event was recorded"
    recorded = canceled[-1]["result"]
    # The stored record is what get_task_result reads; an empty changed_files
    # there is what made a canceled run look like it had done nothing.
    assert recorded["changed_files"] == ["new.txt"]
    assert recorded["executor_session_id"] == "session-abc"


def test_cancel_before_execution_reports_no_changes(tmp_path, cleanup) -> None:
    """A run canceled while queued never touched the workspace, so it must not
    claim credit for whatever state the workspace happens to be in."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pre-existing.txt").write_text("not this run's doing", encoding="utf-8")
    storage = JsonlStorage(tmp_path / "data")
    gateway = GatewayCore(
        router=TaskRouter(
            executors={"codex": _CancelingExecutor(workspace)}, default_executor="codex"
        ),
        storage=storage,
        sessions=SessionManager(storage=storage),
        artifact_max_files=10,
        worker_count=1,
        max_pending_per_user=10,
        max_retries=0,
        retry_backoff_sec=1,
        typing_heartbeat_sec=0,
        running_update_sec=0,
        reconcile_on_start=False,
    )
    cleanup.append(gateway)
    # Never started: submit without start(), so the task sits in the queue.
    adapter = _Adapter()
    task = Task.create(
        channel="local",
        user_id="local",
        text="write a file",
        workspace=workspace,
        metadata={"executor": "codex", "subagent": {"mode": "workspace-write", "agent": "codex"}},
    )
    accepted, _ = gateway.submit_task(task=task, channel_adapter=adapter, channel_context={})
    assert accepted

    ok, message = gateway.cancel_task(task.id)

    assert ok, message
    assert adapter.results[-1].changed_files == []
    assert adapter.results[-1].change_set_file == ""
