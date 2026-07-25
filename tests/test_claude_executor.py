from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from fluxion.core.models.task import Task
from fluxion.executors.claude.executor import ClaudeExecutor


def _wait_for(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _executor(tmp_path: Path, *, model: str = "") -> ClaudeExecutor:
    return ClaudeExecutor(
        timeout_sec=60,
        command="",
        provider="official",
        auth_mode="login",
        model=model,
        base_url="",
        api_key="",
        auth_token="",
        permission_mode="acceptEdits",
        use_bare_mode=False,
        append_system_prompt="",
        allowed_tools="Bash,Read,Edit",
        max_turns=0,
        max_structured_uploads=8,
        logs_dir=tmp_path / "logs",
    )


def _task(tmp_path: Path, task_name: str | None) -> Task:
    metadata = {}
    if task_name is not None:
        metadata["subagent"] = {"task_name": task_name}
    return Task.create(
        channel="local",
        user_id="local",
        text="hi",
        workspace=tmp_path,
        metadata=metadata,
    )


def _model_in(command: list[str]) -> str | None:
    return command[command.index("--model") + 1] if "--model" in command else None


def test_ping_forces_cheapest_tier_alias_and_low_effort(tmp_path):
    # Even with a pricey model configured, a ping must drop to the haiku alias.
    ex = _executor(tmp_path, model="claude-opus-4-8")
    cmd = ex._build_command(_task(tmp_path, "ping-managed_autoping_claude_7d"), "hi", "claude")
    assert _model_in(cmd) == "haiku"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "low"


def test_ping_overrides_default_model_when_unset(tmp_path):
    # No configured model: a normal run omits --model (CLI default), but a ping
    # still pins the cheap tier rather than inheriting the default (Opus).
    ex = _executor(tmp_path, model="")
    ping = ex._build_command(_task(tmp_path, "ping-foo"), "hi", "claude")
    assert _model_in(ping) == "haiku"
    assert "--effort" in ping


def test_non_ping_keeps_configured_model_and_no_effort(tmp_path):
    ex = _executor(tmp_path, model="claude-opus-4-8")
    cmd = ex._build_command(_task(tmp_path, "sched-foo"), "hi", "claude")
    assert _model_in(cmd) == "claude-opus-4-8"
    assert "--effort" not in cmd


def test_non_ping_without_model_omits_model_flag(tmp_path):
    ex = _executor(tmp_path, model="")
    cmd = ex._build_command(_task(tmp_path, None), "hi", "claude")
    assert "--model" not in cmd
    assert "--effort" not in cmd


def test_build_command_uses_stream_json(tmp_path):
    ex = _executor(tmp_path)
    cmd = ex._build_command(_task(tmp_path, None), "hi", "claude")

    assert "--verbose" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--allowedTools=Bash,Read,Edit" in cmd


def test_build_command_uses_model_override_over_ping_default(tmp_path):
    ex = _executor(tmp_path, model="sonnet")
    cmd = ex._build_command(
        Task.create(
            channel="local",
            user_id="local",
            text="hi",
            workspace=tmp_path,
            metadata={
                "model": "claude-fable-5",
                "subagent": {"task_name": "ping-explicit-model"},
            },
        ),
        "hi",
        "claude",
    )

    assert _model_in(cmd) == "claude-fable-5"
    assert "--effort" not in cmd


def test_extract_answer_accepts_markers_without_colon_and_fenced_actions(tmp_path):
    ex = _executor(tmp_path)
    result = 'FINAL_ANSWER\nfinished\nACTIONS_JSON\n```json\n{"upload_files": ["result.json"]}\n```'

    assert ex._extract_final_answer(result) == "finished"
    assert ex._extract_action_upload_paths({"result": result}) == ["result.json"]


def test_extract_partial_answer_from_claude_stream_event(tmp_path):
    ex = _executor(tmp_path)
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "I will inspect first."}]},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "FINAL_ANSWER\nstream me\nACTIONS_JSON\n{}"}
                        ]
                    },
                }
            ),
        ]
    )

    assert ex._extract_partial_user_answer(stdout) == "stream me"


