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
from fluxion.executors.claude.events import (
    ClaudeEventCapture,
    extract_claude_stream_message,
    extract_claude_stream_reasoning,
    extract_claude_stream_text,
    parse_claude_stream_events,
)
from fluxion.executors.common.actions import (
    extract_actions_json,
    find_last_marker,
    upload_paths,
)
from fluxion.executors.common.limits import EXECUTOR_TEXT_HARD_LIMIT, clip_text
from fluxion.executors.common.log_writer import append_live_log, touch_live_log, write_jsonl_log
from fluxion.executors.common.process import (
    CleanupReport,
    drain_reader_threads,
    start_process,
    terminate_process_tree,
)
from fluxion.executors.prompt_builder import AgentPromptBuilder, is_raw_prompt
from fluxion.workspace.artifact_collector import select_uploadable_paths

# Quota keep-alive pings run on the cheapest tier at the lowest effort — they
# only need to register a sliver of usage to open a refreshed quota window, so
# paying for the default (Opus) model would be wasteful. "haiku" is a tier
# alias resolved server-side to the latest Haiku snapshot, so it never goes
# stale as models are upgraded (unlike a dated model id). Mirrors the Codex
# executor, which picks its cheapest model + lowest reasoning effort for pings.
_PING_MODEL = "haiku"
_PING_EFFORT = "low"

# Tool policy for a task marked `read_only` in its metadata, used when a caller
# has promised the user that this run cannot change anything — a Codex role file
# declaring `sandbox_mode = "read-only"`, for instance. Codex's own sandbox does
# not reach in here: the tools run in this process, so the promise is ours to
# keep.
_READ_ONLY_TOOLS = "Read,Grep,Glob"
_MUTATING_TOOLS = "Edit,Write,NotebookEdit,Bash"


