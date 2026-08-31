from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_EXECUTORS = {"codex", "claude", "antigravity"}


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    workspace: Path
    default_executor: str = ""
    description: str = ""

    def to_public_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "workspace": str(self.workspace),
            "default_executor": self.default_executor,
            "description": self.description,
        }


@dataclass(frozen=True)
class WorkspaceAuthorization:
    allowed: bool
    reason: str
    policy: str
    workspace: Path
    access: str = "read-only"
    source: str = ""
    authorization_request_id: str = ""
    pending: bool = False
    pending_status: str = ""
    client_id: str = ""
    default_executor: str = ""
    # A task-scoped approval is activated only after the exact retry reaches
    # the task gate.  The runner binds this grant to the created task and
    # retires it when the task publishes its terminal result.
    authorization_grant_id: str = ""
    authorization_scope: str = ""
    authorization_expires_at: str = ""

    def require_allowed(self) -> Path:
        if not self.allowed:
            raise WorkspaceAuthorizationError(self)
        return self.workspace


class WorkspaceAuthorizationError(ValueError):
    """A run was rejected because its workspace authorization was insufficient.

    Keeping the structured authorization on the exception lets MCP and Web
    callers return a retryable request id without parsing a human-readable
    error string.
    """

    def __init__(self, authorization: WorkspaceAuthorization) -> None:
        self.authorization = authorization
        super().__init__(authorization.reason)
