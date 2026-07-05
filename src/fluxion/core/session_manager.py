from __future__ import annotations

from datetime import UTC, datetime

from fluxion.core.models.session import SessionState
from fluxion.core.storage import JsonlStorage


class SessionManager:
    def __init__(
        self,
        *,
        storage: JsonlStorage | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._storage = storage
        self._restore_from_storage()

    def touch(self, *, conversation_key: str, channel: str, user_id: str) -> SessionState:
        state = self._sessions.get(conversation_key)
        if state is None:
            state = SessionState(
                conversation_key=conversation_key, channel=channel, user_id=user_id
            )
            self._sessions[conversation_key] = state
        state.last_seen_at = datetime.now(UTC)
        return state

    def set_active_task(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        task_id: str | None,
    ) -> None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        state.active_task_id = task_id

    def get_executor_session_id(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        executor_name: str,
    ) -> str | None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = executor_name.strip().lower()
        if not key:
            return None
        return state.executor_sessions.get(key)

    def set_executor_session_id(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        executor_name: str,
        session_id: str | None,
    ) -> None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = executor_name.strip().lower()
        if not key:
            return
        value = session_id.strip() if session_id else ""
        if value:
            state.executor_sessions[key] = value
        else:
            state.executor_sessions.pop(key, None)
        self._persist_state(state)

    def get_trajectory_idx(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        session_id: str,
    ) -> int:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = (session_id or "").strip()
        if not key:
            return -1
        # -1 = nothing counted yet, so `WHERE idx > -1` includes a 0-based first step.
        return state.trajectory_idx.get(key, -1)

    def set_trajectory_idx(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        session_id: str,
        idx: int,
    ) -> None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = (session_id or "").strip()
        if not key:
            return
        state.trajectory_idx[key] = int(idx)
        self._persist_state(state)

    def get_executor_override(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
    ) -> str | None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        return state.executor_override

    def set_executor_override(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        executor_name: str | None,
    ) -> None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        state.executor_override = executor_name.strip().lower() if executor_name else None
        self._persist_state(state)

    def get_model_override(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        executor_name: str,
    ) -> str | None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = executor_name.strip().lower()
        if not key:
            return None
        return state.model_overrides.get(key)

    def set_model_override(
        self,
        *,
        conversation_key: str,
        channel: str,
        user_id: str,
        executor_name: str,
        model: str | None,
    ) -> None:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        key = executor_name.strip().lower()
        if not key:
            return
        value = model.strip() if model else ""
        if value:
            state.model_overrides[key] = value
        else:
            state.model_overrides.pop(key, None)
        self._persist_state(state)

    def reset(self, *, conversation_key: str, channel: str, user_id: str) -> bool:
        state = self.touch(conversation_key=conversation_key, channel=channel, user_id=user_id)
        had = bool(
            state.executor_sessions
            or state.active_task_id
            or state.executor_override
            or state.model_overrides
        )
        state.active_task_id = None
        state.executor_sessions.clear()
        state.trajectory_idx.clear()
        state.executor_override = None
        state.model_overrides.clear()
        state.last_seen_at = datetime.now(UTC)
        self._persist_state(state)
        return had

    def _persist_state(self, state: SessionState) -> None:
        if self._storage is None:
            return
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "conversation_key": state.conversation_key,
            "channel": state.channel,
            "user_id": state.user_id,
            "active_task_id": state.active_task_id,
            "executor_sessions": dict(sorted(state.executor_sessions.items())),
            "trajectory_idx": dict(sorted(state.trajectory_idx.items())),
            "executor_override": state.executor_override,
            "model_overrides": dict(sorted(state.model_overrides.items())),
        }
        self._storage.append_session_event(payload)

    def _restore_from_storage(self) -> None:
        if self._storage is None:
            return
        latest = self._storage.load_latest_sessions()
        for conversation_key, payload in latest.items():
            channel = str(payload.get("channel", "")).strip()
            user_id = str(payload.get("user_id", "")).strip()
            if not channel or not user_id:
                continue
            state = SessionState(
                conversation_key=conversation_key,
                channel=channel,
                user_id=user_id,
                active_task_id=None,
                executor_override=payload.get("executor_override"),
            )
            raw_sessions = payload.get("executor_sessions")
            if isinstance(raw_sessions, dict):
                for name, value in raw_sessions.items():
                    key = str(name).strip().lower()
                    session_id = str(value).strip()
                    if key and session_id:
                        state.executor_sessions[key] = session_id
            raw_traj = payload.get("trajectory_idx")
            if isinstance(raw_traj, dict):
                for name, value in raw_traj.items():
                    sid = str(name).strip()
                    try:
                        idx_val = int(value)
                    except (TypeError, ValueError):
                        continue
                    if sid:
                        state.trajectory_idx[sid] = idx_val
            raw_models = payload.get("model_overrides")
            if isinstance(raw_models, dict):
                for name, value in raw_models.items():
                    key = str(name).strip().lower()
                    model = str(value).strip()
                    if key and model:
                        state.model_overrides[key] = model
            state.last_seen_at = datetime.now(UTC)
            self._sessions[conversation_key] = state
