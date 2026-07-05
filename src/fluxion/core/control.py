from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ControlResponse:
    """Structured result for a handled control command.

    `text` is the channel-neutral human copy. Renderers decide whether to add a
    CLI prefix, markdown, or channel-specific polish.
    """

    kind: str
    text: str
    data: dict[str, Any] | None = None
