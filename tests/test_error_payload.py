from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fluxion.config.settings.models import WorkspaceAuthorization, WorkspaceAuthorizationError
from fluxion.mcp_server.payloads import _error_payload


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        projects={},
        allowed_workspaces=[],
        trusted_workspace_roots=[],
        write_allowed_workspaces=[],
        workspace_discovery=True,
    )


def _payload(error: Exception) -> dict:
    return _error_payload(
        error=error,
        agent="codex",
        project="",
        workspace="/tmp/x",
        thread="",
        task_name="t",
        parent_path="/root",
        profile="inspect",
        mode="read-only",
        settings=_settings(),
    )


def test_pending_cap_rejection_is_structured() -> None:
    payload = _payload(RuntimeError("too many pending tasks for user (limit=3)"))

    assert payload["error_code"] == "TOO_MANY_PENDING_TASKS"
    assert "FLUXION_MAX_PENDING_PER_USER" in payload["suggestion"]
    assert payload["accepted"] is False


def test_workspace_rejection_still_structured() -> None:
    payload = _payload(RuntimeError("Workspace-write runs require a registered project"))

    assert payload["error_code"] == "WORKSPACE_NOT_AUTHORIZED"


def test_workspace_rejection_keeps_retryable_request_metadata() -> None:
    authorization = WorkspaceAuthorization(
        allowed=False,
        reason="Workspace is not authorized",
        policy="not-authorized",
        workspace=Path("/tmp/project"),
        authorization_request_id="war-test",
        pending=True,
        pending_status="pending",
        client_id="mcp",
    )
    payload = _payload(WorkspaceAuthorizationError(authorization))

    assert payload["error_code"] == "WORKSPACE_NOT_AUTHORIZED"
    assert payload["authorization_request_id"] == "war-test"
    assert payload["pending"] is True
    assert payload["pending_status"] == "pending"


def _workspace_payload(policy: str, *, pending: bool, status: str) -> dict:
    authorization = WorkspaceAuthorization(
        allowed=False,
        reason="Workspace is not authorized",
        policy=policy,
        workspace=Path("/tmp/project"),
        authorization_request_id="war-test",
        pending=pending,
        pending_status=status,
        client_id="mcp",
    )
    return _payload(WorkspaceAuthorizationError(authorization))


def test_pending_rejection_points_at_the_wait_tool_not_at_a_blind_retry() -> None:
    payload = _workspace_payload("not-authorized", pending=True, status="pending")

    assert payload["authorization_state"] == "pending"
    assert payload["authorization_terminal"] is False
    assert payload["retryable"] is True
    assert payload["next_tools"] == ["wait_for_authorization", "run_subagent"]
    retry = payload["authorization_retry"]
    assert retry["wait_tool"] == "wait_for_authorization"
    assert retry["authorization_request_id"] == "war-test"
    # The wording that matters: the model must keep waiting inside its own turn
    # rather than ending it to ask the user to report back (which is what a
    # "tell the user, then wait" ordering actually produced).
    assert "Do NOT end your turn" in retry["instruction"]
    assert "wait_ms=60000" in retry["instruction"]


def test_denied_rejection_is_marked_terminal_and_not_retryable() -> None:
    payload = _workspace_payload("one-time-denied", pending=False, status="denied")

    # Still 403-shaped for the Web API, but a caller can now tell a refusal
    # apart from a decision the user has not made yet.
    assert payload["error_code"] == "WORKSPACE_NOT_AUTHORIZED"
    assert payload["authorization_state"] == "denied"
    assert payload["authorization_terminal"] is True
    assert payload["retryable"] is False
    assert payload["next_tools"] == []
    assert "authorization_retry" not in payload
    assert "declined" in payload["suggestion"]


def test_finished_one_time_grant_asks_for_a_fresh_request() -> None:
    payload = _workspace_payload("one-time-consumed", pending=False, status="consumed")

    assert payload["authorization_state"] == "consumed"
    assert payload["authorization_terminal"] is True
    assert payload["retryable"] is True
    assert "WITHOUT authorization_request_id" in payload["suggestion"]
