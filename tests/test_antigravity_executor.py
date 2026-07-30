import threading
import time
from pathlib import Path
from unittest.mock import patch

from fluxion.core.models.task import Task
from fluxion.executors.antigravity.executor import AntiGravityExecutor
from fluxion.executors.common.limits import EXECUTOR_TEXT_HARD_LIMIT
from tests.test_antigravity_trajectory_stream import (
    RUN_WC,
    VIEW_A,
    VIEW_B,
    append_steps,
    write_db,
)


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


# ── live working notes ───────────────────────────────────────────────
def _seed_trajectory(tmp_path: Path, task_id: str, payloads: list[bytes]) -> Path:
    """Stand in for what agy writes while it runs: a log naming the conversation,
    and a DB under that name whose steps accumulate."""
    log_file = tmp_path / "logs" / f"task-{task_id}.agy.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("Print mode: conversation=11111111-2222-3333-4444-555555555555\n")
    conversations = tmp_path / "conversations"
    return write_db(conversations / "11111111-2222-3333-4444-555555555555.db", payloads)


def test_raw_mode_streams_working_notes_from_the_trajectory(tmp_path):
    """agy prints nothing until it dumps the answer, so this is the only signal
    a sub-agent window has that anything is happening at all."""
    executor = _executor(tmp_path)
    task = Task.create(
        channel="mcp",
        user_id="user",
        text="count the lines",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw"},
    )
    _seed_trajectory(tmp_path, task.id, [VIEW_A, RUN_WC])
    proc = _LingeringProc(stdout="")
    notes: list[str] = []
    seen = threading.Event()

    def on_reasoning(chunk: str) -> None:
        notes.append(chunk)
        seen.set()

    with (
        patch("subprocess.Popen", return_value=proc),
        patch(
            "fluxion.executors.antigravity.executor.ANTIGRAVITY_CONVERSATIONS_DIRS",
            (tmp_path / "conversations",),
        ),
    ):
        worker = threading.Thread(
            target=lambda: executor.execute(task, stream_reasoning=on_reasoning), daemon=True
        )
        worker.start()
        assert seen.wait(timeout=10), "no working notes arrived while the run was in flight"
        proc.release()
        worker.join(timeout=10)

    assert "Reading a.txt" in "".join(notes)
    assert "$ wc -l *.txt" in "".join(notes)


def test_im_mode_sends_no_working_notes(tmp_path):
    """The IM path renders one answer and has nowhere to put these."""
    executor = _executor(tmp_path)
    task = Task.create(
        channel="wechat",
        user_id="user",
        text="count the lines",
        workspace=tmp_path,
    )
    _seed_trajectory(tmp_path, task.id, [VIEW_A, RUN_WC])
    notes: list[str] = []

    with (
        patch("subprocess.Popen", return_value=_FakeProc(stdout="FINAL_ANSWER:\nhi\n")),
        patch(
            "fluxion.executors.antigravity.executor.ANTIGRAVITY_CONVERSATIONS_DIRS",
            (tmp_path / "conversations",),
        ),
    ):
        executor.execute(task, stream_reasoning=notes.append)

    assert notes == []


def test_a_resumed_run_does_not_replay_the_previous_turn(tmp_path):
    """The DB opens holding every prior turn; only this run's steps are working."""
    executor = _executor(tmp_path)
    task = Task.create(
        channel="mcp",
        user_id="user",
        text="and now count words",
        workspace=tmp_path,
        metadata={
            "prompt_mode": "raw",
            "executor_session_id": "11111111-2222-3333-4444-555555555555",
        },
    )
    db = _seed_trajectory(tmp_path, task.id, [VIEW_A, RUN_WC])
    proc = _LingeringProc(stdout="")
    notes: list[str] = []
    seen = threading.Event()

    def on_reasoning(chunk: str) -> None:
        notes.append(chunk)
        seen.set()

    with (
        patch("subprocess.Popen", return_value=proc),
        patch(
            "fluxion.executors.antigravity.executor.ANTIGRAVITY_CONVERSATIONS_DIRS",
            (tmp_path / "conversations",),
        ),
    ):
        worker = threading.Thread(
            target=lambda: executor.execute(task, stream_reasoning=on_reasoning), daemon=True
        )
        worker.start()
        # Whatever the poller does with the pre-existing rows, it must not be
        # this: give it time to get them wrong before adding the new one.
        assert not seen.wait(timeout=2), f"replayed the prior turn: {notes}"
        append_steps(db, [VIEW_B])
        assert seen.wait(timeout=10)
        proc.release()
        worker.join(timeout=10)

    joined = "".join(notes)
    assert joined == "Reading b.txt"


