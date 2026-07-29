from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from fluxion.channels.base import ChannelAdapter
from fluxion.core.control import ControlResponse
from fluxion.core.models.attachment import (
    AttachmentReferenceRedactor,
    redact_attachment_references,
)
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.core.router import TaskRouter
from fluxion.core.runtime_registry import (
    INTERRUPTED_STATUS,
    RuntimeRegistry,
    current_owner,
    prune_dead_instances,
    reconcile_orphaned_tasks,
)
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage
from fluxion.utils.logger import get_logger
from fluxion.workspace.antigravity_trajectory import collect_antigravity_trajectory
from fluxion.workspace.change_set import (
    ContentSnapshot,
    build_change_set,
    build_stream_change_set,
    save_change_set,
    take_content_snapshot,
)
from fluxion.workspace.git_tools import get_git_diff_summary
from fluxion.workspace.lock_state import LOCK_DIR
from fluxion.workspace.snapshot import FileFingerprint, diff_snapshot, take_snapshot

logger = get_logger(__name__)

# Upper bound the engine waits for an early-returned executor (Antigravity) to
# finish its post-answer housekeeping (flushing the SQLite trajectory DB and any
# file writes) before computing the change report. Comfortably exceeds the
# executor's own reap cap (process wait + terminate + thread joins) so the
# reaper's completion event always fires first under normal operation.
_PENDING_FINALIZE_TIMEOUT_SEC = 180

# How often a task blocked on a busy workspace re-publishes why it is waiting.
_BLOCKED_NOTICE_INTERVAL_SEC = 30
# Polling cadence while waiting for the cross-process workspace lock. The wait
# is bounded and cancellable, which a blocking flock() would not be.
_LOCK_POLL_INTERVAL_SEC = 0.5


def _control_response(kind: str, text: str, data: dict[str, Any] | None = None) -> ControlResponse:
    return ControlResponse(kind=kind, text=text, data=data)


class _WorkspaceBusy(RuntimeError):
    """A workspace-write run could not take the workspace within its budget."""


def _write_lock_holder(handle: Any, *, task: Task) -> None:
    """Stamp the lock file with who holds it, for waiters and for diagnosis."""
    payload = {
        "task_id": task.id,
        "workspace": str(task.workspace),
        "acquired_at": datetime.now(UTC).isoformat(),
        **current_owner().to_payload(),
    }
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.flush()
    except OSError:
        logger.debug("could not stamp workspace lock holder", exc_info=True)


