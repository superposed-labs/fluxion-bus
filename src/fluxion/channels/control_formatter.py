from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fluxion.core.control import ControlResponse

_MARKDOWN_CHANNELS = {"slack", "telegram", "feishu", "qqbot"}
_CHAT_CHANNELS = _MARKDOWN_CHANNELS | {"wechat", "line"}


def render_control_response(response: ControlResponse | str, *, channel: str) -> str:
    """Render a structured control response for one output channel."""

    if isinstance(response, ControlResponse):
        text = response.text.strip()
        kind = response.kind
        data = response.data or {}
    else:
        text = re.sub(r"^\[Fluxion\]\s*", "", response.strip())
        kind = "usage" if text.startswith("Current Subscription Usage / Quota:") else "text"
        data = {}

    if kind == "usage":
        text = (
            _render_usage_payload(data, channel=channel)
            if data
            else _format_usage_response(text, channel=channel)
        )

    if channel not in _CHAT_CHANNELS:
        return _with_fluxion_prefix(text)
    return text


def format_control_response(response: ControlResponse | str, *, channel: str) -> str:
    """Backward-compatible alias for chat adapters."""

    return render_control_response(response, channel=channel)


def _with_fluxion_prefix(text: str) -> str:
    if not text or text.startswith("[Fluxion]"):
        return text
    return "[Fluxion] " + text


def _format_usage_response(text: str, *, channel: str) -> str:
    markdown = channel in _MARKDOWN_CHANNELS
    lines: list[str] = []
    for index, line in enumerate(text.splitlines()):
        if index == 0 and line.rstrip(":") == "Current Subscription Usage / Quota":
            line = _usage_title(markdown=markdown)
            lines.append(line)
            continue
        provider = re.match(r"^\*?(.+?)\s+\[Status:\s*([^\]]+)\]\*?$", line)
        if provider:
            line = _usage_provider_title(
                name=provider.group(1),
                account_label="",
                status=provider.group(2),
                markdown=markdown,
            )
        elif line.startswith("- "):
            line = "• " + line[2:]
            line = line.replace(" (Resets ", " · resets ").removesuffix(")")
        lines.append(line)
    return "\n".join(lines)


def _render_usage_payload(payload: dict[str, Any], *, channel: str) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error:
        return error

    providers = payload.get("providers")
    if not isinstance(providers, list) or not providers:
        return "No quota data found in cache."

    markdown = channel in _MARKDOWN_CHANNELS
    lines = [_usage_title(markdown=markdown)]
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("provider") or "unknown")
        label = str(provider.get("account_label") or "")
        status = str(provider.get("status") or "ok").upper()
        lines.append("")
        lines.append(
            _usage_provider_title(
                name=name,
                account_label=label,
                status=status,
                markdown=markdown,
            )
        )

        windows = provider.get("windows")
        if not isinstance(windows, list):
            continue
        for window in windows:
            if not isinstance(window, dict):
                continue
            lines.append(_render_usage_window(window))
    return "\n".join(lines)


def _render_usage_window(window: dict[str, Any]) -> str:
    label = str(window.get("label") or window.get("key") or "unknown")
    used = window.get("used_percent")
    used_text = f"{used:.1f}%" if isinstance(used, int | float) else "unknown"
    reset_text = _reset_text(window.get("resets_at"))
    suffix = f" · {reset_text}" if reset_text else ""
    return f"• {label}: {used_text}{suffix}"


def _usage_title(*, markdown: bool) -> str:
    title = "Current Subscription Usage / Quota"
    return f"*{title}*" if markdown else title


def _usage_provider_title(
    *,
    name: str,
    account_label: str,
    status: str,
    markdown: bool,
) -> str:
    provider = _display_provider_name(name)
    title_parts = [provider]
    if account_label:
        title_parts.append(account_label)
    title_parts.append(status.upper())
    title = " · ".join(title_parts)
    return f"*{title}*" if markdown else title


def _display_provider_name(name: str) -> str:
    value = (name or "unknown").strip()
    names = {
        "claude": "Claude",
        "codex": "Codex",
        "antigravity": "Antigravity",
    }
    return names.get(value.lower(), value)


def _reset_text(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff_sec = int((dt - now).total_seconds())
        return _format_reset_duration(diff_sec)
    except Exception:
        return f"resets at {raw}"


def _format_reset_duration(diff_sec: int) -> str:
    if diff_sec <= 0:
        return "resetting now"

    days, day_remainder = divmod(diff_sec, 86400)
    if days > 0:
        hours = day_remainder // 3600
        return f"resets in {days}d {hours}h" if hours > 0 else f"resets in {days}d"

    hours, hour_remainder = divmod(diff_sec, 3600)
    mins = hour_remainder // 60
    if hours > 0:
        return f"resets in {hours}h {mins}m"
    return f"resets in {mins}m"
