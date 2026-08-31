from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fluxion.config.settings import Settings
from fluxion.core.engine import GatewayCore
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage
from fluxion.executors.registry import build_enabled_executors
from fluxion.workspace import WorkspaceAccessService

PROFILE_INSTRUCTIONS = {
    "inspect": """Profile: inspect.
- Investigate the requested scope and report back.
- Prefer read-only inspection and focused commands.
- Do not edit files unless the user explicitly requests edits and the mode allows it.
""",
    "implement": """Profile: implement.
- Make minimal, localized changes required by the subtask.
- Avoid broad architectural decisions.
- Report changed files and verification.
""",
    "verify": """Profile: verify.
- Run focused checks, tests, or smoke validation.
- Prefer commands that do not mutate source files.
- If a command generates cache/build artifacts, clean them up or report them.
""",
    "summarize": """Profile: summarize.
- Read the requested material and return a concise summary.
- Do not modify files.
- Highlight blockers, risks, or follow-up questions.
""",
}

MODE_INSTRUCTIONS = {
    "read-only": """Mode: read-only.
- Do not modify workspace files.
- Do not run install, format, codegen, migration, or write commands.
""",
    "workspace-write": """Mode: workspace-write.
- You may modify workspace files only when needed for the subtask.
- Keep changes minimal and scoped.
- Clean up unintended generated files, caches, and build artifacts.
""",
}

SUPPORTED_AGENTS = {"antigravity", "codex", "claude"}
AUTO_AGENT_VALUES = {"", "auto"}
AGENT_ALIASES = {
    "agy": "antigravity",
    "antigravity-cli": "antigravity",
    "antigratity": "antigravity",
    "claude-code": "claude",
    "claude-code-cli": "claude",
}
SESSION_POLICIES = {"auto", "continue", "new"}

TASK_POLICY_HEADER = """Fluxion task policy:
- Follow the user's task exactly.
- Stay within the selected workspace.
- Respect the selected profile and permission mode.
- Keep the final response concise and include blockers, changed files, and verification when relevant.
"""


@dataclass(frozen=True)
class SubagentRunRequest:
    agent: str
    prompt: Sequence[str]
    project: str | None = None
    workspace: str | None = "."
    thread: str = "default"
    task_name: str | None = None
    parent_path: str = "/root"
    user: str = "local"
    profile: str = "inspect"
    mode: str = "read-only"
    session_policy: str = "auto"
    conversation_key: str | None = None
    include_subagent_preamble: bool | None = None
    # Optional per-task model override (e.g. route an Antigravity ping to the
    # Gemini vs External Models quota pool). Executors that don't read it ignore it.
    model: str | None = None
    # Workspace approval is intentionally separate from executor permissions.
    client_id: str = "local"
    authorization_request_id: str | None = None


@dataclass(frozen=True)
class SubagentRunResult:
    run_id: str
    task_id: str
    agent: str
    project: str
    workspace: str
    thread: str
    task_name: str
    parent_path: str
    agent_path: str
    conversation_key: str
    success: bool
    summary: str
    exit_code: int
    executor_session_id: str
    changed_files: list[str]
    diff_summary: str
    artifacts: list[str]
    log_file: str
    result: ExecutionResult
    authorization_request_id: str = ""
    workspace_access_policy: str = ""
    workspace_access_source: str = ""
    authorization_grant_id: str = ""
    authorization_scope: str = ""
    authorization_expires_at: str = ""

    def to_payload(self, *, include_stdout: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "project": self.project,
            "workspace": self.workspace,
            "thread": self.thread,
            "task_name": self.task_name,
            "parent_path": self.parent_path,
            "agent_path": self.agent_path,
            "conversation_key": self.conversation_key,
            "success": self.success,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "executor_session_id": self.executor_session_id,
            "effective_model": self.result.effective_model or "(executor default)",
            "resolved_model": self.result.resolved_model,
            "model_resolution_source": self.result.model_resolution_source,
            "changed_files": self.changed_files,
            "risk_flags": self.result.risk_flags,
            "change_set_file": self.result.change_set_file,
            "diff_summary": self.diff_summary,
            "artifacts": self.artifacts,
            "log_file": self.log_file,
            "authorization_request_id": self.authorization_request_id or None,
            "workspace_access_policy": self.workspace_access_policy,
            "workspace_access_source": self.workspace_access_source,
            "authorization_grant_id": self.authorization_grant_id or None,
            "authorization_scope": self.authorization_scope or None,
            "authorization_expires_at": self.authorization_expires_at or None,
        }
        if include_stdout:
            raw_result = asdict(self.result)
            raw_result["changed_files"] = self.changed_files
            payload["result"] = raw_result
        return payload


