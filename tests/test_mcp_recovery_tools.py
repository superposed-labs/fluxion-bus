"""MCP recovery + diagnostics surface: status, reconcile, force cancel."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fluxion.core.runtime_registry import (
    INTERRUPTED_STATUS,
    RuntimeRegistry,
    current_owner,
)
from fluxion.core.storage import JsonlStorage
from fluxion.mcp_server.diagnostics import force_cancel_view, reconcile_view, status_view
from fluxion.mcp_server.views import _status_poll_view

_DEAD_OWNER = {"instance_id": "long-gone", "pid": 999999, "hostname": "host"}


class _Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.default_executor = "antigravity"
        self.enabled_executors = ["antigravity", "codex"]
        self.task_timeout_sec = 1800
        self.worker_count = 1
        self.workspace_lock_timeout_sec = 1800
        self.max_pending_per_user = 3
        self.change_detection = "off"
        self.revert_capture = "structured"
        self.antigravity_dangerously_skip_permissions = True


class _Gateway:
    def __init__(self, data_dir: Path, *, cancels: bool = False) -> None:
        self._registry = RuntimeRegistry(data_dir, role="test")
        self._cancels = cancels
        self.canceled: list[str] = []

    def get_runtime_status(self):
        return {
            "worker_count": 1,
            "queue_depth": 0,
            "running_tasks": 0,
            "total_submitted": 0,
            "total_completed": 0,
            "uptime_sec": 5,
        }

    def registry_snapshot(self):
        return self._registry.snapshot()

    def get_task_overview(self, task_id):
        return None


class _Runner:
    def __init__(self, data_dir: Path, *, cancels: bool = False) -> None:
        self.gateway = _Gateway(data_dir, cancels=cancels)
        self._cancels = cancels

    def cancel(self, task_id):
        if self._cancels:
            self.gateway.canceled.append(task_id)
            return True, "cancel requested for running task"
        return False, "task not found"

    def live_progress(self, task_id):
        return {}


def _write(storage: JsonlStorage, task_id: str, status: str, **extra) -> None:
    storage.append_task_event(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "task_id": task_id,
            "status": status,
            "task": {"id": task_id, "metadata": {}},
            **extra,
        }
    )


def test_status_view_names_the_installation_actually_in_use(tmp_path) -> None:
    settings = _Settings(tmp_path)
    view = status_view(settings=settings, runner=_Runner(tmp_path))

    install = view["installation"]
    assert install["code_root"].endswith("/fluxion")
    assert install["data_dir"] == str(tmp_path)
    assert "version" in install
    # What THIS process loaded, so a stale .env edit is visible as a mismatch.
    assert view["effective_settings"]["worker_count"] == 1
    assert view["effective_settings"]["default_executor"] == "antigravity"
    assert view["process"]["pid"] == current_owner().pid
    # Only currently-held locks are listed; the long tail of released files is
    # counted so the response stays readable.
    assert isinstance(view["workspace_locks"]["held"], list)
    assert isinstance(view["workspace_locks"]["released_lock_files"], int)


def test_reconcile_tool_closes_out_runs_whose_owner_is_gone(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    _write(storage, "ghost", "RUNNING", owner=_DEAD_OWNER)

    result = reconcile_view(settings=_Settings(tmp_path))

    assert result["reconciled_count"] == 1
    assert result["reconciled"][0]["task_id"] == "ghost"
    last = {e["task_id"]: e for e in storage.list_recent_task_events(limit=50)}
    assert last["ghost"]["status"] == INTERRUPTED_STATUS


def test_force_cancel_reclaims_a_run_whose_owner_died(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    _write(storage, "ghost", "RUNNING", owner=_DEAD_OWNER)

    result = force_cancel_view(
        run_id="ghost", settings=_Settings(tmp_path), runner=_Runner(tmp_path)
    )

    assert result["success"] is True
    assert result["action"] == "reclaimed"
    assert result["status"] == INTERRUPTED_STATUS


def test_force_cancel_refuses_to_close_a_run_another_live_process_owns(tmp_path) -> None:
    storage = JsonlStorage(tmp_path)
    registry = RuntimeRegistry(tmp_path, role="owner")
    registry.start()
    try:
        # Owned by a process that is demonstrably alive (this one).
        _write(storage, "live-elsewhere", "RUNNING", owner=current_owner().to_payload())

        result = force_cancel_view(
            run_id="live-elsewhere",
            settings=_Settings(tmp_path),
            runner=_Runner(tmp_path),  # a runner that does not own the task
        )

        # Marking it terminal here would leave the real agent process running,
        # so the honest answer is a refusal that names the owner.
        assert result["success"] is False
        assert result["owned_by_this_process"] is False
        assert result["owner"]["pid"] == current_owner().pid
        last = {e["task_id"]: e for e in storage.list_recent_task_events(limit=50)}
        assert last["live-elsewhere"]["status"] == "RUNNING"
    finally:
        registry.stop()


def test_force_cancel_uses_the_normal_path_when_this_process_owns_the_run(tmp_path) -> None:
    runner = _Runner(tmp_path, cancels=True)
    result = force_cancel_view(run_id="mine", settings=_Settings(tmp_path), runner=runner)

    assert result["success"] is True
    assert result["owned_by_this_process"] is True
    assert runner.gateway.canceled == ["mine"]


def test_status_poll_flags_a_run_whose_owner_is_gone(tmp_path) -> None:
    view = _status_poll_view(
        {
            "task_id": "ghost",
            "status": "RUNNING",
            "executor": "antigravity",
            "model": "gemini-3.6-flash-high",
            "owner": _DEAD_OWNER,
            "timestamp": {"started_at": datetime.now(UTC).isoformat()},
        },
        settings=_Settings(tmp_path),
        runner=_Runner(tmp_path),
    )

    assert view["owner_alive"] is False
    assert view["stale"] is True
    # Polling a dead run forever was the old failure mode; point at the fix.
    assert view["next_action"] == "reconcile_tasks"
    assert view["model"] == "gemini-3.6-flash-high"


def test_status_poll_reports_why_a_run_is_queued(tmp_path) -> None:
    view = _status_poll_view(
        {
            "task_id": "waiting",
            "status": "QUEUED",
            "executor": "antigravity",
            "owner": current_owner().to_payload(),
            "blocked": {
                "reason": "workspace_busy",
                "workspace": "/repo",
                "holder": {"task_id": "holder-1", "pid": 4242},
            },
            "timestamp": {"queued_at": datetime.now(UTC).isoformat()},
        },
        settings=_Settings(tmp_path),
        runner=_Runner(tmp_path),
    )

    assert view["queue_reason"] == "workspace_busy"
    assert view["blocked_by_workspace"] == "/repo"
    assert view["blocked_by_task_id"] == "holder-1"
