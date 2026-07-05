"""Persist Feishu users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected Feishu open_ids so an admin can approve them later."""

    _PENDING_FILENAME = "feishu_pending_users.json"
    _LABEL = "Feishu"
    _CHANNEL_KEY = "feishu"