@dataclass(frozen=True)
class SubagentRunHandle:
    run_id: str
    task_id: str
    agent: str
    project: str
    workspace: str
    thread: str
    task_name: str
    parent_path: str
    agent_path: str
    conversation_key: str
    accepted: bool
    summary: str
    adapter: LocalChannelAdapter
    requested_model: str = ""
    effective_model: str = ""
    model_resolution_source: str = ""
    resumed_session_model: str = ""
    authorization_request_id: str = ""
    workspace_access_policy: str = ""
    workspace_access_source: str = ""
    authorization_grant_id: str = ""
    authorization_scope: str = ""
    authorization_expires_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "project": self.project,
            "workspace": self.workspace,
            "thread": self.thread,
            "task_name": self.task_name,
            "parent_path": self.parent_path,
            "agent_path": self.agent_path,
            "conversation_key": self.conversation_key,
            "accepted": self.accepted,
            "success": True,
            "status": "QUEUED",
            "summary": self.summary or "Task accepted.",
            # Which model this run actually goes out with. Without it a caller
            # could not tell that its run was about to bill a different quota
            # pool than the one it picked.
            "requested_model": self.requested_model,
            "effective_model": self.effective_model or "(executor default)",
            "resolved_model": "",
            "model_resolution_source": self.model_resolution_source,
            "authorization_request_id": self.authorization_request_id or None,
            "workspace_access_policy": self.workspace_access_policy,
            "workspace_access_source": self.workspace_access_source,
            "authorization_grant_id": self.authorization_grant_id or None,
            "authorization_scope": self.authorization_scope or None,
            "authorization_expires_at": self.authorization_expires_at or None,
        }
        if self.resumed_session_model and self.effective_model:
            if self.resumed_session_model != self.effective_model:
                payload["model_binding_warning"] = (
                    f"This run resumes an executor conversation created with "
                    f"{self.resumed_session_model}, but asks for {self.effective_model}. "
                    "Agent CLIs may keep the conversation's original model, which would "
                    "bill a different quota pool than requested. Use session_policy='new' "
                    "(or a fresh thread) to guarantee the requested model."
                )
        return payload


class LocalChannelAdapter:
    def __init__(
        self,
        *,
        on_result: Callable[[str, ExecutionResult], None] | None = None,
    ) -> None:
        self._done = threading.Event()
        self._lock = threading.Lock()
        self.result: ExecutionResult | None = None
        self._on_result = on_result
        self.statuses: list[dict[str, str]] = []
        self.output_deltas: list[str] = []
        self.output_updated_at: datetime | None = None

    def start(self, gateway: GatewayCore) -> None:
        del gateway

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        del context
        self.statuses.append({"task_id": task_id, "status": status, "detail": detail or ""})

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        del context
        with self._lock:
            if self.result is not None:
                return
            self.result = result
        if self._on_result is not None:
            try:
                self._on_result(task_id, result)
            except Exception:
                # A bookkeeping failure must never hide the executor result
                # from the caller. The expiry guard still closes an orphaned
                # grant if this callback cannot persist its terminal state.
                pass
        self._done.set()

    def send_typing(self, context: dict) -> None:
        del context

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        del task_id, context
        if text:
            with self._lock:
                self.output_deltas.append(text)
                self.output_updated_at = datetime.now(UTC)

    def wait(self, timeout_sec: int) -> ExecutionResult | None:
        self._done.wait(timeout=max(1, timeout_sec))
        return self.result

    def recent_output_tail(self, *, max_lines: int = 20, max_chars: int = 4000) -> dict[str, Any]:
        with self._lock:
            text = "".join(self.output_deltas)
            updated_at = self.output_updated_at
        tail, truncated = compact_tail(text, max_lines=max_lines, max_chars=max_chars)
        return {
            "recent_output_tail": tail,
            "recent_output_tail_truncated": truncated,
            "live_output_chars": len(text),
            "live_output_updated_at": updated_at.isoformat() if updated_at else None,
        }


