from fluxion.channels.control_formatter import format_control_response
from fluxion.core.control import ControlResponse


def test_control_formatter_strips_fluxion_prefix_for_chat_channels():
    assert (
        format_control_response("[Fluxion] Conversation memory cleared.", channel="telegram")
        == "Conversation memory cleared."
    )


def test_control_formatter_renders_structured_response_for_cli():
    response = ControlResponse(kind="ping", text="pong")

    assert format_control_response(response, channel="cli") == "[Fluxion] pong"


def test_control_formatter_formats_structured_usage_for_markdown_channels():
    response = ControlResponse(
        kind="usage",
        text="",
        data={
            "providers": [
                {
                    "provider": "codex",
                    "status": "ok",
                    "account_label": "plus",
                    "windows": [{"key": "7d", "label": "Weekly", "used_percent": 7.0}],
                }
            ]
        },
    )

    assert format_control_response(response, channel="slack") == (
        "*Current Subscription Usage / Quota*\n\n*Codex · plus · OK*\n• Weekly: 7.0%"
    )


def test_control_formatter_formats_structured_usage_for_plain_text_channels():
    response = ControlResponse(
        kind="usage",
        text="",
        data={
            "providers": [
                {
                    "provider": "codex",
                    "status": "ok",
                    "account_label": "plus",
                    "windows": [{"key": "7d", "label": "Weekly", "used_percent": 7.0}],
                }
            ]
        },
    )

    assert format_control_response(response, channel="wechat") == (
        "Current Subscription Usage / Quota\n\nCodex · plus · OK\n• Weekly: 7.0%"
    )


def test_control_formatter_keeps_legacy_usage_text_compatibility():
    source = (
        "[Fluxion] Current Subscription Usage / Quota:\n\n"
        "*CODEX (plus) [Status: OK]*\n"
        "- Weekly: 7.0% (Resets in 6d 3h)"
    )

    assert "• Weekly: 7.0% · resets in 6d 3h" in format_control_response(source, channel="wechat")


def test_control_formatter_formats_structured_usage_provider_parts():
    response = ControlResponse(
        kind="usage",
        text="",
        data={
            "providers": [
                {
                    "provider": "codex",
                    "status": "ok",
                    "account_label": "plus",
                    "windows": [{"key": "7d", "label": "Weekly", "used_percent": 7.0}],
                }
            ]
        },
    )

    assert "*Codex · plus · OK*" in format_control_response(response, channel="slack")
