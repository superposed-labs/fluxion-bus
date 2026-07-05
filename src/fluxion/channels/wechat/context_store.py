"""Persist WeChat reply context tokens for cross-process notifications."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_CONTEXT_FILENAME = "wechat_context_tokens.json"


class ContextTokenStore:
    """Store the latest inbound context token for each WeChat user."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / _CONTEXT_FILENAME

    def save(self, user_id: str, context_token: str) -> None:
        user_id = user_id.strip()
        context_token = context_token.strip()
        if not user_id or not context_token:
            return

        users = self.load_all()
        users[user_id] = {
            "context_token": context_token,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        payload = {"users": users}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        # Create the temp file at 0600 from the start; os.replace then carries
        # those perms to the destination, so the context tokens are never briefly
        # readable by other local users.
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(temp_path, self._path)
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            logger.debug("Failed to set 0600 on %s", self._path, exc_info=True)

    def load_all(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            raw_users = payload.get("users") if isinstance(payload, dict) else None
            if not isinstance(raw_users, dict):
                return {}
            users: dict[str, dict[str, str]] = {}
            for user_id, entry in raw_users.items():
                if not isinstance(entry, dict):
                    continue
                token = str(entry.get("context_token") or "").strip()
                if token:
                    users[str(user_id)] = {
                        "context_token": token,
                        "updated_at": str(entry.get("updated_at") or ""),
                    }
            return users
        except Exception:
            logger.exception("Failed to load WeChat context tokens from %s", self._path)
            return {}

    def notification_targets(self, allowed_users: set[str]) -> list[tuple[str, str]]:
        users = self.load_all()
        target_ids = sorted(allowed_users) if allowed_users else sorted(users)
        return [
            (user_id, users[user_id]["context_token"]) for user_id in target_ids if user_id in users
        ]
