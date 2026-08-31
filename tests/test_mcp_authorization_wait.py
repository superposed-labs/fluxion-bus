from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from fluxion.config.settings.models import (
    WorkspaceAuthorization,
    WorkspaceAuthorizationError,
)
from fluxion.mcp_server import server as server_mod
from fluxion.mcp_server.server import run_subagent_tool, wait_for_authorization_tool
from fluxion.workspace import WorkspaceAccessService


class _Access:
    def __init__(self, request: dict) -> None:
        self.request = request
        self.timeouts: list[float] = []

    def wait_for_request(self, request_id: str, *, timeout_sec: float = 0.0) -> dict:
        self.timeouts.append(timeout_sec)
        return {"found": True, "authorization_request_id": request_id, **self.request}


def _call(request: dict, **kwargs) -> dict:
    access = _Access(request)
    view = wait_for_authorization_tool(
        workspace_access=access,
        authorization_request_id=kwargs.pop("authorization_request_id", "war-1"),
        **kwargs,
    )
    view["_timeouts"] = access.timeouts
    return view


def test_pending_wait_reports_a_timeout_not_a_refusal() -> None:
    view = _call({"status": "pending"}, wait_ms=10_000, max_wait_ms=1_000)

    assert view["status"] == "pending"
    assert view["terminal"] is False
    assert view["should_retry"] is False
    assert view["timed_out"] is True
    assert view["next_tools"] == ["wait_for_authorization"]
    # The client's per-call request timeout, not the caller's optimism, bounds the wait.
    assert view["_timeouts"] == [1.0]
    assert view["wait_cap_ms"] == 1_000


def test_approved_wait_hands_back_the_id_to_retry_with() -> None:
    view = _call({"status": "approved"})

    assert view["should_retry"] is True
    assert view["terminal"] is True
    assert view["timed_out"] is False
    assert view["retry_authorization_request_id"] == "war-1"
    assert view["next_tools"] == ["run_subagent"]


def test_project_allowed_wait_retries_without_the_request_id() -> None:
    view = _call({"status": "project-allowed"})

    assert view["should_retry"] is True
    assert view["retry_authorization_request_id"] is None
    assert "WITHOUT authorization_request_id" in view["next_action"]


def test_denied_wait_tells_the_caller_to_stop() -> None:
    view = _call({"status": "denied"})

    assert view["terminal"] is True
    assert view["should_retry"] is False
    assert view["next_tools"] == []
    assert "Do not retry" in view["next_action"]


def test_expired_and_consumed_waits_are_terminal_but_re_requestable() -> None:
    for status in ("expired", "consumed", "active", "not-found"):
        view = _call({"status": status})
        assert view["terminal"] is True, status
        assert view["should_retry"] is False, status