class ClaudeExecutor:
    def __init__(
        self,
        *,
        timeout_sec: int,
        command: str,
        provider: str,
        auth_mode: str,
        model: str,
        base_url: str,
        api_key: str,
        auth_token: str,
        permission_mode: str,
        use_bare_mode: bool,
        append_system_prompt: str,
        allowed_tools: str,
        max_turns: int,
        max_structured_uploads: int,
        logs_dir: Path,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._command = command.strip()
        parsed_provider = provider.strip().lower()
        self._provider = (
            "third_party"
            if parsed_provider in {"ollama", "custom"}
            else (parsed_provider or "official")
        )
        self._auth_mode = auth_mode.strip().lower() or "login"
        self._model = model.strip()
        self._base_url = base_url.strip()
        self._api_key = api_key.strip()
        self._auth_token = auth_token.strip()
        self._permission_mode = permission_mode.strip()
        self._use_bare_mode = use_bare_mode
        self._append_system_prompt = append_system_prompt.strip()
        self._allowed_tools = allowed_tools.strip()
        self._max_turns = max(0, max_turns)
        self._max_structured_uploads = max(1, max_structured_uploads)
        self._logs_dir = logs_dir
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._prompt_builder = AgentPromptBuilder()

    def name(self) -> str:
        return "claude"

    def enforces_read_only(self) -> bool:
        """Honored in `_build_command` via `--disallowedTools`."""
        return True

    def supports(self, task: Task) -> bool:
        preferred = str(task.metadata.get("executor") or "").strip().lower()
        return preferred in {"", "claude"}

    def execute(
        self,
        task: Task,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: Callable[[str], None] | None = None,
        stream_reasoning: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        prompt = self._prompt_builder.build(task)
        resolved_command = self._resolve_command()
        command = self._build_command(task, prompt, resolved_command)
        start = time.monotonic()
        live_log_file = self._logs_dir / f"task-{task.id}.claude.log"
        try:
            touch_live_log(live_log_file)
            env = self._build_env(task.workspace)
            # Own process group: the CLI spawns MCP servers and tool
            # subprocesses that would otherwise survive termination and keep our
            # stdout/stderr pipes open forever.
            proc = start_process(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(task.workspace),
                env=env,
            )
            out_holder: dict[str, list[str]] = {"stdout": [], "stderr": []}
            comm_error: list[Exception] = []
            stream_state = {"sent_len": 0, "reasoning_len": 0}
            stream_lock = threading.Lock()
            raw_prompt = is_raw_prompt(task)

            def _emit_stream_delta() -> None:
                with stream_lock:
                    current = self._extract_partial_user_answer(
                        "".join(out_holder["stdout"]), raw=raw_prompt
                    )
                    if stream_output is None:
                        return
                    if len(current) <= stream_state["sent_len"]:
                        return
                    delta = current[stream_state["sent_len"] :]
                    stream_state["sent_len"] = len(current)
                if delta:
                    stream_output(delta)

            def _emit_reasoning_delta() -> None:
                # Only in raw mode: the IM path renders one answer and has
                # nowhere to put working notes.
                if stream_reasoning is None or not raw_prompt:
                    return
                with stream_lock:
                    current = extract_claude_stream_reasoning("".join(out_holder["stdout"]))
                    if len(current) <= stream_state["reasoning_len"]:
                        return
                    delta = current[stream_state["reasoning_len"] :]
                    stream_state["reasoning_len"] = len(current)
                if delta:
                    stream_reasoning(delta)

            def _read_pipe(name: str, pipe: subprocess.PIPE[str] | None) -> None:
                if pipe is None:
                    return
                try:
                    for chunk in iter(pipe.readline, ""):
                        if not chunk:
                            break
                        out_holder[name].append(chunk)
                        append_live_log(live_log_file, chunk)
                        if name == "stdout":
                            _emit_reasoning_delta()
                            _emit_stream_delta()
                except Exception as exc:  # pragma: no cover
                    comm_error.append(exc)
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

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

            cancelled = False
            timed_out = False
            cleanup = CleanupReport()
            next_live_touch = time.monotonic() + 30
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                now = time.monotonic()
                if now >= next_live_touch:
                    touch_live_log(live_log_file)
                    next_live_touch = now + 30
                if cancel_requested and cancel_requested():
                    cancelled = True
                    cleanup = self._terminate_process(proc)
                    break
                if elapsed >= self._timeout_sec:
                    timed_out = True
                    cleanup = self._terminate_process(proc)
                    break
                time.sleep(0.25)

            drain_reader_threads((stdout_thread, stderr_thread), proc=proc)
            if comm_error:
                raise comm_error[0]

            out_stdout = "".join(out_holder["stdout"])
            out_stderr = "".join(out_holder["stderr"])
            _emit_reasoning_delta()
            _emit_stream_delta()
            duration = time.monotonic() - start
            returncode = proc.returncode if proc.returncode is not None else -1
            payload = self._parse_json_output(out_stdout)
            event_capture = parse_claude_stream_events(out_stdout, workspace=task.workspace)
            if payload is None and event_capture.is_stream_json:
                payload = {
                    "result": event_capture.final_message,
                    "session_id": event_capture.session_id,
                }

            if cancelled:
                return self._build_result(
                    task=task,
                    command=command,
                    success=False,
                    summary="Task canceled by user request.",
                    stdout=out_stdout,
                    stderr=out_stderr,
                    process_cleanup=cleanup,
                    exit_code=130,
                    duration_sec=duration,
                    payload=payload,
                    event_capture=event_capture,
                )
            if timed_out:
                return self._build_result(
                    task=task,
                    command=command,
                    success=False,
                    summary=f"Claude Code timed out after {self._timeout_sec}s",
                    stdout=out_stdout,
                    stderr=out_stderr,
                    process_cleanup=cleanup,
                    exit_code=124,
                    duration_sec=duration,
                    payload=payload,
                    event_capture=event_capture,
                )

            success = returncode == 0
            if success:
                summary = self._extract_user_answer(payload, out_stdout)
            else:
                summary = self._extract_error_summary(payload, out_stderr)
            return self._build_result(
                task=task,
                command=command,
                success=success,
                summary=summary,
                stdout=out_stdout,
                stderr=out_stderr,
                exit_code=returncode,
                duration_sec=duration,
                payload=payload,
                event_capture=event_capture,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start
            searched = ", ".join(self._command_search_candidates())
            return self._build_result(
                task=task,
                command=command,
                success=False,
                summary="`claude` command not found on host PATH.",
                stdout="",
                stderr=(
                    "Install Claude Code CLI and ensure `claude` is available in PATH, "
                    f"or set FLUXION_CLAUDE_COMMAND explicitly. Searched: {searched}"
                ),
                exit_code=127,
                duration_sec=duration,
                payload=None,
                event_capture=None,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return self._build_result(
                task=task,
                command=command,
                success=False,
                summary=f"Claude Code execution crashed: {exc}",
                stdout="",
                stderr=str(exc),
                exit_code=1,
                duration_sec=duration,
                payload=None,
                event_capture=None,
            )

    def _build_result(
        self,
        *,
        task: Task,
        command: list[str],
        success: bool,
        summary: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        duration_sec: float,
        payload: dict | None,
        process_cleanup: CleanupReport | None = None,
        event_capture: ClaudeEventCapture | None = None,
    ) -> ExecutionResult:
        action_uploads = (
            self._resolve_action_uploads(payload=payload, workspace=task.workspace)
            if success
            else []
        )
        return ExecutionResult(
            success=success,
            summary=summary,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            process_cleanup=process_cleanup.to_payload() if process_cleanup else {},
            artifacts=action_uploads,
            changed_files=event_capture.changed_files if success and event_capture else [],
            risk_flags=event_capture.risk_flags if event_capture else [],
            token_usage=event_capture.token_usage if event_capture else {},
            file_operations=event_capture.operations if success and event_capture else [],
            diff_summary="",
            log_file=self._write_log(
                task_id=task.id,
                command=command,
                stdout=stdout,
                stderr=stderr,
            ),
            executor_session_id=(
                event_capture.session_id
                if event_capture and event_capture.session_id
                else self._extract_session_id(payload)
            ),
            resolved_model=event_capture.resolved_model if event_capture else "",
            model_resolution_source=(
                "executor_runtime" if event_capture and event_capture.resolved_model else ""
            ),
            duration_sec=duration_sec,
        )

    def _write_log(
        self,
        *,
        task_id: str,
        stdout: str,
        stderr: str,
        command: list[str] | None = None,
    ) -> str:
        return write_jsonl_log(
            path=self._logs_dir / f"task-{task_id}.log",
            task_id=task_id,
            command=command,
            stdout=stdout or "",
            stderr=stderr or "",
        )

    def _terminate_process(self, proc: subprocess.Popen[str]) -> CleanupReport:
        return terminate_process_tree(proc)

    def _build_env(self, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        # Normalize Claude provider auth so Fluxion owns the execution contract
        # instead of inheriting whatever ambient shell state happens to exist.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        venv = workspace / ".venv"
        venv_bin = venv / "bin"
        if venv_bin.exists() and venv_bin.is_dir():
            current_path = env.get("PATH", "")
            env["PATH"] = f"{venv_bin}:{current_path}" if current_path else str(venv_bin)
            env["VIRTUAL_ENV"] = str(venv)
        base_url = self._resolved_base_url()
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        api_key, auth_token = self._resolved_credentials()
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        return env

    def _resolved_base_url(self) -> str:
        if self._provider == "third_party":
            return self._base_url
        if self._provider == "official":
            return self._base_url
        return ""

    def _resolved_credentials(self) -> tuple[str, str]:
        if self._provider == "third_party":
            if self._auth_mode == "api_key":
                return self._api_key, ""
            if self._auth_mode == "auth_token":
                return "", self._auth_token
            if self._auth_mode == "none":
                return "", ""
            return "", ""

        if self._auth_mode == "api_key":
            return self._api_key, ""
        if self._auth_mode == "auth_token":
            return "", self._auth_token
        if self._auth_mode == "none":
            return "", ""
        # login mode intentionally leaves credentials unset so Claude CLI can use
        # the locally stored Claude.ai / Console login state.
        return "", ""

    @staticmethod
    def _is_ping_task(task: Task) -> bool:
        """A quota keep-alive ping, identified by its task name (set in the
        sub-agent metadata by the scheduler as ``ping-<rule id>``)."""
        subagent = task.metadata.get("subagent")
        if isinstance(subagent, dict):
            task_name = str(subagent.get("task_name", ""))
            return task_name.startswith("ping-") or "ping" in task_name.lower()
        return False

    def resolve_effective_model(self, task: Task) -> tuple[str, str]:
        """Return the model this executor can determine before launching Claude."""
        explicit = str(task.metadata.get("model") or "").strip()
        if explicit:
            return explicit, "requested_override"
        if self._is_ping_task(task):
            return _PING_MODEL, "fluxion_ping_policy"
        if self._model:
            return self._model, "executor_config"
        return "", "executor_runtime"

    def _build_command(self, task: Task, prompt: str, resolved_command: str) -> list[str]:
        command = [resolved_command]
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        if session_id:
            command.extend(["--resume", session_id])
        command.extend(["-p", "--verbose", "--output-format", "stream-json"])
        if is_raw_prompt(task):
            # Without this the CLI only reports a message once it is finished,
            # so a caller rendering output live shows nothing for the length of
            # a turn and then everything at once. Enabled for raw prompts only:
            # the IM path waits for the FINAL_ANSWER marker anyway and gains
            # nothing from token-level chunks.
            command.append("--include-partial-messages")
        if self._use_bare_mode:
            command.append("--bare")
        read_only = bool(task.metadata.get("read_only"))
        if read_only:
            # `--allowedTools` is an auto-approval list, not a restriction, and
            # `acceptEdits` waves edits through regardless — so neither one can
            # make a run read-only on its own. `--disallowedTools` is the hard
            # deny, and Bash belongs in it: a read-only role that can shell out
            # can still write.
            command.extend(["--allowedTools", _READ_ONLY_TOOLS])
            command.extend(["--disallowedTools", _MUTATING_TOOLS])
        else:
            if self._permission_mode:
                command.extend(["--permission-mode", self._permission_mode])
            if self._allowed_tools:
                command.append(f"--allowedTools={self._allowed_tools}")
        model_override = str(task.metadata.get("model") or "").strip()
        effort_override = str(task.metadata.get("reasoning_effort") or "").strip().lower()
        if model_override:
            command.extend(["--model", model_override])
        elif self._is_ping_task(task):
            # Keep-alive ping: force the cheapest tier + lowest effort,
            # overriding any configured model.
            command.extend(["--model", _PING_MODEL])
            if not effort_override:
                command.extend(["--effort", _PING_EFFORT])
        elif self._model:
            command.extend(["--model", self._model])
        if effort_override:
            command.extend(["--effort", effort_override])
        if self._append_system_prompt:
            command.extend(["--append-system-prompt", self._append_system_prompt])
        if self._max_turns > 0:
            command.extend(["--max-turns", str(self._max_turns)])
        command.append(prompt)
        return command

    def _resolve_command(self) -> str:
        if self._command and self._command != "claude":
            return self._command
        resolved = shutil.which("claude")
        if resolved:
            return resolved
        for candidate in self._command_search_candidates():
            path = Path(candidate).expanduser()
            if path.exists() and path.is_file():
                return str(path)
        return "claude"

    def _command_search_candidates(self) -> list[str]:
        return [
            "~/.local/bin/claude",
            "~/bin/claude",
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
        ]

    def _parse_json_output(self, stdout: str) -> dict | None:
        text = (stdout or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _extract_user_answer(self, payload: dict | None, stdout: str) -> str:
        if payload:
            result = self._extract_final_answer(str(payload.get("result", "")))
            if result:
                return self._clip(result, EXECUTOR_TEXT_HARD_LIMIT)
        return self._clip((stdout or "").strip() or "Task completed.", EXECUTOR_TEXT_HARD_LIMIT)

    def _extract_partial_user_answer(self, stdout: str, *, raw: bool = False) -> str:
        if raw:
            # No marker will ever arrive, so there is nothing to strip and
            # nothing to wait for.
            return extract_claude_stream_text(stdout)
        stream_message = extract_claude_stream_message(stdout)
        if not stream_message:
            return ""
        return self._extract_final_answer(stream_message)

    def _extract_error_summary(self, payload: dict | None, stderr: str) -> str:
        if payload:
            for key in ("error", "message", "result"):
                value = str(payload.get(key, "")).strip()
                if value:
                    return self._clip(value, EXECUTOR_TEXT_HARD_LIMIT)
        message = (stderr or "").strip()
        if message:
            return self._clip(message, EXECUTOR_TEXT_HARD_LIMIT)
        return "Claude Code execution failed"

    def _extract_session_id(self, payload: dict | None) -> str:
        if not payload:
            return ""
        session_id = str(payload.get("session_id", "")).strip()
        return session_id

    def _resolve_action_uploads(self, *, payload: dict | None, workspace: Path) -> list[str]:
        paths = self._extract_action_upload_paths(payload)
        if not paths:
            return []
        return select_uploadable_paths(
            workspace=workspace,
            raw_paths=paths,
            max_files=self._max_structured_uploads,
        )

    def _extract_action_upload_paths(self, payload: dict | None) -> list[str]:
        """Declared uploads, from the structured field or the trailing block.

        Claude Code can surface the ACTIONS_JSON object directly as
        `structured_output`; when it doesn't, it is parsed out of the result text
        like the other executors do.
        """
        if not isinstance(payload, dict):
            return []
        actions = payload.get("structured_output")
        if not isinstance(actions, dict):
            actions = extract_actions_json(str(payload.get("result", "")))
        return upload_paths(actions)

    def _extract_final_answer(self, result_text: str) -> str:
        text = (result_text or "").strip()
        if not text:
            return ""
        marker_match = self._find_last_marker(text, "FINAL_ANSWER")
        if marker_match is None:
            return text
        block = text[marker_match.end() :].strip()
        if not block:
            return ""
        lines: list[str] = []
        for line in block.splitlines():
            if re.match(r"^\s*ACTIONS_JSON:?\s*$", line):
                break
            lines.append(line)
        return "\n".join(lines).strip()

    def _find_last_marker(self, text: str, marker: str) -> re.Match[str] | None:
        return find_last_marker(text, marker)

    def _clip(self, text: str, limit: int) -> str:
        return clip_text(text, limit)
