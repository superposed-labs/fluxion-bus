"""LINE Channel adapter for Fluxion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.attachments import (
    AttachmentNormalizationError,
    DownloadedFile,
    copy_stream_limited,
    normalize_downloaded_files,
)
from fluxion.channels.base import SettingsHotReloader, authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.inbox import make_inbox_dir
from fluxion.channels.line.pending_store import PendingUserStore
from fluxion.config.settings import Settings
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import normalize_locale, resolve_locale
from fluxion.renderers.line_renderer import LineRenderer, clip_text, markdown_to_plain_text
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)


class LineChannelAdapter:
    """Fluxion channel adapter bridging LINE Messaging API via webhooks and push messages."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = LineRenderer()
        self._gateway = None
        self._stream_lock = threading.Lock()
        self._stream_buffers: dict[str, str] = {}
        self._reply_tokens: dict[str, str] = {}
        self._pending_store = PendingUserStore(settings.data_dir)
        self._settings_reloader = SettingsHotReloader(channel="LINE")

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, gateway: GatewayCore) -> None:
        self._gateway = gateway

        # Start uvicorn webhook server in a background daemon thread
        server_thread = threading.Thread(
            target=self._run_server,
            name="fluxion-line-webhook",
            daemon=True,
        )
        server_thread.start()
        logger.info("LINE Channel adapter server thread started")

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status not in self._settings.status_updates:
            return
        if status == "RUNNING":
            # LINE does not support editing/updating messages or typing indicators,
            # so we ignore RUNNING to avoid sending redundant, permanent status bubbles.
            return
        locale = self._locale_for_context(context)
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        full_text = text

        # If a reply token is still associated with this task, try to use it
        # for terminal status notifications (e.g. CANCELED, FAILED).
        reply_token = self._reply_tokens.pop(task_id, None)
        if reply_token:
            success = self._reply_line(reply_token, full_text)
            if success:
                return
        self._send_text(context=context, text=full_text)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        buffered = self._pop_stream_buffer(task_id)
        locale = self._locale_for_context(context)

        if buffered and result.success:
            combined = buffered
        else:
            rendered = self._renderer.render_result(task_id, result, locale=locale)
            if buffered:
                combined = buffered + "\n\n" + rendered
            else:
                combined = rendered

        # Convert any markdown to clean plain text format for LINE
        combined_plain = clip_text(markdown_to_plain_text(combined))

        # Try to use the replyToken first to reply for free and directly
        reply_token = self._reply_tokens.pop(task_id, None)
        if reply_token:
            success = self._reply_line(reply_token, combined_plain)
            if success:
                return

        # Fall back to push message if reply failed or no reply token was found
        self._send_text(context=context, text=combined_plain)

    def send_typing(self, context: dict) -> None:
        user_id = context.get("user")
        if user_id:
            self._show_loading(user_id, seconds=20)

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        chunk = text or ""
        if not chunk:
            return
        with self._stream_lock:
            self._stream_buffers[task_id] = self._stream_buffers.get(task_id, "") + chunk

    # ------------------------------------------------------------------
    # Internals & Webhook Server
    # ------------------------------------------------------------------

    def _run_server(self) -> None:
        app = FastAPI()

        @app.get("/health")
        def health():
            return Response(content="ok", media_type="text/plain")

        @app.post("/line/webhook")
        async def line_webhook(request: Request, x_line_signature: str = Header(None)):
            body = await request.body()
            if not self._verify_signature(body, x_line_signature):
                logger.warning("Invalid LINE signature received")
                raise HTTPException(status_code=401, detail="invalid signature")

            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                raise HTTPException(status_code=400, detail="invalid json") from None

            events = payload.get("events") or []
            for event in events:
                # Handle follow (friend addition) events
                if event.get("type") == "follow":
                    source = event.get("source") or {}
                    user_id = source.get("userId")
                    reply_token = event.get("replyToken")
                    if user_id and reply_token:
                        user_lang = self._get_user_language(user_id)
                        logger.info(
                            "LINE user followed/added bot: %s, language: %s", user_id, user_lang
                        )

                        # Default fallback greeting (trilingual)
                        welcome_msg = (
                            f"Welcome! 友だち追加ありがとうございます！ 欢迎使用 Fluxion！\n\n"
                            f"当ボットを利用するには、管理者によるLINEユーザーIDのホワイトリスト登録が必要です。\n"
                            f"以下のユーザーIDを管理者に伝えて登録を依頼してください。\n\n"
                            f"To get started, please ask the administrator to whitelist your LINE User ID.\n"
                            f"要开始对话，请联系管理员将您的 LINE User ID 加入白名单。\n\n"
                            f"Your User ID / ユーザーID / 您的 ID:\n{user_id}"
                        )

                        if user_lang:
                            lang_lower = user_lang.lower()
                            if lang_lower.startswith("ja"):
                                welcome_msg = (
                                    f"友だち追加ありがとうございます！\n\n"
                                    f"当ボットを利用するには、管理者によるLINEユーザーIDのホワイトリスト登録が必要です。\n"
                                    f"以下のユーザーIDを管理者に伝えて登録を依頼してください。\n\n"
                                    f"あなたのユーザーID:\n{user_id}"
                                )
                            elif lang_lower.startswith("zh"):
                                welcome_msg = (
                                    f"欢迎使用 Fluxion！\n\n"
                                    f"要开始对话，请联系管理员将您的 LINE User ID 加入白名单。\n\n"
                                    f"您的 User ID:\n{user_id}"
                                )
                            elif lang_lower.startswith("en"):
                                welcome_msg = (
                                    f"Welcome to Fluxion!\n\n"
                                    f"To get started, please ask the administrator to whitelist your LINE User ID.\n\n"
                                    f"Your User ID:\n{user_id}"
                                )

                        self._reply_line(reply_token, welcome_msg)
                    continue

                if event.get("type") != "message":
                    continue
                message = event.get("message") or {}
                mtype = message.get("type")
                if mtype not in {"text", "image", "file"}:
                    continue

                source = event.get("source") or {}
                user_id = source.get("userId")
                reply_token = event.get("replyToken")
                message_id = message.get("id")

                if not user_id or not reply_token or not message_id:
                    continue

                # Fail-closed whitelist check (an empty allowlist denies everyone).
                reloader = getattr(self, "_settings_reloader", None)
                if reloader is not None:
                    self._settings = reloader.reload_if_changed(self._settings)
                allowed, _reason = authorize_inbound(
                    channel="line",
                    user_id=user_id,
                    allowed_users=self._settings.line_allowed_users,
                )
                if not allowed:
                    self._pending_store.record(
                        user_id,
                        str(message.get("text") or "") or "[attachment]",
                        notify_locale=self._locale_for_context({"source_text": ""}),
                    )
                    # Prefer the language from the sender's LINE profile; fall
                    # back to the message text / configured locale.
                    locale = normalize_locale(
                        self._get_user_language(user_id), default=""
                    ) or self._locale_for_context({"source_text": str(message.get("text") or "")})
                    self._reply_line(
                        reply_token, self._format_rejection(_reason, user_id, locale=locale)
                    )
                    continue
                self._pending_store.remove(user_id)

                text = message.get("text")
                logger.info(
                    "LINE inbound %s message: %s from %s", mtype, text or "(binary)", user_id
                )

                # Process local control commands (only for text messages)
                if mtype == "text" and text and self._handle_command(text, user_id, reply_token):
                    continue

                # 1. Store the reply token to be consumed when the task completes
                # We no longer reply immediately with a placeholder to avoid extra chat bubbles.

                try:
                    line_ws = self._settings.line_default_workspace
                    raw_ws = line_ws if line_ws else self._default_workspace()
                    workspace = self._settings.resolve_workspace(raw_ws)
                except ValueError as exc:
                    self._reply_line(reply_token, f"Rejected: {exc}")
                    continue

                task_text = text or ""
                downloaded: list[DownloadedFile] = []
                if mtype in {"image", "file"}:
                    # Show loading animation early since downloading takes a moment
                    self._show_loading(user_id, seconds=30)

                    attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
                    try:
                        saved_attachment = self._download_attachment(
                            message_id,
                            attach_dir,
                            filename=str(message.get("fileName") or "") if mtype == "file" else "",
                        )
                    except AttachmentNormalizationError as exc:
                        self._reply_line(reply_token, f"Rejected attachment: {exc}")
                        continue
                    if saved_attachment:
                        downloaded.append(saved_attachment)
                    else:
                        self._reply_line(reply_token, "Error: Failed to download the attachment.")
                        continue
                try:
                    task_attachments, task_images = normalize_downloaded_files(downloaded)
                except AttachmentNormalizationError as exc:
                    self._reply_line(reply_token, f"Rejected attachment: {exc}")
                    continue

                convo_key = f"line:{user_id}:{user_id}"
                override = self._gateway._sessions.get_executor_override(
                    conversation_key=convo_key,
                    channel="line",
                    user_id=user_id,
                )
                executor_to_use = override if override else self._settings.default_executor

                task = Task.create(
                    channel="line",
                    user_id=user_id,
                    text=task_text,
                    workspace=workspace,
                    metadata={
                        "executor": executor_to_use,
                        "conversation_key": convo_key,
                    },
                    attachments=task_attachments,
                    image_attachments=task_images,
                )

                # Store the reply token to be consumed when the task completes
                self._reply_tokens[task.id] = reply_token

                # Show loading animation immediately to inform user the agent is working
                self._show_loading(user_id, seconds=30)

                channel_context = {
                    "user": user_id,
                    "channel_type": "line_dm",
                    "source_text": task_text,
                }

                if self._gateway is None:
                    self._reply_tokens.pop(task.id, None)
                    self._reply_line(reply_token, "Error: Gateway not ready.")
                    continue

                ok, info = self._gateway.submit_task(
                    task=task,
                    channel_adapter=self,
                    channel_context=channel_context,
                )
                if not ok:
                    self._reply_tokens.pop(task.id, None)
                    self._reply_line(reply_token, f"Rejected: {info}")

            return Response(content="ok", media_type="text/plain")

        # Start uvicorn
        port = int(os.environ.get("PORT", 8766))
        logger.info("Starting background FastAPI server for LINE on port %s", port)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    def _handle_command(self, text: str, user_id: str, reply_token: str) -> bool:
        if self._gateway is None:
            return False
        convo_key = f"line:{user_id}:{user_id}"
        response = self._gateway.handle_control_command(
            text=text,
            user_id=user_id,
            convo_key=convo_key,
            channel="line",
        )
        if response is not None:
            self._reply_line(reply_token, format_control_response(response, channel="line"))
            return True
        return False

    def _verify_signature(self, body: bytes, signature: str | None) -> bool:
        if not signature or not self._settings.line_channel_secret:
            return False
        hash_val = hmac.new(
            self._settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256
        ).digest()
        expected = base64.b64encode(hash_val).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def _reply_line(self, reply_token: str, text: str) -> bool:
        return self._call_api(
            "https://api.line.me/v2/bot/message/reply",
            {"replyToken": reply_token, "messages": [{"type": "text", "text": clip_text(text)}]},
        )

    def _push_line(self, user_id: str, text: str) -> bool:
        return self._call_api(
            "https://api.line.me/v2/bot/message/push",
            {"to": user_id, "messages": [{"type": "text", "text": clip_text(text)}]},
        )

    def _send_text(self, context: dict, text: str) -> bool:
        user_id = context.get("user")
        if user_id:
            return self._push_line(user_id, text)
        return False

    def _show_loading(self, user_id: str, seconds: int = 20) -> bool:
        return self._call_api(
            "https://api.line.me/v2/bot/chat/loading/start",
            {"chatId": user_id, "loadingSeconds": seconds},
        )

    def _get_user_language(self, user_id: str) -> str | None:
        """Call LINE Get Profile API to retrieve user's language setting (e.g. 'ja', 'en', 'zh-TW')."""
        if not self._settings.line_channel_access_token:
            return None
        url = f"https://api.line.me/v2/bot/profile/{user_id}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._settings.line_channel_access_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("language")
        except Exception:
            logger.exception("Failed to get LINE user profile for language detection")
            return None

    def _download_attachment(
        self,
        message_id: str,
        dest_dir: Path,
        *,
        filename: str = "",
    ) -> DownloadedFile | None:
        """Download binary content of a LINE image/file message."""
        if not self._settings.line_channel_access_token:
            logger.error("LINE token not configured for image download")
            return None
        url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._settings.line_channel_access_token}"},
            method="GET",
        )
        dest_path: Path | None = None
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(req, timeout=30) as resp:
                content_type = resp.headers.get("Content-Type") or ""
                ext = ".png"
                if "jpeg" in content_type or "jpg" in content_type:
                    ext = ".jpg"
                elif "gif" in content_type:
                    ext = ".gif"

                safe_name = Path(filename).name.strip()
                if not safe_name:
                    safe_name = f"line_attachment_{message_id}{ext}"
                dest_path = dest_dir / safe_name

                with open(dest_path, "wb") as f:
                    copy_stream_limited(resp, f)

                logger.info("Successfully downloaded LINE image to %s", dest_path)
                return DownloadedFile(path=dest_path, media_type=content_type)
        except AttachmentNormalizationError:
            if dest_path is not None:
                dest_path.unlink(missing_ok=True)
            raise
        except Exception:
            if dest_path is not None:
                dest_path.unlink(missing_ok=True)
            logger.exception("Failed to download LINE message content: %s", message_id)
            return None

    def _default_workspace(self) -> str | None:
        resolver = getattr(self._settings, "default_channel_workspace", None)
        if callable(resolver):
            return resolver()
        allowed = getattr(self._settings, "allowed_workspaces", [])
        if allowed:
            return str(allowed[0])
        return None

    def _call_api(self, url: str, payload: dict[str, Any]) -> bool:
        if not self._settings.line_channel_access_token:
            logger.error("LINE token not configured")
            return False
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._settings.line_channel_access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8")
            logger.error("LINE API call failed: %s %s", exc.code, err_body)
            return False
        except Exception:
            logger.exception("LINE API call encountered unexpected error")
            return False

    def _pop_stream_buffer(self, task_id: str) -> str:
        with self._stream_lock:
            return self._stream_buffers.pop(task_id, "")

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=self._settings.locale_mode,
            fixed_locale=self._settings.ui_locale,
            context=context,
        )

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="LINE user ID",
            env_key="FLUXION_LINE_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> LINE -> Pending Users",
        )


# Avoid circular import at runtime
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
