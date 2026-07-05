"""Send a best-effort "you've been approved" notice to a newly allowlisted user.

The macOS app runs this as a one-shot process right after appending a pending
user to a channel allowlist (via the Preferences button or the notification
"Allow" action), so the user learns they were approved without having to probe
the bot again.

Every sender is best-effort: a missing token or a failed API call is logged
and swallowed, because the approval itself already succeeded — only invalid
arguments exit non-zero.

The QQ notice is an *active* C2C send, which QQ caps tightly (the official
docs list ~4 active single-chat messages per user per month). A one-time
approval notice fits that budget, but it is the same pool the quota-reset
notifications draw from.
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from collections.abc import Callable

from fluxion.config.settings import Settings
from fluxion.i18n import normalize_locale, resolve_locale, t

log = logging.getLogger("fluxion.channels.approval_notify")


def approval_notice_text(settings: Settings, *, locale: str = "", text: str = "") -> str:
    override = text.strip()
    if override:
        return override

    selected = normalize_locale(locale, default="")
    if selected not in {"zh", "en", "ja"}:
        selected = resolve_locale(
            mode=settings.locale_mode,
            fixed_locale=settings.ui_locale,
            context={},
        )
    return t(selected, "approval.allowed")


def _send_wechat(settings: Settings, user_id: str, text: str) -> bool:
    from fluxion.channels.wechat.context_store import ContextTokenStore
    from fluxion.channels.wechat.credential_store import CredentialStore
    from fluxion.channels.wechat.ilink_client import ILinkClient
    from fluxion.channels.wechat.models import MessageItem

    creds = CredentialStore(settings.data_dir).load()
    if creds is None:
        log.warning("WeChat approval notice skipped: credentials missing")
        return False

    context = ContextTokenStore(settings.data_dir).load_all().get(user_id) or {}
    context_token = str(context.get("context_token") or "").strip()
    if not context_token:
        log.warning("WeChat approval notice skipped: no context token for %s", user_id)
        return False

    client = ILinkClient()
    client.apply_credentials(creds)
    # Note: the iLink context token expires a few hours after the user's last
    # inbound message, so a long-delayed approval may fail here (logged below).
    client.send_message(
        to_user_id=user_id,
        context_token=context_token,
        items=[MessageItem.text(text)],
    )
    return True


def _send_telegram(settings: Settings, user_id: str, text: str) -> bool:
    from fluxion.channels.telegram.telegram_client import TelegramClient

    token = settings.telegram_bot_token
    if not token:
        log.warning("Telegram approval notice skipped: TELEGRAM_BOT_TOKEN missing")
        return False
    # For private chats the chat id equals the user id recorded at rejection.
    TelegramClient(token).send_message(chat_id=user_id, text=text)
    return True


def _send_slack(settings: Settings, user_id: str, text: str) -> bool:
    from slack_sdk import WebClient

    token = settings.slack_bot_token
    if not token:
        log.warning("Slack approval notice skipped: SLACK_BOT_TOKEN missing")
        return False
    client = WebClient(token=token)
    dm = client.conversations_open(users=[user_id])
    channel_id = str((dm.get("channel") or {}).get("id") or "")
    if not channel_id:
        raise RuntimeError(f"conversations.open returned no channel for {user_id}")
    client.chat_postMessage(channel=channel_id, text=text)
    return True


def _send_line(settings: Settings, user_id: str, text: str) -> bool:
    token = settings.line_channel_access_token
    if not token:
        log.warning("LINE approval notice skipped: LINE_CHANNEL_ACCESS_TOKEN missing")
        return False
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text}]}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    return True


def _send_feishu(settings: Settings, user_id: str, text: str) -> bool:
    from fluxion.channels.feishu.feishu_client import FeishuClient

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        log.warning("Feishu approval notice skipped: app credentials missing")
        return False
    # Pending users are recorded by open_id (see feishu/adapter.py).
    client = FeishuClient(settings.feishu_app_id, settings.feishu_app_secret)
    client.send_text(user_id, text, receive_id_type="open_id")
    return True


def _send_qqbot(settings: Settings, user_id: str, text: str) -> bool:
    from fluxion.channels.qqbot.qqbot_client import QQBotClient
    from fluxion.channels.qqbot.token_manager import QQBotTokenManager

    if not settings.qqbot_app_id or not settings.qqbot_client_secret:
        log.warning("QQ approval notice skipped: app credentials missing")
        return False
    tokens = QQBotTokenManager(
        app_id=settings.qqbot_app_id,
        client_secret=settings.qqbot_client_secret,
    )
    client = QQBotClient(tokens, sandbox=settings.qqbot_sandbox)
    # No msg_id: this is an active send, drawing from QQ's tight monthly
    # active-C2C quota (see module docstring).
    client.send_c2c_text(user_id, text)
    return True


SENDERS: dict[str, Callable[[Settings, str, str], bool]] = {
    "wechat": _send_wechat,
    "telegram": _send_telegram,
    "slack": _send_slack,
    "line": _send_line,
    "feishu": _send_feishu,
    "qqbot": _send_qqbot,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a best-effort approval notice to a newly allowlisted user."
    )
    parser.add_argument("--channel", required=True, choices=sorted(SENDERS))
    parser.add_argument("user_id")
    parser.add_argument("--locale", default="")
    parser.add_argument("--text", default="")
    args = parser.parse_args(argv)

    from fluxion.utils.logger import setup_logging

    settings = Settings.load()
    setup_logging(filename=settings.data_dir / "logs" / "fluxion.log")

    user_id = args.user_id.strip()
    if not user_id:
        log.warning("%s approval notice skipped: empty user id", args.channel)
        return 2

    notice_text = approval_notice_text(settings, locale=args.locale, text=args.text)
    try:
        if SENDERS[args.channel](settings, user_id, notice_text):
            log.info("Sent %s approval notice to %s", args.channel, user_id)
    except Exception:
        log.exception("Failed to send %s approval notice to %s", args.channel, user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