def run_subagent(
    request: SubagentRunRequest,
    *,
    settings: Settings | None = None,
) -> SubagentRunResult:
    settings = settings or Settings.load()
    return SubagentRunner(settings).run(request)


class SubagentRunner:
    def __init__(
        self,
        settings: Settings,
        workspace_access: WorkspaceAccessService | None = None,
    ) -> None:
        settings.validate(require_slack=False)
        self._settings = settings
        self._workspace_access = workspace_access or WorkspaceAccessService(settings)
        self._gateway = build_gateway(settings)
        self._gateway.start()
        self._adapters_lock = threading.Lock()
        self._adapters: dict[str, LocalChannelAdapter] = {}

    def run(self, request: SubagentRunRequest) -> SubagentRunResult:
        handle = self.submit(request)
        result = handle.adapter.wait(timeout_sec=self._settings.task_timeout_sec + 5)
        if result is None:
            raise TimeoutError("Timed out waiting for local task result.")
        changed_files = visible_changed_files(
            # The submitted task owns this canonical workspace snapshot.  A
            # later permission edit must not invalidate changed-file reporting
            # for work that was already accepted.
            workspace=Path(handle.workspace),
            result=result,
        )
        return SubagentRunResult(
            run_id=handle.run_id,
            task_id=handle.task_id,
            agent=handle.agent,
            project=handle.project,
            workspace=handle.workspace,
            thread=handle.thread,
            task_name=handle.task_name,
            parent_path=handle.parent_path,
            agent_path=handle.agent_path,
            conversation_key=handle.conversation_key,
            success=result.success,
            summary=result.summary,
            exit_code=result.exit_code,
            executor_session_id=result.executor_session_id,
            changed_files=changed_files,
            diff_summary=result.diff_summary,
            artifacts=result.artifacts,
            log_file=result.log_file,
            result=result,
            authorization_request_id=handle.authorization_request_id,
            workspace_access_policy=handle.workspace_access_policy,
            workspace_access_source=handle.workspace_access_source,
            authorization_grant_id=handle.authorization_grant_id,
            authorization_scope=handle.authorization_scope,
            authorization_expires_at=handle.authorization_expires_at,
        )

    def submit(self, request: SubagentRunRequest) -> SubagentRunHandle:
        validate_request(request)
        # Resolve the project and workspace from a fresh managed/legacy
        # snapshot.  This is the only authorization gate before a task enters
        # GatewayCore; the gateway itself is deliberately not rebuilt when the
        # JSON permission file changes.
        project = self._workspace_access.resolve_project(request.project)
        task_scope_id = f"task-{uuid4().hex}"
        authorization = self._workspace_access.authorize_run_workspace(
            raw_workspace=request.workspace,
            project_key=request.project,
            mode=request.mode,
            client_id=request.client_id,
            authorization_request_id=request.authorization_request_id,
            task_scope_id=task_scope_id,
            request_if_denied=True,
        )
        workspace = authorization.require_allowed()
        authorization_grant_id = authorization.authorization_grant_id
        task: Task | None = None
        submitted = False
        try:
            # Local import keeps availability detection off subagent's import graph.
            from fluxion.availability import available_executors

            agent = resolve_agent(
                requested=request.agent,
                project_default=(
                    project.default_executor
                    if project is not None
                    else authorization.default_executor
                ),
                settings_default=effective_default_executor(self._settings),
                enabled_agents=self._settings.enabled_executors,
                available_agents=available_executors(self._settings),
            )
            agent_path = agent_path_for_run(
                parent_path=request.parent_path,
                task_name=request.task_name,
                fallback_thread=request.thread,
            )
            conversation_key = request.conversation_key or conversation_key_for_run(
                workspace=workspace,
                thread=request.thread,
                session_policy=request.session_policy,
            )
            existing_session = self._gateway._sessions.get_executor_session_id(
                conversation_key=conversation_key,
                channel="local",
                user_id=request.user,
                executor_name=agent,
            )
            include_preamble = request.include_subagent_preamble
            if existing_session:
                # Always omit the preamble on resumed sessions to save tokens —
                # the executor already has Fluxion context from the prior turn.
                # This takes priority over any explicit caller value.
                include_preamble = False
            elif include_preamble is None:
                # New session: include preamble by default.
                include_preamble = True

            task = Task.create(
                channel="local",
                user_id=request.user,
                text=task_text(
                    request.prompt,
                    subagent=include_preamble,
                    profile=request.profile,
                    mode=request.mode,
                ),
                workspace=workspace,
                metadata={
                    "executor": agent,
                    "conversation_key": conversation_key,
                    "model": request.model or "",
                    "requested_model": request.model or "",
                    "model_resolution_source": (
                        "fluxion_ping_policy"
                        if request.model
                        and request.task_name
                        and (
                            request.task_name.startswith("ping-")
                            or "ping" in request.task_name.lower()
                        )
                        else ("requested_override" if request.model else "executor_runtime")
                    ),
                    "prompt": " ".join(request.prompt).strip(),
                    "workspace_access": {
                        "policy": authorization.policy,
                        "source": authorization.source,
                        "mode": request.mode,
                        "client_id": request.client_id,
                        "authorization_request_id": authorization.authorization_request_id,
                        "authorization_grant_id": authorization_grant_id,
                        "authorization_scope": authorization.authorization_scope,
                        "authorization_expires_at": authorization.authorization_expires_at,
                    },
                    "subagent": {
                        "agent": agent,
                        "project": project.key if project is not None else "",
                        "workspace": str(workspace),
                        "thread": request.thread,
                        "task_name": request.task_name or "",
                        "parent_path": request.parent_path,
                        "agent_path": agent_path,
                        "profile": request.profile,
                        "mode": request.mode,
                        "session_policy": request.session_policy,
                    },
                },
            )
            if authorization_grant_id and not self._workspace_access.bind_task_authorization(
                authorization_grant_id, task.id
            ):
                raise RuntimeError("Workspace task authorization could not be bound to the task.")

            adapter = LocalChannelAdapter(
                on_result=(
                    lambda completed_task_id, _result, _gid=authorization_grant_id: (
                        self._workspace_access.complete_task_authorization(
                            _gid,
                            task_id=completed_task_id,
                        )
                        if _gid
                        else None
                    )
                )
            )
            accepted, reason = self._gateway.submit_task(
                task=task,
                channel_adapter=adapter,
                channel_context={"channel": "local", "thread": request.thread},
            )
            if not accepted:
                raise RuntimeError(reason)
            submitted = True
        except Exception:
            if authorization_grant_id and not submitted:
                try:
                    self._workspace_access.abort_task_authorization(
                        authorization_grant_id,
                        task_id=task.id if task is not None else "",
                    )
                except Exception:
                    pass
            raise
        with self._adapters_lock:
            self._adapters[task.id] = adapter
        # Read back after submit: the gateway fills in a conversation-level
        # default when the caller didn't name a model.
        effective_model = str(
            task.metadata.get("effective_model") or task.metadata.get("model") or ""
        )
        model_resolution_source = str(
            task.metadata.get("model_resolution_source") or "executor_runtime"
        )
        resumed_session_model = (
            self._gateway._sessions.get_session_model(
                conversation_key=conversation_key,
                channel="local",
                user_id=request.user,
                session_id=existing_session,
            )
            if existing_session
            else ""
        )
        return SubagentRunHandle(
            run_id=task.id,
            task_id=task.id,
            agent=agent,
            project=project.key if project is not None else "",
            workspace=str(workspace),
            thread=request.thread,
            task_name=request.task_name or "",
            parent_path=request.parent_path,
            agent_path=agent_path,
            conversation_key=conversation_key,
            accepted=True,
            summary="Task accepted.",
            adapter=adapter,
            requested_model=request.model or "",
            effective_model=effective_model,
            model_resolution_source=model_resolution_source,
            resumed_session_model=resumed_session_model,
            authorization_request_id=authorization.authorization_request_id,
            workspace_access_policy=authorization.policy,
            workspace_access_source=authorization.source,
            authorization_grant_id=authorization_grant_id,
            authorization_scope=authorization.authorization_scope,
            authorization_expires_at=authorization.authorization_expires_at,
        )

    @property
    def gateway(self) -> GatewayCore:
        return self._gateway

    @property
    def workspace_access(self) -> WorkspaceAccessService:
        return self._workspace_access

    def cancel(self, task_id: str) -> tuple[bool, str]:
        return self._gateway.cancel_task(task_id)

    def live_progress(self, task_id: str) -> dict[str, Any]:
        with self._adapters_lock:
            adapter = self._adapters.get(task_id)
        if adapter is None:
            return {}
        return adapter.recent_output_tail()