def test_missing_id_and_missing_service_do_not_raise() -> None:
    blank = wait_for_authorization_tool(
        workspace_access=_Access({"status": "pending"}), authorization_request_id="  "
    )
    assert blank["status"] == "not-found"
    assert blank["should_retry"] is False

    unavailable = wait_for_authorization_tool(
        workspace_access=None, authorization_request_id="war-1", wait_ms=5_000
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["terminal"] is True


def test_invalid_id_is_reported_instead_of_raising() -> None:
    class _Strict:
        def wait_for_request(self, request_id: str, *, timeout_sec: float = 0.0) -> dict:
            raise ValueError("Invalid authorization_request_id")

    view = wait_for_authorization_tool(
        workspace_access=_Strict(), authorization_request_id="../../etc/passwd"
    )
    assert view["status"] == "not-found"
    assert view["should_retry"] is False


def test_real_service_round_trip_from_rejection_to_retry(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    service = WorkspaceAccessService(
        SimpleNamespace(
            workspace_root=tmp_path,
            data_dir=tmp_path / "data",
            allowed_workspaces=[authorized],
            denied_workspaces=[],
            write_allowed_workspaces=[],
            trusted_workspace_roots=[],
            workspace_discovery=False,
            projects={},
        ),
        notification_queue=lambda request: None,
    )
    rejected = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    request_id = rejected.authorization_request_id

    started = time.monotonic()
    waited = wait_for_authorization_tool(
        workspace_access=service,
        authorization_request_id=request_id,
        wait_ms=10_000,
        max_wait_ms=200,
    )
    assert waited["status"] == "pending"
    assert waited["timed_out"] is True
    # The cap is honoured: a human-scale wait never holds one MCP call open.
    assert time.monotonic() - started < 3

    service.approve_request(request_id)
    approved = wait_for_authorization_tool(
        workspace_access=service, authorization_request_id=request_id, wait_ms=1_000
    )
    assert approved["should_retry"] is True

    retried = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        authorization_request_id=approved["retry_authorization_request_id"],
    )
    assert retried.allowed is True


# ── inline wait inside run_subagent ──────────────────────────────────


class _PendingRunner:
    """Rejects the first submit, then accepts the replay."""

    def __init__(self, decision: dict, *, reject_always: bool = False) -> None:
        self.workspace_access = _DecidingAccess(decision)
        self.reject_always = reject_always
        self.submits: list[str | None] = []

    def submit(self, request):
        self.submits.append(request.authorization_request_id)
        if len(self.submits) == 1 or self.reject_always:
            authorization = WorkspaceAuthorization(
                allowed=False,
                reason="Workspace is not authorized",
                policy="not-authorized",
                workspace=Path("/tmp/project"),
                authorization_request_id="war-1",
                pending=True,
                pending_status="pending",
                client_id="mcp",
            )
            raise WorkspaceAuthorizationError(authorization)
        return _Handle()


class _DecidingAccess:
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.waits: list[float] = []

    def wait_for_request(self, request_id: str, *, timeout_sec: float = 0.0) -> dict:
        self.waits.append(timeout_sec)
        return {"found": True, "authorization_request_id": request_id, **self.decision}


class _Handle:
    workspace = "/tmp/project"
    adapter = None

    def to_payload(self) -> dict:
        return {"run_id": "r1", "task_id": "r1", "executor": "antigravity"}


def _run(runner, **kwargs) -> dict:
    return run_subagent_tool(
        runner=runner,
        settings=SimpleNamespace(
            projects={},
            allowed_workspaces=[],
            trusted_workspace_roots=[],
            write_allowed_workspaces=[],
            workspace_discovery=False,
            task_timeout_sec=60,
        ),
        prompt="do it",
        agent="antigravity",
        project="",
        workspace="/tmp/project",
        thread="",
        task_name="t",
        parent_path="/root",
        profile="implement",
        mode="workspace-write",
        **kwargs,
    )


def test_approval_during_the_inline_wait_replays_the_task(monkeypatch) -> None:
    monkeypatch.setattr(server_mod, "reset_cache", lambda: None)
    runner = _PendingRunner({"status": "approved"})

    payload = _run(runner, authorization_wait_ms=5_000)

    # The user clicked while the call was waiting, so it just runs — no
    # follow-up call and no round trip through the caller.
    assert payload["run_id"] == "r1"
    assert runner.submits == [None, "war-1"]
    assert runner.workspace_access.waits == [5.0]


def test_project_approval_during_the_inline_wait_replays_without_the_id(monkeypatch) -> None:
    monkeypatch.setattr(server_mod, "reset_cache", lambda: None)
    runner = _PendingRunner({"status": "project-allowed"})

    payload = _run(runner, authorization_wait_ms=5_000)

    assert payload["run_id"] == "r1"
    # The request id is closed once it became a permanent project.
    assert runner.submits == [None, None]


def test_no_answer_during_the_inline_wait_returns_the_pending_payload() -> None:
    runner = _PendingRunner({"status": "pending"}, reject_always=True)

    payload = _run(runner, authorization_wait_ms=1_000)

    assert payload["error_code"] == "WORKSPACE_NOT_AUTHORIZED"
    assert payload["authorization_state"] == "pending"
    assert payload["next_tools"] == ["wait_for_authorization", "run_subagent"]
    assert runner.submits == [None]


def test_refusal_during_the_inline_wait_is_reported_as_denied() -> None:
    runner = _PendingRunner({"status": "denied"}, reject_always=True)

    payload = _run(runner, authorization_wait_ms=1_000)

    # Without this the payload would still say "pending" and send the caller off
    # to wait for a decision that was already made against it.
    assert payload["authorization_state"] == "denied"
    assert payload["authorization_terminal"] is True
    assert payload["retryable"] is False
    assert payload["next_tools"] == []


def test_inline_wait_is_off_by_default_for_non_mcp_callers() -> None:
    runner = _PendingRunner({"status": "approved"}, reject_always=True)

    payload = _run(runner)

    # The Web API relies on this: no worker is held waiting for a human.
    assert payload["authorization_state"] == "pending"
    assert runner.workspace_access.waits == []
