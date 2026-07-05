from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fluxion.scheduler import cron
from fluxion.scheduler.autoping import (
    get_autoping_modes,
    set_autoping_mode,
)
from fluxion.scheduler.models import (
    ACTION_SUBAGENT,
    ACTION_TYPES,
    TRIGGER_CRON,
    TRIGGER_QUOTA_REFRESH,
    TRIGGER_TYPES,
    Action,
    Policy,
    ScheduleRule,
    Trigger,
)
from fluxion.web.deps import get_schedule_store

router = APIRouter()

_PROVIDERS = {"claude", "codex", "antigravity"}
_AGENTS = {"auto", "claude", "codex", "antigravity"}
_MODES = {"read-only", "workspace-write"}


# --- request bodies -------------------------------------------------------


class TriggerIn(BaseModel):
    type: str
    cron: str = ""
    timezone: str = "UTC"
    provider: str = ""
    window_key: str = ""


class ActionIn(BaseModel):
    type: str = ACTION_SUBAGENT
    agent: str = "auto"
    prompt: str = ""
    project: str | None = None
    workspace: str = "."
    profile: str = "inspect"
    mode: str = "read-only"
    thread: str = "scheduler"
    task_name: str | None = None


class PolicyIn(BaseModel):
    cooldown_sec: int = 3600
    catch_up: str = "skip"
    max_runs_per_day: int = 24
    jitter_sec: int = 0


class ScheduleIn(BaseModel):
    name: str = ""
    enabled: bool = True
    trigger: TriggerIn
    action: ActionIn
    policy: PolicyIn = Field(default_factory=PolicyIn)


class EnableIn(BaseModel):
    enabled: bool


class AutoPingIn(BaseModel):
    provider: str
    mode: str


# --- validation -----------------------------------------------------------


def _validate(trigger: Trigger, action: Action) -> None:
    if trigger.type not in TRIGGER_TYPES:
        raise HTTPException(400, f"unknown trigger type: {trigger.type!r}")
    if trigger.type == TRIGGER_CRON:
        if not trigger.cron.strip():
            raise HTTPException(400, "cron expression is required")
        try:
            cron.parse_cron(trigger.cron)
        except cron.CronError as exc:
            raise HTTPException(400, f"invalid cron: {exc}") from exc
        try:
            ZoneInfo(trigger.timezone or "UTC")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"invalid timezone: {trigger.timezone!r}") from exc
    if trigger.type == TRIGGER_QUOTA_REFRESH:
        if trigger.provider not in _PROVIDERS:
            raise HTTPException(400, f"invalid provider: {trigger.provider!r}")
        if not trigger.window_key.strip():
            raise HTTPException(400, "window_key is required for quota_refresh")

    if action.type not in ACTION_TYPES:
        raise HTTPException(400, f"unknown action type: {action.type!r}")
    if action.agent not in _AGENTS:
        raise HTTPException(400, f"invalid agent: {action.agent!r}")
    if action.mode not in _MODES:
        raise HTTPException(400, f"invalid mode: {action.mode!r}")
    if action.type == ACTION_SUBAGENT and not action.prompt.strip():
        raise HTTPException(400, "prompt is required for a subagent action")


def _dataclasses(body: ScheduleIn) -> tuple[Trigger, Action, Policy]:
    raw = body.model_dump()
    return (
        Trigger.from_dict(raw["trigger"]),
        Action.from_dict(raw["action"]),
        Policy.from_dict(raw["policy"]),
    )


def _validate_no_duplicate_ping_in(
    rules: list[ScheduleRule],
    trigger: Trigger,
    action: Action,
    *,
    exclude_id: str | None = None,
) -> None:
    if trigger.type != TRIGGER_QUOTA_REFRESH or action.type != "ping":
        return
    for rule in rules:
        if (
            rule.id != exclude_id
            and rule.enabled
            and rule.trigger.type == trigger.type
            and rule.trigger.provider == trigger.provider
            and rule.trigger.window_key == trigger.window_key
            and rule.action.type == action.type
        ):
            raise HTTPException(
                409,
                f"ping schedule already exists for {trigger.provider}/{trigger.window_key}",
            )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- routes ---------------------------------------------------------------


