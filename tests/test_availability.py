from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fluxion.availability import (
    Availability,
    available_executors,
    detect_executor,
    detected_default_executor,
    detected_usage_providers,
    initialize_env,
)


def test_detect_executor_uses_path() -> None:
    with patch("fluxion.codex_command.shutil.which", return_value="/opt/bin/codex"):
        result = detect_executor("codex")
    assert result.status == "available"
    assert result.path == "/opt/bin/codex"


@pytest.mark.parametrize(
    "bundled",
    [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ],
)
def test_detect_executor_finds_app_bundled_cli(bundled: str) -> None:

    def fake_access(path: object, _mode: int) -> bool:
        return str(path) == bundled

    with (
        patch("fluxion.codex_command.shutil.which", return_value=None),
        patch("fluxion.codex_command.Path.is_file", return_value=True),
        patch("fluxion.codex_command.os.access", side_effect=fake_access),
    ):
        result = detect_executor("codex")

    assert result.status == "available"
    assert result.path == bundled


def test_available_executors_returns_only_installed_providers() -> None:
    def fake_detect(provider: str, *, configured_command: str = "") -> Availability:
        if provider == "codex":
            return Availability(status="available", detail="found", path="/x/codex")
        return Availability(status="unavailable", detail="not found")

    class _Settings:
        claude_command = ""
        antigravity_command = ""

    with patch("fluxion.availability.detect_executor", side_effect=fake_detect):
        result = available_executors(_Settings())  # type: ignore[arg-type]

    assert result == {"codex"}


def test_detect_executor_reports_bad_configured_command() -> None:
    result = detect_executor("claude", configured_command="/missing/claude")
    assert result.status == "misconfigured"
    assert "not executable" in result.detail


def test_detected_defaults_keep_usage_and_executor_separate() -> None:
    snapshot = {
        "usage": {
            "claude": {"status": "ok"},
            "codex": {"status": "unavailable"},
            "antigravity": {"status": "error"},
        },
        "executors": {
            "claude": {"status": "unavailable"},
            "codex": {"status": "available"},
            "antigravity": {"status": "available"},
        },
    }
    assert detected_usage_providers(snapshot) == ["claude"]
    assert detected_default_executor(snapshot) == "codex"


def test_initialize_env_only_writes_missing_settings(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FLUXION_DEFAULT_EXECUTOR=claude\n", encoding="utf-8")
    snapshot = {
        "usage": {
            "claude": {"status": "ok"},
            "codex": {"status": "ok"},
            "antigravity": {"status": "unavailable"},
        },
        "executors": {},
    }

    additions = initialize_env(snapshot, env_path)

    assert additions == [
        "FLUXION_USAGE_PROVIDERS=claude,codex",
        "FLUXION_ENABLED_EXECUTORS=claude,codex,antigravity",
    ]
    text = env_path.read_text(encoding="utf-8")
    assert text.count("FLUXION_DEFAULT_EXECUTOR") == 1
    assert "FLUXION_USAGE_PROVIDERS=claude,codex" in text
    assert "FLUXION_ENABLED_EXECUTORS=claude,codex,antigravity" in text
