from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

from fluxion.codex_command import resolve_codex_command
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.executors.codex.events import (
    CodexEventCapture,
    extract_codex_json_stream_message,
    extract_codex_json_stream_text,
    extract_codex_stream_reasoning,
    parse_codex_json_events,
)
from fluxion.executors.codex.prompt_builder import CodexPromptBuilder
from fluxion.executors.common.actions import (
    extract_actions_json,
    find_last_marker,
    resolve_uploads_from_text,
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
from fluxion.executors.prompt_builder import is_raw_prompt
from fluxion.usage.history import pricing
from fluxion.usage.model_identity import identify_model
from fluxion.usage.model_rates import short_request_price_rank

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


# Providers the Provider Gateway installs into `~/.codex/config.toml`. A child
# `codex exec` pointed at one of these would be talking to Fluxion, not to a model.
_GATEWAY_PROVIDER_PREFIX = "fluxion_"
_NATIVE_PROVIDER = "openai"


def _codex_home() -> Path:
    # Read per call, not at import: `_build_env` hands the child whatever
    # CODEX_HOME this process has, so the config inspected here is the one the
    # child will actually load.
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def _configured_model_provider(config_path: Path) -> str:
    """The top-level `model_provider` a child `codex exec` would inherit.

    Profiles are not consulted: one is active only under `--profile`, which this
    executor never passes.
    """
    try:
        with config_path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        # A config Codex itself cannot read is Codex's problem to report.
        return ""
    value = parsed.get("model_provider")
    return value if isinstance(value, str) else ""


def _sandbox_mode(configured: str, read_only: bool) -> str:
    """The sandbox policy to pass to `codex exec`.

    Deliberately never `--full-auto`. That flag is deprecated, and when present
    Codex forces `workspace-write` and ignores `--sandbox` outright
    (`exec/src/lib.rs`: `if removed_full_auto { Some(SandboxMode::WorkspaceWrite) }`
    is checked before the `--sandbox` argument is even read).

    Passing both is what made a "read-only" run edit files in testing, and it had
    also been quietly discarding `FLUXION_CODEX_SANDBOX_MODE` for every run.
    """
    if read_only:
        return "read-only"
    return configured or "workspace-write"


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

    def enforces_read_only(self) -> bool:
        """`codex exec -s read-only` is a documented sandbox policy."""
        return True

    def supports_native_images(self) -> bool:
        """Both fresh and resumed `codex exec` turns accept `--image`."""
        return True

    def native_image_media_types(self) -> frozenset[str]:
        """Formats passed to Codex's native ``--image`` interface."""
        return frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})

    def supports(self, task: Task) -> bool:
        return True

    def execute(
        self,
        task: Task,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: Callable[[str], None] | None = None,
        stream_reasoning: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        prompt = self._prompt_builder.build(
            task,
            native_image_media_types=self.native_image_media_types(),
        )
        start = time.monotonic()
        command: list[str] | None = None
        live_log_file = self._logs_dir / f"task-{task.id}.codex.log"
        try:
            touch_live_log(live_log_file)
            command = self._build_command(task)
            env = self._build_env(task.workspace)
            # Own process group: the CLI spawns MCP servers and tool
            # subprocesses that would otherwise survive termination and keep our
            # stdout/stderr pipes open forever.
            proc = start_process(
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
                # Raw mode only: the IM path renders one answer and has
                # nowhere to put working notes.
                if stream_reasoning is None or not raw_prompt:
                    return
                with stream_lock:
                    current = extract_codex_stream_reasoning("".join(out_holder["stdout"]))
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

            # Wait for the reader threads to drain stdout/stderr completely before
            # returning. If execute() returns early, Gateway may finalize the Slack
            # stream first and any late stdout deltas will show up as a second
            # follow-up message or make "reply complete" appear too early.
            drain_reader_threads((stdout_thread, stderr_thread), proc=proc)
            if comm_error:
                raise comm_error[0]
            out_stdout = "".join(out_holder["stdout"])
            out_stderr = "".join(out_holder["stderr"])
            _emit_reasoning_delta()
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
                    process_cleanup=cleanup.to_payload(),
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
                    process_cleanup=cleanup.to_payload(),
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
                self._extract_user_answer(answer_source, raw=raw_prompt)
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

    def _terminate_process(self, proc: subprocess.Popen[str]) -> CleanupReport:
        return terminate_process_tree(proc)

    def _build_env(self, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        venv = workspace / ".venv"
        venv_bin = venv / "bin"
        if venv_bin.exists() and venv_bin.is_dir():
            current_path = env.get("PATH", "")
            env["PATH"] = f"{venv_bin}:{current_path}" if current_path else str(venv_bin)
            env["VIRTUAL_ENV"] = str(venv)
        return env

    def _extract_user_answer(self, stdout: str, *, raw: bool = False) -> str:
        text = (stdout or "").strip()
        if not text:
            return "Task completed."
        if raw:
            # The caller owns the prompt, so no FINAL_ANSWER marker will ever
            # appear and there is no `codex` banner line to scan for. Without
            # this the scan below falls through and throws the real answer
            # away, reporting "Task completed." in its place.
            return self._clip(text, EXECUTOR_TEXT_HARD_LIMIT)

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
                    return self._clip(cleaned, EXECUTOR_TEXT_HARD_LIMIT)

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
                    return self._clip(candidate, EXECUTOR_TEXT_HARD_LIMIT)

        return "Task completed."

    def _extract_partial_user_answer(self, stdout: str, *, raw: bool = False) -> str:
        if raw:
            # No marker will ever arrive, so there is nothing to strip and
            # nothing to wait for.
            return extract_codex_json_stream_text(stdout)
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
        return resolve_uploads_from_text(
            text=stdout,
            workspace=workspace,
            max_files=self._max_structured_uploads,
        )

    def _extract_action_upload_paths(self, stdout: str) -> list[str]:
        return upload_paths(extract_actions_json(stdout))

    def _extract_actions_json(self, stdout: str) -> dict | None:
        return extract_actions_json(stdout)

    def _find_last_marker(self, text: str, marker: str) -> re.Match[str] | None:
        return find_last_marker(text, marker)

    def _clip(self, text: str, limit: int) -> str:
        return clip_text(text, limit)

    def _extract_session_id(self, stderr: str) -> str:
        match = re.search(r"session id:\s*([0-9a-fA-F-]{36})", stderr or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _build_command(self, task: Task) -> list[str]:
        resolved_command = self._resolve_command()
        session_id = str(task.metadata.get("executor_session_id", "")).strip()
        model_override = str(task.metadata.get("model") or "").strip()
        read_only = bool(task.metadata.get("read_only"))
        # A read-only run must not be handed the bypass flag, whatever the
        # instance was configured with — that flag turns the sandbox off wholesale
        # and would quietly void the promise the caller made to the user.
        use_bypass = not read_only and (
            self._bypass_sandbox or self._sandbox_mode == "danger-full-access"
        )

        is_ping = False
        subagent = task.metadata.get("subagent")
        if isinstance(subagent, dict):
            task_name = str(subagent.get("task_name", ""))
            if task_name.startswith("ping-") or "ping" in task_name.lower():
                is_ping = True

        # Kept as one name because it decides two things: whether the ping trims
        # the user's config, and whether the recursion guard has anything to guard.
        ignores_user_config = is_ping and not model_override
        guard = self._recursion_guard(ignores_user_config=ignores_user_config)

        if session_id:
            command = [resolved_command, "exec", "resume"]
            command.append("--json")
            if use_bypass:
                command.append("--dangerously-bypass-approvals-and-sandbox")
            else:
                # Resuming does not inherit the original run's sandbox, so the
                # mode has to be restated or a second turn could write.
                command.extend(["--sandbox", _sandbox_mode(self._sandbox_mode, read_only)])
            if self._skip_git_repo_check:
                command.append("--skip-git-repo-check")
            if model_override:
                command.extend(["-m", model_override])
            elif ignores_user_config:
                # Skip ~/.codex/config.toml so the keep-alive ping doesn't load
                # the user's MCP servers + plugins into context (~21% fewer
                # tokens, measured). Auth still resolves via CODEX_HOME.
                self._append_ping_model_args(command)
            command.extend(guard)
            for attachment in task.image_attachments:
                command.extend(["--image", str(attachment.path)])
            # The session id is positional and `codex exec resume` reads it last.
            command.append(session_id)
            return command

        command = [resolved_command, "exec", "--json"]
        if use_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["--sandbox", _sandbox_mode(self._sandbox_mode, read_only)])
        if self._skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if model_override:
            command.extend(["-m", model_override])
        elif ignores_user_config:
            # See resume branch above: trim MCP/plugin context for the ping.
            self._append_ping_model_args(command)
        command.extend(guard)
        for attachment in task.image_attachments:
            command.extend(["--image", str(attachment.path)])
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

    def _recursion_guard(self, *, ignores_user_config: bool) -> list[str]:
        """Overrides that keep a child `codex exec` from calling back into Fluxion.

        Someone who wants every Codex session served by their local agents writes
        `model_provider = "fluxion_auto"` at the top of `~/.codex/config.toml`.
        The `codex exec` launched here reads that same line, so a gateway route
        reaching `local_codex` spawns a Codex that calls the gateway — which is
        holding the workspace lock while it waits for that very process to exit.
        Neither side can move, and nothing times out. A workspace that happens to
        differ turns the deadlock into unbounded process spawning instead, which
        is not an improvement.

        Fluxion picking this executor means "run the Codex CLI", never "be a
        gateway client", so a provider pointing back at us is replaced. Any other
        custom provider — someone proxying Codex through their own endpoint — is
        left alone, which is why this matches on the prefix rather than pinning
        the provider unconditionally.

        The chosen override lands in the recorded command, so a run that was
        redirected says so in its own log.
        """
        if ignores_user_config:
            return []
        provider = _configured_model_provider(_codex_home() / "config.toml")
        if not provider.startswith(_GATEWAY_PROVIDER_PREFIX):
            return []
        return ["-c", f"model_provider={_NATIVE_PROVIDER}"]

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
        read_only = bool(task.metadata.get("read_only"))
        use_bypass = not read_only and (
            self._bypass_sandbox or self._sandbox_mode == "danger-full-access"
        )
        resolved_command = self._resolve_command()
        command = [resolved_command, "exec", "--json"]
        if use_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["--sandbox", _sandbox_mode(self._sandbox_mode, read_only)])
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
            summary=self._extract_user_answer(answer_source, raw=is_raw_prompt(task))
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
