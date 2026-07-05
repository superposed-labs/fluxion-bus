from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from fluxion.availability import PROVIDERS, detect_executor
from fluxion.config.settings import Settings

router = APIRouter()


def _configured_executor_names(settings: Settings) -> list[str]:
    enabled = {name.strip() for name in settings.enabled_executors}
    ordered = [provider for provider in PROVIDERS if provider in enabled]
    return ordered or [
        settings.default_executor if settings.default_executor in PROVIDERS else "codex"
    ]


def _detect_configured(settings: Settings, provider: str) -> dict[str, str]:
    if provider == "claude":
        return asdict(detect_executor(provider, configured_command=settings.claude_command))
    if provider == "antigravity":
        return asdict(detect_executor(provider, configured_command=settings.antigravity_command))
    return asdict(detect_executor(provider))


@router.get("/executors")
def get_executors() -> dict[str, Any]:
    # Reload from the env file so UI-only config changes such as usage providers
    # are reflected without bouncing the web process.
    settings = Settings.reload()
    names = _configured_executor_names(settings)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "default_executor": settings.default_executor,
        "enabled_executors": settings.enabled_executors,
        "usage_providers": settings.usage_providers,
        "executors": [
            {
                "name": name,
                "default": name == settings.default_executor,
                "usage_enabled": name in settings.usage_providers,
                **_detect_configured(settings, name),
            }
            for name in names
        ],
    }
