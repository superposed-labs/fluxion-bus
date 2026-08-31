from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workspace_client(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    install = tmp_path / "install"
    allowed = tmp_path / "allowed"
    target = tmp_path / "target"
    data_dir.mkdir()
    install.mkdir()
    allowed.mkdir()
    target.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FLUXION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(install))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", str(allowed))
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))

    from fluxion.web import deps
    from fluxion.web.services import aggregator

    deps.get_settings.cache_clear()
    deps.get_schedule_store.cache_clear()
    deps.get_usage_service.cache_clear()
    deps.get_usage_history_service.cache_clear()
    aggregator.reset_cache()
    from fluxion.web.server import create_app

    with TestClient(create_app()) as client:
        yield client, allowed, target, data_dir

    deps.get_settings.cache_clear()
    deps.get_schedule_store.cache_clear()
    deps.get_usage_service.cache_clear()
    deps.get_usage_history_service.cache_clear()
    aggregator.reset_cache()


def test_workspace_api_lists_legacy_and_supports_app_crud(workspace_client) -> None:
    client, allowed, _target, data_dir = workspace_client
    listing = client.get("/api/workspaces")
    assert listing.status_code == 200
    legacy = next(
        row for row in listing.json()["workspaces"] if row["path"] == str(allowed.resolve())
    )
    assert "FLUXION_ALLOWED_WORKSPACES" in legacy["source"]
    assert listing.json()["config_path"] == str(data_dir / "config" / "workspace_access.json")

    created = client.post(
        "/api/workspaces",
        json={"path": str(allowed), "key": "managed", "access": "read-only"},
    )
    assert created.status_code == 201
    entry = created.json()
    assert entry["managed"] is True
    assert entry["access"] == "read-only"

    updated = client.put(
        f"/api/workspaces/{entry['id']}",
        json={"access": "read-write", "description": "build repo"},
    )
    assert updated.status_code == 200
    assert updated.json()["access"] == "read-write"

    deleted = client.delete(f"/api/workspaces/{entry['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["removed_access"] == "read-write"
    assert deleted.json()["permission_still_effective"] is True
    assert deleted.json()["remaining_access"] == "read-write"
    assert deleted.json()["remaining_root"] == str(allowed.resolve())


def test_workspace_api_exposes_pending_request_and_approval(workspace_client) -> None:
    client, _allowed, target, _data_dir = workspace_client
    from fluxion.config.settings import Settings
    from fluxion.workspace import WorkspaceAccessService

    service = WorkspaceAccessService(Settings.reload(), notification_queue=lambda request: None)
    pending = service.authorize_run_workspace(raw_workspace=str(target), client_id="web")
    assert pending.pending is True

    requests = client.get("/api/workspaces/requests")
    assert requests.status_code == 200
    assert (
        requests.json()["requests"][0]["authorization_request_id"]
        == pending.authorization_request_id
    )

    approved = client.post(
        f"/api/workspaces/requests/{pending.authorization_request_id}/approve",
        json={"path": str(target), "mode": "read-only", "client_id": "web"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True


def test_workspace_api_reads_one_request_without_listing_the_others(workspace_client) -> None:
    client, _allowed, target, _data_dir = workspace_client
    from fluxion.config.settings import Settings
    from fluxion.workspace import WorkspaceAccessService

    service = WorkspaceAccessService(Settings.reload(), notification_queue=lambda request: None)
    mine = service.authorize_run_workspace(raw_workspace=str(target), client_id="web")
    other_target = target.parent / "other"
    other_target.mkdir()
    service.authorize_run_workspace(raw_workspace=str(other_target), client_id="cli")

    found = client.get(f"/api/workspaces/requests/{mine.authorization_request_id}")
    assert found.status_code == 200
    body = found.json()
    assert body["authorization_request_id"] == mine.authorization_request_id
    assert body["status"] == "pending"
    # Answering "was mine approved?" must not enumerate another client's paths.
    assert body["path"] == str(target.resolve())
    assert str(other_target.resolve()) not in found.text

    service.approve_request(mine.authorization_request_id)
    assert (
        client.get(f"/api/workspaces/requests/{mine.authorization_request_id}").json()["status"]
        == "approved"
    )


def test_workspace_api_rejects_unknown_and_invalid_request_ids(workspace_client) -> None:
    client, _allowed, _target, _data_dir = workspace_client

    assert client.get("/api/workspaces/requests/war-does-not-exist").status_code == 404
    # An id the store could never have written is a bad request, not a 404.
    assert client.get("/api/workspaces/requests/not%20a%20valid%20id").status_code == 400


def test_workspace_api_can_allow_request_as_project(workspace_client) -> None:
    client, _allowed, target, _data_dir = workspace_client
    from fluxion.config.settings import Settings
    from fluxion.workspace import WorkspaceAccessService

    service = WorkspaceAccessService(Settings.reload(), notification_queue=lambda request: None)
    pending = service.authorize_run_workspace(
        raw_workspace=str(target), mode="workspace-write", client_id="web"
    )

    allowed = client.post(
        f"/api/workspaces/requests/{pending.authorization_request_id}/allow-project",
        json={"path": str(target), "mode": "workspace-write", "client_id": "web"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["project_allowed"] is True
    assert allowed.json()["entry"]["access"] == "read-write"
