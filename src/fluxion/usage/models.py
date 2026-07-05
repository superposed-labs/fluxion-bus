from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Provider probe outcomes. "ok" means at least one usage window was read;
# "unavailable" means the provider has no usable endpoint/credential (not an
# error — e.g. API-key-only Claude, or Antigravity); "error" means a probe was
# attempted but failed (network, auth, parse).
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"


@dataclass
class UsageWindow:
    """One rolling quota window reported by a provider (e.g. 5-hour, weekly)."""

    key: str  # stable id: "5h" | "7d" | "prompt" | "flow"
    label: str  # human label: "5-hour" | "Weekly" | "Prompt credits"
    used_percent: float | None = None  # 0..100, None when unknown
    resets_at: str | None = None  # ISO8601 timestamp, None when unknown
    window_minutes: int | None = None
    # Absolute balances, for credit-style providers (e.g. Antigravity). When
    # `total` is set the UI shows "remaining / total" instead of a reset clock.
    remaining: float | None = None
    total: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "used_percent": self.used_percent,
            "resets_at": self.resets_at,
            "window_minutes": self.window_minutes,
            "remaining": self.remaining,
            "total": self.total,
        }


@dataclass
class ProviderUsage:
    """Live quota snapshot for a single executor/provider."""

    provider: str  # "claude" | "codex" | "antigravity"
    status: str = STATUS_UNAVAILABLE
    account_label: str = ""  # plan / email / model — best effort
    windows: list[UsageWindow] = field(default_factory=list)
    fetched_at: str = ""  # ISO8601, when this snapshot was produced
    detail: str = ""  # human note: error reason or why unavailable
    resets: dict[str, Any] | None = None
    # Internal probe metadata used for observability. These fields are
    # intentionally omitted from to_dict() so the public usage API is stable.
    source: str = ""
    source_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "provider": self.provider,
            "status": self.status,
            "account_label": self.account_label,
            "windows": [w.to_dict() for w in self.windows],
            "fetched_at": self.fetched_at,
            "detail": self.detail,
        }
        if self.resets is not None:
            d["resets"] = self.resets
        return d
