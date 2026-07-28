from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.executors.antigravity.trajectory_stream import (
    POLL_INTERVAL_SEC,
    TrajectoryNarrator,
    read_max_step_idx,
)
from fluxion.executors.common.actions import resolve_uploads_from_text
from fluxion.executors.common.log_writer import write_jsonl_log
from fluxion.executors.common.process import (
    drain_reader_threads,
    start_process,
    terminate_process_tree,
)
from fluxion.executors.prompt_builder import AgentPromptBuilder, is_raw_prompt
from fluxion.slack_limits import SLACK_TEXT_SOFT_LIMIT
from fluxion.usage.history.parsing import ANTIGRAVITY_CONVERSATIONS_DIRS
from fluxion.workspace.antigravity_trajectory import find_conversation_db

# After the answer is printed, agy lingers for post-answer housekeeping while
# holding the process open. The reaper waits this out in the background; if the
# tail hangs far longer than ever observed, terminate so the process can't leak.
_HOUSEKEEPING_REAP_TIMEOUT_SEC = 120


class AntiGravityExecutor:
    def __init__(
        self,
        *,
        timeout_sec: int,
        command: str,
        sandbox: bool,
        dangerously_skip_permissions: bool,
        print_timeout_sec: int,
        logs_dir: Path,
        max_structured_uploads: int = 5,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._command = command.strip()
        self._sandbox = sandbox
        self._dangerously_skip_permissions = dangerously_skip_permissions
        self._print_timeout_sec = max(1, print_timeout_sec)
        self._max_structured_uploads = max(1, max_structured_uploads)
        self._logs_dir = Path(logs_dir).resolve()
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._prompt_builder = AgentPromptBuilder()
        # Per-task completion signals for early-return results. When agy answers
        # early we hand the still-running process to a reaper; the reaper sets the
        # event once the process has truly exited (and its post-answer tail — the
        # SQLite trajectory flush and any file writes — is done) so the engine can
        # defer its change report (changed_files / change_set / diff_summary).
        self._finalization_events: dict[str, threading.Event] = {}
        self._finalization_lock = threading.Lock()

    def name(self) -> str:
        return "antigravity"

    def enforces_read_only(self) -> bool:
        """`agy` has no read-only mode.

        Its `--sandbox` restricts terminal access, which is not the same
        promise: the agent can still edit files. Reporting True here would
        turn a refusal into a silent violation.
        """
        return False

    def supports(self, task: Task) -> bool:
        return True

    def execute(
        self,
        task: Task,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: Callable[[str], None] | None = None,
        # agy exposes no thinking anywhere, so this carries tool activity read
        # live from its trajectory DB rather than a train of thought.
        stream_reasoning: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        prompt = self._prompt_builder.build(task)
        raw_prompt = is_raw_prompt(task)
        # Only in raw mode: the IM path renders one answer and has nowhere to
        # put working notes. Read the resumed conversation's existing rows now,
        # before the process can add to them, so the floor excludes exactly the
        # prior turns and nothing of this one.
        narrator = (
            TrajectoryNarrator(since_idx=self._trajectory_floor(task))
            if stream_reasoning is not None and raw_prompt
            else None
        )
        narration_stop = threading.Event()
        start = time.monotonic()
        command: list[str] | None = None
        agy_log_file = self._logs_dir / f"task-{task.id}.agy.log"
        try:
            resolved_command = self._resolve_command()
            command = self._build_command(
                task=task,
                prompt=prompt,
                resolved_command=resolved_command,
                log_file=agy_log_file,
            )
            env = self._build_env(task.workspace)
            # Own process group: `agy` spawns language servers and build /
            # simulator tooling that would otherwise survive termination and
            # keep our stdout/stderr pipes open forever.
            proc = start_process(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(task.workspace),
                env=env,
            )
            out_holder: dict[str, list[str]] = {"stdout": [], "stderr": []}
            read_error: list[Exception] = []
            stream_state = {"sent_len": 0}
            stream_lock = threading.Lock()
            returning = {"on": False}
            answer_done = threading.Event()

            def _emit_stream_delta() -> None:
                with stream_lock:
                    if returning["on"] or stream_output is None:
                        return
                    current = self._extract_partial_user_answer(
                        "".join(out_holder["stdout"]), raw=raw_prompt
                    )
                    if len(current) <= stream_state["sent_len"]:
                        return
                    delta = current[stream_state["sent_len"] :]
                    stream_state["sent_len"] = len(current)
                if delta:
                    # stdout has started, so it takes over as the progress
                    # signal. Normally that means the answer, printed in one
                    # burst at the end; under
                    # FLUXION_ANTIGRAVITY_DANGEROUSLY_SKIP_PERMISSIONS agy also
                    # narrates each step there ("I will view the contents of
                    # a.txt"), from around the same time the trajectory fills
                    # up. Either way the two sources would now be describing
                    # the same work on two channels, and the consumer would
                    # close and reopen an item for every alternation.
                    narration_stop.set()
                    stream_output(delta)

            def _read_pipe(name: str, pipe: object | None) -> None:
                if pipe is None:
                    return
                try:
                    for chunk in iter(pipe.readline, ""):
                        if not chunk:
                            break
                        out_holder[name].append(chunk)
                        if name == "stdout":
                            _emit_stream_delta()
                            if not answer_done.is_set() and self._answer_complete(
                                "".join(out_holder["stdout"])
                            ):
                                answer_done.set()
                except Exception as exc:  # pragma: no cover - defensive
                    read_error.append(exc)
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            def _narrate(active: TrajectoryNarrator, emit: Callable[[str], None]) -> None:
                # agy names the conversation in its own log a few seconds after
                # launch — that is a real language-server startup, not a logging
                # delay — and the DB appears under that name. Until both exist
                # there is nothing to read; after that the log is never read
                # again.
                db_path: Path | None = None
                while not narration_stop.is_set():
                    if db_path is None:
                        session_id = self._extract_session_id(agy_log_file, "")
                        if session_id:
                            db_path = find_conversation_db(
                                session_id, ANTIGRAVITY_CONVERSATIONS_DIRS
                            )
                    if db_path is not None:
                        delta = active.poll(db_path)
                        if delta and not narration_stop.is_set():
                            emit(delta)
                    narration_stop.wait(POLL_INTERVAL_SEC)

            stdout_thread = threading.Thread(
                target=_read_pipe,
                args=("stdout", proc.stdout),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_read_pipe,
                args=("stderr", proc.stderr),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()
            if narrator is not None and stream_reasoning is not None:
                threading.Thread(
                    target=_narrate,
                    args=(narrator, stream_reasoning),
                    name="agy-narrator",
                    daemon=True,
                ).start()

            cancelled = False
            timed_out = False
            answered_early = False
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                if cancel_requested and cancel_requested():
                    cancelled = True
                    self._terminate_process(proc)
                    break
                if elapsed >= self._timeout_sec:
                    timed_out = True
                    self._terminate_process(proc)
                    break
                if answer_done.is_set():
                    # The user-visible answer is fully printed. agy now lingers for
                    # several seconds of housekeeping (title generation, credit
                    # refresh, conversation store flush) while still holding the
                    # process open. Return now and let a background reaper wait out
                    # the tail, so channels and MCP callers don't block on it.
                    answered_early = True
                    break
                time.sleep(0.1)

            if cancelled or timed_out:
                drain_reader_threads((stdout_thread, stderr_thread), proc=proc)
                if read_error:
                    raise read_error[0]
                stdout = "".join(out_holder["stdout"])
                stderr = "".join(out_holder["stderr"])
                _emit_stream_delta()
                duration = time.monotonic() - start
                summary = (
                    "Task canceled by user request."
                    if cancelled
                    else f"AntiGravity timed out after {self._timeout_sec}s"
                )
                return ExecutionResult(
                    success=False,
                    summary=summary,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=130 if cancelled else 124,
                    log_file=self._write_log(
                        task_id=task.id,
                        command=command,
                        stdout=stdout,
                        stderr=stderr,
                        agy_log_file=agy_log_file,
                    ),
                    executor_session_id=self._extract_session_id(agy_log_file, stderr),
                    duration_sec=duration,
                )

            if answered_early:
                # Stop streaming further deltas, snapshot what we have, and hand the
                # still-running process to a reaper that finalizes logs on exit.
                with stream_lock:
                    returning["on"] = True
                stdout = "".join(out_holder["stdout"])
                stderr = "".join(out_holder["stderr"])
                duration = time.monotonic() - start
                execution_error = self._extract_execution_error(
                    agy_log_file, stderr
                ) or self._blocked_tool_error(agy_log_file, stdout, raw=raw_prompt)
                success = not execution_error
                # Register a completion signal the engine awaits before computing
                # the change report — agy keeps flushing its SQLite trajectory DB
                # (and any file writes) during its post-answer tail, so reading it
                # now would yield an incomplete changed_files / diff_summary.
                finalize_event = threading.Event()
                with self._finalization_lock:
                    self._finalization_events[task.id] = finalize_event
                result = ExecutionResult(
                    success=success,
                    summary=self._extract_user_answer(stdout)
                    if success
                    else execution_error or "AntiGravity execution failed",
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=0 if success else 1,
                    log_file=self._write_log(
                        task_id=task.id,
                        command=command,
                        stdout=stdout,
                        stderr=stderr,
                        agy_log_file=agy_log_file,
                    ),
                    executor_session_id=self._extract_session_id(agy_log_file, stderr),
                    duration_sec=duration,
                    pending_finalization=True,
                    artifacts=self._resolve_action_uploads(stdout=stdout, workspace=task.workspace),
                )
                self._reap_in_background(
                    proc=proc,
                    threads=(stdout_thread, stderr_thread),
                    task_id=task.id,
                    command=command,
                    out_holder=out_holder,
                    agy_log_file=agy_log_file,
                    done_event=finalize_event,
                )
                return result

            # Process exited on its own before a complete answer (e.g. no
            # ACTIONS_JSON block, or an early failure).
            drain_reader_threads((stdout_thread, stderr_thread), proc=proc)
            if read_error:
                raise read_error[0]
            stdout = "".join(out_holder["stdout"])
            stderr = "".join(out_holder["stderr"])
            _emit_stream_delta()
            duration = time.monotonic() - start
            returncode = proc.returncode if proc.returncode is not None else -1
            execution_error = self._extract_execution_error(
                agy_log_file, stderr
            ) or self._blocked_tool_error(agy_log_file, stdout, raw=raw_prompt)
            success = returncode == 0 and not execution_error
            return ExecutionResult(
                success=success,
                summary=self._extract_user_answer(stdout)
                if success
                else execution_error or "AntiGravity execution failed",
                stdout=stdout,
                stderr=stderr,
                exit_code=returncode,
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout=stdout,
                    stderr=stderr,
                    agy_log_file=agy_log_file,
                ),
                executor_session_id=self._extract_session_id(agy_log_file, stderr),
                duration_sec=duration,
                artifacts=self._resolve_action_uploads(stdout=stdout, workspace=task.workspace),
            )
        except FileNotFoundError:
            duration = time.monotonic() - start
            stderr = (
                "Install Antigravity CLI and ensure `agy` is available in PATH, "
                "or set FLUXION_ANTIGRAVITY_COMMAND explicitly."
            )
            return ExecutionResult(
                success=False,
                summary="`agy` command not found on host PATH.",
                stdout="",
                stderr=stderr,
                exit_code=127,
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout="",
                    stderr=stderr,
                    agy_log_file=agy_log_file,
                ),
                duration_sec=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                summary=f"AntiGravity execution crashed: {exc}",
                stdout="",
                stderr=str(exc),
                exit_code=1,
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout="",
                    stderr=str(exc),
                    agy_log_file=agy_log_file,
                ),
                duration_sec=duration,
            )
        finally:
            # Every path out of here — answered, cancelled, timed out, crashed
            # before the process even started — ends the run as far as working
            # notes are concerned. Without this the poller outlives the run.
            narration_stop.set()

    def _trajectory_floor(self, task: Task) -> int:
        """The last trajectory row that belongs to an earlier turn.

        A fresh conversation has no DB yet and starts at -1; a resumed one opens
        holding every step of every prior turn, which would otherwise replay as
        this turn's working the moment the first poll lands.
        """
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        if not session_id:
            return -1
        db_path = find_conversation_db(session_id, ANTIGRAVITY_CONVERSATIONS_DIRS)
        return read_max_step_idx(db_path) if db_path is not None else -1

    def _build_command(
        self,
        *,
        task: Task,
        prompt: str,
        resolved_command: str,
        log_file: Path,
    ) -> list[str]:
        command = [
            resolved_command,
            "--add-dir",
            str(task.workspace),
            "--print-timeout",
            f"{self._print_timeout_sec}s",
            "--log-file",
            str(log_file),
        ]
        if self._sandbox:
            command.append("--sandbox")
        if self._dangerously_skip_permissions or self._write_intent_granted(task):
            command.append("--dangerously-skip-permissions")
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        if session_id:
            command.extend(["--conversation", session_id])
        # Per-task model override routes the run to a specific quota pool:
        # a Gemini model hits the Gemini pool, a Claude/GPT model the External
        # Models pool. Without it `agy` uses its default (Gemini), so External
        # never gets exercised.
        model = str(task.metadata.get("model", "")).strip()
        if model:
            command.extend(["--model", model])
        command.extend(["--print", prompt])
        return command

    def _resolve_command(self) -> str:
        if self._command and self._command != "agy":
            return self._command
        resolved = shutil.which("agy")
        if resolved:
            return resolved
        for candidate in self._command_search_candidates():
            path = Path(candidate).expanduser()
            if path.exists() and path.is_file():
                return str(path)
        return self._command or "agy"

    def _command_search_candidates(self) -> list[str]:
        return [
            "~/.local/bin/agy",
            "/opt/homebrew/bin/agy",
            "/usr/local/bin/agy",
        ]

    def _build_env(self, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        local_bin = str(Path.home() / ".local" / "bin")
        current_path = env.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        if local_bin not in path_parts:
            env["PATH"] = f"{local_bin}{os.pathsep}{current_path}" if current_path else local_bin
        venv = workspace / ".venv"
        venv_bin = venv / "bin"
        if venv_bin.exists() and venv_bin.is_dir():
            current_path = env.get("PATH", "")
            env["PATH"] = f"{venv_bin}:{current_path}" if current_path else str(venv_bin)
            env["VIRTUAL_ENV"] = str(venv)
        return env

    def _write_log(
        self,
        *,
        task_id: str,
        stdout: str,
        stderr: str,
        agy_log_file: Path,
        command: list[str] | None = None,
    ) -> str:
        agy_log = ""
        if agy_log_file.exists():
            try:
                agy_log = agy_log_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                agy_log = ""
        return write_jsonl_log(
            path=self._logs_dir / f"task-{task_id}.log",
            task_id=task_id,
            command=command,
            stdout=stdout or "",
            stderr=stderr or "",
            extra_streams={"agy": agy_log} if agy_log else None,
        )

    def _write_intent_granted(self, task: Task) -> bool:
        """Whether this run was already authorized to write, run by run.

        `agy` has one permission control and it is all-or-nothing: without
        `--dangerously-skip-permissions` it soft-denies `Edit` and `Bash`, which
        is every way it could change code. So a run that Fluxion has already
        authorized to write would otherwise stop without answering, and the only
        escape was a global env flag that auto-approved *every* run in *every*
        workspace — far broader than the task at hand.

        Deriving it instead grants nothing new. Two explicit decisions must
        already have been made before a task reaches here:

        * the caller asked for `mode="workspace-write"`, and
        * the engine authorized that workspace for writes — a registered project
          or an allow-listed path — refusing the run outright otherwise
          (`Settings.authorize_run_workspace`).

        Read-only runs are untouched — the flag is withheld — but that is not a
        guarantee they cannot write. Whether agy then soft-denies `Edit` is its
        own decision, and once it has trusted a workspace it stops asking:
        measured here, a read-only run in a previously used workspace edited a
        file with zero soft-deny entries in agy's log. `enforces_read_only()`
        returns False for exactly this reason. Withholding the flag is a
        narrower blast radius, not a sandbox.
        """
        subagent = task.metadata.get("subagent")
        if not isinstance(subagent, dict):
            # No declared mode (IM/gateway tasks): fall back to the global flag
            # rather than inferring an intent the caller never expressed.
            return False
        return str(subagent.get("mode") or "") == "workspace-write"

    def _resolve_action_uploads(self, *, stdout: str, workspace: Path) -> list[str]:
        """Files the agent declared via ACTIONS_JSON.upload_files.

        agy emits the same trailing block as the other executors, but nothing
        ever read it here, so every declared upload was dropped on the floor.
        """
        return resolve_uploads_from_text(
            text=stdout,
            workspace=workspace,
            max_files=self._max_structured_uploads,
        )

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        terminate_process_tree(proc)

    def _reap_in_background(
        self,
        *,
        proc: subprocess.Popen[str],
        threads: tuple[threading.Thread, ...],
        task_id: str,
        command: list[str] | None,
        out_holder: dict[str, list[str]],
        agy_log_file: Path,
        done_event: threading.Event | None = None,
    ) -> None:
        """Wait out agy's post-answer housekeeping off the request path.

        The result has already been returned to the caller; this thread only
        reaps the process (so it can't become a zombie), drains the reader
        threads, and rewrites the bundled log once the full agy log exists.
        ``done_event`` is set once the process has truly exited so the engine
        can compute an accurate change report (its SQLite trajectory is fully
        flushed and any file writes are on disk).
        """

        def _reap() -> None:
            try:
                proc.wait(timeout=_HOUSEKEEPING_REAP_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                self._terminate_process(proc)
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                # Bounded, and it kills the process group if a descendant is
                # still holding the pipes — otherwise these reader threads leak
                # one pair per run for the lifetime of the process.
                drain_reader_threads(threads, proc=proc)
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                self._write_log(
                    task_id=task_id,
                    command=command,
                    stdout="".join(out_holder["stdout"]),
                    stderr="".join(out_holder["stderr"]),
                    agy_log_file=agy_log_file,
                )
            except Exception:  # pragma: no cover - defensive
                pass
            finally:
                if done_event is not None:
                    done_event.set()

        threading.Thread(
            target=_reap,
            name=f"agy-reaper-{task_id[:8]}",
            daemon=True,
        ).start()

    def wait_for_finalization(self, task_id: str, timeout: float | None = None) -> bool:
        """Block until an early-returned task's process has fully exited.

        Returns True if finalization completed (or there was nothing pending),
        False on timeout. The engine calls this before computing the change
        report so changed_files / diff_summary reflect agy's fully-flushed
        trajectory (and any final file writes).
        """
        with self._finalization_lock:
            event = self._finalization_events.get(task_id)
        if event is None:
            return True
        finished = event.wait(timeout)
        if finished:
            with self._finalization_lock:
                self._finalization_events.pop(task_id, None)
        return finished

    def _extract_user_answer(self, stdout: str) -> str:
        text = (stdout or "").strip()
        if not text:
            return "Task completed."

        marker = "FINAL_ANSWER:"
        idx = text.rfind(marker)
        if idx != -1:
            block = text[idx + len(marker) :].strip()
            if block:
                lines = []
                for line in block.splitlines():
                    if line.strip().startswith("ACTIONS_JSON:"):
                        break
                    lines.append(line)
                cleaned = "\n".join(lines).strip()
                if cleaned:
                    return self._clip(cleaned, SLACK_TEXT_SOFT_LIMIT)
        return self._clip(text, SLACK_TEXT_SOFT_LIMIT)

    def _extract_partial_user_answer(self, stdout: str, *, raw: bool = False) -> str:
        text = stdout or ""
        if raw:
            # No marker will ever arrive. agy prints its narration to stdout, so
            # forwarding it verbatim is both the only option and the useful one:
            # the caller sees progress instead of a silent stream.
            return text
        marker = "FINAL_ANSWER:"
        idx = text.rfind(marker)
        if idx == -1:
            return ""
        block = text[idx + len(marker) :]
        action_idx = block.find("\nACTIONS_JSON:")
        if action_idx != -1:
            block = block[:action_idx]
        return block.lstrip("\n")

    def _answer_complete(self, stdout: str) -> bool:
        """True once stdout holds a final answer plus a parseable ACTIONS_JSON.

        agy emits ``FINAL_ANSWER: ... ACTIONS_JSON: {json}`` and then prints
        nothing more to stdout, so a complete trailing JSON object is a reliable
        marker that the user-visible answer is done.
        """
        text = stdout or ""
        if "FINAL_ANSWER:" not in text:
            return False
        marker = "ACTIONS_JSON:"
        idx = text.rfind(marker)
        if idx == -1:
            return False
        tail = text[idx + len(marker) :].strip()
        if not tail:
            return False
        if tail.startswith("```"):
            lines = tail.splitlines()[1:]
            content: list[str] = []
            for line in lines:
                if line.strip().startswith("```"):
                    break
                content.append(line)
            tail = "\n".join(content).strip()
            if not tail:
                return False
        try:
            obj, _ = json.JSONDecoder().raw_decode(tail)
        except Exception:
            return False
        return isinstance(obj, dict | list)

    def _extract_session_id(self, log_file: Path, stderr: str) -> str:
        text = stderr or ""
        if log_file.exists():
            try:
                text += "\n" + log_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        patterns = [
            r"Print mode:\s*conversation=([0-9a-fA-F-]{36})",
            r"Created conversation\s+([0-9a-fA-F-]{36})",
            r"conversationID=\"([0-9a-fA-F-]{36})\"",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            if matches:
                return matches[-1]
        return ""

    def _extract_execution_error(self, log_file: Path, stderr: str) -> str:
        """Detect executor failures that agy reports while still exiting zero.

        On resumed conversations, agy may print the full historical transcript
        to stdout after a quota/auth failure. Treating that as success causes
        Fluxion to resend the last historical FINAL_ANSWER.
        """
        text = (stderr or "") + "\n" + self._read_log(log_file)

        matches = re.findall(
            r"(?:agent executor error:\s*)?((?:RESOURCE_EXHAUSTED|UNAUTHENTICATED|PERMISSION_DENIED)"
            r".*)",
            text,
            flags=re.IGNORECASE,
        )
        if not matches:
            return ""
        detail = matches[-1].strip()
        return self._clip(f"AntiGravity execution failed: {detail}", SLACK_TEXT_SOFT_LIMIT)

    def _blocked_tool_error(self, log_file: Path, stdout: str, *, raw: bool) -> str:
        """Detect a run that a permission refusal ended before it could answer.

        Without `--dangerously-skip-permissions`, agy auto-approves its
        read-only tools (Search, ReadFile, ListDir) but soft-denies `Bash` and
        `Edit`. A soft-denied step is not reported as an error: agy exits zero
        and prints no answer, so the run reads as a success whose summary is
        the empty-stdout fallback ("Task completed.") or, when it narrated
        first, a few lines of "I will search for ..." — which is worse, because
        it looks like a real reply.

        Both were observed in practice before this check existed: of four
        soft-denied runs on this machine, none produced a FINAL_ANSWER, and two
        answered with narration alone. The task that suffers is the one that
        says "read-only" but still needs a shell to honour it — reviewing an
        uncommitted diff, for instance.

        Requiring *both* a refusal and a missing answer keeps a run that
        recovered from a denied step reporting success.
        """
        matches = re.findall(
            r'soft-denying tool confirmation:?\s*"([A-Za-z_]+)"',
            self._read_log(log_file),
        )
        if not matches:
            return ""
        # In raw mode no marker is ever printed, so any output at all is the
        # answer such as it is; this catches only the silent case. Narration
        # that stops short of an answer still gets through there.
        answered = bool(stdout.strip()) if raw else "FINAL_ANSWER:" in (stdout or "")
        if answered:
            return ""
        blocked = ", ".join(dict.fromkeys(matches))
        return self._clip(
            f"AntiGravity was blocked from using {blocked} and stopped without answering. "
            "Rerun with a task that needs no shell or file edits, or set "
            "FLUXION_ANTIGRAVITY_DANGEROUSLY_SKIP_PERMISSIONS=true to let it act "
            "on the workspace unattended.",
            SLACK_TEXT_SOFT_LIMIT,
        )

    def _read_log(self, log_file: Path) -> str:
        if not log_file.exists():
            return ""
        try:
            return log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 16] + "\n...(truncated)"
