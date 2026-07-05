"""Provider quota / usage probes for the Fluxion web console.

Split per provider; this package re-exports the public surface so existing
``from fluxion.usage.probes import ...`` imports keep working.
"""

from __future__ import annotations

import sys  # noqa: F401 - kept importable so tests can patch ``probes.sys.platform``

from fluxion.usage.probes._common import (
    CLAUDE_CODE_CLIENT_ID,
    CLAUDE_OAUTH_BETA,
    CLAUDE_OAUTH_TOKEN_URL,
    CLAUDE_USAGE_URL,
    CODEX_DEFAULT_BASE_URL,
    ProbeConfig,
    UsageProbe,
    _normalize_percent,
    _normalize_reset,
    _now_iso,
)
from fluxion.usage.probes.antigravity import AntigravityUsageProbe
from fluxion.usage.probes.claude import ClaudeUsageProbe
from fluxion.usage.probes.codex import (
    CodexAccountUsage,
    CodexAccountUsageProbe,
    CodexUsageProbe,
)

__all__ = [
    "AntigravityUsageProbe",
    "ClaudeUsageProbe",
    "CodexAccountUsage",
    "CodexAccountUsageProbe",
    "CodexUsageProbe",
    "ProbeConfig",
    "UsageProbe",
    "build_probe",
]


_PROBE_BY_PROVIDER: dict[str, type] = {
    "claude": ClaudeUsageProbe,
    "codex": CodexUsageProbe,
    "antigravity": AntigravityUsageProbe,
}


def build_probe(provider: str, config: ProbeConfig) -> UsageProbe | None:
    cls = _PROBE_BY_PROVIDER.get(provider.strip().lower())
    if cls is None:
        return None
    return cls(config)
