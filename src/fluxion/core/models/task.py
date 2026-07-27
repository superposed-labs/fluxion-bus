from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fluxion.core.models.attachment import Attachment, ImageAttachment


@dataclass
class Task:
    id: str
    channel: str
    user_id: str
    text: str
    workspace: Path
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: tuple[Attachment, ...] = ()
    image_attachments: tuple[ImageAttachment, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        user_id: str,
        text: str,
        workspace: Path,
        metadata: dict[str, Any] | None = None,
        attachments: Sequence[Attachment] = (),
        image_attachments: Sequence[ImageAttachment] = (),
    ) -> Task:
        return cls(
            id=str(uuid4()),
            channel=channel,
            user_id=user_id,
            text=text.strip(),
            workspace=workspace,
            created_at=datetime.now(UTC),
            metadata=metadata or {},
            attachments=tuple(attachments),
            image_attachments=tuple(image_attachments),
        )
