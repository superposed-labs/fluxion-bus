"""Typed task attachments shared by gateways and executors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class Attachment:
    """A bounded protocol attachment materialized inside the task workspace."""

    path: Path
    media_type: str
    sha256: str
    byte_size: int
    ordinal: int = 1


@dataclass(frozen=True, kw_only=True)
class ImageAttachment(Attachment):
    """An image fully validated for an executor's native image interface."""

    width: int
    height: int
    frame_count: int = 1

    @property
    def pixel_count(self) -> int:
        return self.width * self.height * self.frame_count


def attachment_reference_patterns(
    attachments: Sequence[Attachment], *, workspace: Path
) -> tuple[str, ...]:
    """Every internal attachment spelling an executor might echo."""
    patterns: set[str] = set()
    for attachment in attachments:
        patterns.add(str(attachment.path))
        try:
            patterns.add(str(attachment.path.relative_to(workspace)))
        except ValueError:
            pass
        patterns.add(attachment.path.name)
    return tuple(sorted((pattern for pattern in patterns if pattern), key=len, reverse=True))


def redact_attachment_references(
    text: str,
    attachments: Sequence[Attachment],
    *,
    workspace: Path,
) -> str:
    """Replace internal attachment references in a complete output string."""
    for pattern in attachment_reference_patterns(attachments, workspace=workspace):
        text = text.replace(pattern, "the attached file")
    return text


class AttachmentReferenceRedactor:
    """Streaming-safe redaction for output split across arbitrary chunks."""

    def __init__(
        self,
        attachments: Sequence[Attachment],
        *,
        workspace: Path,
    ) -> None:
        self._patterns = attachment_reference_patterns(attachments, workspace=workspace)
        self._max_pattern_length = max((len(pattern) for pattern in self._patterns), default=0)
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        if not self._patterns:
            return chunk
        self._buffer += chunk
        output: list[str] = []
        while self._buffer:
            match = self._earliest_match()
            if match is not None:
                index, pattern = match
                output.append(self._buffer[:index])
                output.append("the attached file")
                self._buffer = self._buffer[index + len(pattern) :]
                continue
            safe_length = len(self._buffer) - self._max_pattern_length + 1
            if safe_length <= 0:
                break
            output.append(self._buffer[:safe_length])
            self._buffer = self._buffer[safe_length:]
            break
        return "".join(output)

    def flush(self) -> str:
        output = self._buffer
        self._buffer = ""
        for pattern in self._patterns:
            output = output.replace(pattern, "the attached file")
        return output

    def _earliest_match(self) -> tuple[int, str] | None:
        found: tuple[int, str] | None = None
        for pattern in self._patterns:
            index = self._buffer.find(pattern)
            if index < 0:
                continue
            if (
                found is None
                or index < found[0]
                or (index == found[0] and len(pattern) > len(found[1]))
            ):
                found = (index, pattern)
        return found
