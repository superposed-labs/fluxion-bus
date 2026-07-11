from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

# Trigger types. quota_threshold is intentionally deferred to v2.
TRIGGER_CRON = "cron"
TRIGGER_QUOTA_REFRESH = "quota_refresh"
TRIGGER_TYPES = {TRIGGER_CRON, TRIGGER_QUOTA_REFRESH}

# Action types.
ACTION_SUBAGENT = "subagent"  # run a real sub-agent task
ACTION_PING = "ping"  # minimal read-only "say hello" to open a quota window
ACTION_NOTIFY = "notify"  # detect a quota reset and notify only — no ping
ACTION_TYPES = {ACTION_SUBAGENT, ACTION_PING, ACTION_NOTIFY}

# Catch-up policy for cron triggers when the daemon was down at fire time.
CATCH_UP_SKIP = "skip"  # do not run a missed occurrence late
CATCH_UP_RUN_ONCE = "run_once"  # run once on the next tick after downtime
CATCH_UP_MODES = {CATCH_UP_SKIP, CATCH_UP_RUN_ONCE}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO8601 string to an aware UTC datetime, or None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class Trigger:
    """When a rule fires. `type` selects which fields are meaningful."""

    type: str
    # type == cron
    cron: str = ""  # 5-field cron expression
    timezone: str = "UTC"
    # type == quota_refresh
    provider: str = ""  # claude | codex | antigravity
    window_key: str = ""  # "7d" | "5h" | "prompt" | "flow"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Trigger:
        return cls(
            type=str(raw.get("type", "")).strip(),
            cron=str(raw.get("cron", "")).strip(),
            timezone=str(raw.get("timezone", "UTC")).strip() or "UTC",
            provider=str(raw.get("provider", "")).strip(),
            window_key=str(raw.get("window_key", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "cron": self.cron,
            "timezone": self.timezone,
            "provider": self.provider,
            "window_key": self.window_key,
        }


@dataclass
class Action:
    """What a rule does when it fires."""

    type: str = ACTION_SUBAGENT
    agent: str = "auto"  # codex | claude | antigravity | auto
    prompt: str = ""
    project: str | None = None
    workspace: str = "."
    profile: str = "inspect"
    mode: str = "read-only"
    thread: str = "scheduler"
    task_name: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Action:
        project = raw.get("project")
        task_name = raw.get("task_name")
        return cls(
            type=str(raw.get("type", ACTION_SUBAGENT)).strip() or ACTION_SUBAGENT,
            agent=str(raw.get("agent", "auto")).strip() or "auto",
            prompt=str(raw.get("prompt", "")),
            project=str(project).strip() if project else None,
            workspace=str(raw.get("workspace", ".")).strip() or ".",
            profile=str(raw.get("profile", "inspect")).strip() or "inspect",
            mode=str(raw.get("mode", "read-only")).strip() or "read-only",
            thread=str(raw.get("thread", "scheduler")).strip() or "scheduler",
            task_name=str(task_name).strip() if task_name else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "agent": self.agent,
            "prompt": self.prompt,
            "project": self.project,
            "workspace": self.workspace,
            "profile": self.profile,
            "mode": self.mode,
            "thread": self.thread,
            "task_name": self.task_name,
        }


@dataclass
class Policy:
    """Safety rails. Edge-trigger + cooldown + daily cap prevent runaway fires."""

    cooldown_sec: int = 3600
    catch_up: str = CATCH_UP_SKIP
    max_runs_per_day: int = 24
    jitter_sec: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Policy:
        catch_up = str(raw.get("catch_up", CATCH_UP_SKIP)).strip() or CATCH_UP_SKIP
        if catch_up not in CATCH_UP_MODES:
            catch_up = CATCH_UP_SKIP
        return cls(
            cooldown_sec=max(0, int(raw.get("cooldown_sec", 3600) or 0)),
            catch_up=catch_up,
            max_runs_per_day=max(1, int(raw.get("max_runs_per_day", 24) or 1)),
            jitter_sec=max(0, int(raw.get("jitter_sec", 0) or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cooldown_sec": self.cooldown_sec,
            "catch_up": self.catch_up,
            "max_runs_per_day": self.max_runs_per_day,
            "jitter_sec": self.jitter_sec,
        }


@dataclass
class ScheduleRule:
    id: str
    name: str
    enabled: bool
    trigger: Trigger
    action: Action
    policy: Policy
    managed_by: str | None = None
    run_now: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        trigger: Trigger,
        action: Action,
        policy: Policy | None = None,
        enabled: bool = True,
    ) -> ScheduleRule:
        now = _now_iso()
        return cls(
            id=f"sch_{uuid4().hex[:8]}",
            name=name,
            enabled=enabled,
            trigger=trigger,
            action=action,
            policy=policy or Policy(),
            managed_by=None,
            run_now=False,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScheduleRule:
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name", "")),
            enabled=bool(raw.get("enabled", True)),
            trigger=Trigger.from_dict(raw.get("trigger", {})),
            action=Action.from_dict(raw.get("action", {})),
            policy=Policy.from_dict(raw.get("policy", {})),
            managed_by=str(raw.get("managed_by")).strip() if raw.get("managed_by") else None,
            run_now=bool(raw.get("run_now", False)),
            created_at=str(raw.get("created_at", "")) or _now_iso(),
            updated_at=str(raw.get("updated_at", "")) or _now_iso(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "trigger": self.trigger.to_dict(),
            "action": self.action.to_dict(),
            "policy": self.policy.to_dict(),
            "managed_by": self.managed_by,
            "run_now": self.run_now,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RuleState:
    """Runtime state the daemon owns. Kept apart from rule definitions so that
    editing a rule never clobbers its firing history or quota baseline."""

    last_fired_at: str | None = None
    last_eval_at: str | None = None
    runs_today: int = 0
    runs_today_date: str = ""
    # Last observed quota window for this rule, used to detect refresh edges.
    last_usage: dict[str, Any] | None = None
    # A refresh edge seen on the previous observation, awaiting confirmation on
    # the next one before it fires. Debounces upstream single-sample glitches
    # (e.g. a provider usage endpoint intermittently serving a stale "fresh
    # window" snapshot) that would otherwise look like a real reset for one poll.
    pending_refresh: dict[str, Any] | None = None
    # An observation whose resets_at moved backward vs `last_usage` — physically
    # impossible for a real window, so it's quarantined instead of adopted as
    # the baseline. Adopted only if the backward state persists a second
    # consecutive observation (see engine.advance_baseline).
    suspect_usage: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RuleState:
        last_usage = raw.get("last_usage")
        pending_refresh = raw.get("pending_refresh")
        suspect_usage = raw.get("suspect_usage")
        return cls(
            last_fired_at=raw.get("last_fired_at"),
            last_eval_at=raw.get("last_eval_at"),
            runs_today=int(raw.get("runs_today", 0) or 0),
            runs_today_date=str(raw.get("runs_today_date", "")),
            last_usage=dict(last_usage) if isinstance(last_usage, dict) else None,
            pending_refresh=dict(pending_refresh) if isinstance(pending_refresh, dict) else None,
            suspect_usage=dict(suspect_usage) if isinstance(suspect_usage, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_fired_at": self.last_fired_at,
            "last_eval_at": self.last_eval_at,
            "runs_today": self.runs_today,
            "runs_today_date": self.runs_today_date,
            "last_usage": self.last_usage,
            "pending_refresh": self.pending_refresh,
            "suspect_usage": self.suspect_usage,
        }
