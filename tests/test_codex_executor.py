from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fluxion.core.models.task import Task
from fluxion.executors.codex.executor import CodexExecutor


def _wait_for(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture(autouse=True)
def reset_executor_cache():
    # Reset the class-level cache before each test to ensure test isolation
    CodexExecutor._cached_cheapest = None
    yield
    CodexExecutor._cached_cheapest = None


def test_resolve_cheapest_model_success_with_mini():
    mock_catalog = {
        "models": [
            {
                "slug": "gpt-5.4-mini",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "low"},
                    {"effort": "medium", "description": "medium"},
                ],
            },
            {
                "slug": "gpt-5.5-mini",
                "supported_reasoning_levels": [
                    {"effort": "medium", "description": "medium"},
                    {"effort": "high", "description": "high"},
                ],
            },
            {
                "slug": "gpt-5.5",
                "supported_reasoning_levels": [{"effort": "low", "description": "low"}],
            },
        ]
    }

    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=Path("/tmp/fluxion_tests_logs"),
    )

    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = json.dumps(mock_catalog)

    with patch("subprocess.run", return_value=mock_run) as mock_subprocess_run:
        model, effort = executor._resolve_cheapest_model_and_effort()
        # Should pick the newest mini (gpt-5.5-mini) and its lowest reasoning level (medium)
        assert model == "gpt-5.5-mini"
        assert effort == "medium"
        assert mock_subprocess_run.call_count == 1


def test_resolve_cheapest_model_success_no_mini():
    mock_catalog = {
        "models": [
            {
                "slug": "gpt-5.2",
                "supported_reasoning_levels": [{"effort": "low", "description": "low"}],
            },
            {
                "slug": "gpt-5.5",
                "supported_reasoning_levels": [
                    {"effort": "medium", "description": "medium"},
                    {"effort": "high", "description": "high"},
                ],
            },
        ]
    }

    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=Path("/tmp/fluxion_tests_logs"),
    )

    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = json.dumps(mock_catalog)

    with patch("subprocess.run", return_value=mock_run):
        model, effort = executor._resolve_cheapest_model_and_effort()
        # No mini model exists, should pick newest general model (gpt-5.5) and its lowest reasoning level (medium)
        assert model == "gpt-5.5"
        assert effort == "medium"


def test_resolve_cheapest_model_subprocess_error():
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=Path("/tmp/fluxion_tests_logs"),
    )

    with patch("subprocess.run", side_effect=subprocess.SubprocessError("Failed")):
        model, effort = executor._resolve_cheapest_model_and_effort()
        # Should fallback to default values
        assert model == "gpt-5.4-mini"
        assert effort == "low"


def test_resolve_cheapest_model_caching():
    mock_catalog = {
        "models": [
            {
                "slug": "gpt-5.5-mini",
                "supported_reasoning_levels": [{"effort": "low", "description": "low"}],
            }
        ]
    }

    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=Path("/tmp/fluxion_tests_logs"),
    )

    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = json.dumps(mock_catalog)

    with patch("subprocess.run", return_value=mock_run) as mock_subprocess_run:
        # First call queries subprocess
        model1, effort1 = executor._resolve_cheapest_model_and_effort()
        assert model1 == "gpt-5.5-mini"
        assert effort1 == "low"
        assert mock_subprocess_run.call_count == 1

        # Second call should use cache and not query subprocess again
        model2, effort2 = executor._resolve_cheapest_model_and_effort()
        assert model2 == "gpt-5.5-mini"
        assert effort2 == "low"
        assert mock_subprocess_run.call_count == 1


def test_build_command_enables_json_events(tmp_path: Path):
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)

    command = executor._build_command(task)

    assert command[1:3] == ["exec", "--json"]


def test_build_resume_command_enables_json_events(tmp_path: Path):
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    task = Task.create(
        channel="local",
        user_id="u",
        text="hi",
        workspace=tmp_path,
        metadata={"executor_session_id": "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"},
    )

    command = executor._build_command(task)

    assert command[1:4] == ["exec", "resume", "--json"]
    assert command[-1] == "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"


def test_build_command_uses_model_override(tmp_path: Path):
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    task = Task.create(
        channel="local",
        user_id="u",
        text="hi",
        workspace=tmp_path,
        metadata={
            "model": "gpt-5.5",
            "subagent": {"task_name": "ping-explicit-model"},
        },
    )

    command = executor._build_command(task)

    assert "-m" in command
    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert "--ignore-user-config" not in command


def test_extract_answer_accepts_markers_without_colon(tmp_path: Path):
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    stdout = 'FINAL_ANSWER\nfinished\nACTIONS_JSON\n{"upload_files": []}'

    assert executor._extract_user_answer(stdout) == "finished"
    assert executor._extract_action_upload_paths(stdout) == []


def test_extract_partial_answer_from_codex_json_event(tmp_path: Path):
    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "I will inspect first."},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "FINAL_ANSWER\nstream me\nACTIONS_JSON\n{}",
                    },
                }
            ),
        ]
    )

    assert executor._extract_partial_user_answer(stdout) == "stream me"


def test_execute_writes_live_log_without_streaming_json_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, sys, time",
                "sys.stdin.read()",
                "print(json.dumps({'type': 'thread.started', 'thread_id': '019f0720-22d4-72f3-9b73-f8c8c9bfdf2f'}), flush=True)",
                "time.sleep(0.5)",
                "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'FINAL_ANSWER\\ncodex done\\nACTIONS_JSON\\n{}'}}), flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    executor = CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)
    live_log = tmp_path / "logs" / f"task-{task.id}.codex.log"
    deltas: list[str] = []
    holder: dict[str, object] = {}

    def _run() -> None:
        holder["result"] = executor.execute(task, stream_output=deltas.append)

    thread = threading.Thread(target=_run)
    thread.start()

    assert _wait_for(lambda: live_log.exists() and live_log.stat().st_size > 0)
    assert '{"type": "thread.started"' in live_log.read_text(encoding="utf-8")

    thread.join(timeout=3)
    assert not thread.is_alive()
    result = holder["result"]

    assert result.success is True
    assert result.summary == "codex done"
    assert result.changed_files == []
    assert "".join(deltas) == "codex done"
    assert "thread.started" not in "".join(deltas)
