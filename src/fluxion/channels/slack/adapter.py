from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.artifacts import upload_channel_artifacts
from fluxion.channels.base import SettingsHotReloader, authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.inbox import make_inbox_dir
from fluxion.channels.slack.pending_store import PendingUserStore
from fluxion.config.settings import Settings
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import resolve_locale, t
from fluxion.renderers.markdown_renderer import MarkdownRenderer
from fluxion.slack_limits import SLACK_TEXT_SOFT_LIMIT
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)
_SLACK_MRKDWN_BLOCK_LIMIT = 3000


@dataclass
class _SlackStreamState:
    ts: str
    streamed_answer: str = ""
    progress_started: bool = False
    phase: str = "analyzing"
    analyzing_started_at: float = 0.0
    responding_started_at: float | None = None


class SlackChannelAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = MarkdownRenderer(soft_limit=SLACK_TEXT_SOFT_LIMIT)
        self._gateway = None
        self._thread_activation_lock = threading.Lock()
        self._activated_threads: set[str] = set()
        self._task_message_lock = threading.Lock()
        self._task_message_ts: dict[str, str] = {}
        self._stream_lock = threading.Lock()
        self._task_streams: dict[str, _SlackStreamState] = {}
        # Tasks whose spinner was already stopped early in finalize_response.
        self._finalized: set[str] = set()
        self._activation_file = settings.data_dir / "slack_activated_threads.json"
        self._load_activated_threads()
        self._pending_store = PendingUserStore(settings.data_dir)
        self._settings_reloader = SettingsHotReloader(channel="Slack")
        self._app = App(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )
        self._register_handlers()

    def start(self, gateway: GatewayCore) -> None:
        self._gateway = gateway
        handler = SocketModeHandler(self._app, self._settings.slack_app_token)
        handler.start()

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status not in self._settings.status_updates:
            return
        locale = self._locale_for_context(context)
        if status == "RUNNING":
            started_at = time.time()
            state, created = self._ensure_stream(
                task_id=task_id,
                context=context,
                initial_text="",
                progress_status="in_progress",
                locale=locale,
                started_at=started_at,
            )
            if state is not None and created:
                self._clear_task_message(task_id=task_id, context=context)
            return
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        self._upsert_task_message(task_id=task_id, context=context, text=text, final=False)

    def finalize_response(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        """Stop the streaming spinner immediately, before the engine's workspace
        snapshot (which can take several seconds on large workspaces). Artifacts
        and the result record are still handled later in send_result."""
        if self._finalize_stream(task_id=task_id, result=result, context=context):
            with self._stream_lock:
                self._finalized.add(task_id)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        with self._stream_lock:
            already_finalized = task_id in self._finalized
            self._finalized.discard(task_id)
        if already_finalized:
            # Spinner was already stopped in finalize_response; just attach files.
            self._upload_artifacts(result=result, context=context)
            return
        stream_completed = self._finalize_stream(task_id=task_id, result=result, context=context)
        if stream_completed:
            self._upload_artifacts(result=result, context=context)
            return
        text = self._renderer.render_result(
            task_id,
            result,
            locale=self._locale_for_context(context),
        )
        self._upsert_task_message(task_id=task_id, context=context, text=text, final=True)
        self._upload_artifacts(result=result, context=context)

    def send_typing(self, context: dict) -> None:
        channel = str(context.get("channel") or "").strip()
        if not channel:
            return
        try:
            self._app.client.api_call("chat.typing", json={"channel": channel})
        except Exception:
            logger.debug("chat.typing failed", exc_info=True)

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        chunk = text or ""
        if not chunk:
            return
        state, created = self._ensure_stream(
            task_id=task_id,
            context=context,
            initial_text=chunk,
            progress_status=None,
            locale=self._locale_for_context(context),
        )
        if state is None:
            return
        if created:
            self._clear_task_message(task_id=task_id, context=context)
            return
        for attempt in range(1, 4):
            try:
                chunks: list[dict[str, Any]] = []
                responding_started_at: float | None = None
                if state.progress_started and state.phase != "responding":
                    responding_started_at = time.time()
                    chunks.append(
                        self._task_update_chunk(
                            task_id=task_id,
                            status="in_progress",
                            phase="responding",
                            locale=self._locale_for_context(context),
                            details=self._task_update_details(
                                phase="responding",
                                locale=self._locale_for_context(context),
                                state=state,
                                responding_started_at=responding_started_at,
                            ),
                        )
                    )
                chunks.append(self._markdown_chunk(chunk))
                self._app.client.chat_appendStream(
                    channel=context["channel"],
                    ts=state.ts,
                    chunks=chunks,
                )
                with self._stream_lock:
                    latest = self._task_streams.get(task_id)
                    if latest is not None:
                        latest.streamed_answer += chunk
                        latest.phase = "responding"
                        if latest.responding_started_at is None:
                            latest.responding_started_at = responding_started_at
                return
            except Exception:
                logger.exception("chat_appendStream failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)

    def _register_handlers(self) -> None:
        @self._app.event("message")
        def on_message(event: dict, say, logger) -> None:  # type: ignore[no-redef]
            # Allow plain messages and file uploads; skip edits/joins/bot echoes etc.
            subtype = event.get("subtype")
            if subtype and subtype != "file_share":
                return
            if not event.get("user"):
                return

            valid, reason = self._validate_inbound(event)
            user_id = str(event.get("user") or "")
            if not valid:
                # Only allowlist rejections park the sender as a pending user
                # — mention/channel-mapping failures say nothing about them.
                if "allowlist" in reason.lower() and user_id:
                    self._pending_store.record(
                        user_id,
                        str(event.get("text") or "").strip() or "[attachment]",
                        notify_locale=self._locale_for_context({"source_text": ""}),
                    )
                channel = event.get("channel")
                if channel:
                    say(
                        text=self._format_rejection(
                            reason,
                            user_id,
                            locale=self._locale_for_context(
                                {"source_text": str(event.get("text") or "")}
                            ),
                        ),
                        channel=channel,
                    )
                return
            self._pending_store.remove(user_id)

            text = (event.get("text") or "").strip()
            text = self._normalize_text(text)
            files = event.get("files") if isinstance(event.get("files"), list) else []
            # Control commands are text-only; a message carrying files is always a task.
            if not files and self._handle_control_command(text=text, event=event, say=say):
                return
            workspace_override, task_text = self._extract_workspace(text)
            if not task_text and not files:
                say(
                    text="Please send a non-empty task description.",
                    channel=event["channel"],
                )
                return

            if self._gateway is None:
                logger.error("Gateway is not ready")
                say(text="Gateway not ready.", channel=event["channel"])
                return

            try:
                workspace = self._settings.resolve_workspace_for_event(
                    channel_id=str(event.get("channel") or ""),
                    channel_type=str(event.get("channel_type") or ""),
                    raw_workspace=workspace_override,
                )
            except ValueError as exc:
                say(
                    text=f"Rejected: {exc}",
                    channel=event["channel"],
                )
                return

            if files:
                saved = self._download_files(files=files, workspace=workspace, event=event, say=say)
                if saved:
                    rel = [str(p.relative_to(workspace)) for p in saved]
                    note = "Attached file(s) saved in the workspace:\n" + "\n".join(
                        f"- {r}" for r in rel
                    )
                    task_text = (
                        f"{task_text}\n\n{note}".strip()
                        if task_text
                        else (
                            "The user sent the following file(s) without a text "
                            "instruction. Inspect them and respond appropriately.\n\n" + note
                        )
                    )

            convo_key = self._build_conversation_key(event)
            executor_to_use = self._resolve_executor_for_event(event)
            model_override = self._resolve_model_override_for_event(
                event=event,
                executor_name=executor_to_use,
            )
            metadata = {
                "executor": executor_to_use,
                "conversation_key": convo_key,
            }
            if model_override:
                metadata["model"] = model_override

            task = Task.create(
                channel="slack",
                user_id=event["user"],
                text=task_text,
                workspace=workspace,
                metadata=metadata,
            )
            context = {
                "channel": event["channel"],
                "thread_ts": event.get("thread_ts") or event.get("ts"),
                "user": event["user"],
                "team": event.get("team") or "",
                "channel_type": event.get("channel_type") or "",
                "source_text": task_text,
                "workspace": str(workspace),
            }
            ok, info = self._gateway.submit_task(
                task=task,
                channel_adapter=self,
                channel_context=context,
            )
            if not ok:
                say(
                    text=f"Rejected: {info}",
                    channel=event["channel"],
                )
                return

    def _validate_inbound(self, event: dict[str, Any]) -> tuple[bool, str]:
        channel_type = str(event.get("channel_type") or "")
        channel_id = str(event.get("channel") or "")
        if channel_type != "im":
            if not self._settings.slack_allow_channels:
                return False, "only direct messages are allowed."
            if channel_type not in {"channel", "group"}:
                return False, f"unsupported channel_type: {channel_type}"
            if channel_id not in self._settings.slack_channel_workspaces:
                return False, "channel is not allowed (not mapped)."
            if self._settings.slack_require_mention_in_channels:
                text = str(event.get("text") or "").strip()
                mentioned = self._is_mentioned(text)
                thread_key = self._thread_key(event)
                if thread_key and mentioned:
                    with self._thread_activation_lock:
                        self._activated_threads.add(thread_key)
                        self._persist_activated_threads()
                if not mentioned:
                    if thread_key:
                        with self._thread_activation_lock:
                            if thread_key not in self._activated_threads:
                                return (
                                    False,
                                    "thread is not activated. mention @Fluxion at least once in this thread.",
                                )
                    else:
                        return False, "channel messages must start with @Fluxion mention."
        user_id = event.get("user")
        reloader = getattr(self, "_settings_reloader", None)
        if reloader is not None:
            self._settings = reloader.reload_if_changed(self._settings)
        return authorize_inbound(
            channel="slack",
            user_id=user_id or "",
            allowed_users=self._settings.slack_allowed_users,
        )

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="Slack member ID",
            env_key="FLUXION_SLACK_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> Slack -> Pending Users",
        )

    def _upload_artifacts(self, *, result: ExecutionResult, context: dict) -> None:
        upload_channel_artifacts(
            result=result,
            context=context,
            max_files=getattr(self._settings, "artifact_max_files", 8),
            upload_log_on_success=getattr(self._settings, "upload_log_on_success", False),
            upload_one=lambda path: self._upload_file(path=path, context=context),
        )

    def _normalize_text(self, text: str) -> str:
        normalized = text.strip()
        for prefix in ("<@", "@Fluxion", "@fluxion"):
            if prefix == "<@":
                if normalized.startswith("<@"):
                    end = normalized.find(">")
                    if end != -1:
                        normalized = normalized[end + 1 :].strip()
            elif normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
        return normalized

    def _extract_workspace(self, text: str) -> tuple[str | None, str]:
        match = re.match(r"^(workspace=|/workspace\s+)(\S+)\s*(.*)$", text.strip())
        if not match:
            return None, text.strip()
        workspace = match.group(2).strip()
        remaining = match.group(3).strip()
        return workspace, remaining

    def _build_conversation_key(self, event: dict[str, Any]) -> str:
        channel = str(event.get("channel") or "")
        thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
        return f"slack:{channel}:{thread_ts or channel}"

    def _resolve_executor_for_event(self, event: dict[str, Any]) -> str:
        if self._gateway is None:
            return self._settings.default_executor
        user_id = str(event.get("user") or "")
        for convo_key in self._override_lookup_keys(event):
            override = self._gateway._sessions.get_executor_override(
                conversation_key=convo_key,
                channel="slack",
                user_id=user_id,
            )
            if override:
                return override
        return self._settings.default_executor

    def _resolve_model_override_for_event(
        self,
        *,
        event: dict[str, Any],
        executor_name: str,
    ) -> str | None:
        if self._gateway is None:
            return None
        user_id = str(event.get("user") or "")
        for convo_key in self._override_lookup_keys(event):
            override = self._gateway._sessions.get_model_override(
                conversation_key=convo_key,
                channel="slack",
                user_id=user_id,
                executor_name=executor_name,
            )
            if override:
                return override
        return None

    @staticmethod
    def _override_lookup_keys(event: dict[str, Any]) -> list[str]:
        channel = str(event.get("channel") or "")
        if not channel:
            return []
        keys: list[str] = []
        thread_ts = str(event.get("thread_ts") or "").strip()
        if thread_ts:
            keys.append(f"slack:{channel}:{thread_ts}")
        keys.append(f"slack:{channel}:{channel}")
        return keys

    def _thread_key(self, event: dict[str, Any]) -> str | None:
        channel = str(event.get("channel") or "").strip()
        thread_ts = str(event.get("thread_ts") or event.get("ts") or "").strip()
        if not channel or not thread_ts:
            return None
        return f"{channel}:{thread_ts}"

    def _load_activated_threads(self) -> None:
        if not self._activation_file.exists():
            return
        try:
            raw = json.loads(self._activation_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                keys = {str(x).strip() for x in raw if str(x).strip()}
                with self._thread_activation_lock:
                    self._activated_threads = keys
        except Exception:
            logger.exception("Failed to load activated thread state")

    def _persist_activated_threads(self) -> None:
        try:
            self._activation_file.parent.mkdir(parents=True, exist_ok=True)
            payload = sorted(self._activated_threads)
            self._activation_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to persist activated thread state")

    def _is_mentioned(self, text: str) -> bool:
        value = text.strip()
        return (
            value.startswith("<@") or value.startswith("@Fluxion") or value.startswith("@fluxion")
        )

    def _post_message(self, *, context: dict, text: str) -> None:
        for attempt in range(1, 4):
            try:
                self._app.client.chat_postMessage(
                    channel=context["channel"],
                    text=text,
                    blocks=self._mrkdwn_blocks(text),
                    thread_ts=context.get("thread_ts"),
                )
                return
            except Exception:
                logger.exception("chat_postMessage failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)

    def _mrkdwn_blocks(self, text: str) -> list[dict[str, Any]]:
        chunks = self._split_mrkdwn_text(text)
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": chunk,
                },
            }
            for chunk in chunks
        ]

    def _split_mrkdwn_text(self, text: str) -> list[str]:
        if len(text) <= _SLACK_MRKDWN_BLOCK_LIMIT:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > _SLACK_MRKDWN_BLOCK_LIMIT:
                if current:
                    chunks.append(current.rstrip())
                    current = ""
                for idx in range(0, len(line), _SLACK_MRKDWN_BLOCK_LIMIT):
                    chunks.append(line[idx : idx + _SLACK_MRKDWN_BLOCK_LIMIT].rstrip())
                continue
            if len(current) + len(line) > _SLACK_MRKDWN_BLOCK_LIMIT:
                chunks.append(current.rstrip())
                current = line
            else:
                current += line
        if current:
            chunks.append(current.rstrip())
        return chunks

    def _upsert_task_message(
        self,
        *,
        task_id: str,
        context: dict,
        text: str,
        final: bool,
    ) -> None:
        ts: str | None
        with self._task_message_lock:
            ts = self._task_message_ts.get(task_id)
        if ts:
            for attempt in range(1, 4):
                try:
                    self._app.client.chat_update(
                        channel=context["channel"],
                        ts=ts,
                        text=text,
                    )
                    if final:
                        with self._task_message_lock:
                            self._task_message_ts.pop(task_id, None)
                    return
                except Exception:
                    logger.exception("chat_update failed on attempt %s", attempt)
                    time.sleep(0.5 * attempt)
        for attempt in range(1, 4):
            try:
                resp = self._app.client.chat_postMessage(
                    channel=context["channel"],
                    text=text,
                    thread_ts=context.get("thread_ts"),
                )
                message_ts = str(resp.get("ts") or "").strip()
                if message_ts and not final:
                    with self._task_message_lock:
                        self._task_message_ts[task_id] = message_ts
                return
            except Exception:
                logger.exception("chat_postMessage (upsert) failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)

    def _ensure_stream(
        self,
        *,
        task_id: str,
        context: dict,
        initial_text: str,
        progress_status: str | None,
        locale: str,
        started_at: float | None = None,
    ) -> tuple[_SlackStreamState | None, bool]:
        with self._stream_lock:
            existing = self._task_streams.get(task_id)
            if existing is not None:
                return existing, False
            chunks: list[dict[str, Any]] = []
            if progress_status:
                chunks.append(
                    self._task_update_chunk(
                        task_id=task_id,
                        status=progress_status,
                        phase="analyzing",
                        locale=locale,
                        details=self._task_update_details(
                            phase="analyzing",
                            locale=locale,
                            started_at=started_at or time.time(),
                        ),
                    )
                )
            if initial_text:
                chunks.append(self._markdown_chunk(initial_text))
            for attempt in range(1, 4):
                try:
                    resp = self._app.client.chat_startStream(
                        channel=context["channel"],
                        thread_ts=context.get("thread_ts"),
                        recipient_user_id=context.get("user"),
                        recipient_team_id=(
                            context.get("team")
                            if str(context.get("channel_type") or "") in {"channel", "group"}
                            else None
                        ),
                        chunks=chunks or None,
                    )
                    stream_ts = str(resp.get("ts") or "").strip()
                    if not stream_ts:
                        return None, False
                    state = _SlackStreamState(
                        ts=stream_ts,
                        streamed_answer=initial_text,
                        progress_started=bool(progress_status),
                        phase="analyzing" if progress_status else "responding",
                        analyzing_started_at=started_at or time.time(),
                        responding_started_at=None
                        if progress_status
                        else (started_at or time.time()),
                    )
                    self._task_streams[task_id] = state
                    return state, True
                except Exception:
                    logger.exception("chat_startStream failed on attempt %s", attempt)
                    time.sleep(0.5 * attempt)
        return None, False

    def _finalize_stream(self, *, task_id: str, result: ExecutionResult, context: dict) -> bool:
        with self._stream_lock:
            state = self._task_streams.pop(task_id, None)
        if state is None:
            return False
        chunks: list[dict[str, Any]] = []
        if state.progress_started:
            chunks.append(
                self._task_update_chunk(
                    task_id=task_id,
                    status="complete" if result.success else "error",
                    phase="complete" if result.success else "error",
                    locale=self._locale_for_context(context),
                    details=self._task_update_details(
                        phase="complete" if result.success else "error",
                        locale=self._locale_for_context(context),
                        state=state,
                        completed_at=time.time(),
                    ),
                    output=result.summary if not result.success else None,
                )
            )
        if result.success and not state.streamed_answer:
            final_text = self._renderer.render_result(
                task_id,
                result,
                locale=self._locale_for_context(context),
            )
            if final_text:
                chunks.append(self._markdown_chunk(final_text))
        if not result.success:
            final_text = self._renderer.render_result(
                task_id,
                result,
                locale=self._locale_for_context(context),
            )
            if final_text:
                prefix = "\n\n" if state.streamed_answer else ""
                chunks.append(self._markdown_chunk(prefix + final_text))
        for attempt in range(1, 4):
            try:
                self._app.client.chat_stopStream(
                    channel=context["channel"],
                    ts=state.ts,
                    chunks=chunks or None,
                )
                return True
            except Exception:
                logger.exception("chat_stopStream failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)
        return False

    def _markdown_chunk(self, text: str) -> dict[str, str]:
        return {"type": "markdown_text", "text": text}

    def _task_update_chunk(
        self,
        *,
        task_id: str,
        status: str,
        phase: str,
        locale: str,
        details: str | None = None,
        output: str | None = None,
    ) -> dict[str, str]:
        title = self._task_update_title(phase, locale)
        chunk: dict[str, str] = {
            "type": "task_update",
            "id": f"reply-{task_id}",
            "title": title,
            "status": status,
        }
        if details:
            chunk["details"] = details
        if output:
            chunk["output"] = output
        return chunk

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=self._settings.locale_mode,
            fixed_locale=self._settings.ui_locale,
            context=context,
        )

    def _task_update_title(self, phase: str, locale: str) -> str:
        key = {
            "analyzing": "task_update.title.analyzing",
            "responding": "task_update.title.responding",
            "complete": "task_update.title.complete",
            "error": "task_update.title.error",
        }.get(phase, "task_update.title.error")
        return t(locale, key)

    def _task_update_details(
        self,
        *,
        phase: str,
        locale: str,
        state: _SlackStreamState | None = None,
        started_at: float | None = None,
        responding_started_at: float | None = None,
        completed_at: float | None = None,
    ) -> str:
        if phase == "analyzing":
            return (
                self._format_timeline_line(
                    locale=locale,
                    phase="analyzing",
                    at=started_at or time.time(),
                )
                + "\n"
            )
        if phase == "responding":
            if state is None:
                return (
                    self._format_timeline_line(
                        locale=locale,
                        phase="responding",
                        at=responding_started_at or time.time(),
                    )
                    + "\n"
                )
            analyze_sec = max(
                0,
                int((responding_started_at or time.time()) - state.analyzing_started_at),
            )
            return (
                self._format_timeline_line(
                    locale=locale,
                    phase="responding",
                    at=responding_started_at or time.time(),
                    analyze_sec=analyze_sec,
                )
                + "\n"
            )
        if phase in {"complete", "error"} and state is not None:
            finished_at = completed_at or time.time()
            responding_at = state.responding_started_at
            if responding_at is not None:
                reply_sec = max(0, int(finished_at - responding_at))
            else:
                reply_sec = 0
            total_sec = max(0, int(finished_at - state.analyzing_started_at))
            return self._format_timeline_line(
                locale=locale,
                phase=phase,
                at=finished_at,
                reply_sec=reply_sec,
                total_sec=total_sec,
            )
        return self._format_timeline_line(locale=locale, phase=phase, at=time.time()) + "\n"

    def _format_timeline_line(
        self,
        *,
        locale: str,
        phase: str,
        at: float,
        analyze_sec: int | None = None,
        reply_sec: int | None = None,
        total_sec: int | None = None,
    ) -> str:
        prefix = datetime.fromtimestamp(at).strftime("%H:%M:%S")
        if phase == "analyzing":
            return t(locale, "timeline.analyzing", time=prefix)
        if phase == "responding":
            return t(
                locale,
                "timeline.responding",
                time=prefix,
                analyze_sec=analyze_sec or 0,
            )
        if phase == "complete":
            return t(
                locale,
                "timeline.complete",
                time=prefix,
                reply_sec=reply_sec or 0,
                total_sec=total_sec or 0,
            )
        return t(locale, "timeline.error", time=prefix)

    def _clear_task_message(self, *, task_id: str, context: dict) -> None:
        with self._task_message_lock:
            ts = self._task_message_ts.pop(task_id, None)
        if not ts:
            return
        for attempt in range(1, 4):
            try:
                self._app.client.chat_delete(
                    channel=context["channel"],
                    ts=ts,
                )
                return
            except Exception:
                logger.exception("chat_delete failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)

    def _download_files(
        self, *, files: list[dict[str, Any]], workspace: Path, event: dict, say
    ) -> list[Path]:
        """Download Slack file uploads into the task workspace.

        Slack file URLs are private: they must be fetched with the bot token in
        an ``Authorization: Bearer`` header (requires the ``files:read`` scope).
        """
        attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
        token = self._settings.slack_bot_token
        saved: list[Path] = []
        for idx, f in enumerate(files):
            if not isinstance(f, dict):
                continue
            url = f.get("url_private_download") or f.get("url_private")
            if not url:
                continue
            name = self._safe_filename(str(f.get("name") or f.get("title") or f"file_{idx}"))
            dest = attach_dir / name
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = resp.read()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                saved.append(dest)
            except Exception:
                logger.exception("Failed to download Slack file %s", f.get("id"))
                say(
                    text="Failed to download attachment (check files:read permission).",
                    channel=event["channel"],
                )
        return saved

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Keep only the basename and strip anything path- or shell-unfriendly.
        base = Path(name).name.strip() or "file"
        cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
        return cleaned or "file"

    def _upload_file(self, *, path: Path, context: dict) -> None:
        for attempt in range(1, 4):
            try:
                self._app.client.files_upload_v2(
                    channel=context["channel"],
                    file=str(path),
                    title=path.name,
                    thread_ts=context.get("thread_ts"),
                )
                return
            except Exception:
                logger.exception("File upload failed on attempt %s: %s", attempt, path)
                time.sleep(0.5 * attempt)

    def _handle_control_command(self, *, text: str, event: dict, say) -> bool:
        if self._gateway is None:
            return False
        thread_ts = event.get("thread_ts")
        channel = event["channel"]
        if thread_ts:
            convo_key = f"slack:{channel}:{thread_ts}"
        else:
            convo_key = f"slack:{channel}:{channel}"
        response = self._gateway.handle_control_command(
            text=text,
            user_id=str(event.get("user") or ""),
            convo_key=convo_key,
            channel="slack",
        )
        if response is not None:
            response = format_control_response(response, channel="slack")
            self._post_message(
                context={
                    "channel": channel,
                    "thread_ts": thread_ts,
                },
                text=response,
            )
            return True
        return False


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
