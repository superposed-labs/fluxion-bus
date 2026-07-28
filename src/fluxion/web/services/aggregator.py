from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fluxion.core.runtime_registry import TERMINAL_STATUSES

# Sub-agent run statuses that mean the run is finished (success or not).
TERMINAL_RUN_STATUSES = TERMINAL_STATUSES

# Bundled artifact extension → kind classification.
_ARTIFACT_KIND_BY_EXT: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".log": "log",
    ".txt": "log",
    ".json": "data",
    ".yaml": "data",
    ".yml": "data",
    ".xlsx": "sheet",
    ".xls": "sheet",
    ".csv": "sheet",
}


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _human_size(byte_count: int) -> str:
    if byte_count > 1024 * 1024:
        return f"{byte_count / (1024 * 1024):.1f} MB"
    if byte_count > 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count} B"


def _normalize_artifact(art: Any) -> dict[str, Any] | None:
    if isinstance(art, dict):
        return art
    if not isinstance(art, str):
        return None
    path = Path(art)
    kind = _ARTIFACT_KIND_BY_EXT.get(path.suffix.lower(), "report")
    size_str = "0 B"
    try:
        if path.exists():
            size_str = _human_size(path.stat().st_size)
    except OSError:
        pass
    return {"name": path.name, "kind": kind, "size": size_str, "path": art}


def _normalize_changed_path(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("path"), str):
        return item["path"]
    return None


def _normalize_changed_file(item: Any) -> dict[str, Any] | None:
    path = _normalize_changed_path(item)
    if path is None:
        return None
    if isinstance(item, dict):
        return {
            "op": str(item.get("op") or "M"),
            "path": path,
            "additions": int(item.get("additions") or 0),
            "deletions": int(item.get("deletions") or 0),
        }
    return {"op": "M", "path": path, "additions": 0, "deletions": 0}


def _display_task_text(task_data: dict[str, Any], metadata: dict[str, Any]) -> str:
    prompt = metadata.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()

    text = task_data.get("text")
    if not isinstance(text, str):
        return ""
    for marker in ("\nUser task:\n", "\nSubtask:\n"):
        if marker in text:
            return text.rsplit(marker, 1)[-1].strip()
    return text.strip()


def _initial_task(task_id: str, event: dict[str, Any]) -> dict[str, Any]:
    task_data = event.get("task", {})
    metadata = task_data.get("metadata", {})
    executor = metadata.get("executor", "codex")
    subagent = metadata.get("subagent") if isinstance(metadata.get("subagent"), dict) else {}
    channel = task_data.get("channel", "local")
    user_id = task_data.get("user_id", "")
    conversation_key = metadata.get("conversation_key") or f"{channel}:{user_id}"

    if channel == "slack":
        channel_meta = {"workspace": "local-slack", "channel": "slack", "user": user_id}
    else:
        channel_meta = {"host": "local", "cwd": task_data.get("workspace", "")}

    return {
        "task_id": task_id,
        "executor": executor,
        "model": str(metadata.get("model") or ""),
        "subagent": {
            "agent": subagent.get("agent", executor),
            "thread": subagent.get("thread", ""),
            "task_name": subagent.get("task_name", ""),
            "parent_path": subagent.get("parent_path", ""),
            "agent_path": subagent.get("agent_path", ""),
            "profile": subagent.get("profile", ""),
            "mode": subagent.get("mode", ""),
            "session_policy": subagent.get("session_policy", ""),
        },
        "channel": channel,
        "channel_meta": channel_meta,
        "conversation_key": conversation_key,
        "status": event.get("status"),
        "summary": _display_task_text(task_data, metadata),
        "success": None,
        "exit_code": None,
        "executor_session_id": None,
        "log_file": f"data/logs/task-{task_id}.log",
        "timestamp": {
            "received_at": None,
            "validated_at": None,
            "queued_at": None,
            "started_at": None,
            "ended_at": None,
        },
        "owner": None,
        "blocked": None,
        "changed_files": [],
        "risk_flags": [],
        "change_set_file": "",
        "diff_summary": {"files": 0, "additions": 0, "deletions": 0},
        "artifacts": [],
        "stdout": [],
        "stderr": [],
    }


