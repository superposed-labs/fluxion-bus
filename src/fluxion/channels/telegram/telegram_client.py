"""HTTP client for the Telegram Bot API.

Implements the handful of endpoints the channel adapter needs (``getUpdates``
long-poll, ``sendMessage``, ``editMessageText``, ``sendChatAction`` and
``sendDocument``) using only the standard library ``urllib`` — no third-party
HTTP dependency required, mirroring the WeChat iLink client.

All methods raise :class:`TelegramAPIError` on a non-OK response so the caller
can decide whether to retry or fall back (e.g. drop ``parse_mode`` when Telegram
rejects malformed Markdown).
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fluxion.channels.attachments import copy_stream_limited
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_API_BASE = "https://api.telegram.org"

# Small JSON calls normally complete in under ~2s; the route to
# api.telegram.org intermittently stalls for 30s+ on fresh connections, so cap
# these tightly and let the caller's retry loop reconnect instead of waiting.
_SMALL_CALL_TIMEOUT_SEC = 8


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram Bot API returns ``ok: false`` or HTTP error."""

    def __init__(self, method: str, error_code: int | None, description: str) -> None:
        self.method = method
        self.error_code = error_code
        self.description = description
        super().__init__(f"{method} failed (code={error_code}): {description}")

    @property
    def is_parse_error(self) -> bool:
        """Whether the failure looks like a Markdown/HTML entity parse error."""
        text = self.description.lower()
        return "parse entities" in text or "can't parse" in text or "entity" in text


class TelegramClient:
    """Thin Telegram Bot API client built on ``urllib``."""

    def __init__(self, token: str, *, api_base: str = _DEFAULT_API_BASE) -> None:
        if not token:
            raise ValueError("Telegram bot token is empty")
        self._token = token
        self._api_base = api_base.rstrip("/")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe", {})

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Long-poll for new updates. ``timeout`` is the server-side poll seconds."""
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates
        # Give the socket more time than the server-side long-poll window.
        result = self._call("getUpdates", params, request_timeout=timeout + 15)
        return result if isinstance(result, list) else []

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        return self._call("sendMessage", params, request_timeout=_SMALL_CALL_TIMEOUT_SEC)

    def edit_message_text(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        return self._call("editMessageText", params, request_timeout=_SMALL_CALL_TIMEOUT_SEC)

    def send_chat_action(
        self,
        *,
        chat_id: int | str,
        action: str = "typing",
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "action": action}
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        return self._call("sendChatAction", params, request_timeout=_SMALL_CALL_TIMEOUT_SEC)

    def send_document(
        self,
        *,
        chat_id: int | str,
        path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        return self._call_multipart(
            "sendDocument",
            fields=self._media_fields(
                chat_id=chat_id,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            ),
            file_field="document",
            file_path=path,
        )

    def send_photo(
        self,
        *,
        chat_id: int | str,
        path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        return self._call_multipart(
            "sendPhoto",
            fields=self._media_fields(
                chat_id=chat_id,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            ),
            file_field="photo",
            file_path=path,
        )

    def get_file(self, *, file_id: str) -> dict[str, Any]:
        """Resolve a ``file_id`` to a downloadable ``file_path`` (<=20MB files)."""
        return self._call("getFile", {"file_id": file_id})

    def download_file(self, *, file_path: str, dest: Path) -> None:
        """Download a resolved ``file_path`` to ``dest`` on disk."""
        url = f"{self._api_base}/file/bot{self._token}/{file_path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as out:
                    copy_stream_limited(resp, out)
        except urllib.error.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise TelegramAPIError("download_file", exc.code, exc.reason or "HTTP error") from exc
        except urllib.error.URLError as exc:
            dest.unlink(missing_ok=True)
            raise TelegramAPIError("download_file", None, str(exc.reason)) from exc
        except Exception:
            dest.unlink(missing_ok=True)
            raise

    @staticmethod
    def _media_fields(
        *,
        chat_id: int | str,
        caption: str | None,
        reply_to_message_id: int | None,
        message_thread_id: int | None,
    ) -> dict[str, str]:
        fields: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        if reply_to_message_id is not None:
            fields["reply_to_message_id"] = str(reply_to_message_id)
        if message_thread_id is not None:
            fields["message_thread_id"] = str(message_thread_id)
        return fields

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _url(self, method: str) -> str:
        return f"{self._api_base}/bot{self._token}/{method}"

    def _call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_timeout: int = 30,
    ) -> Any:
        body = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            self._url(method),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(req, method, request_timeout)

    def _call_multipart(
        self,
        method: str,
        *,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
    ) -> Any:
        boundary = f"----FluxionBoundary{uuid.uuid4().hex}"
        body = self._encode_multipart(
            boundary=boundary,
            fields=fields,
            file_field=file_field,
            file_path=file_path,
        )
        req = urllib.request.Request(
            self._url(method),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._send(req, method, request_timeout=120)

    def _send(self, req: urllib.request.Request, method: str, request_timeout: int) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = self._safe_json(exc)
            if payload is None:
                raise TelegramAPIError(method, exc.code, exc.reason or "HTTP error") from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(method, None, str(exc.reason)) from exc

        if not payload.get("ok", False):
            raise TelegramAPIError(
                method,
                payload.get("error_code"),
                str(payload.get("description") or "unknown error"),
            )
        return payload.get("result")

    @staticmethod
    def _safe_json(exc: urllib.error.HTTPError) -> dict[str, Any] | None:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _encode_multipart(
        *,
        boundary: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
    ) -> bytes:
        crlf = b"\r\n"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(b"--" + boundary.encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
            parts.append(b"")
            parts.append(str(value).encode("utf-8"))
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        parts.append(b"--" + boundary.encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"'
            ).encode()
        )
        parts.append(f"Content-Type: {content_type}".encode())
        parts.append(b"")
        parts.append(file_path.read_bytes())
        parts.append(b"--" + boundary.encode() + b"--")
        parts.append(b"")
        return crlf.join(parts)
