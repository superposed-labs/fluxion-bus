"""Persist LINE users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected LINE user ids so an admin can approve them later."""

    _PENDING_FILENAME = "line_pending_users.json"
    _LABEL = "LINE"
    _CHANNEL_KEY = "line"
