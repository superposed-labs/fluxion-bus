from __future__ import annotations

import asyncio

import fluxion.mcp_server.server as server_mod
from fluxion.mcp_server import _human_log_tail, _is_glog_noise, _status_view
from fluxion.mcp_server.views import _result_view, _status_poll_view


# ── B: agy glog transport noise is filtered out of the tail ──────────
def test_human_log_tail_drops_agy_glog_noise():
    text = "\n".join(
        [
            "I0613 11:46:53.143622 11683 http_helpers.go:186] URL: https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent",
            "agy: I0613 11:48:44.610236 11683 manager.go:611] CLI store manager shutting down",
            "Reading src/fluxion/usage/history.py",  # real agent output — keep
        ]
    )
    out = _human_log_tail(text)
    assert "http_helpers.go" not in out
    assert "manager.go" not in out
    assert "Reading src/fluxion/usage/history.py" in out


def test_glog_noise_matcher_drops_all_runtime_levels_keeps_agent_output():
    # Executor-runtime glog at every level is noise (success/failure comes from
    # the structured status, not this tail), so I/W/E/F are all dropped.
    assert _is_glog_noise("I0613 11:46:53.143622 11683 http_helpers.go:186] URL: x") is True
    assert _is_glog_noise("agy: W0613 11:46:53.000000 11683 quota.go:10] slow") is True
    assert _is_glog_noise("agy: E0613 11:46:47.005054 11683 log.go:398] not logged in") is True
    assert _is_glog_noise("F0613 00:00:00.000000 1 server.go:1] fatal") is True
    # The agent's own text output is kept — even if it mentions a .go file, since
    # it lacks the full timestamped glog signature.
    assert _is_glog_noise("Searching for service_tier in the codebase") is False
    assert _is_glog_noise("see service.go:42 for the handler") is False


def test_human_log_tail_still_humanizes_codex_json_lines():
    text = '{"stream": "stdout", "body": "hello"}\n{"command": "rg service_tier"}'
    out = _human_log_tail(text)
    assert "stdout: hello" in out
    assert "command: rg service_tier" in out


def test_human_log_tail_drops_glog_wrapped_in_json_body():
    # The task log wraps executor output as JSON; agy glog noise rides inside
    # `body`, so it must be filtered after extraction (regression for the path
    # the raw-line check misses).
    text = "\n".join(
        [
            '{"stream": "agy", "lvl": "out", "body": "I0613 11:46:48.4 11683 http_helpers.go:186] URL: x"}',
            '{"stream": "agy", "lvl": "out", "body": "Reading history.py"}',
        ]
    )
    out = _human_log_tail(text)
    assert "http_helpers.go" not in out
    assert "Reading history.py" in out


# ── A: the prompt/preamble isn't echoed while a run is in flight ─────
def _task(status: str, summary: str) -> dict:
    return {
        "task_id": "t1",
        "status": status,
        "summary": summary,
        "timestamp": {"started_at": "2026-06-13T00:00:00+00:00"},
        "subagent": {"mode": "read-only"},
    }


class _NoLiveRunner:
    def live_progress(self, task_id):  # noqa: ARG002
        return {}


class _ServerSettings:
    mcp_status_max_wait_ms = 60_000

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.projects = {}

    @classmethod
    def load(cls):
        raise AssertionError("patched in test")


class _ServerRunner(_NoLiveRunner):
    def __init__(self, settings):
        del settings

    def submit(self, request):
        raise AssertionError("not used")

    def cancel(self, task_id):
        raise AssertionError("not used")


def test_status_view_replaces_prompt_echo_while_running(tmp_path):

    class _S:
        data_dir = tmp_path

    prompt = "You are a sub-agent...\nSubtask:\n" + "x" * 5000
    view = _status_view(_task("RUNNING", prompt), settings=_S(), runner=_NoLiveRunner())
    assert view["summary"] == "Running."
    assert "Subtask" not in view["summary"]
    assert len(view["summary"]) < 60  # no longer carries the whole prompt


