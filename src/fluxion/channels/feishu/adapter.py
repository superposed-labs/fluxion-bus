"""Feishu (Lark) channel adapter for Fluxion.

Bridges a Feishu self-built app to the Fluxion gateway over a **long connection**
(WebSocket) using the official ``lark-oapi`` SDK. The long connection dials *out*
to Feishu, so — like the QQ WebSocket transport — it needs no public address,
tunnel, or callback-signature handshake; the SDK authenticates with the app
credentials and manages the tenant access token, event dispatch, and decryption.

Scope:

* Single chat (``chat_type == "p2p"``) and group @-mentions (``"group"``).
* Text in; the answer streams into an updatable markdown **card**: when work
  starts we post a "thinking…" card, patch it as output arrives (throttled), and
  finalize it with the result — a single bubble that mirrors the official
  ``lark-samples`` AI-bot pattern and substitutes for a typing indicator.
* Sends go out actively to the originating ``chat_id`` (works for both single
  chats and groups), so there is no passive-reply time window to manage.
* Images/files in and out: inbound image/file messages are downloaded into the
  task workspace inbox so the agent can read them; result artifacts are sent back
  as native image (for image extensions) or file messages.

Not yet handled (intentionally): reply threading and a webhook transport. The SDK
is isolated behind this adapter so those can be added later without touching the
gateway wiring.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.artifacts import upload_channel_artifacts
from fluxion.channels.attachments import (
    AttachmentNormalizationError,
    DownloadedFile,
    normalize_downloaded_files,
)
from fluxion.channels.base import SettingsHotReloader, authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.feishu.content import (
    build_markdown_card,
    parse_inbound_resource,
    parse_inbound_text,
)
from fluxion.channels.feishu.feishu_client import FeishuAPIError, FeishuClient
from fluxion.channels.feishu.pending_store import PendingUserStore
from fluxion.channels.inbox import make_inbox_dir
from fluxion.config.settings import Settings
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import resolve_locale

# The answer goes into a Feishu markdown card, so — unlike LINE's flatten-to-plain
# renderer — we keep the executor's markdown intact (bold/lists/code) via the
# shared MarkdownRenderer: raw markdown for success, a localized header for
# failure. clip_text (Slack-sized) bounds the final card text.
from fluxion.renderers.markdown_renderer import MarkdownRenderer
from fluxion.slack_limits import clip_text
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum gap between streaming card patches. Feishu rate-limits message.patch,
# so deltas arriving faster than this are coalesced (the next due delta carries
# the full accumulated text); send_result always does a final un-throttled patch.
_STREAM_PATCH_MIN_INTERVAL_SEC = 0.7


class FeishuChannelAdapter:
    """Fluxion channel adapter bridging a Feishu app via the long connection."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = MarkdownRenderer()
        self._gateway = None
        self._client = FeishuClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        self._pending_store = PendingUserStore(settings.data_dir)
        self._settings_reloader = SettingsHotReloader(channel="Feishu")

        self._lock = threading.Lock()
        # Per-task originating chat_id so terminal/status sends reach the right
        # conversation, and streaming output accumulated until the result is sent.
        self._chat_ids: dict[str, str] = {}
        self._stream_buffers: dict[str, str] = {}
        # Per-task monotonic timestamp of the last streaming card patch (throttle).
        self._stream_last_patch: dict[str, float] = {}
        # Per-task id of a "thinking…" placeholder message. Feishu has no native
        # typing indicator, so we post one bubble when work starts and patch it in
        # place with the answer — a single bubble that never flickers or spams.
        self._thinking_msg_ids: dict[str, str] = {}

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, gateway: GatewayCore) -> None:
        self._gateway = gateway
        thread = threading.Thread(
            target=self._run_long_connection, name="fluxion-feishu-ws", daemon=True
        )
        thread.start()
        logger.info("Feishu channel adapter started (long connection)")

    def _run_long_connection(self) -> None:
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message)
            .build()
        )
        ws_client = lark.ws.Client(
            self._settings.feishu_app_id,
            self._settings.feishu_app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        # Blocking: the SDK runs its own asyncio loop and reconnects internally.
        ws_client.start()

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status == "RUNNING":
            # Stand in for a typing indicator: post a "thinking…" placeholder once
            # (the repeated RUNNING heartbeats are de-duplicated below) that
            # send_result will patch in place with the answer.
            self._ensure_thinking_placeholder(task_id, context)
            return
        if status not in self._settings.status_updates:
            return
        locale = self._locale_for_context(context)
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        chat_id = self._chat_id_for(task_id, context)
        if chat_id:
            self._dispatch(chat_id, text)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        buffered = self._pop_stream_buffer(task_id)
        locale = self._locale_for_context(context)

        if buffered and result.success:
            combined = buffered
        else:
            rendered = self._renderer.render_result(task_id, result, locale=locale)
            combined = buffered + "\n\n" + rendered if buffered else rendered

        # Keep markdown intact — the card renders it (bold/lists/code).
        text = clip_text(combined.strip()) or "…"
        chat_id = self._chat_id_for(task_id, context)
        with self._lock:
            thinking_id = self._thinking_msg_ids.pop(task_id, None)
            self._stream_last_patch.pop(task_id, None)
            self._chat_ids.pop(task_id, None)

        card = build_markdown_card(text)
        # Finalize the streamed card in place so the answer lands in one bubble.
        patched = False
        if thinking_id:
            try:
                self._client.patch_card(thinking_id, card)
                patched = True
            except FeishuAPIError:
                logger.warning(
                    "Feishu final patch failed for task %s; sending answer as a new card", task_id
                )
        if not patched:
            if chat_id:
                try:
                    self._client.send_card(chat_id, card)
                except FeishuAPIError:
                    logger.exception("Failed to send Feishu result card for task %s", task_id)
            else:
                logger.error("Feishu result dropped: no chat_id for task %s", task_id)

        # Send any result artifacts (generated images/files) as follow-up messages.
        if chat_id:
            self._upload_artifacts(result=result, context={**context, "feishu_chat_id": chat_id})

    def send_typing(self, context: dict) -> None:
        # Feishu exposes no typing indicator for bots; the streamed card stands in.
        return

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        chunk = text or ""
        if not chunk:
            return
        with self._lock:
            buffer = self._stream_buffers.get(task_id, "") + chunk
            self._stream_buffers[task_id] = buffer
            thinking_id = self._thinking_msg_ids.get(task_id)
            now = time.monotonic()
            due = bool(thinking_id) and (
                now - self._stream_last_patch.get(task_id, 0.0) >= _STREAM_PATCH_MIN_INTERVAL_SEC
            )
            if due:
                self._stream_last_patch[task_id] = now

        # Live-stream into the placeholder card, throttled to respect rate limits.
        if due and thinking_id:
            try:
                self._client.patch_card(thinking_id, build_markdown_card(clip_text(buffer.strip())))
            except FeishuAPIError:
                logger.debug("Feishu stream patch failed for task %s", task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _chat_id_for(self, task_id: str, context: dict) -> str | None:
        return context.get("feishu_chat_id") or self._chat_ids.get(task_id)

    def _dispatch(self, chat_id: str, text: str) -> None:
        # Strip trailing whitespace so executor output ending in a newline does
        # not render as a dangling blank line in the message bubble.
        try:
            self._client.send_text(chat_id, clip_text(text.strip()))
        except FeishuAPIError:
            logger.exception("Failed to send Feishu message")

    def _ensure_thinking_placeholder(self, task_id: str, context: dict) -> None:
        with self._lock:
            if task_id in self._thinking_msg_ids:
                return
            # Reserve the slot before the network call so a racing RUNNING
            # heartbeat does not post a second placeholder.
            self._thinking_msg_ids[task_id] = ""
        chat_id = self._chat_id_for(task_id, context)
        if not chat_id:
            with self._lock:
                self._thinking_msg_ids.pop(task_id, None)
            return
        locale = self._locale_for_context(context)
        placeholder = self._renderer.render_status(task_id, "RUNNING", locale=locale)
        try:
            # A card (not plain text) so streaming deltas can patch it in place.
            msg_id = self._client.send_card(chat_id, build_markdown_card(placeholder.strip()))
        except FeishuAPIError:
            logger.exception("Feishu thinking placeholder failed for task %s", task_id)
            msg_id = None
        with self._lock:
            if msg_id:
                self._thinking_msg_ids[task_id] = msg_id
            else:
                self._thinking_msg_ids.pop(task_id, None)

    def _reply_now(self, chat_id: str, text: str) -> None:
        """Send a one-off message outside of a task (errors, denials, commands)."""
        content = clip_text(text.strip())
        try:
            self._client.send_text(chat_id, content)
        except FeishuAPIError:
            logger.exception("Failed to send Feishu reply")

    # Extensions sent as native image messages; everything else goes as a file.
    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    def _upload_artifacts(self, *, result: ExecutionResult, context: dict) -> None:
        upload_channel_artifacts(
            result=result,
            context=context,
            max_files=getattr(self._settings, "artifact_max_files", 8),
            upload_log_on_success=getattr(self._settings, "upload_log_on_success", False),
            upload_one=lambda path: self._upload_file(path=path, context=context),
        )

    def _upload_file(self, *, path: Path, context: dict) -> None:
        chat_id = context.get("feishu_chat_id")
        if not chat_id:
            return
        try:
            if path.suffix.lower() in self._IMAGE_EXTS:
                image_key = self._client.upload_image(path)
                self._client.send_image(chat_id, image_key)
            else:
                file_key = self._client.upload_file(path)
                self._client.send_file(chat_id, file_key)
        except FeishuAPIError:
            logger.exception("Failed to send Feishu artifact %s", path.name)

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        try:
            self._handle_message_inner(data)
        except Exception:  # noqa: BLE001 - one bad event must not kill the connection
            logger.exception("Feishu event handler failed")

    def _handle_message_inner(self, data: P2ImMessageReceiveV1) -> None:
        event = getattr(data, "event", None)
        if event is None:
            return
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            return

        sender_id_obj = getattr(sender, "sender_id", None)
        open_id = getattr(sender_id_obj, "open_id", None) if sender_id_obj else None
        message_id = getattr(message, "message_id", None)
        chat_id = getattr(message, "chat_id", None)
        chat_type = getattr(message, "chat_type", None) or "p2p"
        message_type = getattr(message, "message_type", None)

        is_resource = message_type in ("image", "file")
        if message_type != "text" and not is_resource:
            # Handle text + image/file; ignore stickers, audio, rich posts, etc.
            return
        if not open_id or not chat_id or not message_id:
            return

        is_group = chat_type == "group"
        if is_group and not self._settings.feishu_allow_group_chat:
            return

        content_raw = getattr(message, "content", "") or ""
        if is_resource:
            # Image/file messages carry no caption; the agent gets a synthesized
            # instruction once the resource is downloaded into the workspace.
            text = ""
            resource = parse_inbound_resource(content_raw, message_type)
            if resource is None:
                return
        else:
            text = parse_inbound_text(content_raw, getattr(message, "mentions", None))
            resource = None

        # Fail-closed whitelist check (empty allowlist denies everyone).
        reloader = getattr(self, "_settings_reloader", None)
        if reloader is not None:
            self._settings = reloader.reload_if_changed(self._settings)
        allowed, reason = authorize_inbound(
            channel="feishu",
            user_id=open_id,
            allowed_users=self._settings.feishu_allowed_users,
        )
        if not allowed:
            self._pending_store.record(
                open_id,
                text or "[attachment]",
                notify_locale=self._locale_for_context({"source_text": ""}),
            )
            locale = self._locale_for_context({"source_text": text})
            self._reply_now(chat_id, self._format_rejection(reason, open_id, locale=locale))
            return
        self._pending_store.remove(open_id)

        logger.info("Feishu inbound (%s) from %s: %s", chat_type, open_id, text or "(empty)")

        channel_type = "feishu_group" if is_group else "feishu_p2p"
        convo_key = f"feishu:{chat_type}:{chat_id}"

        if text and self._handle_command(text, open_id, convo_key, chat_id):
            return

        try:
            raw_ws = self._settings.feishu_default_workspace or self._default_workspace()
            workspace = self._settings.resolve_workspace(raw_ws)
        except ValueError as exc:
            self._reply_now(chat_id, f"Rejected: {exc}")
            return

        task_text = text
        if resource is not None:
            downloaded = self._download_resource(
                message_id=message_id, resource=resource, workspace=workspace, chat_id=chat_id
            )
            if not downloaded:
                return
            downloaded_files = [downloaded]
        else:
            downloaded_files = []
        try:
            task_attachments, task_images = normalize_downloaded_files(downloaded_files)
        except AttachmentNormalizationError as exc:
            self._reply_now(chat_id, f"Rejected attachment: {exc}")
            return

        override = self._gateway._sessions.get_executor_override(
            conversation_key=convo_key,
            channel="feishu",
            user_id=open_id,
        )
        executor_to_use = override if override else self._settings.default_executor

        task = Task.create(
            channel="feishu",
            user_id=open_id,
            text=task_text,
            workspace=workspace,
            metadata={
                "executor": executor_to_use,
                "conversation_key": convo_key,
            },
            attachments=task_attachments,
            image_attachments=task_images,
        )

        with self._lock:
            self._chat_ids[task.id] = chat_id

        channel_context = {
            "user": open_id,
            "channel_type": channel_type,
            "feishu_chat_id": chat_id,
            # Carry the workspace so result-artifact selection (send_result ->
            # _upload_artifacts) can fall back to changed files under it.
            "workspace": str(workspace),
            # Carry the inbound text so auto locale detection (the "thinking…"
            # placeholder, status, and failure wrappers) follows the question's
            # language instead of always falling back to English.
            "source_text": text,
        }

        if self._gateway is None:
            self._discard_task(task.id)
            self._reply_now(chat_id, "Error: Gateway not ready.")
            return

        ok, info = self._gateway.submit_task(
            task=task,
            channel_adapter=self,
            channel_context=channel_context,
        )
        if not ok:
            self._discard_task(task.id)
            self._reply_now(chat_id, f"Rejected: {info}")

    def _download_resource(
        self, *, message_id: str, resource: dict, workspace: Path, chat_id: str
    ) -> DownloadedFile | None:
        """Download an inbound image/file into the workspace inbox; return its path."""
        attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
        dest = attach_dir / self._safe_filename(str(resource["file_name"]))
        try:
            self._client.download_message_resource(
                message_id=message_id,
                file_key=str(resource["file_key"]),
                resource_type=str(resource["resource_type"]),
                dest=dest,
            )
            return DownloadedFile(
                path=dest,
                media_type="image/png" if resource["resource_type"] == "image" else "",
            )
        except AttachmentNormalizationError as exc:
            self._reply_now(chat_id, f"Rejected attachment: {exc}")
            return None
        except FeishuAPIError as exc:
            logger.exception("Failed to download Feishu resource for message %s", message_id)
            # 99991672 = "access denied": the app lacks the scope to read message
            # resources. Surface the fix instead of a generic failure, since text
            # works without this scope so the cause is non-obvious.
            if exc.code == 99991672:
                self._reply_now(
                    chat_id,
                    "Can't read the attachment: the Feishu app is missing a "
                    "message-read permission. In the developer console grant one of "
                    "im:message / im:message:readonly / im:message.history:readonly, "
                    "publish a new version, then try again.",
                )
            else:
                self._reply_now(chat_id, "Failed to download the attachment.")
            return None

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Keep only the basename and strip anything path- or shell-unfriendly.
        base = Path(name).name.strip() or "file"
        cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
        return cleaned or "file"

    def _handle_command(self, text: str, user_id: str, convo_key: str, chat_id: str) -> bool:
        if self._gateway is None:
            return False
        response = self._gateway.handle_control_command(
            text=text,
            user_id=user_id,
            convo_key=convo_key,
            channel="feishu",
        )
        if response is not None:
            self._reply_now(chat_id, format_control_response(response, channel="feishu"))
            return True
        return False

    def _default_workspace(self) -> str | None:
        resolver = getattr(self._settings, "default_channel_workspace", None)
        if callable(resolver):
            return resolver()
        allowed = getattr(self._settings, "allowed_workspaces", [])
        if allowed:
            return str(allowed[0])
        return None

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="Feishu open_id",
            env_key="FLUXION_FEISHU_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> Feishu -> Pending Users",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _discard_task(self, task_id: str) -> None:
        with self._lock:
            self._chat_ids.pop(task_id, None)
            self._stream_buffers.pop(task_id, None)
            self._stream_last_patch.pop(task_id, None)
            self._thinking_msg_ids.pop(task_id, None)

    def _pop_stream_buffer(self, task_id: str) -> str:
        with self._lock:
            return self._stream_buffers.pop(task_id, "")

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=getattr(self._settings, "locale_mode", "auto"),
            fixed_locale=getattr(self._settings, "ui_locale", "en"),
            context=context,
        )


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