def test_execute_writes_live_log_without_streaming_json_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, time",
                "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'session-1'}), flush=True)",
                "time.sleep(0.5)",
                "print(json.dumps({'type': 'result', 'subtype': 'success', 'session_id': 'session-1', 'result': 'FINAL_ANSWER\\nclaude done\\nACTIONS_JSON\\n{}'}), flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    ex = _executor(tmp_path)
    task = _task(tmp_path, None)
    live_log = tmp_path / "logs" / f"task-{task.id}.claude.log"
    deltas: list[str] = []
    holder: dict[str, object] = {}

    def _run() -> None:
        holder["result"] = ex.execute(task, stream_output=deltas.append)

    thread = threading.Thread(target=_run)
    thread.start()

    assert _wait_for(lambda: live_log.exists() and live_log.stat().st_size > 0)
    assert '"type": "system"' in live_log.read_text(encoding="utf-8")

    thread.join(timeout=3)
    assert not thread.is_alive()
    result = holder["result"]

    assert result.success is True
    assert result.summary == "claude done"
    assert result.changed_files == []
    assert "".join(deltas) == "claude done"
    assert '"type": "system"' not in "".join(deltas)


def test_raw_prompt_mode_sends_the_bare_prompt_and_still_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Raw mode removes the FINAL_ANSWER contract, and the streaming reader used
    # to key on exactly that marker: without this path the consumer would see an
    # empty stream for the entire run and then a single terminal event.
    argv_dump = tmp_path / "argv.json"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, sys, time",
                f"open({str(argv_dump)!r}, 'w').write(json.dumps(sys.argv[1:]))",
                "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1'}), flush=True)",
                "print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'looking around'}]}}), flush=True)",
                "time.sleep(0.2)",
                "print(json.dumps({'type': 'result', 'subtype': 'success', 'session_id': 's1', 'result': 'all done'}), flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    task = Task.create(
        channel="local",
        user_id="local",
        text="review the parser",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw"},
    )
    deltas: list[str] = []
    result = _executor(tmp_path).execute(task, stream_output=deltas.append)

    assert json.loads(argv_dump.read_text(encoding="utf-8"))[-1] == "review the parser"
    assert "looking around" in "".join(deltas)
    assert result.success is True
    assert result.summary == "all done"


def test_raw_prompt_mode_asks_the_cli_for_partial_messages(tmp_path):
    """Message-granular output makes a live consumer stall for a whole turn."""
    task = Task.create(
        channel="local",
        user_id="local",
        text="go",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw"},
    )
    assert "--include-partial-messages" in _executor(tmp_path)._build_command(task, "go", "claude")


def test_default_mode_does_not_ask_for_partial_messages(tmp_path):
    """The IM path waits for the FINAL_ANSWER marker and gains nothing from them."""
    cmd = _executor(tmp_path)._build_command(_task(tmp_path, None), "go", "claude")
    assert "--include-partial-messages" not in cmd


def _read_only_task(tmp_path: Path) -> Task:
    return Task.create(
        channel="local",
        user_id="local",
        text="explain this",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw", "read_only": True},
    )


def test_read_only_task_hard_denies_the_mutating_tools(tmp_path):
    """`--allowedTools` only auto-approves; without a deny list nothing is blocked."""
    cmd = _executor(tmp_path)._build_command(_read_only_task(tmp_path), "go", "claude")
    denied = cmd[cmd.index("--disallowedTools") + 1].split(",")

    assert {"Edit", "Write", "Bash"} <= set(denied)
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"


def test_read_only_task_never_gets_accept_edits(tmp_path):
    """acceptEdits waves edits through no matter what the allow list says."""
    cmd = _executor(tmp_path)._build_command(_read_only_task(tmp_path), "go", "claude")
    assert "acceptEdits" not in cmd


def test_ordinary_task_keeps_its_configured_permissions(tmp_path):
    cmd = _executor(tmp_path)._build_command(_task(tmp_path, None), "go", "claude")
    assert "--disallowedTools" not in cmd
    assert "acceptEdits" in cmd