def build_gateway(settings: Settings) -> GatewayCore:
    storage = JsonlStorage(settings.data_dir)
    sessions = SessionManager(storage=storage)
    executors = build_enabled_executors(settings)
    return GatewayCore(
        router=TaskRouter(
            executors=executors,
            default_executor=effective_default_executor(settings),
        ),
        storage=storage,
        sessions=sessions,
        artifact_max_files=settings.artifact_max_files,
        worker_count=settings.worker_count,
        max_pending_per_user=settings.max_pending_per_user,
        max_retries=settings.max_retries,
        retry_backoff_sec=settings.retry_backoff_sec,
        change_detection=settings.change_detection,
        revert_capture=settings.revert_capture,
        change_set_max_file_bytes=settings.change_set_max_file_bytes,
        change_set_max_total_bytes=settings.change_set_max_total_bytes,
        typing_heartbeat_sec=0,
        running_update_sec=0,
        workspace_lock_timeout_sec=settings.workspace_lock_timeout_sec,
        settings=settings,
    )


def validate_request(request: SubagentRunRequest) -> None:
    normalized_agent = normalize_agent_name(request.agent)
    if normalized_agent not in SUPPORTED_AGENTS and normalized_agent not in AUTO_AGENT_VALUES:
        allowed = ", ".join(sorted(SUPPORTED_AGENTS))
        raise ValueError(
            f"Unsupported sub-agent executor: {request.agent}. "
            f"Allowed: auto, {allowed}. Use lowercase names such as antigravity."
        )
    if request.profile not in PROFILE_INSTRUCTIONS:
        allowed = ", ".join(sorted(PROFILE_INSTRUCTIONS))
        raise ValueError(f"Unsupported sub-agent profile: {request.profile}. Allowed: {allowed}")
    if request.mode not in MODE_INSTRUCTIONS:
        allowed = ", ".join(sorted(MODE_INSTRUCTIONS))
        raise ValueError(f"Unsupported sub-agent mode: {request.mode}. Allowed: {allowed}")
    if request.session_policy not in SESSION_POLICIES:
        allowed = ", ".join(sorted(SESSION_POLICIES))
        raise ValueError(
            f"Unsupported sub-agent session policy: {request.session_policy}. Allowed: {allowed}"
        )
    agent_path_for_run(
        parent_path=request.parent_path,
        task_name=request.task_name,
        fallback_thread=request.thread,
    )


