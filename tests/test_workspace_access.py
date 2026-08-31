from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from fluxion.config.settings.models import ProjectConfig
from fluxion.utils import macos_notify
from fluxion.workspace import WorkspaceAccessService


def _settings(
    *,
    root: Path,
    data_dir: Path,
    allowed: list[Path] | None = None,
    denied: list[Path] | None = None,
    write_allowed: list[Path] | None = None,
    trusted: list[Path] | None = None,
    discovery: bool = False,
    projects: dict[str, ProjectConfig] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=root,
        data_dir=data_dir,
        allowed_workspaces=allowed or [],
        denied_workspaces=denied or [],
        write_allowed_workspaces=write_allowed or [],
        trusted_workspace_roots=trusted or [],
        workspace_discovery=discovery,
        projects=projects or {},
    )


def test_legacy_sources_are_effective_and_visible(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "FLUXION_ALLOWED_WORKSPACES",
        "FLUXION_PROJECTS",
        "FLUXION_PROJECTS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    project_root = tmp_path / "project"
    allowed_root = tmp_path / "allowed"
    project_root.mkdir()
    allowed_root.mkdir()
    settings = _settings(
        root=tmp_path,
        data_dir=data_dir,
        allowed=[allowed_root],
        projects={"demo": ProjectConfig("demo", project_root, "codex")},
    )

    service = WorkspaceAccessService(settings)
    state = service.list_workspaces()
    by_path = {row["path"]: row for row in state["workspaces"]}

    assert by_path[str(project_root.resolve())]["source"] == "legacy:FLUXION_PROJECTS:demo"
    assert by_path[str(allowed_root.resolve())]["source"] == "legacy:FLUXION_ALLOWED_WORKSPACES"
    assert "FLUXION_PROJECTS" in state["runtime_context"]["legacy_sources"]
    assert service.authorize_run_workspace(raw_workspace=str(project_root)).allowed


def test_app_entry_is_atomic_0600_and_delete_reports_legacy_permission(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    root = tmp_path / "root"
    root.mkdir()
    settings = _settings(root=root, data_dir=data_dir, allowed=[root])
    service = WorkspaceAccessService(settings)

    entry = service.create_entry(path=root, key="managed", access="read-write")
    config_path = data_dir / "config" / "workspace_access.json"
    assert config_path.exists()
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert entry["managed"] is True

    deleted = service.delete_entry(entry["id"])
    assert deleted["deleted"] is True
    assert deleted["removed_access"] == "read-write"
    assert deleted["permission_still_effective"] is True
    assert deleted["remaining_access"] == "read-write"
    assert deleted["remaining_root"] == str(root.resolve())
    assert "another configuration" in deleted["message"]


def test_delete_reports_trusted_git_read_as_remaining_permission(tmp_path: Path) -> None:
    trusted = tmp_path / "Developer"
    target = trusted / "demo"
    allowed = tmp_path / "allowed"
    target.mkdir(parents=True)
    (target / ".git").mkdir()
    allowed.mkdir()
    service = WorkspaceAccessService(
        _settings(
            root=tmp_path,
            data_dir=tmp_path / "data",
            allowed=[allowed],
            trusted=[trusted],
            discovery=True,
        )
    )
    entry = service.create_entry(path=target, key="demo", access="read-write")

    deleted = service.delete_entry(entry["id"])

    assert deleted["permission_still_effective"] is True
    assert deleted["remaining_access"] == "read-only"
    assert deleted["remaining_policy"] == "trusted-git-read"
    assert deleted["remaining_source"] == "legacy:trusted_workspace_roots"
    assert deleted["remaining_root"] == str(trusted.resolve())


def test_denied_roots_win_over_app_and_legacy_grants(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    denied = allowed / "private"
    denied.mkdir(parents=True)
    data_dir = tmp_path / "data"
    settings = _settings(root=tmp_path, data_dir=data_dir, allowed=[allowed], denied=[denied])
    service = WorkspaceAccessService(settings, notification_queue=lambda request: None)
    service.create_entry(path=denied, key="private", access="read-write")

    authorization = service.authorize_run_workspace(
        raw_workspace=str(denied), mode="workspace-write", client_id="mcp"
    )
    assert authorization.allowed is False
    assert authorization.policy == "denied"
    assert authorization.pending is False
    denied_row = next(
        row
        for row in service.list_workspaces()["workspaces"]
        if row["path"] == str(denied.resolve())
    )
    assert denied_row["status"] == "denied"


def test_symlink_cannot_escape_a_configured_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "link"
    link.symlink_to(outside, target_is_directory=True)
    settings = _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[allowed])
    service = WorkspaceAccessService(settings, notification_queue=lambda request: None)

    authorization = service.authorize_run_workspace(raw_workspace=str(link), client_id="mcp")
    assert authorization.allowed is False
    assert authorization.workspace == outside.resolve()
    assert authorization.pending is True


def test_trusted_root_is_not_a_direct_read_grant(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    plain_directory = trusted / "plain"
    git_repository = trusted / "repo"
    plain_directory.mkdir(parents=True)
    git_repository.mkdir()
    (git_repository / ".git").mkdir()
    service = WorkspaceAccessService(
        _settings(
            root=tmp_path / "other",
            data_dir=tmp_path / "data",
            trusted=[trusted],
            discovery=False,
        ),
        notification_queue=lambda request: None,
    )

    disabled = service.authorize_run_workspace(raw_workspace=str(git_repository))
    assert disabled.allowed is False
    assert disabled.policy == "trusted-root-not-discovered"

    service._settings.workspace_discovery = True
    plain = service.authorize_run_workspace(raw_workspace=str(plain_directory))
    assert plain.allowed is False
    assert plain.policy == "trusted-root-not-discovered"

    discovered = service.authorize_run_workspace(raw_workspace=str(git_repository))
    assert discovered.allowed is True
    assert discovered.policy == "trusted-git-read"


def test_config_changes_are_hot_loaded_without_rebuilding_service(tmp_path: Path) -> None:
    root = tmp_path / "root"
    new_root = tmp_path / "new-root"
    root.mkdir()
    new_root.mkdir()
    data_dir = tmp_path / "data"
    current = {"settings": _settings(root=root, data_dir=data_dir, allowed=[root])}
    service = WorkspaceAccessService(
        current["settings"],
        settings_loader=lambda: current["settings"],
        notification_queue=lambda request: None,
    )

    before = service.authorize_run_workspace(raw_workspace=str(new_root), client_id="web")
    assert before.pending is True
    service.store.save_entries(
        [
            {
                "id": "app-new",
                "key": "new",
                "path": str(new_root),
                "access": "read-write",
            }
        ]
    )
    after = service.authorize_run_workspace(raw_workspace=str(new_root), mode="workspace-write")
    assert after.allowed is True
    assert after.policy == "app"


def test_app_entry_key_is_a_project_and_preserves_read_only_access(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    service = WorkspaceAccessService(
        _settings(root=root, data_dir=tmp_path / "data"),
        notification_queue=lambda request: None,
    )
    service.create_entry(
        path=target,
        key="target",
        access="read-only",
        default_executor="codex",
    )

    project = service.resolve_project("target")
    assert project is not None
    assert project.workspace == target.resolve()
    assert project.default_executor == "codex"
    assert (
        service.authorize_run_workspace(
            raw_workspace=None,
            project_key="target",
        ).allowed
        is True
    )

    write = service.authorize_run_workspace(
        raw_workspace=None,
        project_key="target",
        mode="workspace-write",
        client_id="mcp",
    )
    assert write.allowed is False
    assert write.policy == "write-not-authorized"


def test_separate_service_instances_do_not_lose_concurrent_entries(tmp_path: Path) -> None:
    root = tmp_path / "root"
    first = tmp_path / "first"
    second = tmp_path / "second"
    root.mkdir()
    first.mkdir()
    second.mkdir()
    settings = _settings(root=root, data_dir=tmp_path / "data")
    services = [WorkspaceAccessService(settings), WorkspaceAccessService(settings)]
    errors: list[Exception] = []

    def create(service: WorkspaceAccessService, path: Path, key: str) -> None:
        try:
            service.create_entry(path=path, key=key)
        except Exception as error:  # pragma: no cover - asserted below.
            errors.append(error)

    threads = [
        Thread(target=create, args=(services[0], first, "first")),
        Thread(target=create, args=(services[1], second, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    keys = {row["key"] for row in services[0].list_workspaces()["workspaces"] if row["managed"]}
    assert keys == {"first", "second"}


def test_one_time_grant_is_exact_single_use_and_expires(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    authorized.mkdir()
    target.mkdir()
    now = [datetime(2026, 8, 30, tzinfo=UTC)]
    notices: list[str] = []
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        clock=lambda: now[0],
        one_time_ttl_sec=60,
        notification_queue=lambda request: notices.append(request.request_id),
    )

    pending = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    duplicate = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    assert pending.pending is True
    assert duplicate.authorization_request_id == pending.authorization_request_id
    assert notices == [pending.authorization_request_id]

    request_id = pending.authorization_request_id
    assert service.approve_request(request_id)["approved"] is True
    wrong_mode = service.authorize_run_workspace(
        raw_workspace=str(target),
        mode="workspace-write",
        client_id="mcp",
        authorization_request_id=request_id,
    )
    assert wrong_mode.allowed is False
    assert wrong_mode.policy == "one-time-mismatch"

    allowed_once = service.authorize_run_workspace(
        raw_workspace=str(target), client_id="mcp", authorization_request_id=request_id
    )
    assert allowed_once.allowed is True
    assert allowed_once.policy == "one-time"
    reused = service.authorize_run_workspace(
        raw_workspace=str(target), client_id="mcp", authorization_request_id=request_id
    )
    assert reused.allowed is False
    assert reused.policy == "one-time-consumed"

    pending_again = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    now[0] += timedelta(seconds=61)
    expired = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        authorization_request_id=pending_again.authorization_request_id,
    )
    assert expired.allowed is False
    assert expired.policy == "one-time-expired"


def test_workspace_request_queues_structured_macos_notification(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(macos_notify.sys, "platform", "darwin")
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    data_dir = tmp_path / "data"
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=data_dir, allowed=[authorized])
    )

    pending = service.authorize_run_workspace(
        raw_workspace=str(target),
        mode="workspace-write",
        client_id="codex",
    )

    records = [
        json.loads(line) for line in (data_dir / macos_notify.FILENAME).read_text().splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "workspace_access_request"
    assert record["authorization_request_id"] == pending.authorization_request_id
    assert record["client_id"] == "codex"
    assert record["workspace"] == str(target.resolve())
    assert record["mode"] == "workspace-write"
    assert record["title"] == "Workspace access requested"
    assert record["timestamp"]


def test_one_time_approval_validates_notification_context(tmp_path: Path) -> None:
    target = tmp_path / "target"
    other = tmp_path / "other"
    authorized = tmp_path / "authorized"
    target.mkdir()
    other.mkdir()
    authorized.mkdir()
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        notification_queue=lambda request: None,
    )
    pending = service.authorize_run_workspace(
        raw_workspace=str(target),
        mode="workspace-write",
        client_id="codex",
    )
    request_id = pending.authorization_request_id

    assert service.approve_request(request_id, path=other)["status"] == "mismatch"
    assert (
        service.approve_request(request_id, path=target, mode="read-only")["status"] == "mismatch"
    )
    assert (
        service.approve_request(
            request_id,
            path=target,
            mode="workspace-write",
            client_id="claude",
        )["status"]
        == "mismatch"
    )
    approved = service.approve_request(
        request_id,
        path=target,
        mode="workspace-write",
        client_id="codex",
    )
    assert approved["approved"] is True


def test_one_time_approval_cannot_override_a_new_deny(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    authorized = tmp_path / "authorized"
    authorized.mkdir()
    settings = _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized])
    service = WorkspaceAccessService(settings, notification_queue=lambda request: None)
    pending = service.authorize_run_workspace(raw_workspace=str(target), client_id="web")
    settings.denied_workspaces = [target]

    result = service.approve_request(pending.authorization_request_id)
    assert result["approved"] is False
    assert result["status"] == "denied"
    retry = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="web",
        authorization_request_id=pending.authorization_request_id,
    )
    assert retry.allowed is False
    assert retry.policy == "denied"


def test_task_scoped_approval_is_bound_and_released_with_task(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        one_time_ttl_sec=24 * 60 * 60,
        notification_queue=lambda request: None,
    )

    pending = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    request_id = pending.authorization_request_id
    assert service.approve_request(request_id)["approved"] is True

    authorization = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        task_scope_id="task-scope-1",
    )
    assert authorization.allowed is True
    assert authorization.authorization_request_id == request_id
    assert authorization.authorization_grant_id
    assert authorization.authorization_scope == "task"
    assert service.bind_task_authorization(authorization.authorization_grant_id, "task-1")

    duplicate = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        authorization_request_id=request_id,
        task_scope_id="task-scope-2",
    )
    assert duplicate.allowed is False
    assert duplicate.policy == "one-time-in-use"

    assert service.approve_request(request_id)["status"] == "active"
    assert service.deny_request(request_id)["status"] == "active"

    assert service.complete_task_authorization(
        authorization.authorization_grant_id, task_id="task-1"
    )
    ended = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        authorization_request_id=request_id,
        task_scope_id="task-scope-2",
    )
    assert ended.allowed is False
    assert ended.policy == "one-time-consumed"


