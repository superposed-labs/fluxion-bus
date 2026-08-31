from __future__ import annotations

from typing import Any

from fluxion.config.settings import Settings
from fluxion.workspace import RETRYABLE_REQUEST_STATUSES


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
    client_id: str = "",
    authorization_request_id: str = "",
    settings: Settings,
    workspace_access: Any | None = None,
) -> dict[str, Any]:
    summary = str(error)
    authorization = getattr(error, "authorization", None)
    structured_request_id = str(
        getattr(authorization, "authorization_request_id", "") or authorization_request_id or ""
    )
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
        "client_id": client_id,
        "authorization_request_id": structured_request_id or None,
        "authorization_scope": str(getattr(authorization, "authorization_scope", "") or "") or None,
        "authorization_expires_at": str(
            getattr(authorization, "authorization_expires_at", "") or ""
        )
        or None,
        "pending": bool(getattr(authorization, "pending", False)),
        "pending_status": str(getattr(authorization, "pending_status", "") or "") or None,
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
    elif authorization is not None or "Workspace" in summary or "workspace" in summary:
        projects = getattr(settings, "projects", {})
        allowed_workspaces = getattr(settings, "allowed_workspaces", [])
        trusted_workspace_roots = getattr(settings, "trusted_workspace_roots", [])
        write_allowed_workspaces = getattr(settings, "write_allowed_workspaces", [])
        workspace_access = workspace_access or getattr(settings, "workspace_access", None)
        payload.update(
            {
                "error_code": "WORKSPACE_NOT_AUTHORIZED",
                "suggestion": (
                    "The request is visible in Fluxion Preferences. Approve it there for "
                    "this exact client, path, and mode, then retry with the returned "
                    "authorization_request_id. Permanent access is managed in Project Permissions."
                ),
                "configured_projects": sorted(projects),
                "allowed_workspaces": [str(path) for path in allowed_workspaces],
                "trusted_workspace_roots": [str(path) for path in trusted_workspace_roots],
                "write_allowed_workspaces": [str(path) for path in write_allowed_workspaces],
                "workspace_discovery": getattr(settings, "workspace_discovery", False),
            }
        )
        if authorization is not None:
            payload.update(
                {
                    "authorization_policy": str(getattr(authorization, "policy", "") or ""),
                    "authorization_access": str(getattr(authorization, "access", "") or ""),
                    "authorization_source": str(getattr(authorization, "source", "") or ""),
                    "authorization_scope": str(
                        getattr(authorization, "authorization_scope", "") or "task"
                    ),
                    "authorization_expires_at": str(
                        getattr(authorization, "authorization_expires_at", "") or ""
                    )
                    or None,
                    "pending": bool(getattr(authorization, "pending", False)),
                    "pending_status": str(getattr(authorization, "pending_status", "") or "")
                    or None,
                }
            )
        if workspace_access is not None:
            payload["workspace_access_config_path"] = str(
                getattr(workspace_access, "config_path", "") or ""
            )
        payload.update(
            _authorization_next_step(
                authorization=authorization,
                structured_request_id=structured_request_id,
            )
        )
    return payload


