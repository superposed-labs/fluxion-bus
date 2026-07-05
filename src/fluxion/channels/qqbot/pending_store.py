"""Persist QQ users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected QQ openids so an admin can approve them later."""

    _PENDING_FILENAME = "qqbot_pending_users.json"
    _LABEL = "QQ"
    _CHANNEL_KEY = "qqbot"
