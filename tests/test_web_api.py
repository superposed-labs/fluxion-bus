"""Endpoint tests for the web console API.

Covers the surfaces not exercised by test_schedules_api.py: tasks, sessions,
logs, usage (disabled path), and the monitor read/write round-trip. Each test
runs against an isolated FLUXION_DATA_DIR / FLUXION_ENV_FILE so nothing touches
the developer's real data or .env.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + env file, with all console caches cleared."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setenv("FLUXION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))

    # Settings loads .env into os.environ (setdefault), so a prior test that read
    # the developer's real .env can leak monitor keys here. Clear them for a
    # deterministic baseline; monkeypatch restores prior values on teardown.
    for key in (
        "FLUXION_AUTOPING_ENABLED",
        "FLUXION_MENU_SLACK_NOTIFY_REFRESH",
        "FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH",
        "FLUXION_MENU_QQBOT_NOTIFY_REFRESH",
        "FLUXION_MENU_WECHAT_NOTIFY_REFRESH",
        "FLUXION_MENU_LINE_NOTIFY_REFRESH",
    ):
        monkeypatch.delenv(key, raising=False)

    from fluxion.web import deps
    from fluxion.web.services import aggregator

    def _clear() -> None:
        deps.get_settings.cache_clear()
        deps.get_schedule_store.cache_clear()
        deps.get_usage_service.cache_clear()
        deps.get_usage_history_service.cache_clear()
        aggregator.reset_cache()

    _clear()
    yield data_dir
    _clear()


@pytest.fixture
def client(env):
    from fluxion.web.server import create_app

    return TestClient(create_app())


def _write_jsonl(path, events) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _task_events(task_id: str, *, status: str = "RETURNED", summary: str = "done"):
    """A minimal RECEIVED→<status> pair the aggregator can fold into one task."""
    received = {
        "task_id": task_id,
        "status": "RECEIVED",
        "timestamp": "2026-06-24T00:00:00+00:00",
        "task": {
            "text": "do the thing",
            "channel": "local",
            "user_id": "u1",
            "workspace": "/tmp/project",
            "metadata": {"executor": "codex", "conversation_key": "local:u1"},
        },
    }
    terminal = {
        "task_id": task_id,
        "status": status,
        "timestamp": "2026-06-24T00:01:00+00:00",
        "task": {"text": "do the thing"},
        "result": {"success": status == "RETURNED", "exit_code": 0, "summary": summary},
    }
    return [received, terminal]


def test_aggregator_exposes_model_override(env):
    from fluxion.web.services.aggregator import aggregate_tasks

    events = _task_events("model-task")
    events[0]["task"]["metadata"]["model"] = "gpt-5.4-mini"
    _write_jsonl(env / "tasks.jsonl", events)

    task = aggregate_tasks(env)[0]

    assert task["model"] == "gpt-5.4-mini"


def test_aggregator_normalizes_changed_files_for_web(env):
    from fluxion.web.services.aggregator import aggregate_tasks

    events = _task_events("changed-paths")
    events[-1]["result"].update(
        {
            "changed_files": ["foo.txt"],
            "artifacts": ["/tmp/project/foo.txt"],
            "diff_summary": {"files": 1, "additions": 2, "deletions": 0},
        }
    )
    _write_jsonl(env / "tasks.jsonl", events)

    task = aggregate_tasks(env)[0]

    assert task["changed_files"] == [{"op": "M", "path": "foo.txt", "additions": 0, "deletions": 0}]
    assert task["diff_summary"] == {"files": 1, "additions": 2, "deletions": 0}


def test_aggregator_does_not_treat_workspace_diffstat_as_task_delta(env):
    from fluxion.web.services.aggregator import aggregate_tasks

    events = _task_events("workspace-diffstat", status="FAILED")
    events[-1]["result"].update(
        {
            "changed_files": [],
            "diff_summary": "Diff stat:\n9 files changed, 471 insertions(+), 4 deletions(-)",
        }
    )
    _write_jsonl(env / "tasks.jsonl", events)

    task = aggregate_tasks(env)[0]

    assert task["changed_files"] == []
    # lines_counted marks the 471/4 above as not measured for this run, rather
    # than leaving a bare 0 to be misread as "nothing changed".
    assert task["diff_summary"] == {
        "files": 0,
        "additions": 0,
        "deletions": 0,
        "lines_counted": False,
    }


# ── tasks ──────────────────────────────────────────────────────────


def test_tasks_empty(client):
    r = client.get("/api/tasks")
    assert r.status_code == 200
    assert r.json() == {"tasks": []}


def test_tasks_list_and_get(client, env):
    _write_jsonl(env / "tasks.jsonl", _task_events("abc123"))

    listing = client.get("/api/tasks")
    assert listing.status_code == 200
    tasks = listing.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "abc123"
    assert tasks[0]["status"] == "RETURNED"
    assert tasks[0]["summary"] == "done"
    # _hydrate_logs always provides stdout/stderr and strips the fallbacks.
    assert "stdout" in tasks[0]
    assert "_fallback_stdout" not in tasks[0]

    one = client.get("/api/tasks/abc123")
    assert one.status_code == 200
    assert one.json()["task_id"] == "abc123"


def test_get_task_hydrates_diff_hunks_from_change_set(client, env):
    task_id = "with-diff"
    change_set = env / "change_sets" / f"{task_id}.json"
    change_set.parent.mkdir()
    change_set.write_text(
        json.dumps(
            {
                "run_id": task_id,
                "workspace": "/tmp/project",
                "status": "RETURNED",
                "created_at": "2026-06-24T00:01:00+00:00",
                "changed_files": ["foo.txt"],
                "recoverable_files": ["foo.txt"],
                "unrecoverable_files": [],
                "files": [
                    {
                        "path": "foo.txt",
                        "change_type": "modified",
                        "old_content": "same\nold\n",
                        "new_content": "same\nnew\nadded\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = _task_events(task_id)
    events[-1]["result"].update(
        {
            "changed_files": ["foo.txt"],
            "change_set_file": str(change_set),
        }
    )
    _write_jsonl(env / "tasks.jsonl", events)

    task = client.get(f"/api/tasks/{task_id}").json()

    assert task["diff_hunks"]["foo.txt"] == [
        {"type": "hunk", "text": "@@ -1,2 +1,3 @@"},
        {"type": "ctx", "n1": 1, "n2": 1, "text": "same"},
        {"type": "del", "n1": 2, "text": "old"},
        {"type": "add", "n2": 2, "text": "new"},
        {"type": "add", "n2": 3, "text": "added"},
    ]
    assert task["changed_files"] == [{"op": "M", "path": "foo.txt", "additions": 2, "deletions": 1}]
    assert task["diff_summary"] == {"files": 1, "additions": 2, "deletions": 1}


def test_get_task_hydrates_fragment_diff_hunks(client, env):
    task_id = "fragment-diff"
    change_set = env / "change_sets" / f"{task_id}.json"
    change_set.parent.mkdir()
    change_set.write_text(
        json.dumps(
            {
                "run_id": task_id,
                "workspace": "/tmp/project",
                "status": "RETURNED",
                "created_at": "2026-06-24T00:01:00+00:00",
                "changed_files": ["foo.txt"],
                "recoverable_files": ["foo.txt"],
                "unrecoverable_files": [],
                "files": [
                    {
                        "path": "foo.txt",
                        "change_type": "edited",
                        "edits": [{"old": "before", "new": "after"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = _task_events(task_id)
    events[-1]["result"].update(
        {
            "changed_files": ["foo.txt"],
            "change_set_file": str(change_set),
        }
    )
    _write_jsonl(env / "tasks.jsonl", events)

    task = client.get(f"/api/tasks/{task_id}").json()

    assert task["diff_hunks"]["foo.txt"] == [
        {"type": "hunk", "text": "@@ edit fragment 1 @@"},
        {"type": "del", "text": "before"},
        {"type": "add", "text": "after"},
    ]


def test_get_task_marks_added_file_from_diff_hunks(client, env):
    task_id = "added-diff"
    change_set = env / "change_sets" / f"{task_id}.json"
    change_set.parent.mkdir()
    change_set.write_text(
        json.dumps(
            {
                "run_id": task_id,
                "workspace": "/tmp/project",
                "status": "RETURNED",
                "created_at": "2026-06-24T00:01:00+00:00",
                "changed_files": ["new.txt"],
                "recoverable_files": ["new.txt"],
                "unrecoverable_files": [],
                "files": [
                    {
                        "path": "new.txt",
                        "change_type": "added",
                        "new_content": "created\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = _task_events(task_id)
    events[-1]["result"].update(
        {
            "changed_files": ["new.txt"],
            "change_set_file": str(change_set),
        }
    )
    _write_jsonl(env / "tasks.jsonl", events)

    task = client.get(f"/api/tasks/{task_id}").json()

    assert task["changed_files"] == [{"op": "A", "path": "new.txt", "additions": 1, "deletions": 0}]


def test_get_task_404(client):
    r = client.get("/api/tasks/nope")
    assert r.status_code == 404


# ── sessions ───────────────────────────────────────────────────────


def test_sessions_empty(client):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_sessions_latest_per_conversation(client, env):
    _write_jsonl(
        env / "sessions.jsonl",
        [
            {"conversation_key": "local:u1", "timestamp": "2026-06-24T00:00:00+00:00"},
            {"conversation_key": "local:u1", "timestamp": "2026-06-24T01:00:00+00:00"},
            {"conversation_key": "local:u2", "timestamp": "2026-06-24T00:30:00+00:00"},
        ],
    )
    sessions = client.get("/api/sessions").json()["sessions"]
    # One row per conversation_key, keeping the latest, newest-first.
    assert [s["conversation_key"] for s in sessions] == ["local:u1", "local:u2"]
    assert sessions[0]["timestamp"] == "2026-06-24T01:00:00+00:00"


# ── logs ───────────────────────────────────────────────────────────


def test_log_invalid_id_400(client):
    r = client.get("/api/logs/not_a_valid_id")
    assert r.status_code == 400


def test_log_missing_404(client):
    r = client.get("/api/logs/deadbeef")
    assert r.status_code == 404


def test_log_returns_content(client, env):
    logs_dir = env / "logs"
    logs_dir.mkdir()
    (logs_dir / "task-deadbeef.log").write_text("hello log\n", encoding="utf-8")

    r = client.get("/api/logs/deadbeef")
    assert r.status_code == 200
    assert r.json() == {"task_id": "deadbeef", "log": "hello log\n"}


# ── usage ──────────────────────────────────────────────────────────


def test_usage_disabled_path(client, monkeypatch):
    # Disabled path is deterministic and never launches the collector subprocess.
    monkeypatch.setenv("FLUXION_USAGE_PANEL_ENABLED", "false")
    from fluxion.web import deps

    deps.get_settings.cache_clear()

    r = client.get("/api/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["providers"] == []
    assert "generated_at" in body


# ── monitor ────────────────────────────────────────────────────────


def test_monitor_get_defaults(client):
    r = client.get("/api/monitor")
    assert r.status_code == 200
    body = r.json()
    assert body["auto_ping"] is False
    assert set(body["notify"]) == {"slack", "telegram", "qqbot", "feishu", "wechat", "line"}
    assert set(body["channels"]) == {"slack", "telegram", "wechat", "line", "qqbot", "feishu"}


def test_monitor_put_persists_to_env(client, env):
    r = client.put("/api/monitor", json={"auto_ping": True})
    assert r.status_code == 200
    assert r.json()["auto_ping"] is True

    # The write went to the isolated env file and is re-read on the next GET.
    assert client.get("/api/monitor").json()["auto_ping"] is True


def test_monitor_put_toggles_notify(client):
    r = client.put("/api/monitor", json={"notify": {"slack": True}})
    assert r.status_code == 200
    assert r.json()["notify"]["slack"] is True
