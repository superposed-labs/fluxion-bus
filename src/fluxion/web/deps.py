from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fluxion.config.settings import Settings
from fluxion.scheduler.store import ScheduleStore
from fluxion.usage.collector import SharedUsageClient
from fluxion.usage.history import UsageHistoryService
from fluxion.usage.probes import CodexAccountUsageProbe, ProbeConfig


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings.load()
    settings.validate(require_slack=False)
    return settings


def get_data_dir() -> Path:
    return get_settings().data_dir


@lru_cache(maxsize=1)
def get_schedule_store() -> ScheduleStore:
    return ScheduleStore(get_settings().data_dir)


@lru_cache(maxsize=1)
def get_usage_service() -> SharedUsageClient:
    return SharedUsageClient(get_settings())


@lru_cache(maxsize=1)
def get_usage_history_service() -> UsageHistoryService:
    settings = get_settings()
    probe = None
    if settings.codex_history_reconciliation:
        probe = CodexAccountUsageProbe(
            ProbeConfig(codex_usage_base_url=settings.codex_usage_base_url)
        )
    return UsageHistoryService(
        db_path=settings.data_dir / "usage_stats.db",
        refresh_sec=settings.usage_refresh_sec,
        codex_account_usage_probe=probe,
    )
