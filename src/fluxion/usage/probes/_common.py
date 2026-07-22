from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fluxion.usage.models import ProviderUsage
from fluxion.utils.logger import get_logger

logger = get_logger("fluxion.usage.probes")

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_API_BASE = "https://api.anthropic.com"
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Codex talks to the ChatGPT backend; `/wham/usage` returns the live rate-limit
# status that the Codex app shows. API-key auth uses `/api/codex/usage` instead.
CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_percent(value: Any) -> float | None:
    """Coerce a utilization value to a 0..100 percentage.

    Both Claude (`utilization`) and Codex (`used_percent`, an i32) report on a
    0..100 scale, so the value is taken as-is and clamped. (An earlier 0..1
    "fraction" heuristic wrongly turned a literal `1` = 1% into 100%.)
    """
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    return round(min(100.0, max(0.0, float(value))), 1)


def _normalize_reset(value: Any) -> str | None:
    """Accept an ISO8601 string or an epoch-seconds number; return ISO8601."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


@dataclass
class ProbeConfig:
    http_timeout_sec: float = 6.0
    claude_user_agent: str = "claude-code/2.0.0"
    claude_usage_token: str = ""
    claude_credentials_path: Path = field(
        default_factory=lambda: Path.home() / ".claude" / ".credentials.json"
    )
    claude_keychain_service: str = "Claude Code-credentials"
    # Opt-in: only read the OAuth token from the macOS Keychain when explicitly
    # enabled. Off by default so Fluxion never touches the system credential
    # store unless the user asks (env token / plaintext credentials file only).
    claude_use_keychain: bool = False
    # Opt-in: refreshes Claude Code OAuth credentials and writes the updated
    # access token back to the original source when the usage token expires.
    claude_auto_refresh: bool = False
    claude_refresh_url: str = CLAUDE_OAUTH_TOKEN_URL
    claude_client_id: str = CLAUDE_CODE_CLIENT_ID
    codex_sessions_dir: Path = field(default_factory=lambda: Path.home() / ".codex" / "sessions")
    codex_scan_files: int = 8
    codex_auth_path: Path = field(default_factory=lambda: Path.home() / ".codex" / "auth.json")
    codex_usage_base_url: str = ""  # empty → CODEX_DEFAULT_BASE_URL
    codex_user_agent: str = "codex-cli"
    codex_usage_mode: str = "auto"  # "auto" (live→logs) | "live" | "logs"
    antigravity_host: str = "127.0.0.1"
    antigravity_port: int = 0  # 0 → auto-discover from the running sidecar
    antigravity_csrf_token: str = ""  # "" → auto-discover from the process args
    antigravity_sidecar_path: Path = field(
        default_factory=lambda: Path(
            "/Applications/Antigravity.app/Contents/Resources/bin/language_server"
        )
    )


@runtime_checkable
class UsageProbe(Protocol):
    def provider(self) -> str: ...

    def probe(self) -> ProviderUsage: ...
