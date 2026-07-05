from __future__ import annotations

from types import SimpleNamespace

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
