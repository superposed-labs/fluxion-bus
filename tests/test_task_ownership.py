"""Task ownership, orphan reconciliation and workspace-lock behaviour."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fluxion.core.engine import GatewayCore
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.runtime_registry import (
    INSTANCE_ID,
    INTERRUPTED_STATUS,
    RuntimeRegistry,
    current_owner,
    list_instances,
    owner_is_alive,
    reconcile_orphaned_tasks,
)
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage


class _Adapter:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []
        self.results: list[ExecutionResult] = []
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


class _BlockingExecutor:
    """Executor that holds the workspace until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()

    def name(self) -> str:
        return "codex"

    def supports(self, task) -> bool:
        return True

    def enforces_read_only(self) -> bool:
        return False

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.entered.set()
        self.release.wait(timeout=30)
        return ExecutionResult(success=True, summary="done", stdout="", stderr="", exit_code=0)


def _write_task_event(storage: JsonlStorage, task_id: str, status: str, **extra) -> None:
    storage.append_task_event(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "task_id": task_id,
            "status": status,
            "task": {"id": task_id, "channel": "local", "user_id": "local", "metadata": {}},
            **extra,
        }
    )


def _make_task(workspace: Path, *, mode: str = "workspace-write") -> Task:
    return Task.create(
        channel="local",
        user_id="local",
        text="do a thing",
        workspace=workspace,
        metadata={"executor": "codex", "subagent": {"mode": mode, "agent": "codex"}},
    )


@pytest.fixture
def cleanup():
    """Stops each gateway's heartbeat thread before the temp dir is removed."""
    gateways: list[GatewayCore] = []
    yield gateways
    for gateway in gateways:
        gateway.stop()


def _gateway(tmp: Path, executor, **kwargs) -> tuple[GatewayCore, JsonlStorage]:
    storage = JsonlStorage(tmp / "data")
    gateway = GatewayCore(
        router=TaskRouter(executors={"codex": executor}, default_executor="codex"),
        storage=storage,
        sessions=SessionManager(storage=storage),
        artifact_max_files=10,
        worker_count=kwargs.pop("worker_count", 2),
        max_pending_per_user=10,
        max_retries=0,
        retry_backoff_sec=1,
        typing_heartbeat_sec=0,
        running_update_sec=0,
        reconcile_on_start=False,
        **kwargs,
    )
    return gateway, storage


