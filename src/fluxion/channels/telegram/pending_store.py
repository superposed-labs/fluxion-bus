"""Persist Telegram users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected Telegram user ids so an admin can approve them later."""

    _PENDING_FILENAME = "telegram_pending_users.json"
    _LABEL = "Telegram"
    _CHANNEL_KEY = "telegram"
