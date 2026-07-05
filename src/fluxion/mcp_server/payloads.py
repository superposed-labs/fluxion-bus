from __future__ import annotations

from typing import Any

from fluxion.config.settings import Settings


def _timed_out_still_running_payload(
    handle: Any, *, timeout_sec: int, prompt: str, profile: str, mode: str
) -> dict[str, Any]:
    """Payload for a blocking wait that elapsed before the task finished.

    The run is intentionally NOT canceled: it stays queued/running in the
    background (bounded by task_timeout_sec) and the caller collects the result
    later via get_task_result using the returned run_id. This degrades a
    too-short blocking wait into fire-and-forget rather than destroying
    in-progress work.
    """
    payload = handle.to_payload()
    status = _latest_non_terminal_status(handle, payload)
    status_phrase = "queued" if status == "QUEUED" else "running"
    payload.update(
        {
            "success": False,
            "status": status,
            "timed_out": True,
            "summary": (
                f"Fluxion sub-agent run did not finish within {timeout_sec}s; "
                f"it is still {status_phrase} in the background. Fetch the result later "
                "with get_task_result using this run_id, or stop it with "
                "cancel_subagent_run."
            ),
            "cancel_requested": False,
            "cancel_reason": None,
            "wait_for_result": True,
            "next_tools": [
                "get_task_status",
                "get_task_result",
                "cancel_subagent_run",
            ],
        }
    )
    return payload


def _latest_non_terminal_status(handle: Any, payload: dict[str, Any]) -> str:
    adapter = getattr(handle, "adapter", None)
    statuses = getattr(adapter, "statuses", None)
    if isinstance(statuses, list):
        for item in reversed(statuses):
            if isinstance(item, dict):
                status = str(item.get("status") or "").strip().upper()
                if status:
                    return status
    status = str(payload.get("status") or "").strip().upper()
    return status or "RUNNING"


def _error_payload(
    *,
    error: Exception,
    agent: str,
    project: str,
    workspace: str,
    thread: str,
    task_name: str,
    parent_path: str,
    profile: str,
    mode: str,
    settings: Settings,
) -> dict[str, Any]:
    summary = str(error)
    payload: dict[str, Any] = {
        "success": False,
        "accepted": False,
        "timed_out": False,
        "summary": summary,
        "agent": agent,
        "project": project,
        "workspace": workspace,
        "thread": thread,
        "task_name": task_name,
        "parent_path": parent_path,
        "agent_path": "",
        "conversation_key": "",
        "profile": profile,
        "mode": mode,
    }
    if "Unsupported sub-agent executor" in summary:
        payload.update(
            {
                "error_code": "UNSUPPORTED_EXECUTOR",
                "suggestion": (
                    "Use agent=auto or one of: antigravity, codex, claude. "
                    "Common aliases such as agy and antigratity are accepted after restarting MCP."
                ),
                "supported_agents": ["auto", "antigravity", "codex", "claude"],
            }
        )
    elif "Disabled sub-agent executor" in summary:
        payload.update(
            {
                "error_code": "DISABLED_EXECUTOR",
                "suggestion": (
                    "Choose agent=auto or one of FLUXION_ENABLED_EXECUTORS. "
                    "Enable more executors from the Fluxion desktop Preferences."
                ),
                "enabled_agents": ["auto", *settings.enabled_executors],
            }
        )
    elif "Unavailable sub-agent executor" in summary:
        from fluxion.availability import available_executors

        installed = sorted(available_executors(settings))
        payload.update(
            {
                "error_code": "EXECUTOR_UNAVAILABLE",
                "suggestion": (
                    "This executor is enabled but its CLI is not installed. Choose "
                    "agent=auto or an installed executor, install the missing CLI, "
                    "then re-run detection from the Fluxion desktop Preferences."
                ),
                "available_agents": ["auto", *installed],
            }
        )
    elif "too many pending tasks" in summary:
        payload.update(
            {
                "error_code": "TOO_MANY_PENDING_TASKS",
                "suggestion": (
                    "Wait for in-flight runs to finish (poll get_task_status), or collect "
                    "results with get_task_result, before submitting more. The per-user "
                    "concurrency cap is FLUXION_MAX_PENDING_PER_USER."
                ),
            }
        )
    elif "Workspace" in summary or "workspace" in summary:
        payload.update(
            {
                "error_code": "WORKSPACE_NOT_AUTHORIZED",
                "suggestion": (
                    "Call list_projects and prefer run_subagent(project=...). "
                    "For path-based calls, configure FLUXION_TRUSTED_WORKSPACE_ROOTS "
                    "for read-only Git repos or FLUXION_WRITE_ALLOWED_WORKSPACES for writes."
                ),
                "configured_projects": sorted(settings.projects),
                "allowed_workspaces": [str(path) for path in settings.allowed_workspaces],
                "trusted_workspace_roots": [str(path) for path in settings.trusted_workspace_roots],
                "write_allowed_workspaces": [
                    str(path) for path in settings.write_allowed_workspaces
                ],
                "workspace_discovery": settings.workspace_discovery,
            }
        )
    return payload
