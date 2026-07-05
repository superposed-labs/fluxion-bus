from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from fluxion.config.settings import Settings
from fluxion.gateway import _missing_channel_config, _start_channel


def _settings(**overrides):
    values = {
        "slack_bot_token": "",
        "slack_app_token": "",
        "slack_signing_secret": "",
        "telegram_bot_token": "",
        "line_channel_secret": "",
        "line_channel_access_token": "",
        "qqbot_app_id": "",
        "qqbot_client_secret": "",
        "feishu_app_id": "",
        "feishu_app_secret": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_channel_config_reports_im_credentials() -> None:
    settings = _settings()

    assert _missing_channel_config(settings, "slack") == [
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_SIGNING_SECRET",
    ]
    assert _missing_channel_config(settings, "telegram") == ["TELEGRAM_BOT_TOKEN"]
    assert _missing_channel_config(settings, "line") == [
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
    ]
    assert _missing_channel_config(settings, "qqbot") == [
        "QQBOT_APP_ID",
        "QQBOT_CLIENT_SECRET",
    ]
    assert _missing_channel_config(settings, "feishu") == [
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
    ]
    assert _missing_channel_config(settings, "wechat") == []


def test_start_channel_skips_missing_config_without_calling_factory(caplog) -> None:
    called = False

    def factory():
        nonlocal called
        called = True
        raise AssertionError("factory should not be called")

    with caplog.at_level(logging.WARNING, logger="fluxion.gateway"):
        started = _start_channel(
            name="line",
            settings=_settings(),
            gateway=object(),  # type: ignore[arg-type]
            factory=factory,
        )

    assert started is False
    assert called is False
    assert "LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN" in caplog.text


def test_start_channel_isolates_adapter_start_failure(caplog) -> None:
    class BrokenAdapter:
        def start(self, gateway) -> None:
            raise RuntimeError("bad token")

    with caplog.at_level(logging.ERROR, logger="fluxion.gateway"):
        started = _start_channel(
            name="telegram",
            settings=_settings(telegram_bot_token="token"),
            gateway=object(),  # type: ignore[arg-type]
            factory=BrokenAdapter,
        )

    assert started is False
    assert "telegram channel adapter failed to start; skipping it." in caplog.text


def test_start_channel_starts_configured_adapter() -> None:
    started_with = None

    class Adapter:
        def start(self, gateway) -> None:
            nonlocal started_with
            started_with = gateway

    gateway = object()

    assert (
        _start_channel(
            name="line",
            settings=_settings(
                line_channel_secret="secret",
                line_channel_access_token="token",
            ),
            gateway=gateway,  # type: ignore[arg-type]
            factory=Adapter,
        )
        is True
    )
    assert started_with is gateway


def test_settings_validate_does_not_fail_on_incomplete_im_channels(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"FLUXION_WORKSPACE_ROOT={tmp_path}",
                f"FLUXION_ALLOWED_WORKSPACES={tmp_path}",
                "FLUXION_SLACK_ENABLED=true",
                "FLUXION_TELEGRAM_ENABLED=true",
                "FLUXION_LINE_ENABLED=true",
                "FLUXION_QQBOT_ENABLED=true",
                "FLUXION_FEISHU_ENABLED=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_path))
    monkeypatch.setenv("FLUXION_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLUXION_ALLOWED_WORKSPACES", str(tmp_path))
    for key in (
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_SIGNING_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "QQBOT_APP_ID",
        "QQBOT_CLIENT_SECRET",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    Settings.load().validate()
