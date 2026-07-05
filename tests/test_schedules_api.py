from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path))
    # Isolate the env file so Settings.load() doesn't read (and leak into
    # os.environ) the developer's real .env during the suite.
    monkeypatch.setenv("FLUXION_ENV_FILE", str(tmp_path / ".env"))
    from fluxion.web import deps

    deps.get_settings.cache_clear()
    deps.get_schedule_store.cache_clear()
    deps.get_usage_service.cache_clear()

    from fluxion.web.server import create_app

    yield TestClient(create_app())

    deps.get_settings.cache_clear()
    deps.get_schedule_store.cache_clear()
    deps.get_usage_service.cache_clear()


_CRON_BODY = {
    "name": "weekly audit",
    "enabled": True,
    "trigger": {"type": "cron", "cron": "0 9 * * 1", "timezone": "Asia/Shanghai"},
    "action": {"type": "subagent", "agent": "codex", "prompt": "audit", "mode": "read-only"},
    "policy": {"cooldown_sec": 3600, "catch_up": "run_once", "max_runs_per_day": 2},
}
_PING_BODY = {
    "name": "codex kickoff",
    "trigger": {"type": "quota_refresh", "provider": "codex", "window_key": "7d"},
    "action": {"type": "ping", "agent": "auto"},
    "policy": {"cooldown_sec": 7200},
}


def test_list_empty(client):
    r = client.get("/api/schedules")
    assert r.status_code == 200
    assert r.json() == {"schedules": []}


def test_create_and_list(client):
    r = client.post("/api/schedules", json=_CRON_BODY)
    assert r.status_code == 200
    rule = r.json()
    assert rule["id"].startswith("sch_")
    assert rule["trigger"]["timezone"] == "Asia/Shanghai"

    client.post("/api/schedules", json=_PING_BODY)
    listing = client.get("/api/schedules").json()["schedules"]
    assert len(listing) == 2


def test_autoping_api_creates_managed_rules(client):
    r = client.put("/api/autoping", json={"provider": "codex", "mode": "both"})
    assert r.status_code == 200
    assert r.json()["providers"]["codex"] == "both"

    rules = client.get("/api/schedules").json()["schedules"]
    assert {rule["trigger"]["window_key"] for rule in rules} == {"5h", "7d"}
    assert all(rule["managed_by"] == "autoping" for rule in rules)


def test_managed_rule_rejects_generic_mutation(client):
    client.put("/api/autoping", json={"provider": "claude", "mode": "7d"})
    rule = client.get("/api/schedules").json()["schedules"][0]
    rid = rule["id"]

    assert client.post(f"/api/schedules/{rid}/enable", json={"enabled": False}).status_code == 409
    assert client.delete(f"/api/schedules/{rid}").status_code == 409


# Managed monitor rules are ACTION_NOTIFY — they never ping themselves, so a
# manual ping schedule on the same window is allowed to coexist. When global
# Auto Ping is on the daemon dedupes anchor pings per provider/window pool.
def test_manual_ping_coexists_with_managed_autoping(client):
    client.put("/api/autoping", json={"provider": "codex", "mode": "7d"})
    r = client.post("/api/schedules", json=_PING_BODY)
    assert r.status_code == 200


def test_autoping_allows_existing_manual_ping(client):
    client.post("/api/schedules", json=_PING_BODY)
    r = client.put("/api/autoping", json={"provider": "codex", "mode": "7d"})
    assert r.status_code == 200


def test_disabled_manual_ping_can_enable_over_managed_autoping(client):
    body = {**_PING_BODY, "enabled": False}
    rid = client.post("/api/schedules", json=body).json()["id"]
    client.put("/api/autoping", json={"provider": "codex", "mode": "7d"})

    r = client.post(f"/api/schedules/{rid}/enable", json={"enabled": True})
    assert r.status_code == 200


def test_invalid_cron_rejected(client):
    bad = {**_CRON_BODY, "trigger": {"type": "cron", "cron": "nope", "timezone": "UTC"}}
    r = client.post("/api/schedules", json=bad)
    assert r.status_code == 400
    assert "cron" in r.json()["detail"]


def test_invalid_timezone_rejected(client):
    bad = {
        **_CRON_BODY,
        "trigger": {"type": "cron", "cron": "0 9 * * 1", "timezone": "Mars/Phobos"},
    }
    r = client.post("/api/schedules", json=bad)
    assert r.status_code == 400


def test_invalid_provider_rejected(client):
    bad = {
        "name": "x",
        "trigger": {"type": "quota_refresh", "provider": "openai", "window_key": "7d"},
        "action": {"type": "ping", "agent": "auto"},
    }
    r = client.post("/api/schedules", json=bad)
    assert r.status_code == 400


def test_subagent_requires_prompt(client):
    bad = {
        "name": "x",
        "trigger": {"type": "cron", "cron": "0 9 * * 1", "timezone": "UTC"},
        "action": {"type": "subagent", "agent": "codex", "prompt": "   "},
    }
    r = client.post("/api/schedules", json=bad)
    assert r.status_code == 400


def test_enable_toggle(client):
    rid = client.post("/api/schedules", json=_CRON_BODY).json()["id"]
    r = client.post(f"/api/schedules/{rid}/enable", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_update_preserves_created_at(client):
    created = client.post("/api/schedules", json=_CRON_BODY).json()
    rid = created["id"]
    upd = {**_CRON_BODY, "name": "renamed"}
    r = client.put(f"/api/schedules/{rid}", json=upd)
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"
    assert r.json()["created_at"] == created["created_at"]
    assert r.json()["updated_at"] != created["updated_at"]


def test_update_missing_returns_404(client):
    r = client.put("/api/schedules/sch_missing", json=_CRON_BODY)
    assert r.status_code == 404


def test_delete_then_404(client):
    rid = client.post("/api/schedules", json=_CRON_BODY).json()["id"]
    assert client.delete(f"/api/schedules/{rid}").status_code == 200
    assert client.delete(f"/api/schedules/{rid}").status_code == 404
    assert client.get("/api/schedules").json()["schedules"] == []


def test_runs_endpoint_empty(client):
    r = client.get("/api/schedule_runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_manual_run_endpoint(client):
    created = client.post("/api/schedules", json=_CRON_BODY).json()
    rid = created["id"]

    assert created["run_now"] is False

    r = client.post(f"/api/schedules/{rid}/run")
    assert r.status_code == 200
    assert r.json()["run_now"] is True

    # Managed rules cannot be run manually
    client.put("/api/autoping", json={"provider": "codex", "mode": "7d"})
    managed_rule = [
        rule for rule in client.get("/api/schedules").json()["schedules"] if rule["managed_by"]
    ][0]
    mrid = managed_rule["id"]

    assert client.post(f"/api/schedules/{mrid}/run").status_code == 409
