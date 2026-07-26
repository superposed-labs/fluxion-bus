from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fluxion.codex_command import resolve_codex_command
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.executors.codex.events import (
    CodexEventCapture,
    extract_codex_json_stream_message,
    parse_codex_json_events,
)
from fluxion.executors.codex.prompt_builder import CodexPromptBuilder
from fluxion.executors.common.log_writer import append_live_log, touch_live_log, write_jsonl_log
from fluxion.slack_limits import SLACK_TEXT_SOFT_LIMIT
from fluxion.usage.history import pricing
from fluxion.usage.model_identity import identify_model
from fluxion.usage.model_rates import short_request_price_rank
from fluxion.workspace.artifact_collector import select_uploadable_paths

_CODEX_EFFORT_RANKS = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
}


def _codex_failure_summary(event_capture: CodexEventCapture) -> str:
    """Surface the actual stream error so the failure summary is actionable,
    instead of the opaque 'Codex execution failed'."""
    if event_capture.error_message:
        return f"Codex execution failed: {event_capture.error_message}"
    return "Codex execution failed"


class CodexExecutor:
    _cached_cheapest: tuple[str, str] | None = None

    def __init__(
        self,
        *,
        timeout_sec: int,
        skip_git_repo_check: bool,
        sandbox_mode: str,
        bypass_sandbox: bool,
        max_structured_uploads: int,
        logs_dir: Path,
    ) -> None:
        self._timeout_sec = timeout_sec
        self._skip_git_repo_check = skip_git_repo_check
        self._sandbox_mode = sandbox_mode
        self._bypass_sandbox = bypass_sandbox
        self._max_structured_uploads = max(1, max_structured_uploads)
        self._logs_dir = logs_dir
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._prompt_builder = CodexPromptBuilder()

    def name(self) -> str:
        return "codex"

    def supports(self, task: Task) -> bool:
        return True

    def execute(
        self,
        task: Task,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        prompt = self._prompt_builder.build(task)
        start = time.monotonic()
        command: list[str] | None = None
        live_log_file = self._logs_dir / f"task-{task.id}.codex.log"
        try:
            touch_live_log(live_log_file)
            command = self._build_command(task)
            env = self._build_env(task.workspace)
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(task.workspace),
                env=env,
            )
            out_holder: dict[str, list[str]] = {"stdout": [], "stderr": []}
            comm_error: list[Exception] = []
            stream_state = {"sent_len": 0}
            stream_lock = threading.Lock()

            def _emit_stream_delta() -> None:
                with stream_lock:
                    current = self._extract_partial_user_answer("".join(out_holder["stdout"]))
                    if stream_output is None:
                        return
                    if len(current) <= stream_state["sent_len"]:
                        return
                    delta = current[stream_state["sent_len"] :]
                    stream_state["sent_len"] = len(current)
                if delta:
                    stream_output(delta)

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
                            _emit_stream_delta()
                except Exception as exc:  # pragma: no cover
                    comm_error.append(exc)
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()

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
            next_live_touch = time.monotonic() + 30
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                now = time.monotonic()
                if now >= next_live_touch:
                    touch_live_log(live_log_file)
                    next_live_touch = now + 30
                if cancel_requested and cancel_requested():
                    cancelled = True
                    self._terminate_process(proc)
                    break
                if elapsed >= self._timeout_sec:
                    timed_out = True
                    self._terminate_process(proc)
                    break
                time.sleep(0.25)

            # Wait for the reader threads to drain stdout/stderr completely before
            # returning. If execute() returns early, Gateway may finalize the Slack
            # stream first and any late stdout deltas will show up as a second
            # follow-up message or make "reply complete" appear too early.
            stdout_thread.join()
            stderr_thread.join()
            if comm_error:
                raise comm_error[0]
            out_stdout = "".join(out_holder["stdout"])
            out_stderr = "".join(out_holder["stderr"])
            _emit_stream_delta()

            duration = time.monotonic() - start
            returncode = proc.returncode if proc.returncode is not None else -1
            if cancelled:
                return ExecutionResult(
                    success=False,
                    summary="Task canceled by user request.",
                    stdout=out_stdout,
                    stderr=out_stderr,
                    exit_code=130,
                    artifacts=[],
                    diff_summary="",
                    log_file=self._write_log(
                        task_id=task.id,
                        command=command,
                        stdout=out_stdout,
                        stderr=out_stderr,
                    ),
                    executor_session_id=self._extract_session_id(out_stderr),
                    duration_sec=duration,
                )
            if timed_out:
                return ExecutionResult(
                    success=False,
                    summary=f"Codex timed out after {self._timeout_sec}s",
                    stdout=out_stdout,
                    stderr=out_stderr,
                    exit_code=124,
                    artifacts=[],
                    diff_summary="",
                    log_file=self._write_log(
                        task_id=task.id,
                        command=command,
                        stdout=out_stdout,
                        stderr=out_stderr,
                    ),
                    executor_session_id=self._extract_session_id(out_stderr),
                    duration_sec=duration,
                )
            success = returncode == 0
            event_capture = parse_codex_json_events(out_stdout, workspace=task.workspace)
            answer_source = event_capture.final_message if event_capture.is_jsonl else out_stdout
            action_uploads = (
                self._resolve_action_uploads(stdout=answer_source, workspace=task.workspace)
                if success
                else []
            )
            summary = (
                self._extract_user_answer(answer_source)
                if success
                else _codex_failure_summary(event_capture)
            )
            result = ExecutionResult(
                success=success,
                summary=summary,
                stdout=out_stdout,
                stderr=out_stderr,
                exit_code=returncode,
                artifacts=action_uploads,
                changed_files=event_capture.changed_files if success else [],
                risk_flags=event_capture.risk_flags,
                file_operations=event_capture.operations if success else [],
                diff_summary="",
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout=out_stdout,
                    stderr=out_stderr,
                ),
                executor_session_id=event_capture.session_id
                or self._extract_session_id(out_stderr),
                duration_sec=duration,
            )
            if not success and self._should_retry_with_fresh_session(stderr=out_stderr, task=task):
                return self._run_fresh_session(task=task, prompt=prompt, start=start)
            return result
        except FileNotFoundError:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                summary="`codex` command not found on host PATH.",
                stdout="",
                stderr="Install Codex CLI and ensure `codex` is available in PATH.",
                exit_code=127,
                artifacts=[],
                diff_summary="",
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout="",
                    stderr="Install Codex CLI and ensure `codex` is available in PATH.",
                ),
                executor_session_id="",
                duration_sec=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return ExecutionResult(
                success=False,
                summary=f"Codex execution crashed: {exc}",
                stdout="",
                stderr=str(exc),
                exit_code=1,
                artifacts=[],
                diff_summary="",
                log_file=self._write_log(
                    task_id=task.id,
                    command=command,
                    stdout="",
                    stderr=str(exc),
                ),
                executor_session_id="",
                duration_sec=duration,
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

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            if proc.poll() is None:
                proc.kill()

    def _build_env(self, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        venv = workspace / ".venv"
        venv_bin = venv / "bin"
        if venv_bin.exists() and venv_bin.is_dir():
            current_path = env.get("PATH", "")
            env["PATH"] = f"{venv_bin}:{current_path}" if current_path else str(venv_bin)
            env["VIRTUAL_ENV"] = str(venv)
        return env

    def _extract_user_answer(self, stdout: str) -> str:
        text = (stdout or "").strip()
        if not text:
            return "Task completed."

        marker_match = self._find_last_marker(text, "FINAL_ANSWER")
        if marker_match is not None:
            block = text[marker_match.end() :].strip()
            if block:
                lines = []
                for line in block.splitlines():
                    if line.strip().lower().startswith("tokens used"):
                        break
                    if re.match(r"^\s*ACTIONS_JSON:?\s*$", line):
                        break
                    lines.append(line)
                cleaned = "\n".join(lines).strip()
                if cleaned:
                    return self._clip(cleaned, SLACK_TEXT_SOFT_LIMIT)

        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == "codex":
                tail = []
                for next_line in lines[i + 1 :]:
                    if next_line.strip().lower().startswith("tokens used"):
                        break
                    if next_line.strip().startswith("ACTIONS_JSON:"):
                        break
                    tail.append(next_line)
                candidate = "\n".join(tail).strip()
                if candidate:
                    return self._clip(candidate, SLACK_TEXT_SOFT_LIMIT)

        return "Task completed."

    def _extract_partial_user_answer(self, stdout: str) -> str:
        json_message = extract_codex_json_stream_message(stdout)
        if json_message:
            return self._extract_partial_user_answer(json_message)
        if parse_codex_json_events(stdout, workspace=Path.cwd()).is_jsonl:
            return ""
        text = stdout or ""
        marker_match = self._find_last_marker(text, "FINAL_ANSWER")
        if marker_match is None:
            return ""
        block = text[marker_match.end() :]
        action_match = re.search(r"\n\s*ACTIONS_JSON:?\s*(?:\n|$)", block)
        if action_match is not None:
            block = block[: action_match.start()]
        lower_block = block.lower()
        token_idx = lower_block.find("\ntokens used")
        if token_idx != -1:
            block = block[:token_idx]
        return block.lstrip("\n")

    def _resolve_action_uploads(self, *, stdout: str, workspace: Path) -> list[str]:
        paths = self._extract_action_upload_paths(stdout)
        if not paths:
            return []
        return select_uploadable_paths(
            workspace=workspace,
            raw_paths=paths,
            max_files=self._max_structured_uploads,
        )

    def _extract_action_upload_paths(self, stdout: str) -> list[str]:
        payload = self._extract_actions_json(stdout)
        if not isinstance(payload, dict):
            return []
        raw = payload.get("upload_files")
        if not isinstance(raw, list):
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for item in raw:
            value = ""
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                value = str(item.get("path", "")).strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(value)
        return paths

    def _extract_actions_json(self, stdout: str) -> dict | None:
        text = stdout or ""
        marker_match = self._find_last_marker(text, "ACTIONS_JSON")
        if marker_match is None:
            return None
        tail = text[marker_match.end() :].strip()
        if not tail:
            return None

        if tail.startswith("```"):
            lines = tail.splitlines()
            if lines:
                lines = lines[1:]
            content: list[str] = []
            for line in lines:
                if line.strip().startswith("```"):
                    break
                content.append(line)
            tail = "\n".join(content).strip()
            if not tail:
                return None

        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(tail)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
        return None

    def _find_last_marker(self, text: str, marker: str) -> re.Match[str] | None:
        matches = list(re.finditer(rf"(?m)^\s*{re.escape(marker)}:?\s*$", text or ""))
        return matches[-1] if matches else None

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 16] + "\n...(truncated)"

    def _extract_session_id(self, stderr: str) -> str:
        match = re.search(r"session id:\s*([0-9a-fA-F-]{36})", stderr or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _build_command(self, task: Task) -> list[str]:
        resolved_command = self._resolve_command()
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        model_override = str(task.metadata.get("model") or "").strip()
        use_bypass = self._bypass_sandbox or self._sandbox_mode == "danger-full-access"

        is_ping = False
        subagent = task.metadata.get("subagent")
        if isinstance(subagent, dict):
            task_name = str(subagent.get("task_name", ""))
            if task_name.startswith("ping-") or "ping" in task_name.lower():
                is_ping = True

        if session_id:
            command = [resolved_command, "exec", "resume"]
            command.append("--json")
            if use_bypass:
                command.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                command.append("--full-auto")
            if self._skip_git_repo_check:
                command.append("--skip-git-repo-check")
            if model_override:
                command.extend(["-m", model_override])
            elif is_ping:
                # Skip ~/.codex/config.toml so the keep-alive ping doesn't load
                # the user's MCP servers + plugins into context (~21% fewer
                # tokens, measured). Auth still resolves via CODEX_HOME.
                self._append_ping_model_args(command)
            command.append(session_id)
            return command

        command = [resolved_command, "exec", "--json"]
        if use_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.append("--full-auto")
            if self._sandbox_mode:
                command.extend(["--sandbox", self._sandbox_mode])
        if self._skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if model_override:
            command.extend(["-m", model_override])
        elif is_ping:
            # See resume branch above: trim MCP/plugin context for the ping.
            self._append_ping_model_args(command)
        return command

    def _append_ping_model_args(self, command: list[str]) -> None:
        command.append("--ignore-user-config")
        model, effort = self._resolve_cheapest_model_and_effort()
        if not model:
            # The live catalog is unavailable. Let the Codex CLI choose its
            # built-in current default instead of pinning a version that may
            # eventually be retired.
            return
        command.extend(["-m", model])
        if effort:
            command.extend(["-c", f"model_reasoning_effort={effort}"])

    def _should_retry_with_fresh_session(self, *, stderr: str, task: Task) -> bool:
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        if not session_id:
            return False
        text = (stderr or "").lower()
        markers = [
            "unknown session",
            "session not found",
            "could not resume",
            "invalid session",
        ]
        return any(m in text for m in markers)

    def _run_fresh_session(self, *, task: Task, prompt: str, start: float) -> ExecutionResult:
        use_bypass = self._bypass_sandbox or self._sandbox_mode == "danger-full-access"
        resolved_command = self._resolve_command()
        command = [resolved_command, "exec", "--json"]
        if use_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.append("--full-auto")
            if self._sandbox_mode:
                command.extend(["--sandbox", self._sandbox_mode])
        if self._skip_git_repo_check:
            command.append("--skip-git-repo-check")
        env = self._build_env(task.workspace)
        proc = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=str(task.workspace),
            env=env,
            timeout=self._timeout_sec,
            check=False,
        )
        duration = time.monotonic() - start
        success = proc.returncode == 0
        event_capture = parse_codex_json_events(proc.stdout, workspace=task.workspace)
        answer_source = event_capture.final_message if event_capture.is_jsonl else proc.stdout
        action_uploads = (
            self._resolve_action_uploads(stdout=answer_source, workspace=task.workspace)
            if success
            else []
        )
        return ExecutionResult(
            success=success,
            summary=self._extract_user_answer(answer_source)
            if success
            else _codex_failure_summary(event_capture),
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            artifacts=action_uploads,
            changed_files=event_capture.changed_files if success else [],
            risk_flags=event_capture.risk_flags,
            file_operations=event_capture.operations if success else [],
            diff_summary="",
            log_file=self._write_log(
                task_id=task.id,
                command=command,
                stdout=proc.stdout,
                stderr=proc.stderr,
            ),
            executor_session_id=event_capture.session_id or self._extract_session_id(proc.stderr),
            duration_sec=duration,
        )

    def _resolve_cheapest_model_and_effort(self) -> tuple[str, str]:
        if CodexExecutor._cached_cheapest is not None:
            return CodexExecutor._cached_cheapest

        resolved_cmd = self._resolve_command()
        try:
            res = subprocess.run(
                [resolved_cmd, "debug", "models"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5.0,
            )
            data = json.loads(res.stdout)
            models = data.get("models", [])
            if not models:
                return "", ""

            candidates = []
            for model in models:
                slug = str(model.get("slug") or "").strip()
                if not slug:
                    continue
                effort_rank, effort = _lowest_codex_effort(model)
                version_rank = tuple(-part for part in identify_model("codex", slug).version)
                candidates.append(
                    (
                        short_request_price_rank(pricing.current_rates_for("codex", slug)),
                        effort_rank,
                        version_rank,
                        slug,
                        effort,
                    )
                )
            if not candidates:
                return "", ""

            _price, _effort_rank, _version, slug, effort = min(candidates)

            CodexExecutor._cached_cheapest = (slug, effort)
            return slug, effort
        except Exception:
            return "", ""

    def _resolve_command(self) -> str:
        return resolve_codex_command() or "codex"


def _lowest_codex_effort(model: dict) -> tuple[int, str]:
    supported = model.get("supported_reasoning_levels", [])
    efforts = []
    for item in supported if isinstance(supported, list) else []:
        if not isinstance(item, dict):
            continue
        effort = str(item.get("effort") or "").lower()
        if effort in _CODEX_EFFORT_RANKS:
            efforts.append((_CODEX_EFFORT_RANKS[effort], effort))
    return min(efforts, default=(len(_CODEX_EFFORT_RANKS), ""))
