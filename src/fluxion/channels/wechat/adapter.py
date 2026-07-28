"""WeChat iLink channel adapter.

Implements the ``ChannelAdapter`` protocol by polling the iLink ``getupdates``
endpoint for inbound messages and sending replies through ``sendmessage``.

Key design decisions:
  • Runs a dedicated daemon thread for long-poll message retrieval.
  • Buffers streaming output (``send_output_delta``) because WeChat has no
    streaming / append API — the final answer is sent once in ``send_result``.
  • Implements the same control commands as the Slack adapter (help, ping,
    status, tasks, use, reset, cancel) so users have a consistent CLI-like
    experience.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from fluxion.channels.allowlist_messages import format_allowlist_rejection
from fluxion.channels.artifacts import upload_channel_artifacts
from fluxion.channels.attachments import (
    AttachmentNormalizationError,
    DownloadedFile,
    ensure_attachment_count,
    normalize_downloaded_files,
)
from fluxion.channels.base import authorize_inbound
from fluxion.channels.control_formatter import format_control_response
from fluxion.channels.wechat.context_store import ContextTokenStore
from fluxion.channels.wechat.credential_store import CredentialStore
from fluxion.channels.wechat.ilink_client import ILinkClient
from fluxion.channels.wechat.models import MessageItem, WeixinMessage
from fluxion.channels.wechat.pending_store import PendingUserStore
from fluxion.config.settings import Settings, env_file_path
from fluxion.core.models.result import ExecutionResult
from fluxion.core.models.task import Task
from fluxion.i18n import resolve_locale
from fluxion.renderers.markdown_renderer import MarkdownRenderer, clip_text
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_POLL_RETRY_BACKOFF_SEC = 5
_MAX_POLL_RETRY_BACKOFF_SEC = 60
# Waiting for credentials is a cheap stat() per iteration, not an API call,
# so poll fast: after the first login binds, the pending first message should
# be fetched within a couple of seconds instead of a full backoff window.
_CREDENTIAL_WAIT_SEC = 2
# WeChat allows ~4096 chars/message; keep headroom.
WECHAT_TEXT_SOFT_LIMIT = 3800


class WeChatChannelAdapter:
    """Fluxion channel adapter that bridges WeChat via iLink Backend API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._renderer = MarkdownRenderer(soft_limit=WECHAT_TEXT_SOFT_LIMIT)
        self._gateway = None
        self._client = ILinkClient()
        self._cred_store = CredentialStore(settings.data_dir)
        self._context_store = ContextTokenStore(settings.data_dir)
        self._pending_store = PendingUserStore(settings.data_dir)
        self._typing_ticket: str = ""
        self._get_updates_buf: str = ""
        self._last_cred_mtime: float = 0.0
        self._invalid_cred_mtime: float = 0.0
        self._credentials_ready = False
        self._logged_waiting_for_credentials = False
        self._env_file_path = env_file_path()
        self._last_env_mtime = self._get_env_mtime()

        # Stream buffering: task_id → accumulated text
        self._stream_lock = threading.Lock()
        self._stream_buffers: dict[str, str] = {}

        # Track context_token for replies: user_id → latest context_token
        self._context_lock = threading.Lock()
        self._user_context_tokens: dict[str, str] = {}

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, gateway: GatewayCore) -> None:
        """Start the poll loop, waiting for credentials when first binding is pending."""
        self._gateway = gateway
        self._check_and_reload_credentials()

        poll_thread = threading.Thread(
            target=self._poll_loop,
            name="fluxion-wechat-poll",
            daemon=True,
        )
        poll_thread.start()
        if self._credentials_ready:
            logger.info("WeChat iLink adapter started")
        else:
            logger.info(
                "WeChat iLink adapter started; waiting for %s",
                self._cred_store.path,
            )

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None:
        if status not in self._settings.status_updates:
            return
        if status == "RUNNING":
            self.send_typing(context)
            return
        locale = self._locale_for_context(context)
        text = self._renderer.render_status(task_id, status, detail=detail, locale=locale)
        self._send_text(context=context, text=text)

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None:
        # Prepend any buffered stream output
        buffered = self._pop_stream_buffer(task_id)
        locale = self._locale_for_context(context)

        if buffered and result.success:
            # The streamed answer is the primary response
            self._send_text(context=context, text=buffered)
            self._upload_artifacts(result=result, context=context)
            return

        rendered = self._renderer.render_result(task_id, result, locale=locale)
        if buffered:
            combined = buffered + "\n\n" + rendered
            self._send_text(context=context, text=clip_text(combined, WECHAT_TEXT_SOFT_LIMIT))
        else:
            self._send_text(context=context, text=rendered)
        self._upload_artifacts(result=result, context=context)

    def send_typing(self, context: dict) -> None:
        user_id = str(context.get("user") or "").strip()
        context_token = str(context.get("context_token") or "").strip()
        if not user_id:
            return
        if not context_token:
            with self._context_lock:
                context_token = self._user_context_tokens.get(user_id, "")
        if not context_token:
            return
        try:
            config = self._client.get_config(user_id=user_id, context_token=context_token)
            ticket = str(config.get("typing_ticket") or "")
            if not ticket:
                return
            self._client.send_typing(
                user_id=user_id,
                typing_ticket=ticket,
                status=1,
            )
        except Exception:
            logger.debug("WeChat send_typing failed", exc_info=True)

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None:
        """Buffer streaming output — WeChat has no append API."""
        chunk = text or ""
        if not chunk:
            return
        with self._stream_lock:
            self._stream_buffers[task_id] = self._stream_buffers.get(task_id, "") + chunk

    # ------------------------------------------------------------------
    # Message send helper
    # ------------------------------------------------------------------

    def _send_text(self, *, context: dict, text: str) -> None:
        """Send a plain-text message to the user."""
        user_id = str(context.get("user") or "").strip()
        context_token = str(context.get("context_token") or "").strip()
        if not user_id:
            logger.warning("Cannot send message: no user_id in context")
            return
        # Fall back to cached context_token
        if not context_token:
            with self._context_lock:
                context_token = self._user_context_tokens.get(user_id, "")

        items = [MessageItem.text(text)]
        for attempt in range(1, 4):
            try:
                self._client.send_message(
                    to_user_id=user_id,
                    context_token=context_token,
                    items=items,
                )
                return
            except AttachmentNormalizationError as exc:
                self._send_text(context=context, text=f"Rejected attachment: {exc}")
            except Exception:
                logger.exception("WeChat sendMessage failed on attempt %s", attempt)
                time.sleep(0.5 * attempt)

    # ------------------------------------------------------------------
    # Stream buffer management
    # ------------------------------------------------------------------

    def _pop_stream_buffer(self, task_id: str) -> str:
        with self._stream_lock:
            return self._stream_buffers.pop(task_id, "")

    # ------------------------------------------------------------------
    # Long-poll loop
    # ------------------------------------------------------------------

    def _credential_mtime(self) -> float:
        try:
            return self._cred_store.path.stat().st_mtime
        except OSError:
            return 0.0

    def _check_and_reload_credentials(self) -> None:
        try:
            path = self._cred_store.path
            if not path.exists():
                if not self._logged_waiting_for_credentials:
                    logger.warning(
                        "No WeChat credentials found; waiting for %s. "
                        "Run WeChat login from the current Fluxion install to bind first.",
                        path,
                    )
                    self._logged_waiting_for_credentials = True
                return
            mtime = path.stat().st_mtime
            if mtime > self._last_cred_mtime and mtime > self._invalid_cred_mtime:
                logger.info("Detecting updated WeChat credentials file, reloading...")
                creds = self._cred_store.load()
                if creds:
                    self._client.apply_credentials(creds)
                    # Clear typing ticket so we fetch it fresh
                    self._typing_ticket = ""
                    self._last_cred_mtime = mtime
                    self._credentials_ready = True
                    self._logged_waiting_for_credentials = False
                    logger.info(
                        "WeChat credentials reloaded successfully (bot_id=%s)", creds.ilink_bot_id
                    )
                else:
                    self._credentials_ready = False
                    logger.warning(
                        "WeChat credentials file exists but could not be loaded: %s", path
                    )
        except Exception:
            logger.exception("Failed to check or reload WeChat credentials")

    def _mark_credentials_expired(self, detail: str) -> None:
        mtime = self._credential_mtime()
        self._credentials_ready = False
        self._invalid_cred_mtime = max(self._invalid_cred_mtime, mtime)
        logger.error(
            "WeChat credentials are no longer valid (%s); waiting for a fresh login at %s",
            detail,
            self._cred_store.path,
        )

    def _poll_loop(self) -> None:
        """Continuously long-poll for new WeChat messages."""
        backoff = _POLL_RETRY_BACKOFF_SEC
        while True:
            try:
                self._check_and_reload_credentials()
                if not self._credentials_ready:
                    time.sleep(_CREDENTIAL_WAIT_SEC)
                    continue
                resp = self._client.get_updates(self._get_updates_buf)
                if resp.get_updates_buf:
                    self._get_updates_buf = resp.get_updates_buf
                backoff = _POLL_RETRY_BACKOFF_SEC  # reset on success

                for msg in resp.msgs:
                    try:
                        self._handle_message(msg)
                    except Exception:
                        logger.exception("Error handling WeChat message %s", msg.message_id)

            except Exception as exc:
                detail = str(exc)
                if "session timeout" in detail.lower() or "errcode=-14" in detail.lower():
                    self._mark_credentials_expired(detail)
                logger.exception("WeChat getUpdates failed, retrying in %ss", backoff)
                self._check_and_reload_credentials()
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_POLL_RETRY_BACKOFF_SEC)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    def _handle_message(self, msg: WeixinMessage) -> None:
        text = msg.extract_text().strip()
        attachments = self._extract_attachments(msg)
        if not text and not attachments:
            return

        user_id = msg.from_user_id
        context_token = msg.context_token

        # Cache context_token for replies
        with self._context_lock:
            self._user_context_tokens[user_id] = context_token
        try:
            self._context_store.save(user_id, context_token)
        except OSError:
            logger.warning(
                "Failed to persist WeChat context token for %s; replies will still work until restart",
                user_id,
                exc_info=True,
            )

        context = {
            "user": user_id,
            "context_token": context_token,
            "session_id": msg.session_id,
            "channel_type": "wechat_im",
            "source_text": text,
        }

        # Validate inbound
        self._check_and_reload_settings()
        valid, reason = self._validate_inbound(msg)
        if not valid:
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

        # Handle control commands (only if text is present)
        if text and self._handle_control_command(text=text, msg=msg, context=context):
            return

        # Extract workspace override
        workspace_override, task_text = self._extract_workspace(text)

        if self._gateway is None:
            self._send_text(context=context, text="Gateway not ready.")
            return

        # Resolve workspace
        try:
            wechat_ws = self._settings.wechat_default_workspace
            raw_ws = workspace_override or (wechat_ws if wechat_ws else self._default_workspace())
            workspace = self._settings.resolve_workspace(raw_ws)
        except ValueError as exc:
            self._send_text(context=context, text=f"Rejected: {exc}")
            return

        # Download attachments if present
        if attachments:
            try:
                ensure_attachment_count(len(attachments))
            except AttachmentNormalizationError as exc:
                self._send_text(context=context, text=f"Rejected attachment: {exc}")
                return
            downloaded = self._download_attachments(
                attachments=attachments,
                workspace=workspace,
                context=context,
            )
        else:
            downloaded = []
        if attachments and not downloaded and not task_text:
            self._send_text(
                context=context,
                text="Please send a non-empty task description or attachments.",
            )
            return
        try:
            task_attachments, task_images = normalize_downloaded_files(downloaded)
        except AttachmentNormalizationError as exc:
            self._send_text(context=context, text=f"Rejected attachment: {exc}")
            return

        # Build conversation key and resolve executor
        convo_key = f"wechat:{user_id}:{msg.session_id or user_id}"
        override = self._gateway._sessions.get_executor_override(
            conversation_key=convo_key,
            channel="wechat",
            user_id=user_id,
        )
        executor_to_use = override if override else self._settings.default_executor

        task = Task.create(
            channel="wechat",
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

        submit_context = {**context, "workspace": str(workspace)}
        ok, info = self._gateway.submit_task(
            task=task,
            channel_adapter=self,
            channel_context=submit_context,
        )
        if not ok:
            self._send_text(context=context, text=f"Rejected: {info}")

    def _default_workspace(self) -> str | None:
        resolver = getattr(self._settings, "default_channel_workspace", None)
        if callable(resolver):
            return resolver()
        allowed = getattr(self._settings, "allowed_workspaces", [])
        if allowed:
            return str(allowed[0])
        return None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inbound(self, msg: WeixinMessage) -> tuple[bool, str]:
        return authorize_inbound(
            channel="wechat",
            user_id=msg.from_user_id or "",
            allowed_users=self._settings.wechat_allowed_users,
        )

    def _get_env_mtime(self) -> float:
        path = self._env_file_path
        if path is None:
            return 0.0
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _check_and_reload_settings(self) -> None:
        """Hot-reload allowlist edits before authorizing the next message."""
        path = self._env_file_path
        if path is None:
            path = env_file_path()
            self._env_file_path = path
        mtime = self._get_env_mtime()
        if mtime <= self._last_env_mtime:
            return

        old_data_dir = self._settings.data_dir
        try:
            new_settings = Settings.reload()
        except Exception:
            logger.exception("Failed to reload settings from %s", path)
            self._last_env_mtime = mtime
            return

        self._settings = new_settings
        if new_settings.data_dir != old_data_dir:
            self._cred_store = CredentialStore(new_settings.data_dir)
            self._context_store = ContextTokenStore(new_settings.data_dir)
            self._pending_store = PendingUserStore(new_settings.data_dir)
        self._last_env_mtime = mtime
        logger.info(
            "Reloaded WeChat settings from %s (allowed_users=%d)",
            path,
            len(new_settings.wechat_allowed_users),
        )

    def _format_rejection(self, reason: str, user_id: str, *, locale: str | None = None) -> str:
        return format_allowlist_rejection(
            reason=reason,
            user_id=user_id,
            id_label="WeChat iLink user ID",
            env_key="FLUXION_WECHAT_ALLOWED_USERS",
            locale=locale or self._locale_for_context({"source_text": ""}),
            macos_pending_location="Messaging -> WeChat -> Pending Users",
        )

    # ------------------------------------------------------------------
    # Control commands
    # ------------------------------------------------------------------

    def _handle_control_command(
        self,
        *,
        text: str,
        msg: WeixinMessage,
        context: dict,
    ) -> bool:
        user_id = msg.from_user_id

        if self._gateway is None:
            return False
        convo_key = f"wechat:{user_id}:{msg.session_id or user_id}"
        response = self._gateway.handle_control_command(
            text=text,
            user_id=user_id,
            convo_key=convo_key,
            channel="wechat",
        )
        if response is not None:
            self._send_text(
                context=context,
                text=format_control_response(response, channel="wechat"),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_workspace(self, text: str) -> tuple[str | None, str]:
        match = re.match(r"^(workspace=|/workspace\s+)(\S+)\s*(.*)$", text.strip())
        if not match:
            return None, text.strip()
        workspace = match.group(2).strip()
        remaining = match.group(3).strip()
        return workspace, remaining

    def _locale_for_context(self, context: dict[str, Any]) -> str:
        return resolve_locale(
            mode=getattr(self._settings, "locale_mode", "auto"),
            fixed_locale=getattr(self._settings, "ui_locale", "en"),
            context=context,
        )

    def _extract_attachments(self, msg: WeixinMessage) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for item in msg.item_list:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == 2:  # image
                image_item = item.get("image_item")
                if isinstance(image_item, dict):
                    media = image_item.get("media") or image_item.get("cdn_media")
                    if isinstance(media, dict):
                        name = (
                            image_item.get("name")
                            or image_item.get("title")
                            or media.get("name")
                            or media.get("title")
                            or item.get("name")
                            or item.get("title")
                            or "image.png"
                        )
                        attachments.append(
                            {
                                "type": 2,
                                "encrypt_query_param": media.get("encrypt_query_param"),
                                "aes_key": media.get("aes_key") or image_item.get("aeskey"),
                                "full_url": media.get("full_url") or image_item.get("full_url"),
                                "name": name,
                            }
                        )
            elif itype == 4:  # file
                file_item = item.get("file_item")
                if isinstance(file_item, dict):
                    media = file_item.get("media") or file_item.get("cdn_media")
                    if isinstance(media, dict):
                        name = (
                            file_item.get("name")
                            or file_item.get("title")
                            or media.get("name")
                            or media.get("title")
                            or item.get("name")
                            or item.get("title")
                            or "file.bin"
                        )
                        attachments.append(
                            {
                                "type": 4,
                                "encrypt_query_param": media.get("encrypt_query_param"),
                                "aes_key": media.get("aes_key") or file_item.get("aeskey"),
                                "full_url": media.get("full_url") or file_item.get("full_url"),
                                "name": name,
                            }
                        )
        return attachments

    def _download_attachments(
        self,
        *,
        attachments: list[dict[str, Any]],
        workspace: Path,
        context: dict,
    ) -> list[DownloadedFile]:
        from fluxion.channels.inbox import make_inbox_dir

        attach_dir = make_inbox_dir(workspace, ttl_hours=self._settings.inbox_ttl_hours)
        saved: list[DownloadedFile] = []
        for idx, att in enumerate(attachments):
            param = att.get("encrypt_query_param")
            aes_key = att.get("aes_key")
            if not param or not aes_key:
                continue
            name = self._safe_filename(str(att.get("name") or f"file_{idx}"))
            dest = attach_dir / name
            key_is_hex = att.get("type") != 2
            try:
                self._client.download_file_from_cdn(
                    encrypt_query_param=param,
                    dest_path=dest,
                    aes_key=aes_key,
                    key_is_hex=key_is_hex,
                    full_url=att.get("full_url"),
                )
                saved.append(
                    DownloadedFile(
                        path=dest,
                        media_type="",
                    )
                )
            except Exception:
                logger.exception("Failed to download WeChat attachment %s", name)
                self._send_text(
                    context=context,
                    text=f"Failed to download and decrypt attachment {name}.",
                )
        return saved

    @staticmethod
    def _safe_filename(name: str) -> str:
        # Keep only the basename and strip anything path- or shell-unfriendly.
        base = Path(name).name.strip() or "file"
        cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", base).strip("._")
        return cleaned or "file"

    def _upload_artifacts(self, *, result: ExecutionResult, context: dict) -> None:
        upload_channel_artifacts(
            result=result,
            context=context,
            max_files=getattr(self._settings, "artifact_max_files", 8),
            upload_log_on_success=getattr(self._settings, "upload_log_on_success", False),
            upload_one=lambda path: self._upload_file(path=path, context=context),
        )

    def _upload_file(self, *, path: Path, context: dict) -> None:
        import hashlib
        import secrets

        from fluxion.channels.wechat.crypto import encode_aes_key
        from fluxion.channels.wechat.models import MessageItem

        user_id = str(context.get("user") or "").strip()
        context_token = str(context.get("context_token") or "").strip()
        if not user_id:
            logger.warning("Cannot upload file: no user_id in context")
            return
        if not context_token:
            with self._context_lock:
                context_token = self._user_context_tokens.get(user_id, "")

        ext = path.suffix.lower()
        is_image = ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
        media_type = 1 if is_image else 3

        try:
            raw_bytes = path.read_bytes()
            raw_size = len(raw_bytes)
            raw_md5 = hashlib.md5(raw_bytes).hexdigest()
            raw_key = secrets.token_bytes(16)
            aes_key_hex = raw_key.hex()
            aes_key_encoded = encode_aes_key(raw_key, key_is_hex=True)
            pad_len = 16 - (raw_size % 16)
            cipher_size = raw_size + pad_len
        except Exception:
            logger.exception("Failed to prepare file %s for WeChat CDN upload", path)
            return

        file_key = f"{secrets.token_hex(8)}{ext}"
        try:
            res = self._client.get_upload_url(
                file_key=file_key,
                media_type=media_type,
                to_user_id=user_id,
                raw_size=raw_size,
                raw_file_md5=raw_md5,
                filesize=cipher_size,
                aes_key_hex=aes_key_hex,
            )
            upload_full_url = res.get("upload_full_url")
            upload_param = res.get("upload_param")
            if upload_full_url:
                upload_url = upload_full_url
            elif upload_param:
                upload_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={upload_param}&filekey={file_key}"
            else:
                raise RuntimeError(
                    f"getuploadurl response missing both upload_full_url and upload_param: {res}"
                )
        except Exception:
            logger.exception("Failed to get WeChat CDN upload URL for %s", path)
            self._send_text(
                context=context,
                text=f"Failed to get upload URL for {path.name}.",
            )
            return

        for attempt in range(1, 4):
            try:
                enc_param = self._client.upload_file_to_cdn(
                    upload_url=upload_url,
                    file_path=path,
                    aes_key=aes_key_encoded,
                    key_is_hex=True,
                )
                break
            except Exception:
                logger.exception("WeChat CDN upload failed on attempt %s for %s", attempt, path)
                if attempt == 3:
                    self._send_text(
                        context=context,
                        text=f"Failed to upload {path.name} to CDN.",
                    )
                    return
                time.sleep(0.5 * attempt)

        if is_image:
            item = MessageItem.image(
                encrypt_query_param=enc_param, aes_key=aes_key_encoded, name=path.name
            )
        else:
            item = MessageItem.file(
                encrypt_query_param=enc_param, aes_key=aes_key_encoded, name=path.name
            )

        for attempt in range(1, 4):
            try:
                self._client.send_message(
                    to_user_id=user_id,
                    context_token=context_token,
                    items=[item],
                )
                return
            except Exception:
                logger.exception(
                    "WeChat send media message failed on attempt %s for %s", attempt, path
                )
                time.sleep(0.5 * attempt)


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
