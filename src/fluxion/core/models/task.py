from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class Task:
    id: str
    channel: str
    user_id: str
    text: str
    workspace: Path
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        user_id: str,
        text: str,
        workspace: Path,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        return cls(
            id=str(uuid4()),
            channel=channel,
            user_id=user_id,
            text=text.strip(),
            workspace=workspace,
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