@router.get("/schedules")
def list_schedules() -> dict[str, list[dict[str, Any]]]:
    rules = get_schedule_store().load_rules()
    return {"schedules": [r.to_dict() for r in rules]}


@router.get("/autoping")
def get_autoping() -> dict[str, dict[str, str]]:
    return {"providers": get_autoping_modes(get_schedule_store())}


@router.put("/autoping")
def set_autoping(body: AutoPingIn) -> dict[str, dict[str, str]]:
    try:
        modes = set_autoping_mode(get_schedule_store(), body.provider, body.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"providers": modes}


@router.post("/schedules")
def create_schedule(body: ScheduleIn) -> dict[str, Any]:
    trigger, action, policy = _dataclasses(body)
    _validate(trigger, action)
    rule = ScheduleRule.new(
        name=body.name or "untitled",
        trigger=trigger,
        action=action,
        policy=policy,
        enabled=body.enabled,
    )

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        if rule.enabled:
            _validate_no_duplicate_ping_in(rules, trigger, action)
        return [*rules, rule]

    get_schedule_store().mutate_rules(mutate)
    return rule.to_dict()


@router.put("/schedules/{rule_id}")
def update_schedule(rule_id: str, body: ScheduleIn) -> dict[str, Any]:
    store = get_schedule_store()
    trigger, action, policy = _dataclasses(body)
    _validate(trigger, action)
    updated: ScheduleRule | None = None

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        nonlocal updated
        for i, existing in enumerate(rules):
            if existing.id != rule_id:
                continue
            if existing.managed_by:
                raise HTTPException(409, "managed schedule must be changed through its settings")
            if body.enabled:
                _validate_no_duplicate_ping_in(rules, trigger, action, exclude_id=rule_id)
            updated = ScheduleRule(
                id=existing.id,
                name=body.name or "untitled",
                enabled=body.enabled,
                trigger=trigger,
                action=action,
                policy=policy,
                created_at=existing.created_at,
                updated_at=_now_iso(),
            )
            rules[i] = updated
            return rules
        raise HTTPException(404, "schedule not found")

    store.mutate_rules(mutate)
    assert updated is not None
    return updated.to_dict()


@router.post("/schedules/{rule_id}/enable")
def set_enabled(rule_id: str, body: EnableIn) -> dict[str, Any]:
    store = get_schedule_store()
    updated: ScheduleRule | None = None

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        nonlocal updated
        for rule in rules:
            if rule.id != rule_id:
                continue
            if rule.managed_by:
                raise HTTPException(409, "managed schedule must be changed through its settings")
            if body.enabled:
                _validate_no_duplicate_ping_in(rules, rule.trigger, rule.action, exclude_id=rule_id)
            rule.enabled = body.enabled
            rule.updated_at = _now_iso()
            updated = rule
            return rules
        raise HTTPException(404, "schedule not found")

    store.mutate_rules(mutate)
    assert updated is not None
    return updated.to_dict()


@router.delete("/schedules/{rule_id}")
def delete_schedule(rule_id: str) -> dict[str, bool]:
    store = get_schedule_store()

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        for rule in rules:
            if rule.id != rule_id:
                continue
            if rule.managed_by:
                raise HTTPException(409, "managed schedule must be changed through its settings")
            return [candidate for candidate in rules if candidate.id != rule_id]
        raise HTTPException(404, "schedule not found")

    store.mutate_rules(mutate)
    return {"deleted": True}


@router.post("/schedules/{rule_id}/run")
def run_schedule(rule_id: str) -> dict[str, Any]:
    store = get_schedule_store()
    updated: ScheduleRule | None = None

    def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
        nonlocal updated
        for rule in rules:
            if rule.id != rule_id:
                continue
            if rule.managed_by:
                raise HTTPException(409, "managed schedule cannot be triggered manually")
            rule.run_now = True
            rule.updated_at = _now_iso()
            updated = rule
            return rules
        raise HTTPException(404, "schedule not found")

    store.mutate_rules(mutate)
    assert updated is not None
    return updated.to_dict()


@router.get("/schedule_runs")
def list_schedule_runs(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    runs = get_schedule_store().list_runs(limit=max(1, min(limit, 500)))
    return {"runs": runs}
