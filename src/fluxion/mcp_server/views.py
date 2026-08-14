from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.config.settings import Settings
from fluxion.core.runtime_registry import TERMINAL_STATUSES, owner_is_alive
from fluxion.executors.antigravity.trajectory_stream import read_max_step_idx
from fluxion.executors.model_resolution import extract_antigravity_resolved_model
from fluxion.mcp_server.logs import _log_progress
from fluxion.subagent import SubagentRunner, compact_tail
from fluxion.usage.history.parsing import ANTIGRAVITY_CONVERSATIONS_DIRS
from fluxion.web.services.aggregator import aggregate_tasks_cached
from fluxion.workspace.antigravity_trajectory import (
    extract_agy_session_id,
    find_conversation_db,
)

_POLL_TAIL_MAX_LINES = 8
_POLL_TAIL_MAX_CHARS = 1000


def _tasks() -> list[dict[str, Any]]:
    return aggregate_tasks_cached(Settings.load().data_dir)


def _find_task(run_id: str) -> dict[str, Any] | None:
    wanted = run_id.strip()
    if not wanted:
        return None
    for task in _tasks():
        if task.get("task_id") == wanted:
            return task
    return None


_TERMINAL_STATUSES = set(TERMINAL_STATUSES)
_ACTIVE_STATUSES = {"RECEIVED", "VALIDATED", "QUEUED", "RUNNING", "RETRYING"}
# A non-terminal run whose log file was written within this window is reported as
# actively "working" even when there's no human-readable tail yet (Antigravity's
# working phase is almost entirely filtered glog plumbing). Lets a caller tell a
# live run from a hung one instead of a misleading no_output_seen.
_LOG_ACTIVE_WINDOW_SEC = 90
# How long an Antigravity run can go without a new trajectory step before its
# status stops claiming progress. The log file is no help here: agy's glog keeps
# writing transport and quota chatter whether or not the agent is doing
# anything, so a wedged run looked exactly like a working one. Steps land every
# two or three seconds during real work, so a gap this long is not a slow tool
# call — but it is still a suspicion, not a verdict, hence "possibly".
_AGENT_STALL_WINDOW_SEC = 120


def _status_view(
    task: dict[str, Any], *, settings: Settings, runner: SubagentRunner
) -> dict[str, Any]:
    slim = _slim_task(task)
    _attach_live_model_resolution(slim, settings=settings)
    status = str(slim.get("status") or "")
    terminal = _is_terminal_status(status)
    # While a run is in flight `summary` holds the rendered prompt + sub-agent
    # preamble (it only becomes the result text at terminal). Echoing the whole
    # prompt back on every status poll is pure context bloat for the caller, so
    # replace it with a one-line phrase until the run finishes; the result text
    # is still available via get_task_result (final_summary) once terminal.
    if not terminal:
        slim["summary"] = _RUNNING_SUMMARY.get(status, f"Status: {status or 'unknown'}.")
    progress = _progress_view(task, settings=settings, runner=runner, terminal=terminal)
    elapsed = _elapsed_sec(task)
    liveness = _liveness_view(task, settings=settings, runner=runner, terminal=terminal)
    slim.update(
        {
            "found": True,
            "is_terminal": terminal,
            "result_available": terminal,
            "changed_files_available": terminal,
            "can_cancel": status in _ACTIVE_STATUSES,
            "elapsed_sec": elapsed,
            **liveness,
            "next_action": _next_action(status, liveness),
            "suggested_poll_after_sec": _suggested_poll_after_sec(
                status=status,
                terminal=terminal,
                elapsed=elapsed,
                executor=str(task.get("executor") or ""),
                settings=settings,
            ),
            **progress,
            "note": (
                "changed_files is finalized only after RETURNED/FAILED/CANCELED. "
                "A RUNNING task may already have modified files."
                if not terminal
                else ""
            ),
        }
    )
    return slim


def _attach_live_model_resolution(task: dict[str, Any], *, settings: Settings) -> None:
    """Expose agy's selected CLI default as soon as its live log reports it."""
    if task.get("resolved_model") or task.get("executor") != "antigravity":
        return
    task_id = str(task.get("task_id") or "")
    if not task_id:
        return
    resolved = extract_antigravity_resolved_model(
        settings.data_dir / "logs" / f"task-{task_id}.agy.log"
    )
    if resolved:
        task["resolved_model"] = resolved
        task["model_resolution_source"] = "executor_runtime"


