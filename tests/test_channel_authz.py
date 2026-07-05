"""Tests for the shared, fail-closed inbound authorization helper.

All four messaging adapters (Slack, Telegram, WeChat, LINE) route their
allowlist decision through ``authorize_inbound``. The security-critical
invariant is deny-by-default: an unconfigured (empty) allowlist must reject
every user, since a channel that can reach the bot would otherwise let any
stranger drive agent execution in the authorized workspace.
"""

from __future__ import annotations

from fluxion.channels.base import authorize_inbound


def test_empty_allowlist_denies_everyone() -> None:
    """Fail-closed: an unconfigured allowlist rejects any user."""
    allowed, reason = authorize_inbound(channel="slack", user_id="U1", allowed_users=set())
    assert allowed is False
    assert reason


def test_member_is_allowed() -> None:
    allowed, reason = authorize_inbound(
        channel="telegram", user_id="U1", allowed_users={"U1", "U2"}
    )
    assert allowed is True
    assert reason == ""


def test_non_member_is_denied() -> None:
    allowed, _ = authorize_inbound(channel="wechat", user_id="U9", allowed_users={"U1"})
    assert allowed is False


def test_missing_user_is_denied() -> None:
    allowed, reason = authorize_inbound(channel="line", user_id="", allowed_users={"U1"})
    assert allowed is False
    assert "user" in reason.lower()


def test_asterisk_is_not_a_wildcard() -> None:
    """``*`` carries no special meaning; only a literal '*' user id matches it."""
    denied, _ = authorize_inbound(channel="slack", user_id="U1", allowed_users={"*"})
    assert denied is False
    literal, _ = authorize_inbound(channel="slack", user_id="*", allowed_users={"*"})
    assert literal is True


def test_empty_allowlist_logs_warning(caplog) -> None:
    """The empty-allowlist rejection is logged so the misconfig is discoverable."""
    import logging

    with caplog.at_level(logging.WARNING):
        authorize_inbound(channel="telegram", user_id="U42", allowed_users=set())
    assert any("ALLOWED_USERS" in rec.getMessage() for rec in caplog.records)
    assert any("U42" in rec.getMessage() for rec in caplog.records)
