from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class SessionState:
    conversation_key: str
    channel: str
    user_id: str
    active_task_id: str | None = None
    executor_sessions: dict[str, str] = field(default_factory=dict)
    # Per agy session_id high-water mark of the trajectory steps.idx already
    # attributed to a prior run, so a resumed session reports only the current
    # run's changed_files (see SessionManager.get/set_trajectory_idx).
    trajectory_idx: dict[str, int] = field(default_factory=dict)
    # Model each executor session was created with, keyed by executor session id.
    # An agent CLI resuming a conversation may keep the model that conversation
    # was started on, which decides the quota pool the run bills to — so a
    # resume that asks for a different model has to be flagged, not assumed.
    session_models: dict[str, str] = field(default_factory=dict)
    executor_override: str | None = None
    model_overrides: dict[str, str] = field(default_factory=dict)
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