def _status_poll_view(
    task: dict[str, Any], *, settings: Settings, runner: SubagentRunner
) -> dict[str, Any]:
    detail = _status_view(task, settings=settings, runner=runner)
    if detail.get("is_terminal", False):
        tail = ""
        tail_truncated = False
    else:
        tail, tail_truncated = compact_tail(
            str(detail.get("recent_output_tail") or ""),
            max_lines=_POLL_TAIL_MAX_LINES,
            max_chars=_POLL_TAIL_MAX_CHARS,
        )
    return {
        "found": detail["found"],
        "run_id": detail.get("run_id") or detail.get("task_id", ""),
        "task_id": detail.get("task_id", ""),
        "status": detail.get("status", ""),
        "success": detail.get("success"),
        "summary": detail.get("summary", ""),
        "executor": detail.get("executor", ""),
        "requested_model": detail.get("requested_model", ""),
        "effective_model": detail.get("effective_model", "(executor default)"),
        "resolved_model": detail.get("resolved_model", ""),
        "model_resolution_source": detail.get("model_resolution_source", ""),
        "is_terminal": detail.get("is_terminal", False),
        "result_available": detail.get("result_available", False),
        "changed_files_available": detail.get("changed_files_available", False),
        "can_cancel": detail.get("can_cancel", False),
        "elapsed_sec": detail.get("elapsed_sec"),
        "next_action": detail.get("next_action", "inspect_status"),
        "suggested_poll_after_sec": detail.get("suggested_poll_after_sec", 3),
        "model": detail.get("model", ""),
        "owner_alive": detail.get("owner_alive"),
        "stale": detail.get("stale", False),
        "stale_hint": detail.get("stale_hint", ""),
        "queue_reason": detail.get("queue_reason", ""),
        "blocked_by_workspace": detail.get("blocked_by_workspace", ""),
        "blocked_by_task_id": detail.get("blocked_by_task_id", ""),
        "progress_signal": detail.get("progress_signal", ""),
        "progress_source": detail.get("progress_source", ""),
        # Only present for executors that can report the agent's own activity
        # rather than its runtime's log noise (Antigravity's trajectory).
        **(
            {"last_agent_activity_at": detail["last_agent_activity_at"]}
            if detail.get("last_agent_activity_at")
            else {}
        ),
        **({"agent_steps": detail["agent_steps"]} if detail.get("agent_steps") else {}),
        "recent_output_tail": tail,
        "recent_output_tail_truncated": bool(
            detail.get("recent_output_tail_truncated", False) or tail_truncated
        ),
        "live_output_chars": detail.get("live_output_chars", 0),
        "live_output_updated_at": detail.get("live_output_updated_at"),
        "log_size": detail.get("log_size", 0),
        "log_updated_at": detail.get("log_updated_at"),
        "log_file": detail.get("log_file", ""),
        "note": detail.get("note", ""),
    }


def _liveness_view(
    task: dict[str, Any],
    *,
    settings: Settings,
    runner: SubagentRunner,
    terminal: bool,
) -> dict[str, Any]:
    """Who owns this run, whether that owner still exists, and what blocks it.

    A queued run used to be indistinguishable from a wedged one. The two
    questions a caller actually has — "is anything still working on this?" and
    "if it is waiting, on what?" — are answered here.
    """
    task_id = str(task.get("task_id") or "")
    owner = task.get("owner") if isinstance(task.get("owner"), dict) else None
    # The owning process's own record is authoritative and more current than the
    # log; for a run owned elsewhere there is nothing in memory to read.
    gateway = getattr(runner, "gateway", None)
    overview = (gateway.get_task_overview(task_id) or {}) if gateway is not None else {}
    blocked = task.get("blocked") if isinstance(task.get("blocked"), dict) else None
    reason = str(overview.get("blocked_reason") or "")
    if not reason and blocked and not overview:
        reason = str(blocked.get("reason") or "")
    workspace = str(overview.get("blocked_by_workspace") or (blocked or {}).get("workspace") or "")
    holder = (blocked or {}).get("holder") if isinstance(blocked, dict) else None
    blocked_by_task_id = str(
        overview.get("blocked_by_task_id") or (holder or {}).get("task_id") or ""
    )
    # Unknown, not dead: a run recorded before ownership tracking has no owner
    # to check, and claiming it is stale on that basis would be a guess.
    # Reconciliation still ages those out on its own schedule.
    owner_alive = (
        owner_is_alive(owner, data_dir=settings.data_dir)
        if owner is not None and not terminal
        else None
    )
    view: dict[str, Any] = {
        "model": str(task.get("model") or ""),
        "owner": owner,
        "owner_alive": owner_alive,
        "owned_by_this_process": bool(overview),
        "queue_reason": reason or ("" if terminal else _default_queue_reason(task, overview)),
        "blocked_by_workspace": workspace,
        "blocked_by_task_id": blocked_by_task_id,
    }
    if owner_alive is False:
        view["stale"] = True
        view["stale_hint"] = (
            "The process that owned this run is gone, so its status will not change "
            "again. Call reconcile_tasks (or force_cancel_subagent_run) to close it out."
        )
    return view


