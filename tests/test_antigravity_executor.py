import threading
from pathlib import Path
from unittest.mock import patch

from fluxion.core.models.task import Task
from fluxion.executors.antigravity.executor import AntiGravityExecutor


class _FakePipe:
    """Minimal stand-in for a Popen pipe that readline-drains a fixed string."""

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True)
        self._index = 0

    def readline(self) -> str:
        if self._index >= len(self._lines):
            return ""
        line = self._lines[self._index]
        self._index += 1
        return line

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = _FakePipe(stdout)
        self.stderr = _FakePipe(stderr)
        self.returncode = returncode

    def poll(self) -> int:
        # Process is already finished; the executor drains the pipes via its
        # reader threads and then joins them.
        return self.returncode

    def terminate(self) -> None:  # pragma: no cover - not exercised here
        pass


class _LingeringPipe:
    """Pipe that delivers its lines, then blocks (like agy holding stdout open
    during post-answer housekeeping) until the process is released."""

    def __init__(self, text: str, release: threading.Event) -> None:
        self._lines = text.splitlines(keepends=True)
        self._index = 0
        self._release = release

    def readline(self) -> str:
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        self._release.wait(timeout=10)
        return ""

    def close(self) -> None:
        pass


class _LingeringProc:
    """Fake process that stays alive (poll() is None) after printing the answer,
    so the executor can exercise its early-return-during-housekeeping path."""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self._release = threading.Event()
        self._rc = returncode
        self.returncode: int | None = None
        self.stdout = _LingeringPipe(stdout, self._release)
        self.stderr = _LingeringPipe(stderr, self._release)

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._release.wait(timeout=timeout)
        self.returncode = self._rc
        return self._rc

    def terminate(self) -> None:
        self.release()

    def release(self) -> None:
        self.returncode = self._rc
        self._release.set()


def _executor(tmp_path: Path) -> AntiGravityExecutor:
    return AntiGravityExecutor(
        timeout_sec=60,
        command="agy",
        sandbox=False,
        dangerously_skip_permissions=False,
        print_timeout_sec=30,
        logs_dir=tmp_path / "logs",
    )


def test_extract_execution_error_from_zero_exit_log(tmp_path):
    executor = _executor(tmp_path)
    log_file = tmp_path / "task.agy.log"
    log_file.write_text(
        "E0614 log.go:398] agent executor error: RESOURCE_EXHAUSTED (code 429): "
        "Individual quota reached. Resets in 1h15m43s.\n",
        encoding="utf-8",
    )

    error = executor._extract_execution_error(log_file, "")

    assert error.startswith("AntiGravity execution failed: RESOURCE_EXHAUSTED")
    assert "Individual quota reached" in error


def test_extract_execution_error_ignores_nonterminal_log_noise(tmp_path):
    executor = _executor(tmp_path)
    log_file = tmp_path / "task.agy.log"
    log_file.write_text(
        "W0614 log_context.go:117] Cache refresh failed temporarily\n"
        "I0614 printmode.go:155] sending message\n",
        encoding="utf-8",
    )

    assert executor._extract_execution_error(log_file, "") == ""


def test_extract_user_answer_uses_latest_final_answer(tmp_path):
    executor = _executor(tmp_path)
    stdout = (
        "FINAL_ANSWER:\nold image answer\nACTIONS_JSON:\n{}\n"
        "FINAL_ANSWER:\ncurrent text answer\nACTIONS_JSON:\n{}"
    )

    assert executor._extract_user_answer(stdout) == "current text answer"


def test_zero_exit_with_executor_error_does_not_return_stale_answer(tmp_path, monkeypatch):
    executor = _executor(tmp_path)
    task = Task.create(
        channel="wechat",
        user_id="user",
        text="What is today's news?",
        workspace=tmp_path,
    )
    proc = _FakeProc(
        stdout="FINAL_ANSWER:\nstale image answer\nACTIONS_JSON:\n{}",
        returncode=0,
    )
    monkeypatch.setattr(
        executor,
        "_extract_execution_error",
        lambda log_file, stderr: "AntiGravity execution failed: RESOURCE_EXHAUSTED",
    )

    with patch("subprocess.Popen", return_value=proc):
        result = executor.execute(task)

    assert result.success is False
    assert result.summary == "AntiGravity execution failed: RESOURCE_EXHAUSTED"