def _read_lock_holder(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class _TaskEnvelope:
    task: Task
    channel_adapter: ChannelAdapter
    channel_context: dict[str, Any]


@dataclass
class _TaskRecord:
    task: Task
    channel_adapter: ChannelAdapter
    channel_context: dict[str, Any]
    status: str = "RECEIVED"
    attempts: int = 0
    cancel_requested: bool = False
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    summary: str = ""
    # Why a task that has left the queue still hasn't started — almost always
    # another run holding its workspace. Without this a caller polling a queued
    # task can only see that nothing is happening, not what to do about it.
    blocked_reason: str = ""
    blocked_by_workspace: str = ""
    blocked_by: dict[str, Any] | None = None


class GatewayCore:
    def __init__(
        self,
        *,
        router: TaskRouter,
        storage: JsonlStorage,
        sessions: SessionManager,
        artifact_max_files: int,
        worker_count: int,
        max_pending_per_user: int,
        max_retries: int,
        retry_backoff_sec: int,
        change_detection: str = "off",
        revert_capture: str = "structured",
        change_set_max_file_bytes: int = 1_000_000,
        change_set_max_total_bytes: int = 20_000_000,
        typing_heartbeat_sec: int = 6,
        running_update_sec: int = 30,
        workspace_lock_timeout_sec: int = 1800,
        reconcile_on_start: bool = True,
        settings: Any | None = None,
    ) -> None:
        self._router = router
        self._storage = storage
        self._sessions = sessions
        self._settings = settings
        self._artifact_max_files = artifact_max_files
        self._worker_count = max(1, worker_count)
        self._max_pending_per_user = max(1, max_pending_per_user)
        self._max_retries = max(0, max_retries)
        self._retry_backoff_sec = max(1, retry_backoff_sec)
        self._change_detection = (change_detection or "off").strip().lower()
        self._revert_capture = (revert_capture or "structured").strip().lower()
        self._change_set_max_file_bytes = max(0, change_set_max_file_bytes)
        self._change_set_max_total_bytes = max(0, change_set_max_total_bytes)
        self._typing_heartbeat_sec = max(0, typing_heartbeat_sec)
        self._running_update_sec = max(0, running_update_sec)
        self._workspace_lock_timeout_sec = max(1, workspace_lock_timeout_sec)
        self._reconcile_on_start = reconcile_on_start
        self._registry = RuntimeRegistry(storage.data_dir, role="engine")
        self._queue: queue.Queue[_TaskEnvelope] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._metrics_lock = threading.Lock()
        self._records_lock = threading.Lock()
        self._records: dict[str, _TaskRecord] = {}
        self._workspace_locks_guard = threading.Lock()
        self._workspace_write_locks: dict[str, threading.Lock] = {}
        self._started_at = datetime.now(UTC)
        self._total_submitted = 0
        self._total_completed = 0
        self._running_tasks = 0

    def start(self) -> None:
        if self._workers:
            return
        self._registry.start()
        if self._reconcile_on_start:
            # Any task still marked non-terminal by a process that no longer
            # exists is closed out now, before this process starts adding its
            # own. Without it those rows stay RUNNING for good and mislead every
            # later status poll.
            try:
                reconcile_orphaned_tasks(storage=self._storage, data_dir=self._storage.data_dir)
                # Heartbeat files outlive processes that were killed rather than
                # shut down — an MCP client that starts a server just to read its
                # tool list leaves one behind every launch. Sweep them here so the
                # directory stays self-limiting.
                prune_dead_instances(self._storage.data_dir)
            except Exception:
                logger.exception("startup task reconciliation failed")
        for idx in range(self._worker_count):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"fluxion-worker-{idx + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def stop(self) -> None:
        """Stop publishing this process's heartbeat.

        Workers are daemon threads that die with the process; this only retracts
        the ownership claim, so a clean shutdown leaves no heartbeat file that a
        later reconciliation would have to age out.
        """
        self._registry.stop()

    def submit_task(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
    ) -> tuple[bool, str]:
        conversation_key = self._conversation_key(task)
        executor_name, _ = self._router.select_executor_with_name(task)
        executor_session_id = self._sessions.get_executor_session_id(
            conversation_key=conversation_key,
            channel=task.channel,
            user_id=task.user_id,
            executor_name=executor_name,
        )
        task.metadata["executor"] = executor_name
        task.metadata["executor_session_id"] = executor_session_id or ""
        model_override = self._sessions.get_model_override(
            conversation_key=conversation_key,
            channel=task.channel,
            user_id=task.user_id,
            executor_name=executor_name,
        )
        # The conversation-level override is a default for turns that didn't
        # pick a model, not an override of one that did: a caller passing an
        # explicit model (MCP `run_subagent(model=...)`) must get that model, or
        # its run silently lands in a different quota pool than it asked for.
        if model_override and not str(task.metadata.get("model") or "").strip():
            task.metadata["model"] = model_override

        with self._records_lock:
            pending_for_user = sum(
                1
                for record in self._records.values()
                if record.task.user_id == task.user_id
                and record.status in {"RECEIVED", "QUEUED", "RUNNING", "RETRYING"}
            )
            if pending_for_user >= self._max_pending_per_user:
                return (
                    False,
                    f"too many pending tasks for user (limit={self._max_pending_per_user})",
                )
            record = _TaskRecord(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
            )
            self._records[task.id] = record

        self._set_status(task, "RECEIVED")
        channel_adapter.send_status(task.id, "RECEIVED", channel_context)
        self._update_record(task.id, status="VALIDATED")
        self._set_status(task, "VALIDATED")
        self._update_record(task.id, status="QUEUED")
        self._set_status(task, "QUEUED")
        queued_before = self._queue.qsize()
        channel_adapter.send_status(
            task.id, "QUEUED", channel_context, detail=f"queue_depth={queued_before + 1}"
        )
        with self._metrics_lock:
            self._total_submitted += 1
        self._queue.put(
            _TaskEnvelope(
                task=task, channel_adapter=channel_adapter, channel_context=channel_context
            )
        )
        return True, task.id

    def cancel_task(self, task_id: str) -> tuple[bool, str]:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return False, "task not found"
            if record.status in {"RETURNED", "FAILED", "CANCELED", INTERRUPTED_STATUS}:
                return False, f"task already finished with status={record.status}"
            if record.status in {"RUNNING", "RETRYING"}:
                record.cancel_requested = True
                record.last_updated_at = datetime.now(UTC)
                return True, "cancel requested for running task"
            record.cancel_requested = True
            record.status = "CANCELED"
            record.last_updated_at = datetime.now(UTC)
            record.finished_at = record.last_updated_at
        result = self._canceled_result(summary="Task canceled before execution.")
        self._set_status(record.task, "CANCELED", extra={"result": asdict(result)})
        record.channel_adapter.send_status(
            record.task.id, "CANCELED", record.channel_context, detail="before execution"
        )
        record.channel_adapter.send_result(record.task.id, result, record.channel_context)
        return True, "task canceled"

    def reset_conversation(self, *, conversation_key: str, channel: str, user_id: str) -> bool:
        return self._sessions.reset(
            conversation_key=conversation_key,
            channel=channel,
            user_id=user_id,
        )

    def get_quota_payload(self) -> dict[str, Any]:
        import json

        cache_path = self._storage.data_dir / "usage_cache.json"
        if not cache_path.exists():
            return {"error": "Quota cache file not found."}
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)

            providers = data.get("providers") or []
            if not providers:
                return {"error": "No quota data found in cache."}
            return data if isinstance(data, dict) else {"error": "No quota data found in cache."}
        except Exception as exc:
            return {"error": f"Error loading quota summary: {exc}"}

    def handle_control_command(
        self,
        *,
        text: str,
        user_id: str,
        convo_key: str,
        channel: str,
    ) -> ControlResponse | None:
        """Process remote control commands and return a structured response if handled."""
        cleaned = text.strip()
        command = cleaned.lower()

        if command in {"help", "/help", "/start"}:
            return _control_response(
                "help",
                (
                    "Commands:\n"
                    "- help: show this help\n"
                    "- ping: health check\n"
                    "- usage / quota: show current subscription remaining quota\n"
                    "- status: show gateway runtime status\n"
                    "- tasks: show recent task list\n"
                    "- history: show recent task statuses from storage\n"
                    "- task <task_id>: show task detail\n"
                    "- cancel <task_id>: cancel queued/running task\n"
                    "- reset: reset conversation memory and thread overrides\n"
                    "- executors: list supported executors & active override\n"
                    "- models: list selectable models for the active executor\n"
                    "- models <executor>: list selectable models for an executor\n"
                    "- use executor <executor>: switch executor for this session/thread\n"
                    "- use executor default: clear executor override\n"
                    "- use model <model-id>: switch model for the active executor\n"
                    "- use model default: clear model override for the active executor\n"
                    "- workspace=/path <task>: run task under specific workspace\n"
                    "All workspaces must be inside FLUXION_ALLOWED_WORKSPACES."
                ),
            )

        if command in {"ping", "/ping"}:
            return _control_response("ping", "pong")

        if command in {"usage", "/usage", "quota", "/quota"}:
            payload = self.get_quota_payload()
            return _control_response("usage", "", data=payload)

        if command in {"reset", "/reset"}:
            cleared = self.reset_conversation(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
            )
            return _control_response(
                "reset",
                (
                    "Conversation memory and thread overrides cleared."
                    if cleared
                    else "No conversation memory to clear for this session."
                ),
            )

        if command in {"executors", "/executors"}:
            current_override = self._sessions.get_executor_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
            )
            default_exec = self._router._default_executor
            lines = ["Supported Executors:"]
            for name in self._router._executors:
                is_default = name == default_exec
                is_active = (name == current_override) if current_override else is_default
                active_tag = " ← active" if is_active else ""
                default_tag = " (default)" if is_default else ""
                lines.append(f"- {name}{active_tag}{default_tag}")
            return _control_response("executors", "\n".join(lines))

        if command in {"models", "/models"}:
            active_executor = self._active_executor_name(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
            )
            return _control_response(
                "models",
                self._format_model_list_response(
                    active_executor,
                    conversation_key=convo_key,
                    channel=channel,
                    user_id=user_id,
                ),
            )

        model_list_agent = self._parse_model_list_command(cleaned)
        if model_list_agent is not None:
            return _control_response("models", self._format_model_list_response(model_list_agent))

        if command == "use" or command == "/use":
            return _control_response(
                "usage_error", "Usage: use executor <executor> | use model <model-id>"
            )

        if command.startswith("use ") or command.startswith("/use "):
            return _control_response(
                "use",
                self._handle_use_command(
                    cleaned=cleaned,
                    user_id=user_id,
                    convo_key=convo_key,
                    channel=channel,
                ),
            )

        if command in {"status", "/status"}:
            stats = self.get_runtime_status()
            return _control_response(
                "status",
                (
                    "Runtime status:\n"
                    f"- worker_count: {stats['worker_count']}\n"
                    f"- queue_depth: {stats['queue_depth']}\n"
                    f"- running_tasks: {stats['running_tasks']}\n"
                    f"- total_submitted: {stats['total_submitted']}\n"
                    f"- total_completed: {stats['total_completed']}\n"
                    f"- uptime_sec: {stats['uptime_sec']}"
                ),
            )

        if command in {"tasks", "/tasks"}:
            tasks = self.list_recent_tasks(limit=8)
            if not tasks:
                return _control_response("tasks", "No tasks yet.")
            lines = ["Recent tasks:"]
            for item in tasks:
                lines.append(
                    f"- {item['task_id']} | {item['status']} | attempts={item['attempts']}"
                )
            return _control_response("tasks", "\n".join(lines))

        if command in {"history", "/history"}:
            items = self.list_recent_tasks_from_storage(limit=8)
            if not items:
                return _control_response("history", "No task history in storage.")
            lines = ["Recent task history:"]
            for item in items:
                lines.append(f"- {item['task_id']} | {item['status']} | {item['timestamp']}")
            return _control_response("history", "\n".join(lines))

        if cleaned.startswith("task ") or cleaned.startswith("/task "):
            task_id = cleaned.split(maxsplit=1)[1].strip()
            detail = self.get_task_overview(task_id)
            if detail is None:
                return _control_response("task", f"Task not found: {task_id}")
            return _control_response(
                "task",
                (
                    "Task detail:\n"
                    f"- task_id: {detail['task_id']}\n"
                    f"- status: {detail['status']}\n"
                    f"- attempts: {detail['attempts']}\n"
                    f"- cancel_requested: {detail['cancel_requested']}\n"
                    f"- workspace: {detail['workspace']}\n"
                    f"- summary: {detail['summary'] or '(none)'}"
                ),
            )

        if command in {"task", "/task"}:
            return _control_response("usage_error", "Usage: task <task_id>")

        if cleaned.startswith("cancel ") or cleaned.startswith("/cancel "):
            task_id = cleaned.split(maxsplit=1)[1].strip()
            ok, message = self.cancel_task(task_id)
            prefix = "Canceled" if ok else "Cancel not completed"
            return _control_response("cancel", f"{prefix}: {message}")

        if command in {"cancel", "/cancel"}:
            return _control_response("usage_error", "Usage: cancel <task_id>")

        return None

    def _parse_model_list_command(self, cleaned: str) -> str | None:
        parts = cleaned.split()
        if not parts:
            return None
        head = parts[0].lower()
        if head in {"models", "/models"} and len(parts) > 1:
            return parts[1].lower()
        return None

    def _handle_use_command(
        self,
        *,
        cleaned: str,
        user_id: str,
        convo_key: str,
        channel: str,
    ) -> str:
        parts = cleaned.split(maxsplit=2)
        if parts[0].startswith("/"):
            parts[0] = parts[0][1:]
        if len(parts) < 3:
            return "Usage: use executor <executor> | use model <model-id>"
        scope = parts[1].strip().lower()
        target = parts[2].strip()
        if scope == "executor":
            return self._set_executor_override_command(
                target=target,
                user_id=user_id,
                convo_key=convo_key,
                channel=channel,
            )
        if scope == "model":
            return self._set_model_override_command(
                target=target,
                user_id=user_id,
                convo_key=convo_key,
                channel=channel,
            )
        return "Usage: use executor <executor> | use model <model-id>"

    def _set_executor_override_command(
        self,
        *,
        target: str,
        user_id: str,
        convo_key: str,
        channel: str,
    ) -> str:
        normalized = target.strip().lower()
        if not normalized:
            return "Usage: use executor <executor>"
        if normalized in {"default", "clear", "reset"}:
            self._sessions.set_executor_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name=None,
            )
            return f"Cleared executor override. Reverted to default ({self._router._default_executor})."
        valid_executors = list(self._router._executors.keys())
        if normalized not in valid_executors:
            return f"Unknown executor: {target}. Supported: {', '.join(valid_executors)}"
        self._sessions.set_executor_override(
            conversation_key=convo_key,
            channel=channel,
            user_id=user_id,
            executor_name=normalized,
        )
        return f"Executor switched to {normalized}."

    def _set_model_override_command(
        self,
        *,
        target: str,
        user_id: str,
        convo_key: str,
        channel: str,
    ) -> str:
        model = target.strip()
        active_executor = self._active_executor_name(
            conversation_key=convo_key,
            channel=channel,
            user_id=user_id,
        )
        if not model:
            return "Usage: use model <model-id>"
        if model.lower() in {"default", "clear", "reset"}:
            self._sessions.set_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name=active_executor,
                model=None,
            )
            return f"Cleared model override for {active_executor}."

        valid_models, error = self._selectable_model_ids(active_executor)
        if error:
            return error
        if model not in valid_models:
            return (
                f"Unknown model for {active_executor}: {model}. "
                f"Use `models` to list selectable model IDs."
            )
        self._sessions.set_model_override(
            conversation_key=convo_key,
            channel=channel,
            user_id=user_id,
            executor_name=active_executor,
            model=model,
        )
        return f"Model for {active_executor} switched to {model}."

    def _active_executor_name(self, *, conversation_key: str, channel: str, user_id: str) -> str:
        current_override = self._sessions.get_executor_override(
            conversation_key=conversation_key,
            channel=channel,
            user_id=user_id,
        )
        return current_override or self._router._default_executor

    def _selectable_model_ids(self, agent: str) -> tuple[set[str], str]:
        if self._settings is None:
            return (
                set(),
                "Model switching is unavailable: gateway settings are not attached.",
            )
        try:
            from fluxion.mcp_server.model_catalog import list_agent_models_view

            view = list_agent_models_view(agent=agent, project="", settings=self._settings)
        except Exception as exc:
            logger.exception("model switch command failed for agent %s", agent)
            return set(), f"Error loading models for {agent}: {exc}"
        if not view.get("found", False):
            summary = str(view.get("summary") or "unknown agent")
            return set(), f"Model list unavailable for {agent}: {summary}"
        models = view.get("models")
        if not isinstance(models, list):
            return set(), f"No selectable models found for {agent}."
        ids = {
            str(item.get("id") or "").strip()
            for item in models
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        if not ids:
            return set(), f"No selectable models found for {agent}."
        return ids, ""

    def _format_model_list_response(
        self,
        agent: str,
        *,
        conversation_key: str | None = None,
        channel: str | None = None,
        user_id: str | None = None,
    ) -> str:
        if self._settings is None:
            return "Model listing is unavailable: gateway settings are not attached."
        try:
            from fluxion.mcp_server.model_catalog import list_agent_models_view

            view = list_agent_models_view(agent=agent, project="", settings=self._settings)
        except Exception as exc:
            logger.exception("model list command failed for agent %s", agent)
            return f"Error loading models for {agent}: {exc}"

        if not view.get("found", False):
            summary = str(view.get("summary") or "unknown agent")
            return f"Model list unavailable for {agent}: {summary}"

        resolved = str(view.get("agent") or agent)
        source = str(view.get("source") or "unknown")
        default_model = str(view.get("default_model") or "")
        active_model = ""
        if conversation_key is not None and channel is not None and user_id is not None:
            active_model = (
                self._sessions.get_model_override(
                    conversation_key=conversation_key,
                    channel=channel,
                    user_id=user_id,
                    executor_name=resolved,
                )
                or ""
            )
        effective_active = active_model or "executor default"
        if not active_model and default_model:
            effective_active = f"executor default ({default_model})"
        lines = [f"Models for {resolved}:"]
        lines.append(f"- active: {effective_active}")

        models = view.get("models") if isinstance(view.get("models"), list) else []
        if models:
            for item in models:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                detail = self._model_price_detail(item)
                efforts = item.get("supported_reasoning_efforts")
                if isinstance(efforts, list) and efforts:
                    detail = f"{detail}; efforts={','.join(str(e) for e in efforts)}"
                markers: list[str] = []
                if active_model and model_id == active_model:
                    markers.append("active")
                if default_model and model_id == default_model:
                    markers.append("default")
                marker_text = f" ← {', '.join(markers)}" if markers else ""
                lines.append(f"- {model_id}{detail}{marker_text}")
        else:
            lines.append("- no selectable models reported")
        lines.append("Use `use model <id>` to switch, `use model default` to clear.")

        price_refs = view.get("price_references")
        warnings = view.get("warnings")
        if isinstance(price_refs, list) and price_refs:
            lines.append("Note: pricing references are omitted from this chat list.")
        if source == "executor_aliases+local_prices":
            lines.append("Note: Claude models are CLI aliases passed to --model.")
        elif isinstance(warnings, list) and warnings:
            lines.append("Note: model catalog returned warnings; use listed ids only.")
        return "\n".join(lines)

    @staticmethod
    def _model_price_detail(item: dict[str, Any]) -> str:
        input_rate = item.get("input_per_1m")
        output_rate = item.get("output_per_1m")
        details: list[str] = []
        if isinstance(input_rate, (int, float)):
            details.append(f"in=${input_rate:g}/1M")
        if isinstance(output_rate, (int, float)):
            details.append(f"out=${output_rate:g}/1M")
        return f" ({', '.join(details)})" if details else ""

    def registry_snapshot(self) -> dict[str, Any]:
        """This process's ownership identity, for the operator status view."""
        return self._registry.snapshot()

    def get_runtime_status(self) -> dict[str, int]:
        with self._metrics_lock:
            uptime_sec = int((datetime.now(UTC) - self._started_at).total_seconds())
            return {
                "worker_count": self._worker_count,
                "queue_depth": self._queue.qsize(),
                "running_tasks": self._running_tasks,
                "total_submitted": self._total_submitted,
                "total_completed": self._total_completed,
                "uptime_sec": uptime_sec,
            }

    def get_task_overview(self, task_id: str) -> dict[str, Any] | None:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            return {
                "task_id": record.task.id,
                "status": record.status,
                "attempts": record.attempts,
                "cancel_requested": record.cancel_requested,
                "submitted_at": record.submitted_at.isoformat(),
                "last_updated_at": record.last_updated_at.isoformat(),
                "finished_at": record.finished_at.isoformat() if record.finished_at else None,
                "summary": record.summary,
                "workspace": str(record.task.workspace),
                "blocked_reason": record.blocked_reason,
                "blocked_by_workspace": record.blocked_by_workspace,
                "blocked_by_task_id": str((record.blocked_by or {}).get("task_id") or ""),
            }

    def list_recent_tasks(self, *, limit: int = 8) -> list[dict[str, Any]]:
        with self._records_lock:
            records = sorted(
                self._records.values(),
                key=lambda r: r.last_updated_at,
                reverse=True,
            )[:limit]
            return [
                {
                    "task_id": r.task.id,
                    "status": r.status,
                    "attempts": r.attempts,
                    "last_updated_at": r.last_updated_at.isoformat(),
                    "summary": r.summary,
                }
                for r in records
            ]

    def list_recent_tasks_from_storage(self, *, limit: int = 8) -> list[dict[str, Any]]:
        return self._storage.list_recent_task_summaries(limit=limit)

    def _worker_loop(self) -> None:
        while True:
            envelope = self._queue.get()
            try:
                if self._is_canceled(envelope.task.id):
                    continue
                self._execute_task(
                    task=envelope.task,
                    channel_adapter=envelope.channel_adapter,
                    channel_context=envelope.channel_context,
                )
            except Exception:
                logger.exception("Unhandled worker failure for task %s", envelope.task.id)
            finally:
                self._queue.task_done()

    def _execute_task(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
    ) -> None:
        # Take the workspace locks BEFORE announcing RUNNING. Waiting for
        # another run to release the workspace is queueing, not running, and
        # reporting it as RUNNING made a blocked task indistinguishable from a
        # working one — including to the caller deciding whether to cancel.
        try:
            workspace_lock, workspace_file_lock = self._acquire_workspace_locks(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
            )
        except _WorkspaceBusy as busy:
            self._finalize_lock_timeout(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
                reason=str(busy),
            )
            return
        if self._is_canceled(task.id):
            # Canceled while it sat behind the workspace lock.
            self._release_workspace_locks(workspace_lock, workspace_file_lock)
            self._finalize_canceled(task_id=task.id)
            return

        self._sessions.set_active_task(
            conversation_key=self._conversation_key(task),
            channel=task.channel,
            user_id=task.user_id,
            task_id=task.id,
        )
        with self._metrics_lock:
            self._running_tasks += 1
        self._registry.track(task.id)
        self._update_record(task.id, status="RUNNING", blocked_reason="")
        channel_adapter.send_status(
            task.id,
            "RUNNING",
            channel_context,
            detail="started",
        )
        self._set_status(task, "RUNNING")
        before_snapshot = self._take_snapshot(task.workspace)
        before_content_snapshot = self._take_content_snapshot(task)
        heartbeat_stop = threading.Event()
        start_monotonic = time.monotonic()

        def _heartbeat_loop() -> None:
            last_progress_sent = 0
            while not heartbeat_stop.wait(max(1, self._typing_heartbeat_sec or 1)):
                try:
                    channel_adapter.send_typing(channel_context)
                    if self._running_update_sec > 0:
                        elapsed = int(time.monotonic() - start_monotonic)
                        if elapsed - last_progress_sent >= self._running_update_sec:
                            channel_adapter.send_status(
                                task.id,
                                "RUNNING",
                                channel_context,
                                detail=f"elapsed={elapsed}s",
                            )
                            last_progress_sent = elapsed
                except Exception:
                    logger.debug("heartbeat send failed for task %s", task.id, exc_info=True)

        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"fluxion-heartbeat-{task.id[:8]}",
            daemon=True,
        )
        if self._typing_heartbeat_sec > 0:
            heartbeat_thread.start()

        def _stop_heartbeat() -> None:
            heartbeat_stop.set()
            if heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=1)

        try:
            executor_name, executor = self._router.select_executor_with_name(task)
            task.metadata["executor"] = executor_name
            result: ExecutionResult | None = None
            for attempt in range(1, self._max_retries + 2):
                if self._is_canceled(task.id):
                    _stop_heartbeat()
                    self._finalize_canceled(
                        task_id=task.id,
                        before_snapshot=before_snapshot,
                        before_content_snapshot=before_content_snapshot,
                    )
                    return
                self._update_record(task.id, attempts=attempt)
                task_attachments = (*task.attachments, *task.image_attachments)
                attachment_redactor = AttachmentReferenceRedactor(
                    task_attachments,
                    workspace=task.workspace,
                )

                def send_redacted_output(
                    text: str,
                    *,
                    redactor: AttachmentReferenceRedactor = attachment_redactor,
                ) -> None:
                    redacted = redactor.feed(text)
                    if redacted:
                        channel_adapter.send_output_delta(task.id, redacted, channel_context)

                result = executor.execute(
                    task,
                    cancel_requested=lambda task_id=task.id: self._cancel_requested(task_id),
                    stream_output=send_redacted_output,
                )
                redacted_tail = attachment_redactor.flush()
                if redacted_tail:
                    channel_adapter.send_output_delta(task.id, redacted_tail, channel_context)
                result.summary = redact_attachment_references(
                    result.summary,
                    task_attachments,
                    workspace=task.workspace,
                )
                result.stdout = redact_attachment_references(
                    result.stdout,
                    task_attachments,
                    workspace=task.workspace,
                )
                result.stderr = redact_attachment_references(
                    result.stderr,
                    task_attachments,
                    workspace=task.workspace,
                )
                if self._cancel_requested(task.id):
                    _stop_heartbeat()
                    self._finalize_canceled(
                        task_id=task.id,
                        partial=result,
                        before_snapshot=before_snapshot,
                        before_content_snapshot=before_content_snapshot,
                    )
                    return
                if result.success:
                    break
                if not self._should_retry(result=result, attempt=attempt):
                    break
                self._update_record(task.id, status="RETRYING")
                self._set_status(
                    task,
                    "RETRYING",
                    extra={"attempt": attempt, "summary": result.summary},
                )
                channel_adapter.send_status(
                    task.id,
                    "RETRYING",
                    channel_context,
                    detail=f"attempt={attempt}/{self._max_retries + 1}",
                )
                time.sleep(self._retry_backoff_sec * attempt)

            if result is None:
                raise RuntimeError("Executor returned no result")
            final_status = "RETURNED" if result.success else "FAILED"
            # Stop any live "responding" indicator now, before the workspace
            # snapshot below. Executors return as soon as the answer is ready, but
            # _attach_workspace_delta can take several seconds on large workspaces;
            # without this the channel's spinner would linger for that whole time.
            # Only channels with a live indicator (Slack) implement this.
            finalize_response = getattr(channel_adapter, "finalize_response", None)
            if finalize_response is not None:
                finalize_response(task.id, result, channel_context)
            if result.pending_finalization:
                # The executor (Antigravity) answered early while its process keeps
                # doing post-answer housekeeping — flushing its SQLite trajectory DB
                # (the default source of changed_files) and any pending file writes.
                # finalize_response above already stopped any live channel indicator
                # and the answer is with the caller, so only this internal worker
                # waits here — for the process to truly exit — before _attach_workspace_delta
                # reads the trajectory (and, when FLUXION_CHANGE_DETECTION is on,
                # snapshots the tree). The run stays non-terminal until the delta is
                # attached, so changed_files_available reports false until it's accurate.
                waiter = getattr(executor, "wait_for_finalization", None)
                if waiter is not None:
                    waiter(task.id, timeout=_PENDING_FINALIZE_TIMEOUT_SEC)
            self._attach_workspace_delta(
                task=task,
                result=result,
                status=final_status,
                before_snapshot=before_snapshot,
                before_content_snapshot=before_content_snapshot,
            )
            self._update_record(
                task.id,
                status=final_status,
                summary=result.summary,
                finished_at=datetime.now(UTC),
            )
            self._set_status(task, final_status, extra={"result": asdict(result)})
            if result.executor_session_id:
                self._sessions.set_executor_session_id(
                    conversation_key=self._conversation_key(task),
                    channel=task.channel,
                    user_id=task.user_id,
                    executor_name=executor_name,
                    session_id=result.executor_session_id,
                )
                # Remember which model created this conversation, so a later
                # resume can tell the caller it may not get the model it asked
                # for (and therefore not the quota pool it expected).
                self._sessions.set_session_model(
                    conversation_key=self._conversation_key(task),
                    channel=task.channel,
                    user_id=task.user_id,
                    session_id=result.executor_session_id,
                    model=str(task.metadata.get("model") or ""),
                )
            _stop_heartbeat()
            channel_adapter.send_result(task.id, result, channel_context)
        except Exception as exc:
            logger.exception("Task failed: %s", task.id)
            result = ExecutionResult(
                success=False,
                summary=f"Task failed with internal error: {exc}",
                stdout="",
                stderr=traceback.format_exc(),
                exit_code=1,
            )
            self._attach_workspace_delta(
                task=task,
                result=result,
                status="FAILED",
                before_snapshot=before_snapshot,
                before_content_snapshot=before_content_snapshot,
            )
            self._update_record(
                task.id,
                status="FAILED",
                summary=result.summary,
                finished_at=datetime.now(UTC),
            )
            self._set_status(task, "FAILED", extra={"result": asdict(result)})
            _stop_heartbeat()
            channel_adapter.send_result(task.id, result, channel_context)
            if result.executor_session_id:
                executor_name = str(task.metadata.get("executor") or "").strip()
                self._sessions.set_executor_session_id(
                    conversation_key=self._conversation_key(task),
                    channel=task.channel,
                    user_id=task.user_id,
                    executor_name=executor_name,
                    session_id=result.executor_session_id,
                )
        finally:
            _stop_heartbeat()
            with self._metrics_lock:
                self._running_tasks = max(0, self._running_tasks - 1)
                self._total_completed += 1
            self._sessions.set_active_task(
                conversation_key=self._conversation_key(task),
                channel=task.channel,
                user_id=task.user_id,
                task_id=None,
            )
            self._registry.untrack(task.id)
            self._release_workspace_locks(workspace_lock, workspace_file_lock)

    def _should_retry(self, *, result: ExecutionResult, attempt: int) -> bool:
        if attempt > self._max_retries:
            return False
        if result.exit_code in {124, 137}:
            return True
        message = f"{result.summary}\n{result.stderr}".lower()
        transient_hints = [
            "rate limit",
            "temporarily",
            "timeout",
            "connection reset",
            "service unavailable",
        ]
        return any(hint in message for hint in transient_hints)

    def _is_canceled(self, task_id: str) -> bool:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return False
            return record.cancel_requested and record.status == "CANCELED"

    def _cancel_requested(self, task_id: str) -> bool:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return False
            return record.cancel_requested

    def _finalize_canceled(
        self,
        *,
        task_id: str,
        partial: ExecutionResult | None = None,
        before_snapshot: dict[str, FileFingerprint] | None = None,
        before_content_snapshot: ContentSnapshot | None = None,
    ) -> None:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return
            if record.status == "CANCELED" and record.finished_at is not None:
                # cancel_task already finalized this one (it was still queued
                # when the request landed); don't emit a second result.
                return
            record.status = "CANCELED"
            record.summary = "Task canceled by request."
            record.last_updated_at = datetime.now(UTC)
            record.finished_at = record.last_updated_at
        result = self._canceled_result(summary=record.summary, partial=partial)
        if partial is not None or before_snapshot is not None:
            # A canceled run still changed files, and the caller needs to know
            # which ones before it can review or revert them. The delta is only
            # meaningful once the executor actually ran: a run canceled while it
            # queued or waited for the workspace lock touched nothing, and
            # attributing the workspace's pre-existing state to it would be a lie.
            self._attach_workspace_delta(
                task=record.task,
                result=result,
                status="CANCELED",
                before_snapshot=before_snapshot,
                before_content_snapshot=before_content_snapshot,
            )
        self._set_status(record.task, "CANCELED", extra={"result": asdict(result)})
        record.channel_adapter.send_status(
            record.task.id, "CANCELED", record.channel_context, detail="execution canceled"
        )
        record.channel_adapter.send_result(record.task.id, result, record.channel_context)

    def _canceled_result(
        self, *, summary: str, partial: ExecutionResult | None = None
    ) -> ExecutionResult:
        """The result to report for a canceled run.

        When the executor got far enough to return something, that partial
        result is what carries the run's identity — its session id, log file and
        file operations. Replacing it with an empty shell used to throw all of
        that away, which left the caller unable to see what the run had already
        written, and left nothing for revert_subagent_run to work from.
        """
        if partial is None:
            return ExecutionResult(
                success=False,
                summary=summary,
                stdout="",
                stderr="",
                exit_code=130,
            )
        partial.success = False
        partial.summary = summary
        partial.exit_code = 130
        # Whatever the executor was still waiting to finalize, it is not coming:
        # the run ends here.
        partial.pending_finalization = False
        return partial

    def _attach_workspace_delta(
        self,
        *,
        task: Task,
        result: ExecutionResult,
        status: str,
        before_snapshot: dict[str, FileFingerprint] | None,
        before_content_snapshot: ContentSnapshot | None,
    ) -> None:
        if before_snapshot is not None:
            after_snapshot = self._take_snapshot(task.workspace)
            if after_snapshot is not None:
                delta = diff_snapshot(before_snapshot, after_snapshot)
                result.changed_files = delta.changed
            else:
                delta = None
            if result.artifacts:
                # Structured upload actions from executor take top priority.
                result.artifacts = result.artifacts[: self._artifact_max_files]
        else:
            if result.artifacts:
                result.artifacts = result.artifacts[: self._artifact_max_files]

        trajectory = self._capture_antigravity_trajectory(
            task=task,
            result=result,
            status=status,
        )
        if trajectory is not None:
            if not result.changed_files:
                result.changed_files = trajectory.changed_files
            if not result.change_set_file and trajectory.change_set_file:
                result.change_set_file = trajectory.change_set_file
            result.risk_flags = _merge_unique(result.risk_flags, trajectory.risk_flags)
        if (
            not result.change_set_file
            and self._revert_capture == "structured"
            and result.file_operations
        ):
            # No before-snapshot was taken (the perf-sensitive default), so build a
            # best-effort revert ChangeSet straight from the executor stream. Only
            # creates/edits end up recoverable; overwrites/updates/deletes are
            # recorded as unrecoverable. See build_stream_change_set.
            try:
                change_set = build_stream_change_set(
                    run_id=task.id,
                    workspace=task.workspace,
                    status=status,
                    operations=result.file_operations,
                )
                if change_set.has_changes:
                    path = save_change_set(self._storage.data_dir, change_set)
                    result.change_set_file = str(path)
                    if not result.changed_files:
                        result.changed_files = change_set.changed_files
            except Exception:
                logger.debug("stream change set capture failed for task %s", task.id, exc_info=True)
        if before_content_snapshot is not None:
            try:
                after_content_snapshot = self._take_content_snapshot(task)
                if after_content_snapshot is not None:
                    change_set = build_change_set(
                        run_id=task.id,
                        workspace=task.workspace,
                        status=status,
                        before=before_content_snapshot,
                        after=after_content_snapshot,
                    )
                    if change_set.has_changes:
                        path = save_change_set(self._storage.data_dir, change_set)
                        result.change_set_file = str(path)
                        if not result.changed_files:
                            result.changed_files = change_set.changed_files
            except Exception:
                logger.debug("change set capture failed for task %s", task.id, exc_info=True)
        if not result.diff_summary:
            result.diff_summary = get_git_diff_summary(task.workspace)

    def _take_content_snapshot(self, task: Task) -> ContentSnapshot | None:
        if self._revert_capture != "full":
            return None
        if self._change_set_max_file_bytes <= 0 or self._change_set_max_total_bytes <= 0:
            return None
        try:
            return take_content_snapshot(
                task.workspace,
                max_file_bytes=self._change_set_max_file_bytes,
                max_total_bytes=self._change_set_max_total_bytes,
            )
        except Exception:
            logger.debug("content snapshot failed for task %s", task.id, exc_info=True)
            return None

    def _take_snapshot(self, workspace: Path) -> dict[str, FileFingerprint] | None:
        if self._change_detection not in {"snapshot", "force"}:
            return None
        return take_snapshot(workspace)

    def _capture_antigravity_trajectory(
        self,
        *,
        task: Task,
        result: ExecutionResult,
        status: str,
    ):
        if str(task.metadata.get("executor") or "").strip() != "antigravity":
            return None
        session_id = (result.executor_session_id or "").strip()
        if not session_id:
            return None
        conversation_key = self._conversation_key(task)
        # Only attribute trajectory steps newer than the high-water mark from this
        # session's prior run, so a resumed session reports just the current run's
        # files instead of the whole accumulated DB. Fresh sessions have no mark
        # (since_idx=0) and read their single-run DB unchanged.
        since_idx = self._sessions.get_trajectory_idx(
            conversation_key=conversation_key,
            channel=task.channel,
            user_id=task.user_id,
            session_id=session_id,
        )
        # Still allow shell-derived risk flags and changed files, but skip saving
        # structured revert metadata when the user explicitly disabled it.
        revert_capture = (
            "off"
            if (self._revert_capture == "off" and self._change_detection == "off")
            else self._revert_capture
        )
        capture = collect_antigravity_trajectory(
            session_id=session_id,
            workspace=task.workspace,
            data_dir=self._storage.data_dir,
            run_id=task.id,
            status=status,
            revert_capture=revert_capture,
            since_idx=since_idx,
        )
        self._sessions.set_trajectory_idx(
            conversation_key=conversation_key,
            channel=task.channel,
            user_id=task.user_id,
            session_id=session_id,
            idx=capture.max_step_idx,
        )
        return (
            capture
            if capture.changed_files or capture.change_set_file or capture.risk_flags
            else None
        )

    def _acquire_workspace_locks(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
    ) -> tuple[threading.Lock | None, Any]:
        """Serialize writes to one workspace, in-process and across processes.

        Both waits are bounded and observable. The previous implementation used
        a blocking ``Lock.acquire()`` and a blocking ``flock()``, so a holder
        that never finished parked this worker forever with no way to see why.

        Raises ``_WorkspaceBusy`` if the workspace stays held past the timeout.
        """
        if not self._needs_workspace_write_lock(task):
            return None, None
        workspace = str(task.workspace.resolve())
        deadline = time.monotonic() + self._workspace_lock_timeout_sec
        thread_lock = self._workspace_write_lock_for(workspace)
        acquired = False
        while not acquired:
            acquired = thread_lock.acquire(timeout=_LOCK_POLL_INTERVAL_SEC)
            if acquired:
                break
            if self._cancel_requested(task.id):
                raise _WorkspaceBusy(f"canceled while waiting for workspace {workspace}")
            if time.monotonic() >= deadline:
                raise _WorkspaceBusy(
                    f"workspace {workspace} still held by another run in this process "
                    f"after {self._workspace_lock_timeout_sec}s"
                )
            self._note_blocked(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
                workspace=workspace,
                holder=None,
            )
        try:
            file_lock = self._acquire_workspace_file_lock(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
                workspace=workspace,
                deadline=deadline,
            )
        except BaseException:
            thread_lock.release()
            raise
        return thread_lock, file_lock

    def _needs_workspace_write_lock(self, task: Task) -> bool:
        subagent = task.metadata.get("subagent")
        if not isinstance(subagent, dict):
            return False
        return str(subagent.get("mode") or "") == "workspace-write"

    def _workspace_write_lock_for(self, workspace: str) -> threading.Lock:
        with self._workspace_locks_guard:
            lock = self._workspace_write_locks.get(workspace)
            if lock is None:
                lock = threading.Lock()
                self._workspace_write_locks[workspace] = lock
            return lock

    def _acquire_workspace_file_lock(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
        workspace: str,
        deadline: float,
    ):
        """Cross-process advisory lock for workspace-write runs.

        The in-memory lock only serializes within one process, and each caller
        conversation gets its own fluxion-mcp process. This flock (keyed by the
        resolved workspace path) closes that gap; the OS releases it if a holder
        crashes. The holder's identity is written into the file so a blocked run
        can say who it is waiting for. Returns the open handle, or None when not
        applicable (read-only run, or no fcntl on this platform).
        """
        if fcntl is None:
            return None
        digest = hashlib.sha1(workspace.encode("utf-8")).hexdigest()
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = LOCK_DIR / f"{digest}.lock"
        # r+ so a waiter can read the holder's identity; the file is never
        # truncated on open, which would erase it for everyone.
        handle = open(lock_path, "a+", encoding="utf-8")
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                pass
            except OSError:
                handle.close()
                raise
            if self._cancel_requested(task.id):
                handle.close()
                raise _WorkspaceBusy(f"canceled while waiting for workspace {workspace}")
            if time.monotonic() >= deadline:
                holder = _read_lock_holder(lock_path)
                handle.close()
                raise _WorkspaceBusy(
                    f"workspace {workspace} still locked by another Fluxion process "
                    f"after {self._workspace_lock_timeout_sec}s (holder: {holder or 'unknown'})"
                )
            self._note_blocked(
                task=task,
                channel_adapter=channel_adapter,
                channel_context=channel_context,
                workspace=workspace,
                holder=_read_lock_holder(lock_path),
            )
            time.sleep(_LOCK_POLL_INTERVAL_SEC)
        _write_lock_holder(handle, task=task)
        return handle

    def _release_workspace_locks(self, thread_lock: threading.Lock | None, file_lock: Any) -> None:
        if file_lock is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(file_lock.fileno(), fcntl.LOCK_UN)
            except Exception:
                logger.debug("workspace file lock release failed", exc_info=True)
            finally:
                try:
                    file_lock.close()
                except Exception:
                    logger.debug("workspace file lock close failed", exc_info=True)
        if thread_lock is not None:
            try:
                thread_lock.release()
            except RuntimeError:
                logger.debug("workspace lock was already released", exc_info=True)

    def _note_blocked(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
        workspace: str,
        holder: dict[str, Any] | None,
    ) -> None:
        """Record why a task that left the queue still hasn't started."""
        reason = "workspace_busy"
        with self._records_lock:
            record = self._records.get(task.id)
            if record is None:
                return
            already_noted = record.blocked_reason == reason
            record.blocked_reason = reason
            record.blocked_by_workspace = workspace
            record.blocked_by = holder
            record.last_updated_at = datetime.now(UTC)
            since_notice = (record.last_updated_at - record.submitted_at).total_seconds()
        if already_noted and since_notice % _BLOCKED_NOTICE_INTERVAL_SEC > _LOCK_POLL_INTERVAL_SEC:
            return
        detail = f"waiting for workspace {workspace}"
        if holder:
            detail += f" held by task {str(holder.get('task_id') or '')[:8]}"
        self._set_status(
            task,
            "QUEUED",
            extra={"blocked": {"reason": reason, "workspace": workspace, "holder": holder}},
        )
        try:
            channel_adapter.send_status(task.id, "QUEUED", channel_context, detail=detail)
        except Exception:
            logger.debug("blocked status notice failed for task %s", task.id, exc_info=True)

    def _finalize_lock_timeout(
        self,
        *,
        task: Task,
        channel_adapter: ChannelAdapter,
        channel_context: dict[str, Any],
        reason: str,
    ) -> None:
        if self._cancel_requested(task.id):
            self._finalize_canceled(task_id=task.id)
            return
        result = ExecutionResult(
            success=False,
            summary=f"Task did not start: {reason}.",
            stdout="",
            stderr="",
            exit_code=1,
        )
        self._update_record(
            task.id,
            status="FAILED",
            summary=result.summary,
            finished_at=datetime.now(UTC),
        )
        self._set_status(task, "FAILED", extra={"result": asdict(result)})
        channel_adapter.send_status(task.id, "FAILED", channel_context, detail="workspace busy")
        channel_adapter.send_result(task.id, result, channel_context)

    def _update_record(
        self,
        task_id: str,
        *,
        status: str | None = None,
        attempts: int | None = None,
        summary: str | None = None,
        finished_at: datetime | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        with self._records_lock:
            record = self._records.get(task_id)
            if record is None:
                return
            if blocked_reason is not None:
                record.blocked_reason = blocked_reason
                if not blocked_reason:
                    record.blocked_by_workspace = ""
                    record.blocked_by = None
            if status is not None:
                record.status = status
            if attempts is not None:
                record.attempts = attempts
            if summary is not None:
                record.summary = summary
            if finished_at is not None:
                record.finished_at = finished_at
            record.last_updated_at = datetime.now(UTC)

    def _set_status(self, task: Task, status: str, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "task_id": task.id,
            "status": status,
            "task": asdict(task),
            # Who to ask about this task — and, once that process is gone, the
            # evidence that lets startup reconciliation close it out.
            "owner": current_owner().to_payload(),
        }
        if extra:
            payload.update(extra)
        self._storage.append_task_event(payload)

    def _conversation_key(self, task: Task) -> str:
        value = str(task.metadata.get("conversation_key", "")).strip()
        if value:
            return value
        return f"{task.channel}:{task.user_id}"


def _merge_unique(existing: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *extra]:
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged
