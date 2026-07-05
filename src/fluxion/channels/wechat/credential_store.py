"""Persist and load iLink bot credentials.

Credentials are saved as JSON to ``$FLUXION_DATA_DIR/wechat_credentials.json``
with strict file permissions (0600) because the ``bot_token`` is effectively a
session key for the bound WeChat account.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from fluxion.channels.wechat.models import Credentials
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_CRED_FILENAME = "wechat_credentials.json"


class CredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / _CRED_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def save(self, creds: Credentials) -> None:
        """Write credentials to disk with 0600 permissions."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bot_token": creds.bot_token,
            "ilink_bot_id": creds.ilink_bot_id,
            "baseurl": creds.baseurl,
            "created_at": creds.created_at,
        }
        # Create at 0600 from the start so the token is never briefly readable
        # by other local users in the window between write and chmod.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        # Tighten a pre-existing file whose perms O_CREAT's mode did not change.
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            logger.debug("Failed to set 0600 on %s", self._path, exc_info=True)

    def load(self) -> Credentials | None:
        """Return stored credentials or ``None`` if absent / corrupt."""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            token = str(data.get("bot_token") or "").strip()
            bot_id = str(data.get("ilink_bot_id") or "").strip()
            baseurl = str(data.get("baseurl") or "").strip()
            if not token or not bot_id:
                return None
            return Credentials(
                bot_token=token,
                ilink_bot_id=bot_id,
                baseurl=baseurl,
                created_at=str(data.get("created_at") or ""),
            )
        except Exception:
            logger.exception("Failed to load WeChat credentials from %s", self._path)
            return None

    def clear(self) -> None:
        """Delete the credential file."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove %s", self._path, exc_info=True)