def test_execute_streams_final_answer_deltas(tmp_path):
    executor = _executor(tmp_path)
    task = Task.create(
        channel="wechat",
        user_id="user",
        text="What is today's news?",
        workspace=tmp_path,
    )
    proc = _FakeProc(
        stdout="FINAL_ANSWER:\nline one\nline two\nACTIONS_JSON:\n{}",
        returncode=0,
    )
    deltas: list[str] = []

    with patch("subprocess.Popen", return_value=proc):
        result = executor.execute(task, stream_output=deltas.append)

    assert result.success is True
    assert result.summary == "line one\nline two"
    # The streamed deltas reconstruct the answer text without duplication
    # (trailing whitespace is stripped only in the final summary).
    assert "".join(deltas).strip() == "line one\nline two"


def test_execute_returns_early_during_housekeeping(tmp_path):
    executor = _executor(tmp_path)
    task = Task.create(
        channel="slack",
        user_id="user",
        text="What is today's news?",
        workspace=tmp_path,
    )
    proc = _LingeringProc(
        stdout='FINAL_ANSWER:\nthe answer\nACTIONS_JSON:\n{"upload_files": []}',
        returncode=0,
    )

    with patch("subprocess.Popen", return_value=proc):
        result = executor.execute(task)
        # Returned while the process is still "running" its housekeeping tail.
        assert proc.poll() is None
        # Let the background reaper unblock and finish.
        proc.release()

    assert result.success is True
    assert result.summary == "the answer"
    assert result.exit_code == 0


def test_early_return_flags_pending_finalization_and_signals_completion(tmp_path):
    executor = _executor(tmp_path)
    task = Task.create(
        channel="slack",
        user_id="user",
        text="edit a file",
        workspace=tmp_path,
    )
    proc = _LingeringProc(
        stdout='FINAL_ANSWER:\ndone\nACTIONS_JSON:\n{"upload_files": []}',
        returncode=0,
    )

    with patch("subprocess.Popen", return_value=proc):
        result = executor.execute(task)
        # Early return while the process is still flushing its tail.
        assert result.pending_finalization is True
        assert proc.poll() is None
        # The engine's wait would block here — the process hasn't exited yet.
        assert executor.wait_for_finalization(task.id, timeout=0.05) is False
        # Process completes; the reaper sets the completion signal.
        proc.release()
        assert executor.wait_for_finalization(task.id, timeout=5) is True

    # Signal is consumed after the first successful wait — a later wait is a no-op.
    assert executor.wait_for_finalization(task.id, timeout=0) is True


def test_full_exit_does_not_flag_pending_finalization(tmp_path):
    executor = _executor(tmp_path)
    task = Task.create(
        channel="wechat",
        user_id="user",
        text="What is today's news?",
        workspace=tmp_path,
    )
    proc = _FakeProc(
        stdout="FINAL_ANSWER:\nthe answer\nACTIONS_JSON:\n{}",
        returncode=0,
    )

    with patch("subprocess.Popen", return_value=proc):
        result = executor.execute(task)

    # Process exited on its own; nothing to defer, so the engine finalizes now.
    assert result.pending_finalization is False
    assert executor.wait_for_finalization(task.id, timeout=0) is True


def test_answer_complete_requires_parseable_actions_json(tmp_path):
    executor = _executor(tmp_path)
    assert executor._answer_complete("FINAL_ANSWER:\nhi\nACTIONS_JSON:\n{}") is True
    assert executor._answer_complete("FINAL_ANSWER:\nhi\nACTIONS_JSON:\n```json\n{}\n```") is True
    # Incomplete JSON (still streaming) must not trigger early completion.
    assert executor._answer_complete('FINAL_ANSWER:\nhi\nACTIONS_JSON:\n{"upl') is False
    # No ACTIONS_JSON block yet.
    assert executor._answer_complete("FINAL_ANSWER:\nhi") is False
