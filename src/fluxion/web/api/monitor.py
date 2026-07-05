from __future__ import annotations

import platform

from fastapi import APIRouter
from pydantic import BaseModel

from fluxion.config.settings import Settings, update_env_values

router = APIRouter()

# Global "on reset, do" actions for the Quota Monitor. The per-provider watch
# scope lives in the managed schedule rules (see /api/autoping); these are the
# global actions taken when any watched window resets — kept in sync with the
# macOS app's Preferences (same .env keys).
_AUTOPING_ENV = "FLUXION_AUTOPING_ENABLED"
_NOTIFY_ENV: dict[str, str] = {
    "slack": "FLUXION_MENU_SLACK_NOTIFY_REFRESH",
    "telegram": "FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH",
    "qqbot": "FLUXION_MENU_QQBOT_NOTIFY_REFRESH",
    "feishu": "FLUXION_MENU_FEISHU_NOTIFY_REFRESH",
    "wechat": "FLUXION_MENU_WECHAT_NOTIFY_REFRESH",
    "line": "FLUXION_MENU_LINE_NOTIFY_REFRESH",
}
_NOTIFY_ATTR: dict[str, str] = {
    "slack": "menu_slack_notify_refresh",
    "telegram": "menu_telegram_notify_refresh",
    "qqbot": "menu_qqbot_notify_refresh",
    "feishu": "menu_feishu_notify_refresh",
    "wechat": "menu_wechat_notify_refresh",
    "line": "menu_line_notify_refresh",
}
_CHANNEL_LABELS: dict[str, str] = {
    "slack": "Slack",
    "telegram": "Telegram",
    "wechat": "WeChat",
    "line": "LINE",
    "qqbot": "QQ",
    "feishu": "Feishu",
}


def _first(users: object) -> str:
    if isinstance(users, (set, frozenset, list, tuple)):
        return next(iter(sorted(str(u) for u in users)), "")
    return ""


def _channel_targets(settings: Settings) -> dict[str, dict[str, object]]:
    """Per-channel connection state + a human send-to target, so the console
    can show "Slack #fluxion-alerts" / "Telegram @username" and gray out channels
    that aren't wired up — mirroring the macOS app's channel config."""
    slack_target = (settings.scheduler_slack_channel or "").strip()
    return {
        "slack": {
            "label": _CHANNEL_LABELS["slack"],
            "connected": settings.slack_enabled,
            "target": slack_target,
        },
        "telegram": {
            "label": _CHANNEL_LABELS["telegram"],
            "connected": settings.telegram_enabled,
            "target": _first(settings.telegram_allowed_users),
        },
        "wechat": {
            "label": _CHANNEL_LABELS["wechat"],
            "connected": settings.wechat_enabled,
            "target": _first(settings.wechat_allowed_users),
        },
        "line": {
            "label": _CHANNEL_LABELS["line"],
            "connected": settings.line_enabled,
            "target": _first(settings.line_allowed_users),
        },
        "qqbot": {
            "label": _CHANNEL_LABELS["qqbot"],
            "connected": settings.qqbot_enabled,
            "target": _first(settings.qqbot_allowed_users),
        },
        "feishu": {
            "label": _CHANNEL_LABELS["feishu"],
            "connected": settings.feishu_enabled,
            "target": _first(settings.feishu_allowed_users),
        },
    }


class NotifyIn(BaseModel):
    slack: bool | None = None
    telegram: bool | None = None
    qqbot: bool | None = None
    feishu: bool | None = None
    wechat: bool | None = None
    line: bool | None = None


class MonitorIn(BaseModel):
    auto_ping: bool | None = None
    notify: NotifyIn | None = None
    notify_credit_grant: bool | None = None
    notify_credit_expiry: bool | None = None


def _read_state() -> dict[str, object]:
    # Re-read the .env so edits made by the macOS app or daemon are reflected,
    # rather than the web process's startup snapshot.
    settings = Settings.reload()
    return {
        "auto_ping": settings.autoping_enabled,
        "notify_credit_grant": settings.notify_credit_grant,
        "notify_credit_expiry": settings.notify_credit_expiry,
        "notify": {channel: getattr(settings, attr) for channel, attr in _NOTIFY_ATTR.items()},
        "channels": _channel_targets(settings),
        # The companion desktop app is macOS-only; the console tailors the
        # "synced with the macOS app" copy to whether this gateway runs on macOS.
        "host_os": "macos" if platform.system() == "Darwin" else "other",
    }


@router.get("/monitor")
def get_monitor() -> dict[str, object]:
    return _read_state()


@router.put("/monitor")
def set_monitor(body: MonitorIn) -> dict[str, object]:
    updates: dict[str, str] = {}
    if body.auto_ping is not None:
        updates[_AUTOPING_ENV] = "true" if body.auto_ping else "false"
    if body.notify_credit_grant is not None:
        updates["FLUXION_NOTIFY_CREDIT_GRANT"] = "true" if body.notify_credit_grant else "false"
    if body.notify_credit_expiry is not None:
        updates["FLUXION_NOTIFY_CREDIT_EXPIRY"] = "true" if body.notify_credit_expiry else "false"
    if body.notify is not None:
        for channel, env_key in _NOTIFY_ENV.items():
            value = getattr(body.notify, channel)
            if value is not None:
                updates[env_key] = "true" if value else "false"
    if updates:
        update_env_values(updates)
    return _read_state()
