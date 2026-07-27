from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fluxion.core.models.attachment import Attachment, ImageAttachment
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


@pytest.fixture(autouse=True)
def isolated_codex_home(tmp_path_factory, monkeypatch):
    """Keep the command builder off the developer's own `~/.codex/config.toml`.

    `_build_command` consults it for the recursion guard, so a contributor who
    routes their own Codex through the gateway would otherwise see unrelated
    command assertions pick up an extra override.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex-home")))


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


def _image_attachment(path: Path) -> ImageAttachment:
    return ImageAttachment(
        path=path,
        media_type="image/png",
        sha256="abc",
        byte_size=123,
        width=4,
        height=3,
    )


def test_build_command_passes_images_through_the_native_cli_flag(tmp_path: Path):
    image = tmp_path / "screenshot.png"
    task = Task.create(
        channel="local",
        user_id="u",
        text="inspect",
        workspace=tmp_path,
        image_attachments=[_image_attachment(image)],
    )

    command = _executor(tmp_path)._build_command(task)

    assert command[command.index("--image") + 1] == str(image)


def test_resume_command_puts_images_before_the_positional_session_id(tmp_path: Path):
    image = tmp_path / "follow-up.png"
    session_id = "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"
    task = Task.create(
        channel="local",
        user_id="u",
        text="inspect the follow-up",
        workspace=tmp_path,
        metadata={"executor_session_id": session_id},
        image_attachments=[_image_attachment(image)],
    )

    command = _executor(tmp_path)._build_command(task)

    assert command[command.index("--image") + 1] == str(image)
    assert command[-1] == session_id


def test_generic_attachment_is_left_for_the_agent_file_bridge(tmp_path: Path):
    attachment = Attachment(
        path=tmp_path / "sample.heic",
        media_type="image/heic",
        sha256="def",
        byte_size=123,
    )
    task = Task.create(
        channel="local",
        user_id="u",
        text="inspect the attached file",
        workspace=tmp_path,
        attachments=[attachment],
    )

    command = _executor(tmp_path)._build_command(task)

    assert "--image" not in command


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


# ── recursion guard ──────────────────────────────────────────────────
def _executor(tmp_path: Path) -> CodexExecutor:
    return CodexExecutor(
        timeout_sec=30,
        skip_git_repo_check=True,
        sandbox_mode="none",
        bypass_sandbox=False,
        max_structured_uploads=10,
        logs_dir=tmp_path / "logs",
    )


def _write_codex_config(body: str) -> None:
    (Path(os.environ["CODEX_HOME"]) / "config.toml").write_text(body, encoding="utf-8")


def _override_of(command: list[str], key: str) -> str | None:
    for flag, value in zip(command, command[1:], strict=False):
        if flag == "-c" and value.startswith(f"{key}="):
            return value.split("=", 1)[1]
    return None


def test_a_provider_pointing_back_at_the_gateway_is_replaced(tmp_path: Path):
    """Otherwise the child `codex exec` calls the gateway that is waiting on it,
    while that request holds the workspace lock — a hang with no error."""
    _write_codex_config('model_provider = "fluxion_auto"\n')
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)

    command = _executor(tmp_path)._build_command(task)

    assert _override_of(command, "model_provider") == "openai"


def test_someone_elses_custom_provider_is_left_alone(tmp_path: Path):
    """Proxying Codex through your own endpoint is a legitimate setup; only a
    provider that routes back into Fluxion is ours to override."""
    _write_codex_config('model_provider = "my_own_proxy"\n')
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)

    command = _executor(tmp_path)._build_command(task)

    assert _override_of(command, "model_provider") is None


def test_the_usual_config_gets_no_override(tmp_path: Path):
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)

    command = _executor(tmp_path)._build_command(task)

    assert "-c" not in command


def test_a_config_that_cannot_be_parsed_is_left_to_codex(tmp_path: Path):
    """Codex reports its own config errors; guessing here would swap a clear
    message for a mysterious override."""
    _write_codex_config("model_provider = [unclosed\n")
    task = Task.create(channel="local", user_id="u", text="hi", workspace=tmp_path)

    command = _executor(tmp_path)._build_command(task)

    assert _override_of(command, "model_provider") is None


def test_a_guarded_resume_keeps_the_session_id_last(tmp_path: Path):
    """`codex exec resume` reads the session id positionally, so an override
    appended after it would be swallowed as the session."""
    _write_codex_config('model_provider = "fluxion_worker"\n')
    task = Task.create(
        channel="local",
        user_id="u",
        text="hi",
        workspace=tmp_path,
        metadata={"executor_session_id": "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"},
    )

    command = _executor(tmp_path)._build_command(task)

    assert _override_of(command, "model_provider") == "openai"
    assert command[-1] == "019f0720-22d4-72f3-9b73-f8c8c9bfdf2f"


def test_the_ping_needs_no_guard(tmp_path: Path):
    """It already skips the user's config, so nothing can point it back at us."""
    _write_codex_config('model_provider = "fluxion_auto"\n')
    task = Task.create(
        channel="local",
        user_id="u",
        text="hi",
        workspace=tmp_path,
        metadata={"subagent": {"task_name": "ping-keepalive"}},
    )

    with patch.object(CodexExecutor, "_resolve_cheapest_model_and_effort") as resolve:
        resolve.return_value = ("gpt-5.4-mini", "low")
        command = _executor(tmp_path)._build_command(task)

    assert "--ignore-user-config" in command
    assert _override_of(command, "model_provider") is None


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


