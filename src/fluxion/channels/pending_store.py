"""Shared store for IM channel users waiting for allowlist approval.

Each channel subclasses :class:`PendingUserStore` and sets its own
``_PENDING_FILENAME``. The on-disk names are read directly by the macOS
desktop app, so they must stay byte-for-byte stable per channel.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from fluxion.i18n import t
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_PREVIEW_MAX_CHARS = 160


class PendingUserStore:
    """Track rejected channel users so an admin can approve them later.

    Subclasses set ``_PENDING_FILENAME`` (the JSON file name on disk),
    ``_LABEL`` (the channel name used in log messages) and ``_CHANNEL_KEY``
    (the stable identifier the desktop app uses to route notification clicks
    back to the right channel).
    """

    _PENDING_FILENAME: str = "pending_users.json"
    _LABEL: str = "channel"
    _CHANNEL_KEY: str = ""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / self._PENDING_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self, user_id: str, message_preview: str = "", *, notify_locale: str | None = None
    ) -> bool:
        """Record a rejected sender; returns True on their first sighting.

        When ``notify_locale`` is given, a first sighting also queues a macOS
        Notification Center alert so the operator learns someone is waiting
        without having to open Preferences. Repeat messages only bump the
        counter — one alert per pending user.
        """
        user_id = user_id.strip()
        if not user_id:
            return False

        users = self.load_all()
        is_new = user_id not in users
        now = datetime.now(UTC).isoformat()
        preview = " ".join(message_preview.split())[:_PREVIEW_MAX_CHARS]
        entry = users.get(user_id, {})
        users[user_id] = {
            "first_seen_at": str(entry.get("first_seen_at") or now),
            "last_seen_at": now,
            "message_count": int(entry.get("message_count") or 0) + 1,
            "last_message_preview": preview,
        }
        self._write({"users": users})
        if is_new and notify_locale is not None:
            self._queue_macos_notification(user_id, preview, notify_locale)
        return is_new

    def _queue_macos_notification(self, user_id: str, preview: str, locale: str) -> None:
        """Append to the JSONL signal file the desktop app polls and delivers
        through Notification Center (the same channel the scheduler uses).
        No-op off macOS so the file never accumulates without a consumer."""
        if sys.platform != "darwin":
            return
        signal_path = self._path.parent / "macos_notifications.jsonl"
        body_key = "pending.notify.body" if preview else "pending.notify.body_no_preview"
        record = json.dumps(
            {
                "title": t(locale, "pending.notify.title", channel=self._LABEL),
                "body": t(locale, body_key, user_id=user_id, preview=preview),
                "channel": self._CHANNEL_KEY,
                "user_id": user_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        try:
            with open(signal_path, "a", encoding="utf-8") as fp:
                fp.write(record + "\n")
        except OSError:
            logger.warning(
                "Failed to queue macOS notification for pending %s user %s",
                self._LABEL,
                user_id,
                exc_info=True,
            )

    def remove(self, user_id: str) -> None:
        user_id = user_id.strip()
        if not user_id:
            return
        users = self.load_all()
        if user_id not in users:
            return
        users.pop(user_id, None)
        self._write({"users": users})

    def load_all(self) -> dict[str, dict[str, str | int]]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            raw_users = payload.get("users") if isinstance(payload, dict) else None
            if not isinstance(raw_users, dict):
                return {}
            users: dict[str, dict[str, str | int]] = {}
            for user_id, entry in raw_users.items():
                if not isinstance(entry, dict):
                    continue
                uid = str(user_id).strip()
                if not uid:
                    continue
                users[uid] = {
                    "first_seen_at": str(entry.get("first_seen_at") or ""),
                    "last_seen_at": str(entry.get("last_seen_at") or ""),
                    "message_count": int(entry.get("message_count") or 0),
                    "last_message_preview": str(entry.get("last_message_preview") or ""),
                }
            return users
        except Exception:
            logger.exception("Failed to load %s pending users from %s", self._LABEL, self._path)
            return {}

    def _write(self, payload: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(temp_path, self._path)
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            logger.debug("Failed to set 0600 on %s", self._path, exc_info=True)
