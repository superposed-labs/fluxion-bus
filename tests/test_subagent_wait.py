from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fluxion.mcp_server as mcp
import fluxion.mcp_server.server as server_mod
import fluxion.web.services.aggregator as agg
from fluxion.cli.sub import main as cli_main
from fluxion.mcp_server import _suggested_poll_after_sec, _typical_duration_sec
from fluxion.mcp_server.server import run_subagent_tool
from fluxion.web.services.aggregator import wait_for_terminal

UTC = UTC


def _terminal_task(executor: str, dur_sec: int, task_id: str = "x") -> dict:
    start = datetime(2026, 6, 13, tzinfo=UTC)
    end = start + timedelta(seconds=dur_sec)
    return {
        "task_id": task_id,
        "status": "RETURNED",
        "executor": executor,
        "success": True,
        "timestamp": {"started_at": start.isoformat(), "ended_at": end.isoformat()},
    }


# ── wait_for_terminal primitive ─────────────────────────────────────
def test_wait_for_terminal_returns_when_terminal(monkeypatch):
    # First read shows RUNNING, second shows RETURNED.
    states = iter(
        [
            [{"task_id": "r1", "status": "RUNNING"}],
            [{"task_id": "r1", "status": "RETURNED", "success": True}],
        ]
    )
    monkeypatch.setattr(agg, "aggregate_tasks_cached", lambda _d: next(states))
    task = wait_for_terminal(Path("/tmp"), "r1", timeout_sec=5, poll_interval=0.01)
    assert task is not None and task["status"] == "RETURNED"


def test_wait_for_terminal_times_out_returns_last(monkeypatch):
    monkeypatch.setattr(
        agg, "aggregate_tasks_cached", lambda _d: [{"task_id": "r1", "status": "RUNNING"}]
    )
    task = wait_for_terminal(Path("/tmp"), "r1", timeout_sec=0.05, poll_interval=0.01)
    assert task is not None and task["status"] == "RUNNING"  # timed out, still running


def test_wait_for_terminal_unknown_run(monkeypatch):
    monkeypatch.setattr(agg, "aggregate_tasks_cached", lambda _d: [])
    assert wait_for_terminal(Path("/tmp"), "nope", timeout_sec=0.03, poll_interval=0.01) is None


# ── ETA / suggested_poll_after_sec ──────────────────────────────────
def test_suggested_poll_terminal_is_zero():
    s = _make_settings()
    assert (
        _suggested_poll_after_sec(
            status="RETURNED", terminal=True, elapsed=10, executor="", settings=s
        )
        == 0
    )


def test_suggested_poll_queued_is_small():
    s = _make_settings()
    assert (
        _suggested_poll_after_sec(
            status="QUEUED", terminal=False, elapsed=None, executor="", settings=s
        )
        == 3
    )


def test_suggested_poll_running_uses_history(monkeypatch):
    s = _make_settings()
    monkeypatch.setattr(
        mcp.views,
        "aggregate_tasks_cached",
        lambda _d: [
            _terminal_task("antigravity", 100),
            _terminal_task("antigravity", 120),
        ],
    )
    # median ~120; elapsed 90 -> remaining 30, under the 60s cap so returned as-is.
    out = _suggested_poll_after_sec(
        status="RUNNING", terminal=False, elapsed=90, executor="antigravity", settings=s
    )
    assert out == 30


def test_suggested_poll_caps_at_configured_max(monkeypatch):
    s = _make_settings()  # mcp_status_max_wait_ms = 60_000
    monkeypatch.setattr(
        mcp.views,
        "aggregate_tasks_cached",
        lambda _d: [_terminal_task("antigravity", 200), _terminal_task("antigravity", 200)],
    )
    # median 200; elapsed 10 -> remaining 190, capped at the 60s max wait.
    out = _suggested_poll_after_sec(
        status="RUNNING", terminal=False, elapsed=10, executor="antigravity", settings=s
    )
    assert out == 60


def test_suggested_poll_running_no_history_falls_back(monkeypatch):
    s = _make_settings()
    monkeypatch.setattr(mcp.views, "aggregate_tasks_cached", lambda _d: [])
    assert (
        _suggested_poll_after_sec(
            status="RUNNING", terminal=False, elapsed=5, executor="x", settings=s
        )
        == 12
    )


def test_typical_duration_filters_by_executor(monkeypatch):
    s = _make_settings()
    monkeypatch.setattr(
        mcp.views,
        "aggregate_tasks_cached",
        lambda _d: [
            _terminal_task("antigravity", 60),
            _terminal_task("claude", 999),
            {"task_id": "live", "status": "RUNNING", "executor": "antigravity"},
        ],
    )
    assert _typical_duration_sec("antigravity", settings=s) == 60  # only the terminal agy run


# ── CLI --wait mode ─────────────────────────────────────────────────
def test_cli_wait_success(monkeypatch, capsys):
    monkeypatch.setattr(
        agg,
        "wait_for_terminal",
        lambda *a, **k: {"task_id": "r1", "status": "RETURNED", "success": True, "summary": "done"},
    )
    _patch_settings(monkeypatch)
    assert cli_main(["--wait", "r1"]) == 0
    assert "RETURNED" in capsys.readouterr().out


def test_cli_wait_timeout_exit_2(monkeypatch):
    monkeypatch.setattr(
        agg, "wait_for_terminal", lambda *a, **k: {"task_id": "r1", "status": "RUNNING"}
    )
    _patch_settings(monkeypatch)
    assert cli_main(["--wait", "r1"]) == 2  # still running at timeout