def test_lock_pruning_never_removes_a_lock_someone_holds(tmp_path) -> None:
    import fcntl

    from fluxion.workspace.lock_state import list_workspace_locks, prune_released_locks

    held = tmp_path / "held.lock"
    released = tmp_path / "released.lock"
    held.write_text('{"task_id": "live-run", "pid": 1, "workspace": "/repo"}', encoding="utf-8")
    released.write_text('{"task_id": "old-run", "pid": 2}', encoding="utf-8")

    handle = open(held, "a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        view = list_workspace_locks(tmp_path)
        # Only the live one is reported; the stale file is a count, not noise.
        assert [row["holder"]["task_id"] for row in view["held"]] == ["live-run"]
        assert view["released_lock_files"] == 1

        assert prune_released_locks(tmp_path) == 1
        assert held.exists()
        assert not released.exists()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def test_orphaned_running_task_is_reconciled_to_interrupted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = JsonlStorage(Path(tmp))
        # A process that no longer exists claimed this run. pid 1 is alive but
        # publishes no heartbeat file, so it cannot be mistaken for an owner.
        _write_task_event(
            storage,
            "orphan-1",
            "RUNNING",
            owner={"instance_id": "gone-forever", "pid": 999999, "hostname": "host"},
        )
        _write_task_event(storage, "finished-1", "RETURNED")

        reclaimed = reconcile_orphaned_tasks(storage=storage, data_dir=Path(tmp))

        assert [row["task_id"] for row in reclaimed] == ["orphan-1"]
        events = storage.list_recent_task_events(limit=50)
        last = {e["task_id"]: e for e in events}
        assert last["orphan-1"]["status"] == INTERRUPTED_STATUS
        assert last["orphan-1"]["result"]["success"] is False
        assert last["finished-1"]["status"] == "RETURNED"


def test_a_live_owner_keeps_its_task() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        storage = JsonlStorage(data_dir)
        registry = RuntimeRegistry(data_dir, role="test")
        registry.start()
        try:
            registry.track("mine-1")
            _write_task_event(storage, "mine-1", "RUNNING", owner=current_owner().to_payload())

            assert reconcile_orphaned_tasks(storage=storage, data_dir=data_dir) == []
            assert owner_is_alive(current_owner().to_payload(), data_dir=data_dir) is True

            instances = list_instances(data_dir)
            assert [row["instance_id"] for row in instances] == [INSTANCE_ID]
            assert instances[0]["active_task_ids"] == ["mine-1"]
        finally:
            registry.stop()


def test_a_stale_heartbeat_is_not_a_live_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        runtime = data_dir / "runtime"
        runtime.mkdir(parents=True)
        stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        (runtime / "zombie.json").write_text(
            json.dumps(
                {
                    "instance_id": "zombie",
                    "pid": 1,  # alive, but the heartbeat stopped long ago
                    "hostname": "host",
                    "heartbeat_at": stale,
                }
            ),
            encoding="utf-8",
        )

        assert owner_is_alive({"instance_id": "zombie", "pid": 1}, data_dir=data_dir) is False


def test_ownerless_running_rows_are_only_reclaimed_once_stale() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = JsonlStorage(Path(tmp))
        # Written by a build from before ownership tracking: recent enough that
        # a live run could still be behind it.
        _write_task_event(storage, "legacy-fresh", "RUNNING")
        assert reconcile_orphaned_tasks(storage=storage, data_dir=Path(tmp)) == []

        storage.append_task_event(
            {
                "timestamp": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
                "task_id": "legacy-old",
                "status": "RUNNING",
                "task": {"id": "legacy-old"},
            }
        )
        reclaimed = reconcile_orphaned_tasks(storage=storage, data_dir=Path(tmp))
        assert [row["task_id"] for row in reclaimed] == ["legacy-old"]


def test_a_blocked_workspace_reports_why_and_does_not_claim_to_be_running(cleanup) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        executor = _BlockingExecutor()
        gateway, _ = _gateway(Path(tmp), executor, workspace_lock_timeout_sec=30)
        gateway.start()
        cleanup.append(gateway)

        first, second = _Adapter(), _Adapter()
        task_a, task_b = _make_task(workspace), _make_task(workspace)
        gateway.submit_task(task=task_a, channel_adapter=first, channel_context={})
        assert executor.entered.wait(timeout=10)
        gateway.submit_task(task=task_b, channel_adapter=second, channel_context={})

        # The second run is behind the workspace lock: it must not read as
        # RUNNING, and it must say what it is waiting for.
        deadline = time.monotonic() + 10
        overview = {}
        while time.monotonic() < deadline:
            overview = gateway.get_task_overview(task_b.id) or {}
            if overview.get("blocked_reason"):
                break
            time.sleep(0.1)
        assert overview.get("blocked_reason") == "workspace_busy"
        assert overview.get("status") == "QUEUED"
        assert overview.get("blocked_by_workspace") == str(workspace.resolve())

        executor.release.set()
        assert first.done.wait(timeout=20)
        assert second.done.wait(timeout=20)
        assert (gateway.get_task_overview(task_b.id) or {})["status"] == "RETURNED"


def test_a_workspace_held_past_the_timeout_fails_instead_of_hanging(cleanup) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        executor = _BlockingExecutor()
        gateway, _ = _gateway(Path(tmp), executor, workspace_lock_timeout_sec=1)
        gateway.start()
        cleanup.append(gateway)

        first, second = _Adapter(), _Adapter()
        task_a, task_b = _make_task(workspace), _make_task(workspace)
        gateway.submit_task(task=task_a, channel_adapter=first, channel_context={})
        assert executor.entered.wait(timeout=10)
        gateway.submit_task(task=task_b, channel_adapter=second, channel_context={})

        # Bounded: the blocked run gives up and reports a terminal status rather
        # than parking its worker forever.
        assert second.done.wait(timeout=20)
        assert second.results[0].success is False
        assert "workspace" in second.results[0].summary.lower()
        assert (gateway.get_task_overview(task_b.id) or {})["status"] == "FAILED"

        executor.release.set()
        assert first.done.wait(timeout=20)


def test_the_workspace_lock_is_released_after_an_executor_crash(cleanup) -> None:
    class _Crashing:
        def name(self) -> str:
            return "codex"

        def supports(self, task) -> bool:
            return True

        def enforces_read_only(self) -> bool:
            return False

        def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
            raise RuntimeError("boom")

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        gateway, _ = _gateway(Path(tmp), _Crashing(), worker_count=1)
        gateway.start()
        cleanup.append(gateway)

        first = _Adapter()
        gateway.submit_task(task=_make_task(workspace), channel_adapter=first, channel_context={})
        assert first.done.wait(timeout=20)
        assert first.results[0].success is False

        # A crashed run must not strand the workspace: the next one still runs.
        second = _Adapter()
        gateway.submit_task(task=_make_task(workspace), channel_adapter=second, channel_context={})
        assert second.done.wait(timeout=20)