def normalize_agent_name(value: str) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    return AGENT_ALIASES.get(normalized, normalized)


def resolve_agent(
    *,
    requested: str,
    project_default: str,
    settings_default: str,
    enabled_agents: list[str] | tuple[str, ...] | None = None,
    available_agents: Collection[str] | None = None,
) -> str:
    requested = normalize_agent_name(requested)
    project_default = normalize_agent_name(project_default)
    settings_default = normalize_agent_name(settings_default)
    enabled = tuple(
        agent for agent in (enabled_agents or sorted(SUPPORTED_AGENTS)) if agent in SUPPORTED_AGENTS
    ) or tuple(sorted(SUPPORTED_AGENTS))
    # available_agents is None when the caller cannot detect installation state
    # (e.g. list_agent_models): in that case availability is not gated at all.
    gate_availability = available_agents is not None
    available = {normalize_agent_name(agent) for agent in (available_agents or ())}
    if requested in AUTO_AGENT_VALUES:
        preferred = (project_default, settings_default, enabled[0])
        # Prefer an executor that is both enabled and actually installed.
        if gate_availability:
            for candidate in preferred:
                if candidate in enabled and candidate in available:
                    return candidate
        # Fall back to the enabled preference order when nothing installed was
        # found, so a detection false-negative never hard-blocks an auto run.
        for candidate in preferred:
            if candidate in enabled:
                return candidate
        candidate = enabled[0]
    else:
        candidate = requested
    if candidate not in SUPPORTED_AGENTS:
        allowed = ", ".join(sorted(SUPPORTED_AGENTS))
        raise ValueError(f"Unsupported sub-agent executor: {candidate}. Allowed: {allowed}")
    if candidate not in enabled:
        allowed = ", ".join(enabled)
        raise ValueError(f"Disabled sub-agent executor: {candidate}. Enabled executors: {allowed}.")
    # Fail fast on an explicit pick of an enabled-but-uninstalled executor, but
    # only when at least one executor was detected as available — a fully empty
    # sweep is treated as "detection unavailable" rather than "nothing works".
    if gate_availability and available and candidate not in available:
        installed = ", ".join(sorted(available))
        raise ValueError(
            f"Unavailable sub-agent executor: {candidate}. Installed executors: {installed}."
        )
    return candidate


