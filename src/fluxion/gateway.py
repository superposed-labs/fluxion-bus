from __future__ import annotations

import logging
from collections.abc import Callable
from importlib import import_module
from typing import Any

from fluxion.config.settings import Settings
from fluxion.core.engine import GatewayCore
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage
from fluxion.executors.registry import build_enabled_executors
from fluxion.subagent import effective_default_executor
from fluxion.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _missing_channel_config(settings: Settings, channel: str) -> list[str]:
    if channel == "slack":
        missing = []
        if not settings.slack_bot_token:
            missing.append("SLACK_BOT_TOKEN")
        if not settings.slack_app_token:
            missing.append("SLACK_APP_TOKEN")
        if not settings.slack_signing_secret:
            missing.append("SLACK_SIGNING_SECRET")
        return missing
    if channel == "telegram":
        return [] if settings.telegram_bot_token else ["TELEGRAM_BOT_TOKEN"]
    if channel == "line":
        missing = []
        if not settings.line_channel_secret:
            missing.append("LINE_CHANNEL_SECRET")
        if not settings.line_channel_access_token:
            missing.append("LINE_CHANNEL_ACCESS_TOKEN")
        return missing
    if channel == "qqbot":
        missing = []
        if not settings.qqbot_app_id:
            missing.append("QQBOT_APP_ID")
        if not settings.qqbot_client_secret:
            missing.append("QQBOT_CLIENT_SECRET")
        return missing
    if channel == "feishu":
        missing = []
        if not settings.feishu_app_id:
            missing.append("FEISHU_APP_ID")
        if not settings.feishu_app_secret:
            missing.append("FEISHU_APP_SECRET")
        return missing
    return []


def _start_channel(
    *,
    name: str,
    settings: Settings,
    gateway: GatewayCore,
    factory: Callable[[], Any],
) -> bool:
    missing = _missing_channel_config(settings, name)
    if missing:
        logger.warning(
            "%s channel enabled but missing %s; skipping %s adapter.",
            name,
            ", ".join(missing),
            name,
        )
        return False
    try:
        adapter = factory()
        adapter.start(gateway)
    except Exception:
        logger.exception("%s channel adapter failed to start; skipping it.", name)
        return False
    return True


def _adapter_factory(module_name: str, class_name: str, settings: Settings) -> Callable[[], Any]:
    def factory() -> Any:
        module = import_module(module_name)
        return getattr(module, class_name)(settings)

    return factory


def main() -> None:
    settings = Settings.load()
    settings.validate()

    # Configure rotating file logging
    log_file = settings.data_dir / "logs" / "fluxion.log"
    setup_logging(filename=log_file)

    storage = JsonlStorage(settings.data_dir)
    sessions = SessionManager(storage=storage)
    executors = build_enabled_executors(settings)
    router = TaskRouter(executors=executors, default_executor=effective_default_executor(settings))
    gateway = GatewayCore(
        router=router,
        storage=storage,
        sessions=sessions,
        artifact_max_files=settings.artifact_max_files,
        worker_count=settings.worker_count,
        max_pending_per_user=settings.max_pending_per_user,
        max_retries=settings.max_retries,
        retry_backoff_sec=settings.retry_backoff_sec,
        change_detection=settings.change_detection,
        revert_capture=settings.revert_capture,
        change_set_max_file_bytes=settings.change_set_max_file_bytes,
        change_set_max_total_bytes=settings.change_set_max_total_bytes,
        typing_heartbeat_sec=settings.slack_typing_heartbeat_sec,
        running_update_sec=settings.slack_running_update_sec,
        settings=settings,
    )
    gateway.start()

    # Start non-blocking polling adapters first; their start() spawns a daemon
    # thread and returns. The Slack Socket Mode handler blocks, so it must run
    # last or the adapters after it would never start.
    if settings.wechat_enabled:
        _start_channel(
            name="wechat",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.wechat.adapter", "WeChatChannelAdapter", settings
            ),
        )

    if settings.telegram_enabled:
        _start_channel(
            name="telegram",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.telegram.adapter", "TelegramChannelAdapter", settings
            ),
        )

    if settings.line_enabled:
        _start_channel(
            name="line",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.line.adapter", "LineChannelAdapter", settings
            ),
        )

    if settings.qqbot_enabled:
        _start_channel(
            name="qqbot",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.qqbot.adapter", "QQBotChannelAdapter", settings
            ),
        )

    if settings.feishu_enabled:
        _start_channel(
            name="feishu",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.feishu.adapter", "FeishuChannelAdapter", settings
            ),
        )

    if settings.slack_enabled:
        # Blocking: the Socket Mode handler keeps the process alive while it
        # receives Slack events.
        slack_started = _start_channel(
            name="slack",
            settings=settings,
            gateway=gateway,
            factory=_adapter_factory(
                "fluxion.channels.slack.adapter", "SlackChannelAdapter", settings
            ),
        )
        if slack_started:
            return

    # No blocking adapter is running, or Slack failed/skipped. Keep the gateway
    # process alive so non-blocking adapters and future diagnostics remain up.
    import threading

    threading.Event().wait()


if __name__ == "__main__":
    main()
