"""Access-token manager for the QQ official bot API.

QQ's current auth scheme uses a short-lived *app access token* (default lifetime
7200s) obtained from ``https://bots.qq.com/app/getAppAccessToken`` with the bot's
``appId`` + ``clientSecret``. The endpoint does not auto-refresh — each call
mints a fresh token and the caller is responsible for refreshing before expiry.

This manager caches the token in memory and refreshes it a configurable margin
*before* it expires (default 90s, comfortably ahead of QQ's 60s overlap window),
so concurrent senders never race an expired token. Refresh is serialized with a
lock; once one thread refreshes, the others return the cached value.

Only the standard library ``urllib`` is used, matching the other Fluxion channel
clients (no third-party HTTP dependency).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_TOKEN_ENDPOINT = "https://bots.qq.com/app/getAppAccessToken"


class QQBotTokenError(RuntimeError):
    """Raised when an access token cannot be obtained."""


class QQBotTokenManager:
    """Fetches and caches the QQ bot app access token, refreshing before expiry."""

    def __init__(
        self,
        app_id: str,
        client_secret: str,
        *,
        refresh_margin_sec: int = 90,
    ) -> None:
        if not app_id or not client_secret:
            raise ValueError("QQ bot app_id and client_secret are required")
        self._app_id = app_id
        self._client_secret = client_secret
        self._refresh_margin_sec = max(0, refresh_margin_sec)
        self._lock = threading.Lock()
        self._token: str = ""
        self._expires_at: float = 0.0

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it if near expiry.

        Raises :class:`QQBotTokenError` if a token cannot be obtained.
        """
        if self._token and not self._is_expiring():
            return self._token
        with self._lock:
            # Re-check inside the lock: another thread may have refreshed while we
            # waited, so we avoid a redundant network round-trip.
            if self._token and not self._is_expiring():
                return self._token
            self._refresh()
            return self._token

    def invalidate(self) -> None:
        """Drop the cached token so the next call forces a refresh.

        Useful after the API rejects a token mid-flight (e.g. it was revoked).
        """
        with self._lock:
            self._token = ""
            self._expires_at = 0.0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_expiring(self) -> bool:
        return time.monotonic() >= (self._expires_at - self._refresh_margin_sec)

    def _refresh(self) -> None:
        body = json.dumps({"appId": self._app_id, "clientSecret": self._client_secret}).encode(
            "utf-8"
        )
        req = urllib.request.Request(
            _TOKEN_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise QQBotTokenError(f"getAppAccessToken failed: HTTP {exc.code} {detail}") from exc
        except Exception as exc:  # noqa: BLE001 - surface any transport error uniformly
            raise QQBotTokenError(f"getAppAccessToken request failed: {exc}") from exc

        token = str(data.get("access_token") or "")
        if not token:
            raise QQBotTokenError(f"getAppAccessToken returned no token: {data}")
        # expires_in comes back as a string of seconds (e.g. "7200").
        try:
            expires_in = int(str(data.get("expires_in") or "0"))
        except ValueError:
            expires_in = 0
        if expires_in <= 0:
            expires_in = 7200
        self._token = token
        self._expires_at = time.monotonic() + expires_in
        logger.info("Refreshed QQ bot access token (expires in %ss)", expires_in)