_TIMESTAMP_FIELD_BY_STATUS: dict[str, str] = {
    "RECEIVED": "received_at",
    "VALIDATED": "validated_at",
    "QUEUED": "queued_at",
    "RUNNING": "started_at",
    "RETRYING": "started_at",
    "RETURNED": "ended_at",
    "FAILED": "ended_at",
    "CANCELED": "ended_at",
    "INTERRUPTED": "ended_at",
}


def _apply_event(task_obj: dict[str, Any], event: dict[str, Any]) -> None:
    status = event.get("status")
    timestamp = event.get("timestamp")
    task_data = event.get("task", {})
    result_data = event.get("result")

    if status:
        task_obj["status"] = status
    field = _TIMESTAMP_FIELD_BY_STATUS.get(status or "")
    if field and timestamp:
        task_obj["timestamp"][field] = timestamp

    owner = event.get("owner")
    if isinstance(owner, dict):
        task_obj["owner"] = owner
    # Only the latest event's blocked-ness is meaningful: a run that started is
    # no longer waiting on anything.
    blocked = event.get("blocked")
    task_obj["blocked"] = blocked if isinstance(blocked, dict) else None

    if task_data.get("text"):
        metadata = task_data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        task_obj["summary"] = _display_task_text(task_data, metadata)

    if not result_data:
        return

    task_obj["summary"] = result_data.get("summary") or task_obj["summary"]
    task_obj["success"] = result_data.get("success")
    task_obj["exit_code"] = result_data.get("exit_code")
    task_obj["executor_session_id"] = (
        result_data.get("executor_session_id") or task_obj["executor_session_id"]
    )

    changed_files = [
        item
        for item in (_normalize_changed_file(p) for p in result_data.get("changed_files", []))
        if item is not None
    ]
    task_obj["changed_files"] = changed_files
    task_obj["risk_flags"] = result_data.get("risk_flags", [])
    task_obj["change_set_file"] = result_data.get("change_set_file", "")
    result_diff_summary = result_data.get("diff_summary")
    if isinstance(result_diff_summary, dict):
        task_obj["diff_summary"] = result_diff_summary
    else:
        # A string diff_summary is `git diff --stat` over the WHOLE working
        # tree, so its line counts include other people's uncommitted edits and
        # every earlier run. Reporting them next to a run-scoped `files` count
        # would invite exactly the wrong attribution, so they stay 0 and
        # `lines_counted` says so rather than leaving a reader to guess whether
        # zero means "no lines changed" or "not measured".
        task_obj["diff_summary"] = {
            "files": len(changed_files),
            "additions": 0,
            "deletions": 0,
            "lines_counted": False,
        }
    task_obj["artifacts"] = [
        a
        for a in (_normalize_artifact(p) for p in result_data.get("artifacts", []))
        if a is not None
    ]
    task_obj["_fallback_stdout"] = result_data.get("stdout", "")
    task_obj["_fallback_stderr"] = result_data.get("stderr", "")


def aggregate_tasks(data_dir: Path) -> list[dict[str, Any]]:
    tasks_path = data_dir / "tasks.jsonl"
    sessions_path = data_dir / "sessions.jsonl"

    tasks_map: dict[str, dict[str, Any]] = {}
    for event in _iter_jsonl(tasks_path):
        task_id = event.get("task_id")
        if not task_id:
            continue
        if task_id not in tasks_map:
            tasks_map[task_id] = _initial_task(task_id, event)
        _apply_event(tasks_map[task_id], event)

    for sess_event in _iter_jsonl(sessions_path):
        active_task_id = sess_event.get("active_task_id")
        if not active_task_id or active_task_id not in tasks_map:
            continue
        task_obj = tasks_map[active_task_id]
        conv_key = sess_event.get("conversation_key")
        if conv_key:
            task_obj["conversation_key"] = conv_key
        executor_sessions = sess_event.get("executor_sessions")
        if isinstance(executor_sessions, dict):
            sess_id = executor_sessions.get(task_obj.get("executor"))
            if sess_id:
                task_obj["executor_session_id"] = sess_id

    return sorted(
        tasks_map.values(),
        key=lambda x: x["timestamp"]["received_at"] or "",
        reverse=True,
    )