def test_status_poll_view_omits_repeated_detail_fields(tmp_path):
    class _S:
        data_dir = tmp_path

    view = _status_poll_view(
        {
            **_task("RUNNING", "prompt"),
            "executor": "codex",
            "changed_files": [{"path": "foo.py"}],
            "artifacts": [{"path": "/tmp/foo.txt"}],
            "diff_summary": {"files": 1},
            "change_set_file": "/tmp/change.json",
        },
        settings=_S(),
        runner=_NoLiveRunner(),
    )

    assert view["run_id"] == "t1"
    assert view["status"] == "RUNNING"
    assert view["executor"] == "codex"
    assert view["next_action"] == "poll_later_or_cancel"
    assert view["changed_files_available"] is False
    assert "changed_files" not in view
    assert "artifacts" not in view
    assert "diff_summary" not in view
    assert "change_set_file" not in view
    assert "timestamp" not in view
    assert "subagent" not in view


class _LiveRunner:
    def __init__(self, tail: str) -> None:
        self._tail = tail

    def live_progress(self, task_id):  # noqa: ARG002
        return {
            "recent_output_tail": self._tail,
            "recent_output_tail_truncated": False,
            "live_output_chars": len(self._tail),
            "live_output_updated_at": "2026-06-13T00:00:00+00:00",
        }


def test_status_poll_view_uses_short_tail_detail_keeps_full_tail(tmp_path):
    class _S:
        data_dir = tmp_path

    long_tail = "\n".join(f"line-{idx}-" + ("x" * 180) for idx in range(20))
    runner = _LiveRunner(long_tail)

    compact = _status_poll_view(_task("RUNNING", "prompt"), settings=_S(), runner=runner)
    detail = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=runner)

    assert len(compact["recent_output_tail"]) <= 1000
    assert compact["recent_output_tail_truncated"] is True
    assert "line-0-" not in compact["recent_output_tail"]
    assert "line-19-" in compact["recent_output_tail"]
    assert detail["recent_output_tail"] == long_tail
    assert detail["recent_output_tail_truncated"] is False


def test_status_poll_view_omits_terminal_tail_detail_keeps_it(tmp_path):
    class _S:
        data_dir = tmp_path

    tail = "FINAL_ANSWER:\ndone"
    runner = _LiveRunner(tail)

    compact = _status_poll_view(_task("RETURNED", "done"), settings=_S(), runner=runner)
    detail = _status_view(_task("RETURNED", "done"), settings=_S(), runner=runner)

    assert compact["is_terminal"] is True
    assert compact["next_action"] == "get_task_result"
    assert compact["summary"] == "done"
    assert compact["recent_output_tail"] == ""
    assert compact["recent_output_tail_truncated"] is False
    assert detail["recent_output_tail"] == tail


def test_get_task_status_defaults_compact_detail_opt_in(tmp_path, monkeypatch):
    task = {
        **_task("RUNNING", "prompt"),
        "executor": "codex",
        "changed_files": [{"path": "foo.py"}],
        "diff_summary": {"files": 1},
    }

    class _S(_ServerSettings):
        @classmethod
        def load(cls):
            return cls(tmp_path)

    monkeypatch.setattr(server_mod, "Settings", _S)
    monkeypatch.setattr(server_mod, "SubagentRunner", _ServerRunner)
    monkeypatch.setattr(server_mod, "_find_task", lambda run_id: task if run_id == "t1" else None)
    mcp_server = server_mod.create_server()

    async def call_status(arguments):
        # MCP 2.x returns a CallToolResult; the tool payload is structured_content.
        result = await mcp_server.call_tool("get_task_status", arguments)
        return result.structured_content

    compact = asyncio.run(call_status({"run_id": "t1"}))
    detail = asyncio.run(call_status({"run_id": "t1", "detail": True}))

    assert compact["status"] == "RUNNING"
    assert "changed_files" not in compact
    assert "diff_summary" not in compact
    assert detail["status"] == "RUNNING"
    assert detail["changed_files"] == [{"path": "foo.py"}]
    assert detail["diff_summary"] == {"files": 1}


def test_status_view_keeps_summary_when_terminal(tmp_path):
    class _S:
        data_dir = tmp_path

    result = "1. Fast mode is service_tier=priority ..."
    view = _status_view(_task("RETURNED", result), settings=_S(), runner=_NoLiveRunner())
    assert view["summary"] == result  # the real answer survives at terminal


