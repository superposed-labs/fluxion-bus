from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fluxion.usage.models import ProviderUsage, UsageWindow
from fluxion.usage.service import usage_service_from_settings
from fluxion.utils.logger import get_logger

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - Unix fallback
    msvcrt = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from fluxion.config.settings import Settings

logger = get_logger("fluxion.usage.collector")
_LOCAL_LOCK = threading.Lock()


def read_usage_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def payload_is_fresh(payload: dict[str, Any] | None, refresh_sec: int) -> bool:
    if not payload:
        return False
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        return False
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return datetime.now(UTC).timestamp() - timestamp < max(1, refresh_sec)


def payload_to_usage(payload: dict[str, Any] | None) -> list[ProviderUsage]:
    if not payload or not payload.get("enabled", True):
        return []
    usages: list[ProviderUsage] = []
    for provider in payload.get("providers", []):
        if not isinstance(provider, dict) or not isinstance(provider.get("provider"), str):
            continue
        windows = [
            UsageWindow(
                key=window.get("key", ""),
                label=window.get("label", ""),
                used_percent=window.get("used_percent"),
                resets_at=window.get("resets_at"),
                window_minutes=window.get("window_minutes"),
                remaining=window.get("remaining"),
                total=window.get("total"),
                currency=window.get("currency"),
                expires_at=window.get("expires_at"),
                enabled=window.get("enabled"),
            )
            for window in provider.get("windows", [])
            if isinstance(window, dict)
        ]
        usages.append(
            ProviderUsage(
                provider=provider["provider"],
                status=provider.get("status", "unavailable"),
                account_label=provider.get("account_label", ""),
                windows=windows,
                fetched_at=provider.get("fetched_at", ""),
                detail=provider.get("detail", ""),
                resets=provider.get("resets"),
            )
        )
    return usages


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not key.startswith("_")}


def _retired_entries(cached: dict[str, Any] | None, active: set[str]) -> list[dict[str, Any]]:
    """Last snapshots of providers absent from this run (i.e. currently
    disabled), carried forward so a re-enabled provider can render its old
    numbers instantly instead of flashing a loading/error card while the first
    probe runs. Stored under a private key: public payload consumers
    (scheduler quota watch, web API) must keep seeing only active providers.
    """
    if not cached:
        return []
    retained: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous = list(cached.get("providers", [])) + list(cached.get("_retired_providers", []))
    for entry in previous:
        if not isinstance(entry, dict):
            continue
        name = entry.get("provider")
        if not isinstance(name, str) or name in active or name in seen:
            continue
        seen.add(name)
        retained.append(entry)
    return retained


def _atomic_write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _collector_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK:
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                handle.seek(0)
                if not handle.read(1):
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def collect_usage_payload(
    settings: Settings,
    *,
    force: bool = False,
    force_providers: list[str] | None = None,
) -> dict[str, Any]:
    """Collect usage through the shared cache.

    Normal refreshes recheck the cache after taking the cross-process lock, so
    concurrent consumers collapse into one provider request. Forced refreshes
    deliberately always probe, including when another forced refresh just ran.
    """
    base = {"locale": settings.ui_locale, "generated_at": datetime.now(UTC).isoformat()}
    if not settings.usage_panel_enabled:
        return {"enabled": False, "providers": [], **base}

    cache_file = settings.data_dir / "usage_cache.json"
    lock_file = settings.data_dir / "usage_cache.lock"
    is_forced = force or bool(force_providers)
    cached = read_usage_payload(cache_file)
    if not is_forced and payload_is_fresh(cached, settings.usage_refresh_sec):
        return _public_payload(cached)

    with _collector_lock(lock_file):
        cached = read_usage_payload(cache_file)
        if not is_forced and payload_is_fresh(cached, settings.usage_refresh_sec):
            return _public_payload(cached)

        service = usage_service_from_settings(settings)
        providers = service.get_usage(
            force=force,
            force_providers=set(force_providers) if force_providers else None,
        )
        payload = {
            "enabled": True,
            "providers": [provider.to_dict() for provider in providers],
            "locale": settings.ui_locale,
            "generated_at": datetime.now(UTC).isoformat(),
            "_collector_state": service.collector_state(),
        }
        retained = _retired_entries(cached, {provider.provider for provider in providers})
        if retained:
            payload["_retired_providers"] = retained
        try:
            _atomic_write_payload(cache_file, payload)
        except OSError as exc:
            logger.warning("failed to write shared usage cache %s: %s", cache_file, exc)
        return _public_payload(payload)


class SharedUsageClient:
    """Usage reader for long-running components.

    Stale caches are refreshed by a short-lived CLI process so provider parsing
    always uses the code currently installed on disk.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._settings = settings
        self._cache_file = settings.data_dir / "usage_cache.json"
        self._runner = runner

    def get_usage(self, *, force: bool = False) -> list[ProviderUsage]:
        cached = read_usage_payload(self._cache_file)
        if not force and payload_is_fresh(cached, self._settings.usage_refresh_sec):
            return payload_to_usage(cached)

        command = [sys.executable, "-m", "fluxion.cli.usage"]
        if force:
            command.append("--force")
        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            payload = json.loads(result.stdout)
            if isinstance(payload, dict):
                return payload_to_usage(payload)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            logger.warning("shared usage collector failed; serving last cache: %s", exc)
        return payload_to_usage(read_usage_payload(self._cache_file) or cached)
