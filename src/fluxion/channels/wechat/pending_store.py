"""Persist WeChat users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected WeChat users so an admin can approve them later."""

    _PENDING_FILENAME = "wechat_pending_users.json"
    _LABEL = "WeChat"
    _CHANNEL_KEY = "wechat"