def test_cli_requires_prompt_or_wait():
    assert cli_main([]) != 0  # no prompt, no --wait


# ── helpers ─────────────────────────────────────────────────────────
class _S:
    data_dir = Path("/tmp")
    mcp_status_max_wait_ms = 60_000


def _make_settings() -> _S:
    return _S()


def _patch_settings(monkeypatch):
    from fluxion.config import settings as settings_mod

    monkeypatch.setattr(settings_mod.Settings, "load", classmethod(lambda cls: _S()))


# ── blocking wait that elapses must NOT cancel the run ──────────────
class _Handle:
    task_id = "r1"

    def to_payload(self) -> dict:
        return {"run_id": "r1", "task_id": "r1", "executor": "antigravity"}


def test_timed_out_payload_keeps_run_alive():
    # A blocking wait_for_result wait that elapses degrades to fire-and-forget:
    # the task stays active and is collected later via run_id — never canceled.
    payload = mcp._timed_out_still_running_payload(
        _Handle(), timeout_sec=300, prompt="investigate", profile="inspect", mode="read-only"
    )
    assert payload["status"] == "RUNNING"
    assert payload["timed_out"] is True
    # The contract that prevents regressing to cancel-on-timeout:
    assert payload["cancel_requested"] is False
    assert payload["cancel_reason"] is None
    # run_id is preserved so the caller can fetch the result afterwards.
    assert payload["run_id"] == "r1"
    assert "get_task_result" in payload["next_tools"]
    assert "300s" in payload["summary"]


class _WaitAdapter:
    def __init__(self, statuses=None) -> None:
        self.wait_calls: list[int] = []
        self.statuses = list(statuses or [])

    def wait(self, timeout_sec: int):
        self.wait_calls.append(timeout_sec)
        return None


class _RunHandle:
    def __init__(self, statuses=None) -> None:
        self.adapter = _WaitAdapter(statuses=statuses)

    def to_payload(self) -> dict:
        return {
            "run_id": "run-timeout",
            "task_id": "run-timeout",
            "executor": "codex",
            "accepted": True,
            "status": "QUEUED",
            "summary": "Task accepted.",
        }


class _Runner:
    def __init__(self, statuses=None) -> None:
        self.handle = _RunHandle(statuses=statuses)
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return self.handle


class _ToolSettings:
    task_timeout_sec = 60

    @staticmethod
    def resolve_run_workspace(*, raw_workspace: str, project_key: str, mode: str) -> Path:
        del project_key, mode
        return Path(raw_workspace)


def test_run_subagent_tool_wait_timeout_reports_queued_before_start():
    runner = _Runner()

    payload = run_subagent_tool(
        runner=runner,
        settings=_ToolSettings(),
        prompt="slow task",
        agent="codex",
        workspace="/tmp",
        wait_for_result=True,
        timeout_sec=1,
    )

    assert payload["run_id"] == "run-timeout"
    assert payload["status"] == "QUEUED"
    assert payload["timed_out"] is True
    assert "still queued" in payload["summary"]
    assert payload["cancel_requested"] is False
    assert payload["next_tools"] == [
        "get_task_status",
        "get_task_result",
        "cancel_subagent_run",
    ]
    assert runner.handle.adapter.wait_calls == [1]
    assert runner.requests[0].agent == "codex"


def test_run_subagent_tool_wait_timeout_reports_running_after_start():
    runner = _Runner(statuses=[{"task_id": "run-timeout", "status": "RUNNING"}])

    payload = run_subagent_tool(
        runner=runner,
        settings=_ToolSettings(),
        prompt="slow task",
        agent="codex",
        workspace="/tmp",
        wait_for_result=True,
        timeout_sec=1,
    )

    assert payload["status"] == "RUNNING"
    assert payload["timed_out"] is True
    assert "still running" in payload["summary"]


def test_run_subagent_tool_clamps_wait_timeout_to_execution_cap():
    runner = _Runner()

    payload = run_subagent_tool(
        runner=runner,
        settings=_ToolSettings(),
        prompt="slow task",
        agent="codex",
        workspace="/tmp",
        wait_for_result=True,
        timeout_sec=999,
    )

    assert payload["timed_out"] is True
    assert runner.handle.adapter.wait_calls == [65]


def test_fastmcp_run_subagent_binding_preserves_timeout_sec(monkeypatch):
    class _ServerRunner(_Runner):
        latest = None

        def __init__(self, settings):
            del settings
            super().__init__()
            _ServerRunner.latest = self

    class _ServerSettings(_ToolSettings):
        projects = {}
        allowed_workspaces = []
        trusted_workspace_roots = []
        write_allowed_workspaces = []
        workspace_discovery = False

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr(server_mod, "Settings", _ServerSettings)
    monkeypatch.setattr(server_mod, "SubagentRunner", _ServerRunner)
    mcp_server = server_mod.create_server()

    async def call_tool():
        return await mcp_server.call_tool(
            "run_subagent",
            {
                "prompt": "slow task",
                "agent": "codex",
                "workspace": "/tmp",
                "wait_for_result": True,
                "timeout_sec": 1,
            },
        )

    # MCP 2.x returns a CallToolResult; the tool payload is structured_content.
    payload = asyncio.run(call_tool()).structured_content

    assert payload["status"] == "QUEUED"
    assert payload["timed_out"] is True
    assert _ServerRunner.latest is not None
    assert _ServerRunner.latest.handle.adapter.wait_calls == [1]