def test_stdout_takes_over_from_the_trajectory(tmp_path):
    """Under skip-permissions agy narrates its steps on stdout as well.

    Both sources then describe the same work, and alternating between them
    would make the consumer close and reopen an item on every switch. stdout
    wins because it is the one carrying the answer.
    """
    executor = _executor(tmp_path)
    task = Task.create(
        channel="mcp",
        user_id="user",
        text="count the lines",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw"},
    )
    db = _seed_trajectory(tmp_path, task.id, [VIEW_A])
    notes: list[str] = []

    with (
        patch("subprocess.Popen", return_value=_FakeProc(stdout="I will view a.txt\n")),
        patch(
            "fluxion.executors.antigravity.executor.ANTIGRAVITY_CONVERSATIONS_DIRS",
            (tmp_path / "conversations",),
        ),
    ):
        executor.execute(task, stream_output=lambda _: None, stream_reasoning=notes.append)

    append_steps(db, [RUN_WC])
    time.sleep(0.2)
    assert "$ wc -l" not in "".join(notes), "the poller outlived the run"


# ── permission refusals ──────────────────────────────────────────────
SOFT_DENY = 'I0723 18:12:03 tool_confirmation_manager.go:183] Print mode: soft-denying tool confirmation "Bash" at step 9\n'


def _with_agy_log(tmp_path: Path, task_id: str, text: str) -> None:
    log_file = tmp_path / "logs" / f"task-{task_id}.agy.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(text)


def test_a_silent_permission_refusal_is_reported_as_failure(tmp_path):
    """agy exits zero after a refusal, so the run otherwise reads as success.

    What reached the user was the empty-stdout fallback, "Task completed." —
    for a task that had done nothing at all.
    """
    executor = _executor(tmp_path)
    task = Task.create(channel="wechat", user_id="user", text="review the diff", workspace=tmp_path)
    _with_agy_log(tmp_path, task.id, SOFT_DENY)

    with patch("subprocess.Popen", return_value=_FakeProc(stdout="", returncode=0)):
        result = executor.execute(task)

    assert result.success is False
    assert "Bash" in result.summary
    assert "Task completed." not in result.summary


def test_narration_without_an_answer_is_also_a_failure(tmp_path):
    """Worse than the silent case: it looks like a real reply."""
    executor = _executor(tmp_path)
    task = Task.create(channel="wechat", user_id="user", text="review the diff", workspace=tmp_path)
    _with_agy_log(tmp_path, task.id, SOFT_DENY)
    narration = "I will start by searching the codebase for references to UpdaterController.\n"

    with patch("subprocess.Popen", return_value=_FakeProc(stdout=narration, returncode=0)):
        result = executor.execute(task)

    assert result.success is False
    assert "Bash" in result.summary


def test_a_run_that_recovered_from_a_refusal_still_succeeds(tmp_path):
    """A denied step is not fatal on its own; only one that ends the run is."""
    executor = _executor(tmp_path)
    task = Task.create(channel="wechat", user_id="user", text="review the diff", workspace=tmp_path)
    _with_agy_log(tmp_path, task.id, SOFT_DENY)

    with patch(
        "subprocess.Popen",
        return_value=_FakeProc(stdout="FINAL_ANSWER:\nhere is the review\nACTIONS_JSON:\n{}"),
    ):
        result = executor.execute(task)

    assert result.success is True
    assert result.summary == "here is the review"