def effective_default_executor(settings: Settings) -> str:
    enabled = [agent for agent in settings.enabled_executors if agent in SUPPORTED_AGENTS]
    if settings.default_executor in enabled:
        return settings.default_executor
    return enabled[0] if enabled else settings.default_executor


def conversation_key_for_run(*, workspace: Path, thread: str, session_policy: str) -> str:
    clean_thread = (thread or "default").strip() or "default"
    if session_policy == "new":
        clean_thread = f"{clean_thread}:new:{uuid4()}"
    return f"local:{workspace.resolve()}:{clean_thread}"


def agent_path_for_run(
    *,
    parent_path: str,
    task_name: str | None,
    fallback_thread: str,
) -> str:
    parent = normalize_agent_path(parent_path)
    # task_name is free-form human text; slugify it into a valid path segment
    # (the same forgiving normalization used for the fallback) rather than
    # rejecting spaces / uppercase / hyphens. The display name is kept separately.
    source = task_name if task_name and task_name.strip() else fallback_thread
    segment = derive_agent_path_segment(source)
    return f"{parent}/{segment}" if parent != "/" else f"/{segment}"


def derive_agent_path_segment(value: str) -> str:
    raw = (value or "default").strip().lower()
    chars = [ch if ch.isascii() and (ch.islower() or ch.isdigit()) else "_" for ch in raw]
    segment = "_".join(part for part in "".join(chars).split("_") if part)
    if not segment:
        segment = "default"
    validate_agent_path_segment(segment)
    return segment


def normalize_agent_path(path: str) -> str:
    value = (path or "/root").strip()
    if not value.startswith("/"):
        raise ValueError("parent_path must be an absolute agent path starting with /root")
    if value != "/root" and not value.startswith("/root/"):
        raise ValueError("parent_path must start with /root")
    if value.endswith("/") and value != "/":
        value = value.rstrip("/")
    for segment in value.strip("/").split("/"):
        validate_agent_path_segment(segment, allow_root=True)
    return value


def validate_agent_path_segment(segment: str, *, allow_root: bool = False) -> None:
    if not segment:
        raise ValueError("agent path segment must not be empty")
    if segment in {".", ".."}:
        raise ValueError(f"agent path segment `{segment}` is reserved")
    if segment == "root" and not allow_root:
        raise ValueError("agent path segment `root` is reserved")
    if "/" in segment:
        raise ValueError("agent path segment must not contain /")
    if not all(ch.isascii() and (ch.islower() or ch.isdigit() or ch == "_") for ch in segment):
        raise ValueError("agent path segments must use lowercase letters, digits, and underscores")


def task_text(
    prompt_parts: Sequence[str],
    *,
    subagent: bool,
    profile: str,
    mode: str,
) -> str:
    prompt = " ".join(prompt_parts).strip()
    if not subagent:
        return prompt
    profile_block = PROFILE_INSTRUCTIONS.get(profile, PROFILE_INSTRUCTIONS["inspect"])
    mode_block = MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["read-only"])
    return f"{TASK_POLICY_HEADER}\n{profile_block}\n{mode_block}\nUser task:\n{prompt}"


def visible_changed_files(
    *, result: ExecutionResult, task: Task | None = None, workspace: Path | None = None
) -> list[str]:
    resolved_workspace = task.workspace if task is not None else workspace
    if resolved_workspace is None:
        return result.changed_files
    internal_paths: set[str] = set()
    if result.log_file:
        try:
            internal_paths.add(
                Path(result.log_file).resolve().relative_to(resolved_workspace).as_posix()
            )
        except ValueError:
            pass
    return [path for path in result.changed_files if path not in internal_paths]


def compact_tail(text: str, *, max_lines: int, max_chars: int) -> tuple[str, bool]:
    if not text:
        return "", False
    lines = text.splitlines()
    truncated_lines = len(lines) > max_lines
    tail = "\n".join(lines[-max_lines:])
    truncated_chars = len(tail) > max_chars
    if truncated_chars:
        tail = tail[-max_chars:]
    return tail, truncated_lines or truncated_chars