def test_allow_request_as_project_persists_entry_and_closes_request(tmp_path: Path) -> None:
    target = tmp_path / "target-project"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        notification_queue=lambda request: None,
    )

    pending = service.authorize_run_workspace(
        raw_workspace=str(target), mode="workspace-write", client_id="codex"
    )
    result = service.allow_request_as_project(
        pending.authorization_request_id,
        path=target,
        mode="workspace-write",
        client_id="codex",
    )
    assert result["project_allowed"] is True
    assert result["entry"]["key"] == "target-project"
    assert result["request"]["status"] == "project-allowed"
    assert service.authorize_run_workspace(
        raw_workspace=str(target), mode="workspace-write", client_id="codex"
    ).allowed
    assert (
        next(
            row
            for row in service.list_workspaces()["pending_requests"]
            if row["request_id"] == pending.authorization_request_id
        )["status"]
        == "project-allowed"
    )

    # The notification action is safe to repeat if the user clicks it twice.
    repeated = service.allow_request_as_project(
        pending.authorization_request_id,
        path=target,
        mode="workspace-write",
        client_id="codex",
    )
    assert repeated["project_allowed"] is True


def test_denied_request_is_terminal_and_is_not_renotified(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    notices: list[str] = []
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        notification_queue=lambda request: notices.append(request.request_id),
    )

    pending = service.authorize_run_workspace(raw_workspace=str(target), client_id="mcp")
    request_id = pending.authorization_request_id
    assert service.deny_request(request_id)["denied"] is True

    refused = service.authorize_run_workspace(
        raw_workspace=str(target),
        client_id="mcp",
        authorization_request_id=request_id,
    )

    # A refusal is a decision: it must read as terminal rather than as "not yet",
    # and it must not ring the user's Notification Center a second time.
    assert refused.allowed is False
    assert refused.policy == "one-time-denied"
    assert refused.pending is False
    assert refused.pending_status == "denied"
    assert notices == [request_id]


