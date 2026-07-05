from __future__ import annotations

from types import SimpleNamespace

from fluxion.channels.approval_notify import SENDERS, approval_notice_text


def _settings(locale_mode: str = "auto", ui_locale: str = "en"):
    return SimpleNamespace(locale_mode=locale_mode, ui_locale=ui_locale)


def test_approval_notice_uses_requested_locale() -> None:
    text = approval_notice_text(_settings(), locale="zh-Hans")

    assert text == "已批准。你现在可以向 Fluxion 发送任务了。"


def test_approval_notice_falls_back_to_settings_locale() -> None:
    text = approval_notice_text(_settings(locale_mode="fixed", ui_locale="ja"))

    assert text == "承認されました。Fluxion にタスクを送信できるようになりました。"


def test_approval_notice_allows_explicit_override() -> None:
    text = approval_notice_text(_settings(), locale="zh", text="custom")

    assert text == "custom"


def test_senders_cover_every_channel_with_a_pending_store() -> None:
    from fluxion.channels.feishu.pending_store import PendingUserStore as Feishu
    from fluxion.channels.line.pending_store import PendingUserStore as Line
    from fluxion.channels.qqbot.pending_store import PendingUserStore as QQBot
    from fluxion.channels.slack.pending_store import PendingUserStore as Slack
    from fluxion.channels.telegram.pending_store import PendingUserStore as Telegram
    from fluxion.channels.wechat.pending_store import PendingUserStore as WeChat

    all_channel_keys = {
        store._CHANNEL_KEY for store in (Feishu, Line, QQBot, Slack, Telegram, WeChat)
    }
    assert set(SENDERS) == all_channel_keys
