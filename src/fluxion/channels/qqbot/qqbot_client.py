"""HTTP client for sending messages via the QQ official bot API.

Implements the two send endpoints the adapter needs for the first version —
single chat (C2C) and group messages — using only the standard library
``urllib``, mirroring the Telegram/WeChat clients.

QQ free messaging is *passive*: to reply without consuming the limited active
push quota you echo back the inbound ``msg_id`` (or ``event_id``) plus a
``msg_seq`` that must be unique per ``msg_id``. The caller is responsible for
allocating increasing ``msg_seq`` values when it sends more than one message in
reply to the same inbound message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from fluxion.channels.qqbot.token_manager import QQBotTokenManager
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

# Production vs. sandbox API hosts (the sandbox only reaches whitelisted testers).
API_BASE_PROD = "https://api.sgroup.qq.com"
API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"

MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2


class QQBotAPIError(RuntimeError):
    """Raised when the QQ bot API returns an HTTP error."""

    def __init__(self, endpoint: str, code: int | None, detail: str) -> None:
        self.endpoint = endpoint
        self.code = code
        self.detail = detail
        super().__init__(f"{endpoint} failed (HTTP {code}): {detail}")


class QQBotClient:
    """Thin QQ bot API client built on ``urllib`` and a shared token manager."""

    def __init__(
        self,
        token_manager: QQBotTokenManager,
        *,
        sandbox: bool = False,
    ) -> None:
        self._tokens = token_manager
        self._api_base = (API_BASE_SANDBOX if sandbox else API_BASE_PROD).rstrip("/")

    def send_c2c_text(
        self,
        openid: str,
        content: str,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict[str, Any]:
        """Send a text message to a single user (C2C)."""
        return self._send_text(
            f"/v2/users/{openid}/messages", content, msg_id=msg_id, msg_seq=msg_seq
        )

    def send_group_text(
        self,
        group_openid: str,
        content: str,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict[str, Any]:
        """Send a text message to a group."""
        return self._send_text(
            f"/v2/groups/{group_openid}/messages",
            content,
            msg_id=msg_id,
            msg_seq=msg_seq,
        )

    def send_c2c_markdown(
        self,
        openid: str,
        content: str,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict[str, Any]:
        """Send a native (custom) markdown message to a single user (C2C)."""
        return self._send_markdown(
            f"/v2/users/{openid}/messages", content, msg_id=msg_id, msg_seq=msg_seq
        )

    def send_group_markdown(
        self,
        group_openid: str,
        content: str,
        *,
        msg_id: str | None = None,
        msg_seq: int = 1,
    ) -> dict[str, Any]:
        """Send a native (custom) markdown message to a group."""
        return self._send_markdown(
            f"/v2/groups/{group_openid}/messages", content, msg_id=msg_id, msg_seq=msg_seq
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send_text(
        self,
        path: str,
        content: str,
        *,
        msg_id: str | None,
        msg_seq: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content, "msg_type": MSG_TYPE_TEXT}
        return self._call(path, self._with_reply_fields(body, msg_id, msg_seq))

    def _send_markdown(
        self,
        path: str,
        content: str,
        *,
        msg_id: str | None,
        msg_seq: int,
    ) -> dict[str, Any]:
        # Native markdown (no template): the raw markdown rides in markdown.content.
        # Open to all bots for C2C + group since 2026-04; no template registration.
        body: dict[str, Any] = {"markdown": {"content": content}, "msg_type": MSG_TYPE_MARKDOWN}
        return self._call(path, self._with_reply_fields(body, msg_id, msg_seq))

    @staticmethod
    def _with_reply_fields(
        body: dict[str, Any], msg_id: str | None, msg_seq: int
    ) -> dict[str, Any]:
        if msg_id:
            # Passive reply: echo the inbound msg_id. msg_seq must be unique per
            # msg_id, otherwise QQ silently de-duplicates repeated sends.
            body["msg_id"] = msg_id
            body["msg_seq"] = msg_seq
        return body

    def _call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._tokens.get_access_token()
        url = f"{self._api_base}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            # A 401 usually means the token went stale; drop it so the next send
            # mints a fresh one rather than failing again.
            if exc.code == 401:
                self._tokens.invalidate()
            logger.error("QQ bot API %s failed: %s %s", path, exc.code, detail)
            raise QQBotAPIError(path, exc.code, detail) from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("QQ bot API %s request error: %s", path, exc)
            raise QQBotAPIError(path, None, str(exc)) from exc
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