def test_wait_for_request_returns_pending_and_then_sees_the_approval(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        notification_queue=lambda request: None,
    )
    request_id = service.authorize_run_workspace(
        raw_workspace=str(target), client_id="mcp"
    ).authorization_request_id

    assert service.get_request("war-does-not-exist")["status"] == "not-found"

    unanswered = service.wait_for_request(request_id, timeout_sec=0.05, poll_interval=0.01)
    assert unanswered["status"] == "pending"
    assert unanswered["found"] is True

    def approve_late() -> None:
        time.sleep(0.05)
        service.approve_request(request_id)

    approver = Thread(target=approve_late)
    approver.start()
    try:
        answered = service.wait_for_request(request_id, timeout_sec=5, poll_interval=0.01)
    finally:
        approver.join()

    assert answered["status"] == "approved"
    assert answered["pending"] is False


def test_get_request_expires_an_unanswered_pending_request(tmp_path: Path) -> None:
    target = tmp_path / "target"
    authorized = tmp_path / "authorized"
    target.mkdir()
    authorized.mkdir()
    now = [datetime(2026, 8, 30, tzinfo=UTC)]
    service = WorkspaceAccessService(
        _settings(root=tmp_path, data_dir=tmp_path / "data", allowed=[authorized]),
        clock=lambda: now[0],
        one_time_ttl_sec=60,
        notification_queue=lambda request: None,
    )
    request_id = service.authorize_run_workspace(
        raw_workspace=str(target), client_id="mcp"
    ).authorization_request_id

    assert service.get_request(request_id)["status"] == "pending"
    now[0] += timedelta(seconds=61)

    # Expiry is applied on read so a waiting caller stops waiting instead of
    # polling a request that can never be approved.
    assert service.get_request(request_id)["status"] == "expired"
