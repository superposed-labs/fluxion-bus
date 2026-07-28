"""Operator-facing views: which Fluxion is running, and recovery actions.

The MCP surface used to expose only per-run tools, so two classes of question
had no answer from inside a session: "which installation and which .env is this
process actually using?" (a dev checkout and an installed copy can both exist,
and editing the wrong one looks like the setting had no effect), and "this run
is stuck — how do I get out of it?".
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.config.settings import Settings
from fluxion.config.settings.env import env_file_path
from fluxion.core.runtime_registry import (
    INTERRUPTED_STATUS,
    LEASE_TTL_SEC,
    list_instances,
    owner_is_alive,
    prune_dead_instances,
    reconcile_orphaned_tasks,
)
from fluxion.core.storage import JsonlStorage
from fluxion.subagent import SubagentRunner
from fluxion.workspace.lock_state import list_workspace_locks, prune_released_locks


def status_view(*, settings: Settings, runner: SubagentRunner) -> dict[str, Any]:
    """Everything needed to tell one Fluxion installation from another."""
    config_file = env_file_path()
    gateway = runner.gateway
    runtime = gateway.get_runtime_status()
    registry = gateway.registry_snapshot()
    return {
        "installation": {
            "code_root": str(_code_root()),
            "config_file": str(config_file) if config_file else None,
            "config_file_changed_since_start": _config_changed_since_start(
                config_file, registry.get("started_at")
            ),
            "data_dir": str(settings.data_dir),
            "version": _version(),
            "git_commit": _git_commit(_code_root()),
        },
        "process": {
            **registry,
            "worker_count": runtime.get("worker_count"),
            "queue_depth": runtime.get("queue_depth"),
            "running_tasks": runtime.get("running_tasks"),
            "uptime_sec": runtime.get("uptime_sec"),
        },
        # Config is read once at startup. A value edited in .env afterwards is
        # not live in this process, so report what is actually in effect here.
        "effective_settings": {
            "default_executor": settings.default_executor,
            "enabled_executors": list(settings.enabled_executors),
            "task_timeout_sec": settings.task_timeout_sec,
            "worker_count": settings.worker_count,
            "workspace_lock_timeout_sec": settings.workspace_lock_timeout_sec,
            "max_pending_per_user": settings.max_pending_per_user,
            "change_detection": settings.change_detection,
            "revert_capture": settings.revert_capture,
            "antigravity_dangerously_skip_permissions": bool(
                getattr(settings, "antigravity_dangerously_skip_permissions", False)
            ),
        },
        "other_instances": [
            row
            for row in list_instances(settings.data_dir)
            if row.get("instance_id") != registry.get("instance_id")
        ],
        "workspace_locks": list_workspace_locks(),
        "notes": [
            "effective_settings is what THIS process loaded at startup; editing "
            ".env does not change a running process. Restart it to apply changes.",
            "Cancellation is delivered by the owning process. A run owned by "
            "another instance can only be force-closed once that owner is gone.",
        ],
    }


def reconcile_view(*, settings: Settings) -> dict[str, Any]:
    """Close out runs whose owning process is gone; drop dead heartbeats."""
    storage = JsonlStorage(settings.data_dir)
    reclaimed = reconcile_orphaned_tasks(storage=storage, data_dir=settings.data_dir)
    pruned = prune_dead_instances(settings.data_dir)
    pruned_locks = prune_released_locks()
    return {
        "success": True,
        "reconciled": reclaimed,
        "reconciled_count": len(reclaimed),
        "pruned_instances": pruned,
        "pruned_lock_files": pruned_locks,
        "summary": (
            f"Closed out {len(reclaimed)} orphaned run(s) as {INTERRUPTED_STATUS}, "
            f"pruned {pruned} dead instance record(s) and {pruned_locks} released "
            "workspace lock file(s)."
        ),
    }


def force_cancel_view(*, run_id: str, settings: Settings, runner: SubagentRunner) -> dict[str, Any]:
    """Cancel hard: cooperatively if we own the run, by reclaim if we don't."""
    wanted = run_id.strip()
    if not wanted:
        return {"success": False, "run_id": run_id, "summary": "run_id is required."}

    canceled, reason = runner.cancel(wanted)
    if canceled:
        return {
            "success": True,
            "run_id": wanted,
            "owned_by_this_process": True,
            "action": "cancel_requested",
            "summary": (
                f"{reason}. This process owns the run, so the request reaches its "
                "executor and terminates the whole process group."
            ),
        }

    storage = JsonlStorage(settings.data_dir)
    event = _latest_event(storage, wanted)
    if event is None:
        return {
            "success": False,
            "run_id": wanted,
            "found": False,
            "summary": "No such run in the local task store.",
        }
    status = str(event.get("status") or "")
    if status in {"RETURNED", "FAILED", "CANCELED", INTERRUPTED_STATUS}:
        return {
            "success": False,
            "run_id": wanted,
            "found": True,
            "status": status,
            "summary": f"Run is already terminal ({status}); nothing to cancel.",
        }

    owner = event.get("owner") if isinstance(event.get("owner"), dict) else None
    if owner_is_alive(owner, data_dir=settings.data_dir):
        # Refusing here is the honest answer: marking it terminal would not stop
        # the agent process that another instance is still driving.
        return {
            "success": False,
            "run_id": wanted,
            "found": True,
            "status": status,
            "owned_by_this_process": False,
            "owner": owner,
            "summary": (
                "This run is owned by another live Fluxion process "
                f"(pid={(owner or {}).get('pid')}). Cancellation must be requested from "
                "that process; force-closing the record here would leave its agent "
                "running. Stop that process, then call reconcile_tasks."
            ),
        }

    reclaimed = reconcile_orphaned_tasks(storage=storage, data_dir=settings.data_dir)
    hit = [row for row in reclaimed if row.get("task_id") == wanted]
    return {
        "success": bool(hit),
        "run_id": wanted,
        "found": True,
        "owned_by_this_process": False,
        "action": "reclaimed",
        "status": INTERRUPTED_STATUS if hit else status,
        "summary": (
            f"Owning process is gone; run closed out as {INTERRUPTED_STATUS}."
            if hit
            else (
                "Owner looks gone but the run could not be reclaimed — it may have "
                f"been written less than {LEASE_TTL_SEC}s ago. Retry shortly."
            )
        ),
        "also_reconciled": [row["task_id"] for row in reclaimed if row.get("task_id") != wanted],
    }


def _latest_event(storage: JsonlStorage, task_id: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for event in storage.list_recent_task_events(limit=5000):
        if str(event.get("task_id") or "") == task_id:
            found = event
    return found


def _code_root() -> Path:
    import fluxion

    return Path(fluxion.__file__).resolve().parent


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("fluxion")
    except Exception:
        return "unknown"


def _git_commit(code_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(code_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
    return completed.stdout.strip() if completed.returncode == 0 else "not-a-git-checkout"


def _config_changed_since_start(config_file: Path | None, started_at: Any) -> bool | None:
    """True when .env was edited after this process read it — i.e. restart needed."""
    if config_file is None or not isinstance(started_at, str) or not started_at:
        return None
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(config_file), tz=UTC)
        started = datetime.fromisoformat(started_at)
    except (OSError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return mtime > started
