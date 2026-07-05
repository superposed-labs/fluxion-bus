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

    def require_allowed(self) -> Path:
        if not self.allowed:
            raise ValueError(self.reason)
        return self.workspace
