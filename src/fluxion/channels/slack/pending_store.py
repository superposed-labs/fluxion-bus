"""Persist Slack users waiting for allowlist approval."""

from __future__ import annotations

from fluxion.channels.pending_store import PendingUserStore as _PendingUserStore


class PendingUserStore(_PendingUserStore):
    """Track rejected Slack member ids so an admin can approve them later."""

    _PENDING_FILENAME = "slack_pending_users.json"
    _LABEL = "Slack"
    _CHANNEL_KEY = "slack"