def _default_queue_reason(task: dict[str, Any], overview: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    if status not in {"RECEIVED", "VALIDATED", "QUEUED"}:
        return ""
    if overview:
        return "waiting_for_worker"
    # Queued in a process other than this one; we can see the record but not its
    # queue, so don't guess at a cause.
    return "queued_elsewhere"


def _progress_view(
    task: dict[str, Any],
    *,
    settings: Settings,
    runner: SubagentRunner,
    terminal: bool,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    live = runner.live_progress(task_id)
    log = _log_progress(task_id=task_id, data_dir=settings.data_dir)
    live_tail = str(live.get("recent_output_tail") or "")
    log_tail = str(log.get("recent_output_tail") or "")
    if live_tail:
        source = "live_output"
        tail = live_tail
        truncated = bool(live.get("recent_output_tail_truncated"))
        progress_signal = "output_recent"
    elif log_tail:
        source = str(log.get("progress_source") or "log_file")
        tail = log_tail
        truncated = bool(log.get("recent_output_tail_truncated"))
        progress_signal = "terminal_output" if terminal else "output_seen"
    else:
        # No human-meaningful tail. For Antigravity the working phase is almost
        # entirely glog plumbing (filtered out) and the bundled task log only
        # appears at the end, so an empty tail is NOT evidence of an idle run. A
        # log file still being written recently is a positive liveness signal.
        tail = ""
        truncated = False
        log_updated = _parse_time(str(log.get("log_updated_at") or ""))
        log_fresh = (
            log_updated is not None
            and (datetime.now(UTC) - log_updated).total_seconds() <= _LOG_ACTIVE_WINDOW_SEC
        )
        if terminal:
            source = ""
            progress_signal = "terminal"
        elif log.get("log_file") and log_fresh:
            source = str(log.get("progress_source") or "log_file")
            progress_signal = "working"
        else:
            source = ""
            progress_signal = "no_output_seen"
    agent = _agent_activity_view(task, task_id=task_id, settings=settings, terminal=terminal)
    if (
        not terminal
        and not live_tail
        and agent.get("last_agent_activity_at")
        and _is_stale(agent["last_agent_activity_at"], _AGENT_STALL_WINDOW_SEC)
    ):
        # The agent itself has stopped moving. Whatever the log file is doing,
        # saying "working" here is what let a wedged run poll green for an hour.
        progress_signal = "possibly_stalled"
    return {
        "progress_signal": progress_signal,
        "progress_source": source,
        **agent,
        "recent_output_tail": tail,
        "recent_output_tail_truncated": truncated,
        "live_output_chars": live.get("live_output_chars", 0),
        "live_output_updated_at": live.get("live_output_updated_at"),
        "log_file": log.get("log_file") or task.get("log_file", ""),
        "log_size": log.get("log_size", 0),
        "log_updated_at": log.get("log_updated_at"),
    }


def _agent_activity_view(
    task: dict[str, Any],
    *,
    task_id: str,
    settings: Settings,
    terminal: bool,
) -> dict[str, Any]:
    """When the agent last actually did something, for executors that can say.

    Antigravity is the one that can: its trajectory DB gets a row per tool step
    as the run proceeds, which is the same source changed_files and the live
    narration are read from. stdout says nothing until the very end and the glog
    log says nothing about the agent at all, so this is the only honest answer
    to "is it still working?". Other executors stream their work to stdout,
    where the output tail already answers it.
    """
    if terminal or str(task.get("executor") or "") != "antigravity":
        return {}
    db_path = _trajectory_db_path(task_id=task_id, data_dir=settings.data_dir)
    if db_path is None:
        return {}
    try:
        updated = datetime.fromtimestamp(db_path.stat().st_mtime, UTC)
    except OSError:
        return {}
    steps = read_max_step_idx(db_path)
    return {
        "last_agent_activity_at": updated.isoformat(),
        "agent_steps": steps if steps >= 0 else 0,
    }


# agy names the conversation within the first seconds of a run — measured at
# ~7KB into a log that grows to hundreds of KB — so the head is all this has to
# read. Status is polled repeatedly; re-reading a whole run's log each time is
# not worth it for a line that is always near the top.
_SESSION_ID_SCAN_BYTES = 128_000


def _trajectory_db_path(*, task_id: str, data_dir: Path) -> Path | None:
    log_file = data_dir / "logs" / f"task-{task_id}.agy.log"
    try:
        with log_file.open("rb") as handle:
            text = handle.read(_SESSION_ID_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    session_id = extract_agy_session_id(text)
    if not session_id:
        # agy names the conversation a few seconds into the run; until then
        # there is nothing to read and no claim to make.
        return None
    return find_conversation_db(session_id, ANTIGRAVITY_CONVERSATIONS_DIRS)


def _is_stale(timestamp: str, window_sec: int) -> bool:
    moment = _parse_time(timestamp)
    if moment is None:
        return False
    return (datetime.now(UTC) - moment).total_seconds() > window_sec


def _result_view(task: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "")
    subagent = task.get("subagent") if isinstance(task.get("subagent"), dict) else {}
    changed_files = _changed_file_paths(task.get("changed_files", []))
    artifacts = _artifact_paths(task.get("artifacts", []))
    return {
        "found": True,
        "run_id": task.get("task_id", ""),
        "task_id": task.get("task_id", ""),
        "status": status,
        "success": task.get("success"),
        "exit_code": task.get("exit_code"),
        "is_terminal": _is_terminal_status(status),
        "final_summary": task.get("summary", ""),
        "summary": task.get("summary", ""),
        "executor": task.get("executor", ""),
        "requested_model": task.get("requested_model", ""),
        "effective_model": task.get("effective_model") or "(executor default)",
        "resolved_model": task.get("resolved_model", ""),
        "model_resolution_source": task.get("model_resolution_source", ""),
        "executor_session_id": task.get("executor_session_id"),
        "conversation_key": task.get("conversation_key", ""),
        "subagent": subagent,
        "changed_files": changed_files,
        "risk_flags": task.get("risk_flags", []),
        "change_set_file": task.get("change_set_file", ""),
        # revert_subagent_run works off the recorded ChangeSet, so its presence
        # is the answer to "can this run be undone?" — one the caller would
        # otherwise have to infer from a file path it cannot check. Cancel makes
        # this worth stating: Fluxion never rolls a canceled run back, so its
        # changes are still on disk and are the caller's to review or revert.
        "revert_available": bool(task.get("change_set_file")),
        "artifacts": artifacts,
        "diff_summary": task.get("diff_summary", {}),
        "needs_review": _needs_review(task),
        # For a run that had to be terminated: whether anything it started is
        # still running. A cancel that reports no leftovers is a different fact
        # from a cancel that sent the signals and hoped.
        **_process_cleanup_view(task),
        "log_file": task.get("log_file", ""),
        "elapsed_sec": _elapsed_sec(task),
        "next_action": "review_result" if _is_terminal_status(status) else _next_action(status),
    }


def _process_cleanup_view(task: dict[str, Any]) -> dict[str, Any]:
    cleanup = task.get("process_cleanup")
    if not isinstance(cleanup, dict) or not cleanup:
        # Nothing was terminated (the run ended on its own), so there is no
        # claim to make either way — saying "verified" would overstate it.
        return {}
    remaining = cleanup.get("remaining")
    return {
        "process_cleanup_verified": bool(cleanup.get("verified")),
        "remaining_processes": remaining if isinstance(remaining, list) else [],
    }


def _is_terminal_status(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def _changed_file_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.append(item["path"])
    return paths


def _artifact_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            paths.append(item["path"])
    return paths


_RUNNING_SUMMARY = {
    "RECEIVED": "Accepted; not started yet.",
    "VALIDATED": "Validated; queued shortly.",
    "QUEUED": "Queued; waiting to start.",
    "RUNNING": "Running.",
    "RETRYING": "Retrying after a transient failure.",
}


def _typical_duration_sec(executor: str, *, settings: Settings) -> int | None:
    """Median wall-clock duration of recent terminal runs (same executor when
    known), as a basis for an ETA. None when there's no history yet."""
    durations: list[int] = []
    for task in aggregate_tasks_cached(settings.data_dir):
        if str(task.get("status") or "") not in _TERMINAL_STATUSES:
            continue
        if executor and str(task.get("executor") or "") != executor:
            continue
        dur = _elapsed_sec(task)
        if dur and dur > 0:
            durations.append(dur)
    if not durations:
        return None
    durations.sort()
    return durations[len(durations) // 2]


def _suggested_poll_after_sec(
    *, status: str, terminal: bool, elapsed: int | None, executor: str, settings: Settings
) -> int:
    """How long the caller should wait before polling again — so it waits about
    until the run is expected to finish instead of busy-polling. 0 when there's
    nothing left to wait for."""
    if terminal:
        return 0
    if status != "RUNNING":  # RECEIVED/VALIDATED/QUEUED/RETRYING — not started
        return 3
    typical = _typical_duration_sec(executor, settings=settings)
    if typical is not None and elapsed is not None:
        remaining = typical - elapsed
        return int(min(settings.mcp_status_max_wait_ms / 1000, max(3, remaining)))
    return 12


def _next_action(status: str, liveness: dict[str, Any] | None = None) -> str:
    if status in _TERMINAL_STATUSES:
        return "get_task_result"
    if liveness and liveness.get("stale"):
        # Polling a run whose owner died never resolves; say so instead.
        return "reconcile_tasks"
    if status in {"RECEIVED", "VALIDATED", "QUEUED", "RUNNING", "RETRYING"}:
        return "poll_later_or_cancel"
    return "inspect_status"


def _needs_review(task: dict[str, Any]) -> bool:
    subagent = task.get("subagent") if isinstance(task.get("subagent"), dict) else {}
    mode = str(subagent.get("mode") or "")
    changed_files = task.get("changed_files", [])
    artifacts = task.get("artifacts", [])
    return mode == "workspace-write" or bool(changed_files) or bool(artifacts)


def _elapsed_sec(task: dict[str, Any]) -> int | None:
    timestamp = task.get("timestamp") if isinstance(task.get("timestamp"), dict) else {}
    started = _parse_time(str(timestamp.get("started_at") or ""))
    if started is None:
        started = _parse_time(str(timestamp.get("queued_at") or ""))
    if started is None:
        started = _parse_time(str(timestamp.get("received_at") or ""))
    if started is None:
        return None
    ended = _parse_time(str(timestamp.get("ended_at") or ""))
    end_time = ended or datetime.now(UTC)
    return max(0, int((end_time - started).total_seconds()))


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _filtered_tasks(
    *,
    limit: int,
    agent: str,
    project: str,
    status: str,
    agent_path_prefix: str,
) -> list[dict[str, Any]]:
    agent = agent.strip()
    project = project.strip()
    status = status.strip().upper()
    agent_path_prefix = agent_path_prefix.strip()
    rows: list[dict[str, Any]] = []
    for task in _tasks():
        subagent = task.get("subagent")
        if not isinstance(subagent, dict):
            subagent = {}
        if agent and task.get("executor") != agent and subagent.get("agent") != agent:
            continue
        if project and subagent.get("project") != project:
            continue
        if status and task.get("status") != status:
            continue
        agent_path = str(subagent.get("agent_path") or "")
        if agent_path_prefix and not agent_path.startswith(agent_path_prefix):
            continue
        rows.append(task)
        if len(rows) >= limit:
            break
    return rows


def _slim_task(task: dict[str, Any]) -> dict[str, Any]:
    subagent = task.get("subagent") if isinstance(task.get("subagent"), dict) else {}
    timestamp = task.get("timestamp") if isinstance(task.get("timestamp"), dict) else {}
    return {
        "run_id": task.get("task_id", ""),
        "task_id": task.get("task_id", ""),
        "executor": task.get("executor", ""),
        "requested_model": task.get("requested_model", ""),
        "effective_model": task.get("effective_model") or "(executor default)",
        "resolved_model": task.get("resolved_model", ""),
        "model_resolution_source": task.get("model_resolution_source", ""),
        "status": task.get("status", ""),
        "success": task.get("success"),
        "exit_code": task.get("exit_code"),
        "summary": task.get("summary", ""),
        "conversation_key": task.get("conversation_key", ""),
        "executor_session_id": task.get("executor_session_id"),
        "subagent": subagent,
        "changed_files": task.get("changed_files", []),
        "change_set_file": task.get("change_set_file", ""),
        "diff_summary": task.get("diff_summary", {}),
        "artifacts": task.get("artifacts", []),
        "timestamp": timestamp,
        "log_file": task.get("log_file", ""),
    }
