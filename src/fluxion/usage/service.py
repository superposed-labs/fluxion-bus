from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fluxion.usage.models import STATUS_ERROR, ProviderUsage, UsageWindow
from fluxion.usage.probes import ProbeConfig, build_probe
from fluxion.utils.logger import get_logger

logger = get_logger("fluxion.usage.service")

CLAUDE_MIN_REFRESH_SEC = 180.0
MAX_FAILURE_BACKOFF_SEC = 600.0

if TYPE_CHECKING:
    from fluxion.config.settings import Settings


class UsageService:
    """Probes provider quota with a TTL cache and last-good fallback.

    Probing on every UI request would hammer the providers (and risk 429s on
    the Claude endpoint), so successful snapshots are cached for `refresh_sec`.
    A lock prevents a thundering herd of concurrent /api/usage requests from all
    probing at once. When a refresh fails, the previous good snapshot is served
    (with its original timestamp) instead of flapping to an error.
    """

    def __init__(
        self,
        *,
        providers: list[str],
        probe_config: ProbeConfig,
        refresh_sec: int = 60,
        cache_file: Path | None = None,
    ) -> None:
        self._providers = [p for p in providers if build_probe(p, probe_config)]
        self._probe_config = probe_config
        self._refresh_sec = max(1, refresh_sec)
        self._cached_usages: dict[str, ProviderUsage] = {}
        self._last_good: dict[str, ProviderUsage] = {}
        self._next_probe_at: dict[str, float] = {}
        self._current_intervals: dict[str, float] = {
            p: float(self._refresh_sec) for p in self._providers
        }
        self._last_sources: dict[str, str] = {}
        self._antigravity_401_active = False
        self._lock = threading.Lock()
        self._cache_file = cache_file

        self._load_cache_file()

    def _load_cache_file(self) -> None:
        if not self._cache_file or not self._cache_file.exists():
            return
        try:
            with self._cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data.get("enabled", True):
                return

            # Retired snapshots (providers disabled at the time of the last
            # write) are read too: a just-re-enabled provider seeds from its
            # last-good data, so an immediate probe failure falls back to that
            # snapshot instead of surfacing an error card.
            provider_entries = list(data.get("providers", []))
            retired = data.get("_retired_providers", [])
            if isinstance(retired, list):
                provider_entries += retired
            for p_dict in provider_entries:
                if not isinstance(p_dict, dict):
                    continue
                p_name = p_dict.get("provider")
                if p_name not in self._providers:
                    continue

                windows = []
                for w_dict in p_dict.get("windows", []):
                    windows.append(
                        UsageWindow(
                            key=w_dict.get("key"),
                            label=w_dict.get("label"),
                            used_percent=w_dict.get("used_percent"),
                            resets_at=w_dict.get("resets_at"),
                            window_minutes=w_dict.get("window_minutes"),
                            remaining=w_dict.get("remaining"),
                            total=w_dict.get("total"),
                        )
                    )

                pu = ProviderUsage(
                    provider=p_name,
                    status=p_dict.get("status", "unavailable"),
                    account_label=p_dict.get("account_label", ""),
                    windows=windows,
                    fetched_at=p_dict.get("fetched_at", ""),
                    detail=p_dict.get("detail", ""),
                    resets=p_dict.get("resets") if isinstance(p_dict.get("resets"), dict) else None,
                )

                self._cached_usages[p_name] = pu
                if pu.status != STATUS_ERROR:
                    self._last_good[p_name] = pu

                fetched_at_str = p_dict.get("fetched_at")
                if fetched_at_str:
                    try:
                        clean_str = fetched_at_str.replace("Z", "+00:00")
                        fetched_at = datetime.fromisoformat(clean_str)
                        elapsed = datetime.now(UTC).timestamp() - fetched_at.timestamp()

                        if p_name == "claude":
                            base_interval = max(CLAUDE_MIN_REFRESH_SEC, float(self._refresh_sec))
                        else:
                            base_interval = float(self._refresh_sec)

                        self._next_probe_at[p_name] = time.monotonic() + max(
                            0.0, base_interval - elapsed
                        )
                        self._current_intervals[p_name] = base_interval
                    except Exception:
                        pass

            state = data.get("_collector_state", {})
            if isinstance(state, dict):
                now_wall = datetime.now(UTC)
                now_mono = time.monotonic()
                for provider, provider_state in state.items():
                    if provider not in self._providers or not isinstance(provider_state, dict):
                        continue
                    try:
                        interval = float(provider_state["interval_sec"])
                        next_allowed = datetime.fromisoformat(
                            str(provider_state["next_allowed_at"]).replace("Z", "+00:00")
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    remaining = max(0.0, (next_allowed - now_wall).total_seconds())
                    self._current_intervals[provider] = interval
                    self._next_probe_at[provider] = now_mono + remaining
        except Exception:
            pass

    def get_usage(
        self, *, force: bool = False, force_providers: set[str] | None = None
    ) -> list[ProviderUsage]:
        with self._lock:
            now = time.monotonic()
            for provider in self._providers:
                prov_force = force or (force_providers and provider in force_providers)
                if (
                    prov_force
                    or provider not in self._cached_usages
                    or now >= self._next_probe_at.get(provider, 0.0)
                ):
                    probe = build_probe(provider, self._probe_config)
                    if probe is None:
                        continue
                    try:
                        usage = probe.probe()
                        if usage.status == STATUS_ERROR:
                            logger.error(f"Usage probe for {provider} failed: {usage.detail}")
                    except Exception as exc:  # noqa: BLE001 - never let one provider break the rest
                        logger.exception(f"Usage probe for {provider} crashed")
                        usage = ProviderUsage(
                            provider=provider,
                            status=STATUS_ERROR,
                            detail=f"probe crashed: {exc}",
                        )

                    self._log_probe_source(provider, usage)

                    if usage.status == STATUS_ERROR:
                        self._current_intervals[provider] = min(
                            MAX_FAILURE_BACKOFF_SEC, self._current_intervals[provider] * 2.0
                        )
                    else:
                        if provider == "claude":
                            self._current_intervals[provider] = max(
                                CLAUDE_MIN_REFRESH_SEC, float(self._refresh_sec)
                            )
                        else:
                            self._current_intervals[provider] = float(self._refresh_sec)

                    self._next_probe_at[provider] = now + self._current_intervals[provider]
                    self._cached_usages[provider] = self._with_fallback(provider, usage)

            return [self._cached_usages[p] for p in self._providers if p in self._cached_usages]

    def collector_state(self) -> dict[str, dict[str, float | str]]:
        """Serialize cross-process probe timing without exposing monotonic time."""
        now_mono = time.monotonic()
        now_wall = datetime.now(UTC)
        state: dict[str, dict[str, float | str]] = {}
        for provider in self._providers:
            remaining = max(0.0, self._next_probe_at.get(provider, now_mono) - now_mono)
            state[provider] = {
                "interval_sec": self._current_intervals[provider],
                "next_allowed_at": (now_wall + timedelta(seconds=remaining)).isoformat(),
            }
        return state

    def _log_probe_source(self, provider: str, usage: ProviderUsage) -> None:
        if provider != "antigravity":
            return

        source = usage.source
        if source:
            previous = self._last_sources.get(provider)
            if previous and previous != source:
                logger.info("Antigravity quota source changed: %s -> %s", previous, source)
            elif previous is None:
                logger.info("Antigravity quota source selected: %s", source)
            self._last_sources[provider] = source

        is_401_fallback = source == "sidecar" and usage.source_reason == "cloud HTTP 401"
        if is_401_fallback and not self._antigravity_401_active:
            logger.warning(
                "Antigravity cloud quota API returned HTTP 401; falling back to local sidecar."
            )
        self._antigravity_401_active = is_401_fallback

    def _with_fallback(self, provider: str, usage: ProviderUsage) -> ProviderUsage:
        # Serve the previous good snapshot through transient errors so the panel
        # doesn't flap; remember fresh good snapshots for next time.
        if usage.status == STATUS_ERROR and provider in self._last_good:
            # Check if this is a hard auth/credential error. If so, do NOT use fallback.
            detail = (usage.detail or "").lower()
            is_auth_error = (
                "401" in detail
                or "auth" in detail
                or "credential" in detail
                or "unauthorized" in detail
            )

            # Check how old the last good cache is.
            last_good = self._last_good[provider]
            is_stale = False
            if last_good.fetched_at:
                try:
                    clean_str = last_good.fetched_at.replace("Z", "+00:00")
                    fetched_time = datetime.fromisoformat(clean_str)
                    age_seconds = datetime.now(UTC).timestamp() - fetched_time.timestamp()
                    # If older than 30 minutes, consider it stale and show the error.
                    if age_seconds > 1800:
                        is_stale = True
                except Exception:
                    pass

            if not is_auth_error and not is_stale:
                return last_good

        # Codex quota and reset credits come from independent endpoints. If
        # only the optional reset-credit request failed, keep the last confirmed
        # count while accepting the fresh quota windows. A successful response
        # with count=0 is represented by a non-nil resets payload and clears the
        # old value normally.
        if provider == "codex" and usage.resets_fetch_failed:
            previous = self._last_good.get(provider)
            if previous is not None and previous.resets is not None:
                usage.resets = previous.resets

        if usage.status != STATUS_ERROR and usage.windows:
            self._last_good[provider] = usage
        return usage


def usage_service_from_settings(settings: Settings) -> UsageService:
    """Build a UsageService from a loaded Settings — the single place that maps
    config onto the probes, shared by the web API and the CLI."""
    probe_config = ProbeConfig(
        claude_user_agent=settings.claude_code_user_agent,
        claude_usage_token=settings.claude_usage_token,
        claude_use_keychain=settings.claude_usage_keychain,
        claude_auto_refresh=settings.claude_usage_auto_refresh,
        codex_usage_mode=settings.codex_usage_mode,
        codex_usage_base_url=settings.codex_usage_base_url,
        antigravity_group_models=settings.antigravity_group_models,
    )
    if settings.antigravity_usage_models:
        probe_config.antigravity_models = settings.antigravity_usage_models
    return UsageService(
        providers=settings.usage_providers,
        probe_config=probe_config,
        refresh_sec=settings.usage_refresh_sec,
        cache_file=settings.data_dir / "usage_cache.json",
    )