def _ro_executor(tmp_path, **kwargs):
    return CodexExecutor(
        timeout_sec=60,
        skip_git_repo_check=True,
        sandbox_mode=kwargs.pop("sandbox_mode", "workspace-write"),
        bypass_sandbox=kwargs.pop("bypass_sandbox", False),
        max_structured_uploads=8,
        logs_dir=tmp_path / "logs",
    )


def test_read_only_task_forces_the_read_only_sandbox(tmp_path):
    task = Task.create(
        channel="local",
        user_id="local",
        text="explain",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw", "read_only": True},
    )
    cmd = _ro_executor(tmp_path, bypass_sandbox=True)._build_command(task)

    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_read_only_survives_a_resumed_session(tmp_path):
    """Resuming does not inherit the first run's sandbox."""
    task = Task.create(
        channel="local",
        user_id="local",
        text="explain",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw", "read_only": True, "executor_session_id": "s1"},
    )
    cmd = _ro_executor(tmp_path, bypass_sandbox=True)._build_command(task)

    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def test_full_auto_is_never_passed(tmp_path):
    """`--full-auto` forces workspace-write and makes `--sandbox` a no-op.

    Codex reads it first (`exec/src/lib.rs`), so passing both silently discards
    the sandbox policy. A read-only run edited files in real testing because of
    this, and every run had been ignoring FLUXION_CODEX_SANDBOX_MODE.
    """
    ex = _ro_executor(tmp_path, sandbox_mode="read-only")
    plain = Task.create(channel="local", user_id="local", text="go", workspace=tmp_path)
    resumed = Task.create(
        channel="local",
        user_id="local",
        text="go",
        workspace=tmp_path,
        metadata={"executor_session_id": "s1"},
    )

    for task in (plain, resumed):
        assert "--full-auto" not in ex._build_command(task)


def test_configured_sandbox_mode_reaches_the_command_line(tmp_path):
    ex = _ro_executor(tmp_path, sandbox_mode="read-only")
    cmd = ex._build_command(
        Task.create(channel="local", user_id="local", text="go", workspace=tmp_path)
    )
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def test_writable_runs_still_get_a_sandbox(tmp_path):
    """Dropping --full-auto must not leave the mode unset."""
    ex = _ro_executor(tmp_path, sandbox_mode="")
    cmd = ex._build_command(
        Task.create(channel="local", user_id="local", text="go", workspace=tmp_path)
    )
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"


def test_raw_mode_keeps_the_agent_answer(tmp_path):
    """Without a FINAL_ANSWER marker the plain-text scan falls through and
    replaces the real answer with "Task completed."."""
    ex = _ro_executor(tmp_path)
    answer = "README.md is a short notes file."

    assert ex._extract_user_answer(answer, raw=True) == answer
    assert ex._extract_user_answer(answer) == "Task completed."


def test_raw_mode_answer_survives_an_empty_run(tmp_path):
    assert _ro_executor(tmp_path)._extract_user_answer("", raw=True) == "Task completed."