def _authorization_next_step(*, authorization: Any, structured_request_id: str) -> dict[str, Any]:
    """Tell the caller what to do next about a workspace rejection.

    Waiting for a human decision and being refused by that human are different
    outcomes, and a caller that cannot tell them apart retries a refusal as if
    it were a transient failure. `authorization_state` is the discriminator;
    `error_code` stays WORKSPACE_NOT_AUTHORIZED so the Web API keeps mapping the
    whole family to 403.
    """
    policy = str(getattr(authorization, "policy", "") or "")
    pending = bool(getattr(authorization, "pending", False))
    if structured_request_id and pending:
        return {
            "authorization_state": "pending",
            "authorization_terminal": False,
            "retryable": True,
            "next_tools": ["wait_for_authorization", "run_subagent"],
            "authorization_retry": {
                "authorization_request_id": structured_request_id,
                "wait_tool": "wait_for_authorization",
                "instruction": (
                    "Do NOT end your turn here. Call wait_for_authorization now with "
                    "this authorization_request_id and wait_ms=60000; the user "
                    "approves the Fluxion notification while that call is waiting. "
                    "Repeat the wait if it comes back still pending, and mention that "
                    "you are waiting on their approval only as you keep waiting — "
                    "ending the turn to ask the user to report back leaves the task "
                    "undone. When the wait reports status=approved, retry this same "
                    "task with this authorization_request_id; on "
                    "status=project-allowed, retry it with no authorization_request_id. "
                    "The approval is task-scoped and ends when that task returns, "
                    "fails, or is canceled. An immediate retry without waiting cannot "
                    "succeed."
                ),
            },
        }
    if policy == "one-time-denied":
        return {
            "authorization_state": "denied",
            "authorization_terminal": True,
            "retryable": False,
            "next_tools": [],
            "suggestion": (
                "The user declined this workspace authorization request. Do not retry "
                "it and do not raise the same request again: report the refusal and "
                "ask the user how they want to proceed."
            ),
        }
    if policy in {"one-time-expired", "one-time-consumed", "one-time-in-use"}:
        return {
            "authorization_state": policy.removeprefix("one-time-"),
            "authorization_terminal": True,
            "retryable": policy != "one-time-in-use",
            "next_tools": ["run_subagent"],
            "suggestion": (
                "This task authorization is no longer usable: an approval covers one "
                "task and is retired when that task ends. Submit the task again "
                "WITHOUT authorization_request_id to raise a fresh request, then wait "
                "for it with wait_for_authorization."
            ),
        }
    return {
        "authorization_state": "not-authorized",
        "authorization_terminal": False,
        "retryable": True,
        "next_tools": ["run_subagent"],
    }


def _authorization_wait_view(
    request: dict[str, Any], *, waited_ms: int, wait_cap_ms: int
) -> dict[str, Any]:
    """Turn a stored authorization request into a decision the caller can act on.

    Every branch answers the same two questions explicitly — is this still open,
    and may the original task be retried — so a waiting caller never has to infer
    "the user refused" from the same shape as "the user has not clicked yet".
    """
    status = str(request.get("status") or "").strip() or "not-found"
    if status == "pending":
        next_action = (
            "Still undecided. Call wait_for_authorization again with the same id to keep "
            "waiting rather than ending your turn; the user approves the Fluxion "
            "notification while you wait. Do not retry the task yet."
        )
        next_tools = ["wait_for_authorization"]
    elif status == "approved":
        next_action = (
            "Approved. Retry the original run_subagent now, passing this "
            "authorization_request_id unchanged. The grant covers that one task and is "
            "retired when it ends."
        )
        next_tools = ["run_subagent"]
    elif status == "project-allowed":
        next_action = (
            "The user granted this workspace permanently as a Fluxion project. Retry the "
            "original run_subagent WITHOUT authorization_request_id; this request id is "
            "closed."
        )
        next_tools = ["run_subagent", "list_projects"]
    elif status == "denied":
        next_action = (
            "The user declined this request. Do not retry it and do not raise the same "
            "request again: report the refusal and ask the user how they want to proceed."
        )
        next_tools = []
    elif status == "expired":
        next_action = (
            "The request expired before it was answered. If the user still wants this "
            "task, submit run_subagent again WITHOUT authorization_request_id to raise a "
            "fresh request."
        )
        next_tools = ["run_subagent"]
    elif status == "active":
        next_action = (
            "This approval is already attached to a running task and cannot be reused. "
            "Follow that task with get_task_status instead of retrying."
        )
        next_tools = ["get_task_status", "list_subagent_runs"]
    elif status == "consumed":
        next_action = (
            "This approval was already used and its task has ended. Submit run_subagent "
            "again WITHOUT authorization_request_id to raise a fresh request."
        )
        next_tools = ["run_subagent"]
    else:
        next_action = (
            "No such authorization request: it may have been cleared, or the id may be "
            "wrong. Submit run_subagent again WITHOUT authorization_request_id to raise a "
            "fresh request."
        )
        next_tools = ["run_subagent"]
    view: dict[str, Any] = {
        **request,
        "status": status,
        "pending": status == "pending",
        "terminal": status != "pending",
        "should_retry": status in RETRYABLE_REQUEST_STATUSES,
        "retry_authorization_request_id": (
            str(request.get("authorization_request_id") or "") if status == "approved" else None
        ),
        "waited_ms": waited_ms,
        "wait_cap_ms": wait_cap_ms,
        "timed_out": status == "pending" and wait_cap_ms > 0,
        "next_action": next_action,
        "next_tools": next_tools,
    }
    return view