def aggregate_sessions(data_dir: Path) -> list[dict[str, Any]]:
    sessions_path = data_dir / "sessions.jsonl"
    latest_by_key: dict[str, dict[str, Any]] = {}
    for event in _iter_jsonl(sessions_path):
        key = event.get("conversation_key")
        if key:
            latest_by_key[key] = event
    return sorted(
        latest_by_key.values(),
        key=lambda x: x.get("timestamp", ""),
        reverse=True,
    )


# ── mtime+size cache ───────────────────────────────────────────────
# The aggregator re-reads tasks.jsonl + sessions.jsonl on every call.
# At 160 tasks that costs ~50ms — invisible. At 10k+ tasks or with
# many SSE-reconnect-driven /api/tasks hydrations it adds up. Cache
# the result keyed by (mtime, size) of both source files: any append
# bumps both, so the cache invalidates exactly when state changes.
#
# Cache holders are module-level singletons. We accept a benign race
# under concurrent requests — both compute, both write the same
# result — in exchange for not needing a lock on the hot read path.

_Signature = tuple[float, int] | None
_ALL_SOURCES = ("tasks.jsonl", "sessions.jsonl")


def _signature(path: Path) -> _Signature:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _signatures(data_dir: Path) -> tuple[_Signature, ...]:
    return tuple(_signature(data_dir / name) for name in _ALL_SOURCES)


_tasks_cache: tuple[tuple[_Signature, ...], list[dict[str, Any]]] | None = None
_sessions_cache: tuple[_Signature, list[dict[str, Any]]] | None = None


def aggregate_tasks_cached(data_dir: Path) -> list[dict[str, Any]]:
    global _tasks_cache
    sig = _signatures(data_dir)
    if _tasks_cache is not None and _tasks_cache[0] == sig:
        return _tasks_cache[1]
    result = aggregate_tasks(data_dir)
    _tasks_cache = (sig, result)
    return result


def aggregate_sessions_cached(data_dir: Path) -> list[dict[str, Any]]:
    global _sessions_cache
    sig = _signature(data_dir / "sessions.jsonl")
    if _sessions_cache is not None and _sessions_cache[0] == sig:
        return _sessions_cache[1]
    result = aggregate_sessions(data_dir)
    _sessions_cache = (sig, result)
    return result


def reset_cache() -> None:
    """Clear cached aggregations. Intended for tests."""
    global _tasks_cache, _sessions_cache
    _tasks_cache = None
    _sessions_cache = None


def find_task(data_dir: Path, run_id: str) -> dict[str, Any] | None:
    """Look up one task by id from the local store (signature-cached read)."""
    wanted = run_id.strip()
    if not wanted:
        return None
    for task in aggregate_tasks_cached(data_dir):
        if task.get("task_id") == wanted:
            return task
    return None


def wait_for_terminal(
    data_dir: Path,
    run_id: str,
    *,
    timeout_sec: float,
    poll_interval: float = 1.0,
) -> dict[str, Any] | None:
    """Block until run `run_id` reaches a terminal status or `timeout_sec`
    elapses, then return its latest task record (or None if never seen).

    The store read is signature-cached, so it refreshes for free as the runner
    writes state transitions to disk; this just re-reads on a fixed cadence.
    Shared by the `fluxion-sub --wait` CLI and the MCP `get_task_status`
    long-poll, so a caller waits for completion instead of busy-polling."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    interval = max(0.1, poll_interval)
    while True:
        task = find_task(data_dir, run_id)
        if task is not None and str(task.get("status") or "") in TERMINAL_RUN_STATUSES:
            return task
        if time.monotonic() >= deadline:
            return task
        time.sleep(interval)
