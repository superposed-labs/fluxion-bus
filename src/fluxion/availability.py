from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.codex_command import CODEX_COMMAND_CANDIDATES, resolve_command
from fluxion.config.settings import Settings
from fluxion.usage.models import STATUS_OK
from fluxion.usage.probes import ProbeConfig, build_probe

PROVIDERS = ("claude", "codex", "antigravity")
COMMANDS = {"claude": "claude", "codex": "codex", "antigravity": "agy"}
COMMAND_CANDIDATES = {
    "claude": (
        "~/.local/bin/claude",
        "~/bin/claude",
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ),
    # ChatGPT.app (formerly Codex.app) bundles a CLI. Finder-launched apps have
    # a clean PATH, so probe both the current and legacy bundle paths directly.
    "codex": CODEX_COMMAND_CANDIDATES,
    "antigravity": ("~/.local/bin/agy", "/opt/homebrew/bin/agy", "/usr/local/bin/agy"),
}


@dataclass(frozen=True)
class Availability:
    status: str
    detail: str
    path: str = ""


def detect_all(settings: Settings) -> dict[str, Any]:
    probe_config = ProbeConfig(
        http_timeout_sec=3.0,
        claude_user_agent=settings.claude_code_user_agent,
        claude_usage_token=settings.claude_usage_token,
        claude_use_keychain=settings.claude_usage_keychain,
        claude_auto_refresh=False,
        codex_usage_mode=settings.codex_usage_mode,
        codex_usage_base_url=settings.codex_usage_base_url,
    )

    executors = {
        "claude": asdict(detect_executor("claude", configured_command=settings.claude_command)),
        "codex": asdict(detect_executor("codex")),
        "antigravity": asdict(
            detect_executor("antigravity", configured_command=settings.antigravity_command)
        ),
    }
    usage: dict[str, dict[str, str]] = {}
    for provider in PROVIDERS:
        probe = build_probe(provider, probe_config)
        try:
            result = probe.probe() if probe is not None else None
        except Exception as exc:  # noqa: BLE001 - detection must remain best effort
            usage[provider] = {"status": "error", "detail": f"Detection failed: {exc}"}
            continue
        if result is None:
            usage[provider] = {"status": "unavailable", "detail": "No usage probe available."}
        else:
            usage[provider] = {"status": result.status, "detail": result.detail}

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "executors": executors,
        "usage": usage,
    }


def detect_executor(provider: str, *, configured_command: str = "") -> Availability:
    command = COMMANDS[provider]
    if configured_command and configured_command != command:
        path = Path(configured_command).expanduser()
        resolved = resolve_command(configured_command, ())
        if resolved:
            return Availability(
                status="available", detail="Configured CLI command found.", path=resolved
            )
        return Availability(
            status="misconfigured",
            detail=f"Configured command is not executable: {configured_command}",
            path=str(path),
        )
    resolved = resolve_command(command, COMMAND_CANDIDATES[provider])
    if resolved:
        return Availability(status="available", detail="CLI command found.", path=resolved)
    return Availability(status="unavailable", detail=f"`{command}` command not found.")


def available_executors(settings: Settings) -> set[str]:
    """Providers whose executor CLI is installed right now.

    Cheap filesystem-only detection (``shutil.which`` + path probes), so it is
    safe to call on the run path. Does not touch usage probes or subprocesses.
    """
    available: set[str] = set()
    for provider in PROVIDERS:
        if provider == "claude":
            entry = detect_executor(provider, configured_command=settings.claude_command)
        elif provider == "antigravity":
            entry = detect_executor(provider, configured_command=settings.antigravity_command)
        else:
            entry = detect_executor(provider)
        if entry.status == "available":
            available.add(provider)
    return available


def detected_usage_providers(snapshot: dict[str, Any]) -> list[str]:
    usage = snapshot.get("usage", {})
    return [
        provider
        for provider in PROVIDERS
        if isinstance(usage.get(provider), dict) and usage[provider].get("status") == STATUS_OK
    ]


def detected_default_executor(snapshot: dict[str, Any]) -> str:
    executors = snapshot.get("executors", {})
    for provider in ("codex", "claude", "antigravity"):
        if (
            isinstance(executors.get(provider), dict)
            and executors[provider].get("status") == "available"
        ):
            return provider
    return "codex"


def write_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def initialize_env(snapshot: dict[str, Any], path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    additions: list[str] = []
    if "FLUXION_USAGE_PROVIDERS" not in existing:
        additions.append(f"FLUXION_USAGE_PROVIDERS={','.join(detected_usage_providers(snapshot))}")
    if "FLUXION_ENABLED_EXECUTORS" not in existing:
        enabled = [
            provider
            for provider in PROVIDERS
            if snapshot.get("executors", {}).get(provider, {}).get("status") == "available"
        ]
        additions.append(f"FLUXION_ENABLED_EXECUTORS={','.join(enabled or PROVIDERS)}")
    if "FLUXION_DEFAULT_EXECUTOR" not in existing:
        additions.append(f"FLUXION_DEFAULT_EXECUTOR={detected_default_executor(snapshot)}")
    if additions:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["# Auto-detected by Fluxion on first launch.", *additions])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return additions
