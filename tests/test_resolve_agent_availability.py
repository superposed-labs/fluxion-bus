from __future__ import annotations

import pytest

from fluxion.subagent import resolve_agent


def _resolve(requested: str, **kwargs: object) -> str:
    return resolve_agent(
        requested=requested,
        project_default=kwargs.pop("project_default", ""),  # type: ignore[arg-type]
        settings_default=kwargs.pop("settings_default", "codex"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_ungated_behavior_is_unchanged_without_availability() -> None:
    # available_agents=None means availability is not gated at all.
    assert _resolve("claude", enabled_agents=["claude", "codex"]) == "claude"
    assert (
        _resolve("auto", enabled_agents=["claude", "codex"], settings_default="claude") == "claude"
    )


def test_auto_prefers_an_installed_executor() -> None:
    # settings default is codex but only claude is installed → auto picks claude.
    result = _resolve(
        "auto",
        enabled_agents=["claude", "codex"],
        settings_default="codex",
        available_agents={"claude"},
    )
    assert result == "claude"


def test_auto_falls_back_when_nothing_is_installed() -> None:
    # A fully empty availability sweep must not hard-block auto; it falls back
    # to the enabled preference order rather than raising.
    result = _resolve(
        "auto",
        enabled_agents=["codex", "claude"],
        settings_default="codex",
        available_agents=set(),
    )
    assert result == "codex"


def test_explicit_pick_of_uninstalled_executor_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unavailable sub-agent executor: claude"):
        _resolve(
            "claude",
            enabled_agents=["claude", "codex"],
            available_agents={"codex"},
        )


def test_explicit_pick_allowed_when_detection_found_nothing() -> None:
    # Empty availability set is treated as "detection unavailable", so an
    # explicit pick is allowed through rather than blocked by a false negative.
    result = _resolve(
        "claude",
        enabled_agents=["claude", "codex"],
        available_agents=set(),
    )
    assert result == "claude"


def test_disabled_still_beats_availability_check() -> None:
    # An executor that is not enabled reports as disabled, not unavailable.
    with pytest.raises(ValueError, match="Disabled sub-agent executor: claude"):
        _resolve(
            "claude",
            enabled_agents=["codex"],
            available_agents={"codex"},
        )