def test_status_view_reports_working_when_log_fresh_but_only_glog(tmp_path):
    # agy's working phase writes only glog plumbing (filtered to an empty human
    # tail) and no live deltas, yet the run is alive — a fresh, growing log must
    # read as "working", not the misleading no_output_seen.
    class _S:
        data_dir = tmp_path

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "task-t1.agy.log").write_text(
        "I0613 11:46:53.143622 11683 http_helpers.go:186] URL: https://x\n"
        "I0613 11:46:54.000000 11683 manager.go:611] working\n",
        encoding="utf-8",
    )

    view = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=_NoLiveRunner())

    assert view["recent_output_tail"] == ""  # nothing human-readable yet
    assert view["progress_signal"] == "working"
    assert view["progress_source"]  # points at the log file


def test_status_view_reports_working_from_codex_live_json_log(tmp_path):
    class _S:
        data_dir = tmp_path

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "task-t1.codex.log").write_text(
        '{"type": "thread.started", "thread_id": "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"}\n',
        encoding="utf-8",
    )

    view = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=_NoLiveRunner())

    assert view["recent_output_tail"] == ""
    assert view["progress_signal"] == "working"
    assert view["progress_source"] == "codex_log"


def test_status_view_reports_working_from_claude_live_json_log(tmp_path):
    class _S:
        data_dir = tmp_path

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "task-t1.claude.log").write_text(
        '{"type": "system", "subtype": "init", "session_id": "session-1"}\n',
        encoding="utf-8",
    )

    view = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=_NoLiveRunner())

    assert view["recent_output_tail"] == ""
    assert view["progress_signal"] == "working"
    assert view["progress_source"] == "claude_log"


def test_status_view_no_output_seen_when_log_is_stale(tmp_path):
    import os
    import time

    class _S:
        data_dir = tmp_path

    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "task-t1.agy.log"
    log.write_text(
        "I0613 11:46:53.143622 11683 http_helpers.go:186] URL: https://x\n",
        encoding="utf-8",
    )
    stale = time.time() - 600  # well past the active window
    os.utime(log, (stale, stale))

    view = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=_NoLiveRunner())

    assert view["progress_signal"] == "no_output_seen"


def test_status_view_ignores_the_partial_line_a_long_log_starts_on(tmp_path):
    """A log read past its first 64KB starts mid-line, and that fragment used to
    pass for agent output.

    Truncation strips the timestamped prefix the glog filter matches on, so the
    orphan fragment survived it and became the entire `recent_output_tail` —
    turning a run whose log is 100% transport plumbing into `output_seen`."""

    class _S:
        data_dir = tmp_path

    logs = tmp_path / "logs"
    logs.mkdir()
    noise = (
        "I0613 11:46:53.143622 11683 http_helpers.go:186] "
        "URL: https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent\n"
    )
    # Comfortably past the 64KB the tail reader seeks back over.
    (logs / "task-t1.agy.log").write_text(noise * 1200, encoding="utf-8")

    view = _status_view(_task("RUNNING", "prompt"), settings=_S(), runner=_NoLiveRunner())

    assert view["recent_output_tail"] == ""
    # The log is fresh, so the run is still credibly working — but on the
    # strength of the file being written, not of a fake line of "output".
    assert view["progress_signal"] == "working"


def test_result_view_flags_whether_a_run_can_be_reverted():
    reversible = _result_view(
        {
            "task_id": "r1",
            "status": "CANCELED",
            "changed_files": [{"path": "foo.txt"}],
            "change_set_file": "/data/change_sets/r1.json",
        }
    )
    assert reversible["revert_available"] is True

    # Read-only runs (and any run whose changes could not be captured) record no
    # ChangeSet, so revert_subagent_run has nothing to undo.
    assert _result_view({"task_id": "r2", "status": "RETURNED"})["revert_available"] is False


def test_result_view_compacts_paths_for_model_consumers():
    view = _result_view(
        {
            "task_id": "r1",
            "status": "RETURNED",
            "changed_files": [{"op": "M", "path": "foo.txt", "additions": 0, "deletions": 0}],
            "artifacts": [{"name": "foo.txt", "path": "/tmp/project/foo.txt"}],
        }
    )

    assert view["changed_files"] == ["foo.txt"]
    assert view["artifacts"] == ["/tmp/project/foo.txt"]
