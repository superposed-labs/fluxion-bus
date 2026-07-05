from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from fluxion.core.models.result import ExecutionResult
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)


class SettingsHotReloader:
    """Reload the active .env-backed Settings when the file changes.

    Channel adapters keep long-lived client connections, so this deliberately
    updates only the Settings snapshot used for authorization and routing. Client
    credentials and transport-level changes still require the channel's normal
    reconnect/restart path.
    """

    def __init__(self, *, channel: str) -> None:
        self._channel = channel
        self._env_file_path: Path | None = None
        self._last_env_mtime = 0.0
        self._init_env_file()

    def reload_if_changed(self, settings):  # noqa: ANN001
        path = self._env_file_path
        if path is None:
            path = self._resolve_env_file()
            self._env_file_path = path
        mtime = self._get_env_mtime(path)
        if mtime <= self._last_env_mtime:
            return settings

        try:
            from fluxion.config.settings import Settings

            new_settings = Settings.reload()
        except Exception:
            logger.exception("Failed to reload %s settings from %s", self._channel, path)
            self._last_env_mtime = mtime
            return settings

        self._last_env_mtime = mtime
        logger.info("Reloaded %s settings from %s", self._channel, path)
        return new_settings

    def _init_env_file(self) -> None:
        self._env_file_path = self._resolve_env_file()
        self._last_env_mtime = self._get_env_mtime(self._env_file_path)

    @staticmethod
    def _resolve_env_file() -> Path | None:
        from fluxion.config.settings import env_file_path

        return env_file_path()

    @staticmethod
    def _get_env_mtime(path: Path | None) -> float:
        if path is None:
            return 0.0
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


def authorize_inbound(*, channel: str, user_id: str, allowed_users: set[str]) -> tuple[bool, str]:
    """Fail-closed authorization for an inbound message.

    Deny-by-default: an empty allowlist rejects everyone. A channel that can
    drive agent execution against an authorized workspace must name exactly who
    may use it — otherwise anyone who can reach the bot on the platform could run
    tasks (and, in write modes, modify files) under the operator's identity. The
    empty case is almost always a misconfiguration, so it is logged at WARNING
    with the would-be user's id to make onboarding ("add this id") obvious.

    Returns ``(allowed, reason)``; ``reason`` is suitable to surface to the user.
    """
    if not user_id:
        return False, "missing user."
    if not allowed_users:
        logger.warning(
            "%s inbound rejected: FLUXION_%s_ALLOWED_USERS is empty, so fail-closed "
            "denies all users. Add user id %r to that allowlist to grant access.",
            channel,
            channel.upper(),
            user_id,
        )
        return False, "allowlist is not configured; access denied."
    if user_id not in allowed_users:
        return False, "user is not in allowlist."
    return True, ""


@runtime_checkable
class ChannelAdapter(Protocol):
    def start(self, gateway: GatewayCore) -> None: ...

    def send_status(
        self, task_id: str, status: str, context: dict, detail: str | None = None
    ) -> None: ...

    def send_result(self, task_id: str, result: ExecutionResult, context: dict) -> None: ...

    def send_typing(self, context: dict) -> None: ...

    def send_output_delta(self, task_id: str, text: str, context: dict) -> None: ...


# Avoid circular import at runtime while keeping type hints.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from fluxion.core.engine import GatewayCore
