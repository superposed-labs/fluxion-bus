"""Process ownership and liveness for in-flight tasks.

Task state lives in an append-only JSONL log that every Fluxion process can
read, but the worker threads, the child processes and the workspace locks live
inside *one* process. When that process dies mid-run — an MCP server torn down
with its client, a crash, a kill — the log keeps its last word on the task,
which is usually ``RUNNING``. Nothing ever corrected it, so a status poll would
report a run that no longer exists as still working, forever.

This module gives every task an owner that can be checked from the outside:

* each process publishes a small heartbeat file naming itself and the tasks it
  is currently running;
* every task status event records that owner;
* on startup, any non-terminal task whose owner is provably gone is closed out
  as ``INTERRUPTED``.

A live-but-wedged owner is deliberately *not* reconciled: it still heartbeats,
so it stays owned and visible, and the operator-facing force-cancel path deals
with it. Only a demonstrably absent owner is reclaimed.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# This process's identity. Regenerated per process, so a restart never looks
# like the process that owned the previous run.
INSTANCE_ID = uuid4().hex

HEARTBEAT_INTERVAL_SEC = 30
# Four missed beats. Long enough that a machine under heavy load is never
# mistaken for a dead one; short enough that a poll after a crash is honest.
LEASE_TTL_SEC = 120

INTERRUPTED_STATUS = "INTERRUPTED"
TERMINAL_STATUSES = frozenset({"RETURNED", "FAILED", "CANCELED", INTERRUPTED_STATUS})


@dataclass(frozen=True)
class Owner:
    instance_id: str
    pid: int
    hostname: str

    def to_payload(self) -> dict[str, Any]:
        return {"instance_id": self.instance_id, "pid": self.pid, "hostname": self.hostname}


def current_owner() -> Owner:
    return Owner(instance_id=INSTANCE_ID, pid=os.getpid(), hostname=socket.gethostname())


class RuntimeRegistry:
    """Publishes this process's heartbeat file and the tasks it owns."""

    def __init__(self, data_dir: Path, *, role: str = "gateway") -> None:
        self._dir = Path(data_dir) / "runtime"
        self._path = self._dir / f"{INSTANCE_ID}.json"
        self._role = role
        self._owner = current_owner()
        self._started_at = datetime.now(UTC)
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def owner(self) -> Owner:
        return self._owner

    def start(self) -> None:
        if self._thread is not None:
            return
        self._write()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="fluxion-heartbeat-registry",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for path in (self._path, self._path.with_name(f"{self._path.name}.tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def track(self, task_id: str) -> None:
        with self._lock:
            self._active.add(task_id)
        # Publish immediately: a task must never be observable as RUNNING
        # before its owner is observable as alive.
        self._write()

    def untrack(self, task_id: str) -> None:
        with self._lock:
            self._active.discard(task_id)
        self._write()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = sorted(self._active)
        return {
            **self._owner.to_payload(),
            "role": self._role,
            "started_at": self._started_at.isoformat(),
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "active_task_ids": active,
        }

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SEC):
            self._write()

    def _write(self) -> None:
        if self._stop.is_set():
            # Stopped: the ownership claim has been retracted, and a late
            # untrack() from a finishing worker must not resurrect it.
            return
        payload = self.snapshot()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.debug("could not publish runtime heartbeat", exc_info=True)


def list_instances(data_dir: Path) -> list[dict[str, Any]]:
    """Every process that published a heartbeat, freshest first."""
    directory = Path(data_dir) / "runtime"
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict):
            continue
        parsed["alive"] = _instance_is_alive(parsed)
        rows.append(parsed)
    return sorted(rows, key=lambda row: str(row.get("heartbeat_at") or ""), reverse=True)