def test_a_clean_run_is_untouched(tmp_path):
    executor = _executor(tmp_path)
    task = Task.create(channel="wechat", user_id="user", text="hi", workspace=tmp_path)
    _with_agy_log(tmp_path, task.id, "I0723 18:12:03 nothing interesting here\n")

    with patch("subprocess.Popen", return_value=_FakeProc(stdout="", returncode=0)):
        result = executor.execute(task)

    assert result.success is True


def test_raw_mode_reports_a_silent_refusal(tmp_path):
    """The sub-agent path has no marker, so silence is the only signal there."""
    executor = _executor(tmp_path)
    task = Task.create(
        channel="mcp",
        user_id="user",
        text="run the build",
        workspace=tmp_path,
        metadata={"prompt_mode": "raw"},
    )
    _with_agy_log(tmp_path, task.id, SOFT_DENY)

    with patch("subprocess.Popen", return_value=_FakeProc(stdout="", returncode=0)):
        result = executor.execute(task)

    assert result.success is False
    assert "Bash" in result.summary


# ── permission grant follows the run's declared write intent ──────────
def _cmd_for(mode: str | None, *, skip_permissions: bool = False) -> list[str]:
    from pathlib import Path as _Path

    executor = AntiGravityExecutor(
        timeout_sec=10,
        command="agy",
        sandbox=False,
        dangerously_skip_permissions=skip_permissions,
        print_timeout_sec=10,
        logs_dir=_Path("/tmp/fluxion-test-logs"),
    )
    metadata: dict = {"executor": "antigravity"}
    if mode is not None:
        metadata["subagent"] = {"agent": "antigravity", "mode": mode}
    task = Task.create(
        channel="local",
        user_id="local",
        text="do it",
        workspace=_Path("/tmp"),
        metadata=metadata,
    )
    return executor._build_command(
        task=task, prompt="p", resolved_command="agy", log_file=_Path("/tmp/x.log")
    )


def test_a_workspace_write_run_is_allowed_to_act():
    # The engine already refused unauthorized workspaces before this point, so
    # the run carries a write grant; agy's all-or-nothing switch honours it.
    assert "--dangerously-skip-permissions" in _cmd_for("workspace-write")


def test_a_read_only_run_is_not_handed_the_blanket_grant():
    # Withholding the flag is all Fluxion controls; whether agy then declines to
    # edit is agy's own call, and it does not always decline. Not a sandbox.
    assert "--dangerously-skip-permissions" not in _cmd_for("read-only")


def test_a_task_without_a_declared_mode_needs_the_global_flag():
    # IM/gateway tasks express no mode; don't infer an intent for them.
    assert "--dangerously-skip-permissions" not in _cmd_for(None)
    assert "--dangerously-skip-permissions" in _cmd_for(None, skip_permissions=True)


def test_the_global_flag_still_covers_read_only_runs():
    assert "--dangerously-skip-permissions" in _cmd_for("read-only", skip_permissions=True)


# ── answer length ────────────────────────────────────────────────────
# An answer does not only become an IM message. It is also what the provider
# gateway streams back as a Codex sub-agent's report to its parent, where a
# channel-sized cap cut reports mid-sentence — and often mid-path in a list of
# changed files, so the parent could not tell a file was missing.
def test_a_long_answer_is_not_cut_to_a_channel_sized_limit(tmp_path):
    executor = _executor(tmp_path)
    answer = "x" * 8000

    assert executor._extract_user_answer(f"FINAL_ANSWER:\n{answer}\nACTIONS_JSON:\n{{}}") == answer


def test_a_runaway_answer_still_hits_the_defensive_bound(tmp_path):
    executor = _executor(tmp_path)
    stdout = "FINAL_ANSWER:\n" + "x" * (EXECUTOR_TEXT_HARD_LIMIT + 500) + "\nACTIONS_JSON:\n{}"

    clipped = executor._extract_user_answer(stdout)

    assert len(clipped) == EXECUTOR_TEXT_HARD_LIMIT
    assert clipped.endswith("...(truncated)")
