"""User-facing notification copy for scheduler events.

Builds the quota-reset notification once, in every channel dialect (Slack
Block Kit, Telegram Markdown, plain text, macOS), from the structured
`FireDecision` edge details. All copy goes through the i18n catalog so the
notification language follows FLUXION_UI_LOCALE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fluxion.i18n import t
from fluxion.scheduler.engine import FireDecision
from fluxion.scheduler.models import ScheduleRule

PROVIDER_EMOJI = {"claude": "🧡", "codex": "💜", "antigravity": "💙"}


@dataclass
class QuotaResetCopy:
    """The same notification rendered for each channel dialect."""

    slack_fallback: str  # plain text for notifications/clients without blocks
    slack_blocks: list[dict[str, Any]]
    telegram: str  # GitHub-Markdown; converted to HTML by the sender
    plain: str  # QQ / Feishu / WeChat / LINE
    macos_title: str
    macos_body: str


def _detail(locale: str, decision: FireDecision) -> str:
    """Human-readable description of how the reset was detected."""
    kind = decision.edge_kind
    if kind == "usage_dropped":
        data = decision.edge_data
        return t(
            locale,
            "quota_reset.reason.usage_dropped",
            prev=f"{data.get('prev_used', 0):.0f}",
            cur=f"{data.get('cur_used', 0):.0f}",
        )
    if kind in ("reset_advanced", "resets_cleared"):
        return t(locale, f"quota_reset.reason.{kind}")
    # No structured edge (e.g. a manual trigger) — fall back to the raw reason.
    return decision.reason


def build_quota_reset_copy(
    rule: ScheduleRule, decision: FireDecision, locale: str
) -> QuotaResetCopy:
    provider = rule.trigger.provider
    emoji = PROVIDER_EMOJI.get(provider.lower(), "🤖")
    prov_name = provider.title()
    window = rule.trigger.window_key or "N/A"

    title = t(locale, "quota_reset.title")
    detail = _detail(locale, decision)
    detected = t(locale, "quota_reset.detected_via", detail=detail)
    rule_line = t(locale, "quota_reset.rule", rule=rule.name)
    footer = t(locale, "quota_reset.footer")

    def headline(prov: str) -> str:
        return f"{emoji} " + t(locale, "quota_reset.headline", provider=prov, window=window)

    plain_headline = headline(prov_name)

    slack_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🔔 *{title}*\n{headline(f'*{prov_name}*')}",
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{detected} · {rule_line} · ⚡ {footer}"}],
        },
    ]

    telegram = (
        f"🔔 **[{title}]**\n"
        f"{headline(f'**{prov_name}**')}\n"
        f"{detected}\n"
        f"{rule_line}\n"
        f"\n⚡ _{footer}_"
    )

    plain = f"🔔 [{title}]\n{plain_headline}\n{detected}\n{rule_line}\n\n⚡ {footer}"

    return QuotaResetCopy(
        slack_fallback=f"🔔 [{title}] {plain_headline} ({rule_line})",
        slack_blocks=slack_blocks,
        telegram=telegram,
        plain=plain,
        macos_title=title,
        macos_body=plain_headline,
    )
