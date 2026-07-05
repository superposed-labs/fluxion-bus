"""Telegram Bot channel adapter.

Implements the ``ChannelAdapter`` protocol by long-polling the Telegram Bot API
``getUpdates`` endpoint for inbound messages and replying through
``sendMessage`` / ``editMessageText``.

Key design decisions:
  • Runs a dedicated daemon thread for the long-poll loop.
  • **Direct messages only** — group chats are rejected for now.
  • Progressive output uses ``editMessageText``: a single live message per task
    is created on ``RUNNING`` and edited as streaming deltas arrive (throttled
    to respect Telegram's edit rate limits), then finalized on ``send_result``.
  • Messages are sent with ``parse_mode=Markdown`` and transparently fall back
    to plain text when Telegram rejects malformed Markdown entities.
  • Implements the same control commands as the Slack/WeChat adapters (help,
    ping, status, tasks, history, task, cancel, reset, executors, use) for a
    consistent CLI-like experience.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.artifacts import upload_channel_artifacts
from fluxion.channels.base import SettingsHotReloader, authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.inbox import make_inbox_dir
from fluxion.channels.telegram.markdown import markdown_to_telegram_html
from fluxion.channels.telegram.pending_store import PendingUserStore
from fluxion.channels.telegram.telegram_client import TelegramAPIError, TelegramClient
from fluxion.config.settings import Settings
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import resolve_locale
from fluxion.renderers.markdown_renderer import MarkdownRenderer, clip_text
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_POLL_RETRY_BACKOFF_SEC = 5
_MAX_POLL_RETRY_BACKOFF_SEC = 60
_LONG_POLL_TIMEOUT_SEC = 30
# Telegram allows 4096 chars/message; keep headroom for headers/escaping.
TELEGRAM_TEXT_SOFT_LIMIT = 3800
# Telegram rate-limits edits to the same message; coalesce streaming edits.
_EDIT_THROTTLE_SEC = 1.5
# Extensions sent as inline photos (sendPhoto); others go via sendDocument.
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@dataclass
class _TelegramStreamState:
    message_id: int
    answer: str = ""
    phase: str = "analyzing"
    last_rendered: str = ""
    last_edit_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class TelegramChannelAdapter:
    """Fluxion channel adapter bridging a Telegram bot via long polling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = MarkdownRenderer(soft_limit=TELEGRAM_TEXT_SOFT_LIMIT)
        self._gateway = None
        self._client = TelegramClient(settings.telegram_bot_token)
        self._offset: int | None = None
        self._pending_store = PendingUserStore(settings.data_dir)
        self._settings_reloader = SettingsHotReloader(channel="Telegram")

        # task_id → live message state
        self._stream_lock = threading.Lock()
        self._task_streams: dict[str, _TelegramStreamState] = {}

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, gateway: GatewayCore) -> None:
        self._gateway = gateway
        try:
            me = self._client.get_me()
            logger.info(
                "Telegram bot connected (id=%s, username=@%s)",
                me.get("id"),
                me.get("username"),
            )
        except Exception:
            logger.exception("Telegram getMe failed — check TELEGRAM_BOT_TOKEN")
            raise

        poll_thread = threading.Thread(
            target=self._poll_loop,
            name="fluxion-telegram-poll",
            daemon=True,
        )
        poll_thread.start()
        logger.info("Telegram adapter started")

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status not in self._settings.status_updates:
            return
        locale = self._locale_for_context(context)
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        self._upsert_live_message(task_id=task_id, context=context, text=text)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        locale = self._locale_for_context(context)
        state = self._pop_stream(task_id)

        if result.success and state is not None and state.answer.strip():
            final_text = clip_text(state.answer, TELEGRAM_TEXT_SOFT_LIMIT)
        else:
            final_text = self._renderer.render_result(task_id, result, locale=locale)

        if state is not None:
            with state.lock:
                finalized = self._edit_text(
                    chat_id=context["chat_id"],
                    message_id=state.message_id,
                    text=final_text,
                    attempts=3,
                )
            if not finalized:
                # The live message may be stuck showing a stale streamed
                # prefix; deliver the full answer as a fresh message.
                logger.warning(
                    "Telegram finalize edit failed for task %s; sending full answer as a new message",
                    task_id,
                )
                self._send_text(context=context, text=final_text)
        else:
            self._send_text(context=context, text=final_text)

        self._upload_artifacts(result=result, context=context)

    def send_typing(self, context: dict) -> None:
        chat_id = context.get("chat_id")
        if chat_id is None:
            return
        try:
            self._client.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            logger.debug("Telegram sendChatAction failed", exc_info=True)

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        chunk = text or ""
        if not chunk:
            return
        state = self._get_or_create_stream(task_id=task_id, context=context, initial_text="…")
        if state is None:
            return
        with state.lock:
            state.answer += chunk
            state.phase = "responding"
            now = time.time()
            if now - state.last_edit_at < _EDIT_THROTTLE_SEC:
                return  # coalesce; finalize (or a later delta) will flush
            rendered = clip_text(state.answer, TELEGRAM_TEXT_SOFT_LIMIT)
            if rendered == state.last_rendered:
                return
            if self._edit_text(
                chat_id=context["chat_id"],
                message_id=state.message_id,
                text=rendered,
            ):
                state.last_rendered = rendered
                state.last_edit_at = now

    # ------------------------------------------------------------------
    # Live message helpers
    # ------------------------------------------------------------------

    def _get_or_create_stream(
        self, *, task_id: str, context: dict, initial_text: str
    ) -> _TelegramStreamState | None:
        with self._stream_lock:
            existing = self._task_streams.get(task_id)
            if existing is not None:
                return existing

            message_id = self._send_text(context=context, text=initial_text)
            if message_id is None:
                return None
            state = _TelegramStreamState(message_id=message_id, last_rendered=initial_text)
            self._task_streams[task_id] = state
            return state

    def _upsert_live_message(self, *, task_id: str, context: dict, text: str) -> None:
        state = self._get_or_create_stream(task_id=task_id, context=context, initial_text=text)
        if state is None:
            return
        with state.lock:
            # Don't clobber streamed answer once the response has started.
            if state.phase == "responding" and state.answer:
                return
            if text == state.last_rendered:
                return
            if self._edit_text(
                chat_id=context["chat_id"],
                message_id=state.message_id,
                text=text,
            ):
                state.last_rendered = text

    def _pop_stream(self, task_id: str) -> _TelegramStreamState | None:
        with self._stream_lock:
            return self._task_streams.pop(task_id, None)

    # ------------------------------------------------------------------
    # Send / edit: render Markdown -> Telegram HTML, fall back to plain text
    # ------------------------------------------------------------------

    def _send_text(self, *, context: dict, text: str) -> int | None:
        chat_id = context.get("chat_id")
        if chat_id is None:
            logger.warning("Cannot send Telegram message: no chat_id in context")
            return None
        reply_to = context.get("reply_to")
        clipped = clip_text(text, TELEGRAM_TEXT_SOFT_LIMIT)
        html_text = markdown_to_telegram_html(clipped)
        for attempt in range(1, 4):
            try:
                resp = self._client.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    parse_mode="HTML",
                    reply_to_message_id=reply_to,
                )
                return int(resp.get("message_id"))
            except TelegramAPIError as exc:
                if exc.is_parse_error:
                    # Converted HTML was rejected — send the raw text plainly.
                    try:
                        resp = self._client.send_message(
                            chat_id=chat_id,
                            text=clipped,
                            reply_to_message_id=reply_to,
                        )
                        return int(resp.get("message_id"))
                    except Exception:
                        logger.exception("Telegram sendMessage (plain) failed")
                        return None
                logger.warning("Telegram sendMessage failed (attempt %s): %s", attempt, exc)
                time.sleep(0.5 * attempt)
            except Exception:
                logger.exception("Telegram sendMessage failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)
        return None

    def _edit_text(
        self, *, chat_id: int | str, message_id: int, text: str, attempts: int = 1
    ) -> bool:
        # Edits are idempotent ("not modified" counts as success), so retrying
        # transient failures is safe — unlike sendMessage, no duplicate risk.
        clipped = clip_text(text, TELEGRAM_TEXT_SOFT_LIMIT)
        html_text = markdown_to_telegram_html(clipped)
        use_plain = False
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                time.sleep(0.5 * (attempt - 1))
            try:
                if use_plain:
                    self._client.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=clipped
                    )
                else:
                    self._client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=html_text,
                        parse_mode="HTML",
                    )
                return True
            except TelegramAPIError as exc:
                if "not modified" in exc.description.lower():
                    return True
                if exc.is_parse_error and not use_plain:
                    # Converted HTML was rejected — switch to the raw text
                    # plainly for this and any remaining attempts.
                    use_plain = True
                    try:
                        self._client.edit_message_text(
                            chat_id=chat_id, message_id=message_id, text=clipped
                        )
                        return True
                    except TelegramAPIError as plain_exc:
                        if "not modified" in plain_exc.description.lower():
                            return True
                        logger.debug("Telegram editMessageText (plain) failed: %s", plain_exc)
                    except Exception:
                        logger.debug("Telegram editMessageText (plain) failed", exc_info=True)
                else:
                    logger.debug(
                        "Telegram editMessageText failed (attempt %s/%s): %s",
                        attempt,
                        attempts,
                        exc,
                    )
            except Exception:
                logger.debug(
                    "Telegram editMessageText failed (attempt %s/%s)",
                    attempt,
                    attempts,
                    exc_info=True,
                )
        return False

    def _upload_artifacts(self, *, result: ExecutionResult, context: dict) -> None:
        upload_channel_artifacts(
            result=result,
            context=context,
            max_files=getattr(self._settings, "artifact_max_files", 8),
            upload_log_on_success=getattr(self._settings, "upload_log_on_success", False),
            upload_one=lambda path: self._upload_file(path=path, context=context),
        )

    def _upload_file(self, *, path: Path, context: dict) -> None:
        chat_id = context.get("chat_id")
        if chat_id is None:
            return
        reply_to = context.get("reply_to")
        # Images go out as inline photos; fall back to a document if that fails
        # (e.g. too large or wrong dimensions for sendPhoto).
        if path.suffix.lower() in _IMAGE_EXTS:
            try:
                self._client.send_photo(
                    chat_id=chat_id,
                    path=path,
                    caption=path.name,
                    reply_to_message_id=reply_to,
                )
                return
            except Exception:
                logger.debug("Telegram sendPhoto failed, falling back to document: %s", path)
        for attempt in range(1, 4):
            try:
                self._client.send_document(
                    chat_id=chat_id,
                    path=path,
                    caption=path.name,
                    reply_to_message_id=reply_to,
                )
                return
            except Exception:
                logger.exception("Telegram sendDocument failed on attempt %s: %s", attempt, path)
                time.sleep(0.5 * attempt)

    # ------------------------------------------------------------------
    # Long-poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        backoff = _POLL_RETRY_BACKOFF_SEC
        while True:
            try:
                updates = self._client.get_updates(
                    offset=self._offset,
                    timeout=_LONG_POLL_TIMEOUT_SEC,
                    allowed_updates=["message"],
                )
                backoff = _POLL_RETRY_BACKOFF_SEC  # reset on success
                for update in updates:
                    self._offset = int(update["update_id"]) + 1
                    try:
                        self._handle_update(update)
                    except Exception:
                        logger.exception(
                            "Error handling Telegram update %s", update.get("update_id")
                        )
            except Exception:
                logger.exception("Telegram getUpdates failed, retrying in %ss", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_POLL_RETRY_BACKOFF_SEC)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        # Text lives in `text`; for photos/documents the instruction is in `caption`.
        text = str(message.get("text") or message.get("caption") or "").strip()
        attachments = self._collect_attachments(message)
        if not text and not attachments:
            return  # Nothing actionable (sticker, location, etc.)

        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = str(sender.get("id") or "")
        context = {
            "chat_id": chat_id,
            "user": user_id,
            "username": str(sender.get("username") or ""),
            "reply_to": message.get("message_id"),
            "channel_type": "telegram_dm",
            "source_text": text,
        }

        valid, reason = self._validate_inbound(chat=chat, user_id=user_id)
        if not valid:
            # Only allowlist rejections park the sender as a pending user —
            # e.g. a group-chat rejection says nothing about the person.
            if "allowlist" in reason.lower():
                self._pending_store.record(
                    user_id,
                    text or "[attachment]",
                    notify_locale=self._locale_for_context({"source_text": ""}),
                )
            self._send_text(
                context=context,
                text=self._format_rejection(
                    reason, user_id, locale=self._locale_for_context(context)
                ),
            )
            return
        self._pending_store.remove(user_id)

        # Control commands are text-only; a message carrying files is always a task.
        if not attachments and self._handle_control_command(
            text=text, context=context, user_id=user_id
        ):
            return

        workspace_override, task_text = self._extract_workspace(text)
        if not task_text and not attachments:
            self._send_text(
                context=context,
                text="Please send a non-empty task description.",
            )
            return

        if self._gateway is None:
            self._send_text(context=context, text="Gateway not ready.")
            return

        try:
            tg_ws = self._settings.telegram_default_workspace
            raw_ws = workspace_override or (tg_ws if tg_ws else self._default_workspace())
            workspace = self._settings.resolve_workspace(raw_ws)
        except ValueError as exc:
            self._send_text(context=context, text=f"Rejected: {exc}")
            return

        if attachments:
            saved = self._download_attachments(
                attachments=attachments, workspace=workspace, context=context
            )
            if saved:
                rel = [str(p.relative_to(workspace)) for p in saved]
                note = "Attached file(s) saved in the workspace:\n" + "\n".join(
                    f"- {r}" for r in rel
                )
                task_text = (
                    f"{task_text}\n\n{note}".strip()
                    if task_text
                    else (
                        "The user sent the following file(s) without a text instruction. "
                        "Inspect them and respond appropriately.\n\n" + note
                    )
                )

        convo_key = self._conversation_key(chat_id)
        override = self._gateway._sessions.get_executor_override(
            conversation_key=convo_key,
            channel="telegram",
            user_id=user_id,
        )
        executor_to_use = override if override else self._settings.default_executor

        task = Task.create(
            channel="telegram",
            user_id=user_id,
            text=task_text,
            workspace=workspace,
            metadata={
                "executor": executor_to_use,
                "conversation_key": convo_key,
            },
        )
        submit_context = {**context, "workspace": str(workspace)}
        ok, info = self._gateway.submit_task(
            task=task,
            channel_adapter=self,
            channel_context=submit_context,
        )
        if not ok:
            self._send_text(context=context, text=f"Rejected: {info}")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inbound(self, *, chat: dict[str, Any], user_id: str) -> tuple[bool, str]:
        if str(chat.get("type") or "") != "private":
            return False, "only direct messages are allowed."
        reloader = getattr(self, "_settings_reloader", None)
        if reloader is not None:
            self._settings = reloader.reload_if_changed(self._settings)
        return authorize_inbound(
            channel="telegram",
            user_id=user_id,
            allowed_users=self._settings.telegram_allowed_users,
        )

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="Telegram user ID",
            env_key="FLUXION_TELEGRAM_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> Telegram -> Pending Users",
        )

    # ------------------------------------------------------------------
    # Control commands
    # ------------------------------------------------------------------

    def _handle_control_command(self, *, text: str, context: dict, user_id: str) -> bool:
        if self._gateway is None:
            return False
        convo_key = self._conversation_key(context.get("chat_id"))
        response = self._gateway.handle_control_command(
            text=text,
            user_id=user_id,
            convo_key=convo_key,
            channel="telegram",
        )
        if response is not None:
            self._send_text(
                context=context,
                text=format_control_response(response, channel="telegram"),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_workspace(self) -> str | None:
        resolver = getattr(self._settings, "default_channel_workspace", None)
        if callable(resolver):
            return resolver()
        allowed = getattr(self._settings, "allowed_workspaces", [])
        if allowed:
            return str(allowed[0])
        return None

    def _collect_attachments(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract downloadable attachments (largest photo + document) from a message."""
        attachments: list[dict[str, Any]] = []
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            # `photo` is an array of sizes; the last entry is the highest resolution.
            largest = photos[-1]
            if isinstance(largest, dict) and largest.get("file_id"):
                attachments.append({"file_id": largest["file_id"], "name": None})
        doc = message.get("document")
        if isinstance(doc, dict) and doc.get("file_id"):
            attachments.append({"file_id": doc["file_id"], "name": doc.get("file_name")})
        return attachments

    def _download_attachments(
        self, *, attachments: list[dict[str, Any]], workspace: Path, context: dict
    ) -> list[Path]:
        attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
        saved: list[Path] = []
        for idx, att in enumerate(attachments):
            file_id = str(att.get("file_id") or "")
            if not file_id:
                continue
            try:
                info = self._client.get_file(file_id=file_id)
                file_path = str(info.get("file_path") or "")
                if not file_path:
                    continue
                name = att.get("name") or Path(file_path).name or f"file_{idx}"
                dest = attach_dir / self._safe_filename(str(name))
                self._client.download_file(file_path=file_path, dest=dest)
                saved.append(dest)
            except Exception:
                logger.exception("Failed to download Telegram attachment %s", file_id)
                self._send_text(
                    context=context,
                    text="Failed to download attachment (max 20MB).",
                )
        return saved

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Keep only the basename and strip anything path- or shell-unfriendly.
        base = Path(name).name.strip() or "file"
        cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
        return cleaned or "file"

    def _conversation_key(self, chat_id: Any) -> str:
        return f"telegram:{chat_id}:{chat_id}"

    def _extract_workspace(self, text: str) -> tuple[str | None, str]:
        match = re.match(r"^(workspace=|/workspace\s+)(\S+)\s*(.*)$", text.strip())
        if not match:
            return None, text.strip()
        workspace = match.group(2).strip()
        remaining = match.group(3).strip()
        return workspace, remaining

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=self._settings.locale_mode,
            fixed_locale=self._settings.ui_locale,
            context=context,
        )


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
