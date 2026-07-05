from __future__ import annotations

from datetime import UTC, datetime

from fluxion.scheduler.models import (
    ACTION_NOTIFY,
    Action,
    Policy,
    ScheduleRule,
    Trigger,
)
from fluxion.scheduler.store import ScheduleStore

MANAGED_BY = "autoping"
PROVIDERS = ("claude", "codex", "antigravity")
# Per-provider monitor scope: which windows to watch for a reset. The actions
# taken on a detected reset are global: notify (the IM toggles) and/or Auto Ping
# (FLUXION_AUTOPING_ENABLED). Managed rules carry ACTION_NOTIFY ("monitor"), so
# they never ping themselves and can coexist with a manual ping schedule on the
# same window — the daemon dedupes anchor pings per provider/window pool.
MODES = ("off", "5h", "7d", "both")
COOLDOWN_SEC = 600
MAX_RUNS_PER_DAY = 48


_WINDOWS = {
    "claude": {
        "5h": ("5h",),
        "7d": ("7d",),
    },
    "codex": {
        "5h": ("5h",),
        "7d": ("7d",),
    },
    "antigravity": {
        "5h": ("Gemini (5h)", "External Models (5h)", "Gemini"),
        "7d": ("Gemini (Weekly)", "External Models (Weekly)", "External Models"),
    },
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _rule_id(provider: str, window_key: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in window_key).strip("_")
    return f"managed_autoping_{provider}_{slug}"


def _mode_windows(provider: str, mode: str) -> tuple[str, ...]:
    groups = _WINDOWS[provider]
    if mode == "both":
        return groups["5h"] + groups["7d"]
    if mode in {"5h", "7d"}:
        return groups[mode]
    return ()


def _mode_from_windows(provider: str, windows: set[str]) -> str:
    five = set(_WINDOWS[provider]["5h"])
    weekly = set(_WINDOWS[provider]["7d"])
    if five <= windows and weekly <= windows:
        return "both"
    if five <= windows:
        return "5h"
    if weekly <= windows:
        return "7d"
    return "off"


def get_autoping_modes(store: ScheduleStore) -> dict[str, str]:
    enabled: dict[str, set[str]] = {provider: set() for provider in PROVIDERS}
    for rule in store.load_rules():
        if rule.managed_by == MANAGED_BY and rule.enabled and rule.trigger.provider in enabled:
            enabled[rule.trigger.provider].add(rule.trigger.window_key)
    return {
        provider: _mode_from_windows(provider, windows) for provider, windows in enabled.items()
    }


def set_autoping_mode(store: ScheduleStore, provider: str, mode: str) -> dict[str, str]:
    provider = provider.strip().lower()
    mode = mode.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider!r}")
    if mode not in MODES:
        raise ValueError(f"unknown auto-ping mode: {mode!r}")

    # Managed rules are monitor rules (ACTION_NOTIFY). On a detected reset the
    # daemon notifies (IM toggles) and, if FLUXION_AUTOPING_ENABLED, also pings.
    action_type = ACTION_NOTIFY

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        now = _now_iso()
        kept: list[ScheduleRule] = []
        existing: dict[str, ScheduleRule] = {}
        for rule in rules:
            if rule.managed_by == MANAGED_BY and rule.trigger.provider == provider:
                existing[rule.trigger.window_key] = rule
            else:
                kept.append(rule)

        for window_key in _mode_windows(provider, mode):
            label = "Monitor"
            rule = existing.get(window_key)
            if rule is None:
                rule = ScheduleRule(
                    id=_rule_id(provider, window_key),
                    name=f"{provider.title()} {label} ({window_key})",
                    enabled=True,
                    trigger=Trigger(
                        type="quota_refresh",
                        provider=provider,
                        window_key=window_key,
                    ),
                    action=Action(type=action_type, agent=provider),
                    policy=Policy(
                        cooldown_sec=COOLDOWN_SEC,
                        max_runs_per_day=MAX_RUNS_PER_DAY,
                    ),
                    managed_by=MANAGED_BY,
                    created_at=now,
                    updated_at=now,
                )
            else:
                rule.enabled = True
                rule.name = f"{provider.title()} {label} ({window_key})"
                rule.action = Action(type=action_type, agent=provider)
                rule.policy.cooldown_sec = COOLDOWN_SEC
                rule.policy.max_runs_per_day = MAX_RUNS_PER_DAY
                rule.updated_at = now
            kept.append(rule)
        return kept

    store.mutate_rules(mutate)
    return get_autoping_modes(store)