def prune_dead_instances(data_dir: Path) -> int:
    """Delete heartbeat files of processes that are gone. Returns the count."""
    directory = Path(data_dir) / "runtime"
    removed = 0
    for instance in list_instances(data_dir):
        if instance.get("alive"):
            continue
        instance_id = str(instance.get("instance_id") or "")
        if not instance_id or instance_id == INSTANCE_ID:
            continue
        try:
            (directory / f"{instance_id}.json").unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def owner_is_alive(owner: dict[str, Any] | None, *, data_dir: Path) -> bool:
    """Whether the process that claimed a task still exists.

    Requires both a live pid and a fresh heartbeat: a pid alone can be a reused
    number, and a heartbeat alone can outlive the process that wrote it.
    """
    if not isinstance(owner, dict):
        return False
    instance_id = str(owner.get("instance_id") or "")
    if not instance_id:
        return False
    if instance_id == INSTANCE_ID:
        return True
    directory = Path(data_dir) / "runtime"
    path = directory / f"{instance_id}.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return _instance_is_alive(parsed if isinstance(parsed, dict) else None)


def reconcile_orphaned_tasks(
    *,
    storage: Any,
    data_dir: Path,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Close out non-terminal tasks whose owning process is gone.

    Returns one row per reclaimed task. Safe to run from several processes at
    once: the worst case is a duplicate INTERRUPTED event for the same task,
    which reads the same as one.
    """
    latest = _latest_event_per_task(storage.list_recent_task_events(limit=limit))
    stale_before = datetime.now(UTC) - timedelta(seconds=LEASE_TTL_SEC)
    reclaimed: list[dict[str, Any]] = []
    for event in latest:
        status = str(event.get("status") or "")
        if status in TERMINAL_STATUSES or not status:
            continue
        owner = event.get("owner")
        if owner_is_alive(owner if isinstance(owner, dict) else None, data_dir=data_dir):
            continue
        if not isinstance(owner, dict):
            # Written before task ownership existed, or by an older build. Only
            # reclaim once it is old enough that no live run could still be it.
            if not _is_older_than(str(event.get("timestamp") or ""), stale_before):
                continue
        task_id = str(event.get("task_id") or "")
        if not task_id:
            continue
        reason = _orphan_reason(owner)
        storage.append_task_event(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "task_id": task_id,
                "status": INTERRUPTED_STATUS,
                "task": event.get("task", {}),
                "owner": current_owner().to_payload(),
                "reconciled": {
                    "from_status": status,
                    "reason": reason,
                    "previous_owner": owner if isinstance(owner, dict) else None,
                    "at": datetime.now(UTC).isoformat(),
                },
                "result": {
                    "success": False,
                    "summary": (
                        f"Run interrupted: {reason}. It was last seen as {status} and its "
                        "process is gone, so no result was recorded. Re-run if still needed."
                    ),
                    "stdout": "",
                    "stderr": "",
                    "exit_code": 1,
                },
            }
        )
        reclaimed.append({"task_id": task_id, "from_status": status, "reason": reason})
    if reclaimed:
        logger.info("reconciled %d orphaned task(s) on startup", len(reclaimed))
    return reclaimed


def _orphan_reason(owner: Any) -> str:
    if not isinstance(owner, dict):
        return "owner unknown (recorded before task ownership tracking)"
    pid = owner.get("pid")
    return f"owning process is gone (pid={pid}, instance={str(owner.get('instance_id'))[:8]})"


def _latest_event_per_task(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        task_id = str(event.get("task_id") or "")
        if task_id:
            latest[task_id] = event
    return list(latest.values())


def _instance_is_alive(instance: dict[str, Any] | None) -> bool:
    if not isinstance(instance, dict):
        return False
    pid = instance.get("pid")
    if not isinstance(pid, int) or not pid_alive(pid):
        return False
    heartbeat = str(instance.get("heartbeat_at") or "")
    return not _is_older_than(heartbeat, datetime.now(UTC) - timedelta(seconds=LEASE_TTL_SEC))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process: it exists, which is all we asked.
        return True
    except OSError:
        return False
    return True


def _is_older_than(timestamp: str, cutoff: datetime) -> bool:
    parsed = _parse_time(timestamp)
    if parsed is None:
        # No usable timestamp: treat as old rather than pinning a task forever.
        return True
    return parsed < cutoff


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
