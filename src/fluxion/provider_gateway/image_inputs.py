"""Validate and materialize protocol image inputs for local-agent executors.

Formats the gateway can fully validate become typed ``ImageAttachment``
objects. Other declared ``image/*`` payloads are still bounded and safely
materialized, but remain generic ``Attachment`` objects so the selected
executor—not the gateway—decides how to inspect or convert them.

Remote URLs are never fetched here. They are passed to the agent as user input,
keeping network access inside the executor's own sandbox and permission policy.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from fluxion.channels.attachments import (
    AttachmentNormalizationError,
    DownloadedFile,
    normalize_downloaded_files,
)
from fluxion.channels.inbox import make_inbox_dir
from fluxion.core.models.attachment import Attachment, ImageAttachment

MAX_IMAGE_COUNT = 8
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_IMAGE_PIXELS = 80_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_ANIMATION_FRAMES = 100

_VALIDATED_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_GENERIC_EXTENSIONS = {
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/tiff": ".tiff",
}
_MEDIA_TYPE_ALIASES = {"image/jpg": "image/jpeg"}
_IMAGE_MEDIA_TYPE = re.compile(r"^image/[a-z0-9][a-z0-9.+-]{0,126}$")
MAX_REMOTE_URL_LENGTH = 8_192

# Codex App serializes an attached local image into the delegated task text as
# `<image name=[Image #1] path="/absolute/source.png">...</image>` while also
# sending the actual image as a Responses input_image block. The absolute source
# can sit outside the delegated workspace and trigger a headless permission
# prompt even after the gateway has made a safe workspace copy.
_CODEX_IMAGE_TAG = re.compile(r"<image\b(?P<attrs>[^>]*)>.*?</image>", re.IGNORECASE | re.DOTALL)
_CODEX_IMAGE_PATH = re.compile(
    r"\bpath\s*=\s*(?P<quote>[\"'])(?P<path>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


class ImageInputError(ValueError):
    """An image request cannot be served faithfully."""

    def __init__(self, message: str, *, status_code: int = 400, kind: str = "invalid_image_input"):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class _InlineAttachment:
    media_type: str
    data: bytes
    width: int | None = None
    height: int | None = None
    frame_count: int | None = None


def materialize_anthropic_images(
    body: Mapping[str, Any],
    *,
    workspace: Path,
    resuming: bool,
    ttl_hours: float = 24.0,
    storage_key: str = "",
) -> list[Attachment]:
    """Save image blocks from the Anthropic turns relevant to this agent run."""
    return _normalize_materialized(
        _materialize(
            _selected_anthropic_blocks(body, resuming=resuming),
            workspace=workspace,
            decoder=_decode_anthropic_block,
            ttl_hours=ttl_hours,
            storage_key=storage_key,
        )
    )


def materialize_responses_images(
    body: Mapping[str, Any],
    *,
    workspace: Path,
    resuming: bool,
    ttl_hours: float = 24.0,
    storage_key: str = "",
) -> list[Attachment]:
    """Save image blocks from the Responses items relevant to this agent run."""
    return _normalize_materialized(
        _materialize(
            _selected_responses_blocks(body, resuming=resuming),
            workspace=workspace,
            decoder=_decode_responses_block,
            ttl_hours=ttl_hours,
            storage_key=storage_key,
        )
    )


def _normalize_materialized(attachments: list[Attachment]) -> list[Attachment]:
    """Apply the same HEIC/native-image normalization used by IM channels."""
    if not attachments:
        return []
    try:
        generic, images = normalize_downloaded_files(
            [
                DownloadedFile(path=attachment.path, media_type=attachment.media_type)
                for attachment in attachments
            ]
        )
    except AttachmentNormalizationError as exc:
        raise ImageInputError(str(exc)) from exc
    return sorted((*generic, *images), key=lambda attachment: attachment.ordinal)


def anthropic_remote_image_urls(body: Mapping[str, Any], *, resuming: bool) -> tuple[str, ...]:
    """Remote image URLs from the Anthropic turns relevant to this run."""
    return _remote_urls(_selected_anthropic_blocks(body, resuming=resuming), anthropic=True)


def responses_remote_image_urls(body: Mapping[str, Any], *, resuming: bool) -> tuple[str, ...]:
    """Remote image URLs from the Responses items relevant to this run."""
    return _remote_urls(_selected_responses_blocks(body, resuming=resuming), anthropic=False)


def append_attachment_paths(
    prompt: str, attachments: Sequence[Attachment], *, workspace: Path
) -> str:
    """Tell the local agent where non-native protocol attachments were saved."""
    if not attachments:
        return prompt

    def describe(attachment: Attachment) -> str:
        details = attachment.media_type
        if isinstance(attachment, ImageAttachment):
            details += f", {attachment.width}x{attachment.height}"
        return f"{attachment.ordinal}. {attachment.path.relative_to(workspace)} ({details})"

    note = (
        "Internal attachment file(s). Inspect them to answer the user's request. "
        "Do not mention file names, paths, attachment numbers, or delivery details "
        "in the final answer:\n" + "\n".join(describe(attachment) for attachment in attachments)
    )
    if prompt.strip():
        return f"{prompt.rstrip()}\n\n{note}"
    return (
        "The user sent the following image(s) without a text instruction. "
        "Inspect them and respond appropriately.\n\n"
        f"{note}"
    )


def append_image_paths(prompt: str, attachments: Sequence[Attachment], *, workspace: Path) -> str:
    """Backward-compatible name for the file-bridge prompt helper."""
    return append_attachment_paths(prompt, attachments, workspace=workspace)


def append_remote_image_urls(prompt: str, urls: Sequence[str]) -> str:
    """Expose remote image locations without making the gateway fetch them."""
    if not urls:
        return prompt
    note = (
        "The user supplied remote image URL(s). The gateway did not download "
        "them. Access them only through your available network tools and "
        "permissions:\n" + "\n".join(f"{index}. {url}" for index, url in enumerate(urls, start=1))
    )
    return f"{prompt.rstrip()}\n\n{note}" if prompt.strip() else note


def rewrite_materialized_image_references(
    prompt: str,
    attachments: Sequence[Attachment],
    *,
    workspace: Path,
) -> str:
    """Replace Codex's original attachment paths with path-free markers.

    Codex includes the source path both inside an ``<image>`` envelope and,
    commonly, in nearby natural language such as ``图片路径：/Users/...``.
    The executor receives its usable path separately, exactly once. Keeping
    source paths out of the user-authored task prevents both permission errors
    and accidental disclosure in the answer.
    """
    if not prompt or not attachments:
        return prompt

    # Validate the invariant even for native delivery, where no path manifest
    # is appended: protocol attachments must stay inside the task workspace.
    for attachment in attachments:
        attachment.path.relative_to(workspace)

    source_to_marker: dict[str, str] = {}
    tag_index = 0

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal tag_index
        path_match = _CODEX_IMAGE_PATH.search(match.group("attrs"))
        if path_match is None:
            return match.group(0)
        source = path_match.group("path")
        tag_index += 1
        marker = f"[Attached image {min(tag_index, len(attachments))}]"
        source_to_marker.setdefault(source, marker)
        return marker

    rewritten = _CODEX_IMAGE_TAG.sub(replace_tag, prompt)
    for source, marker in source_to_marker.items():
        rewritten = rewritten.replace(source, marker)
    return rewritten


def prepare_image_prompt(
    prompt: str,
    attachments: Sequence[Attachment],
    *,
    workspace: Path,
    native_attachments: Sequence[ImageAttachment] = (),
    remote_urls: Sequence[str] = (),
    native: bool | None = None,
) -> str:
    """Canonicalize references and split native, file, and URL delivery."""
    if native is True and not native_attachments:
        native_attachments = tuple(
            attachment for attachment in attachments if isinstance(attachment, ImageAttachment)
        )
    prompt = rewrite_materialized_image_references(prompt, attachments, workspace=workspace)
    native_paths = {attachment.path for attachment in native_attachments}
    file_attachments = [
        attachment for attachment in attachments if attachment.path not in native_paths
    ]
    prompt = append_attachment_paths(prompt, file_attachments, workspace=workspace)
    prompt = append_remote_image_urls(prompt, remote_urls)
    if prompt:
        return prompt
    if native_attachments:
        return "Inspect the attached image(s) and respond appropriately."
    return ""


def attachment_reference_patterns(
    attachments: Sequence[Attachment], *, workspace: Path
) -> tuple[str, ...]:
    """Every internal attachment spelling an executor might echo."""
    patterns: set[str] = set()
    for attachment in attachments:
        patterns.add(str(attachment.path))
        patterns.add(str(attachment.path.relative_to(workspace)))
        patterns.add(attachment.path.name)
    return tuple(sorted((pattern for pattern in patterns if pattern), key=len, reverse=True))


def redact_attachment_references(
    text: str,
    attachments: Sequence[Attachment],
    *,
    workspace: Path,
) -> str:
    """Remove internal attachment references from a complete output string."""
    for pattern in attachment_reference_patterns(attachments, workspace=workspace):
        text = text.replace(pattern, "attached image")
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
                output.append("attached image")
                self._buffer = self._buffer[index + len(pattern) :]
                continue
            # A sensitive reference may start in the trailing N-1 characters
            # and finish in the next executor chunk.
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
            output = output.replace(pattern, "attached image")
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


def _is_user_item(item: Mapping[str, Any]) -> bool:
    role = item.get("role")
    if role == "user":
        return True
    if role in {"assistant", "developer", "system"}:
        return False
    item_type = str(item.get("type") or "")
    if item_type in {
        "function_call",
        "function_call_output",
        "reasoning",
        "compaction",
        "mcp_call",
        "mcp_approval_request",
        "mcp_approval_response",
    }:
        return False
    return "content" in item or item_type in {"input_image", "image_url"}


def _collect_image_blocks(value: Any, output: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        block_type = value.get("type")
        if block_type in {"image", "input_image", "image_url"}:
            output.append(value)
            return
        for nested in value.values():
            _collect_image_blocks(nested, output)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for nested in value:
            _collect_image_blocks(nested, output)


def _materialize(
    blocks: Sequence[Mapping[str, Any]],
    *,
    workspace: Path,
    decoder: Callable[[Mapping[str, Any]], _InlineAttachment | None],
    ttl_hours: float,
    storage_key: str,
) -> list[Attachment]:
    if not blocks:
        return []
    if len(blocks) > MAX_IMAGE_COUNT:
        raise ImageInputError(
            f"request contains {len(blocks)} images; the limit is {MAX_IMAGE_COUNT}",
            status_code=413,
            kind="image_input_too_large",
        )

    images: list[_InlineAttachment] = []
    total = 0
    total_pixels = 0
    seen: set[str] = set()
    for block in blocks:
        image = decoder(block)
        if image is None:
            continue
        if len(image.data) > MAX_IMAGE_BYTES:
            raise ImageInputError(
                f"one decoded image is {len(image.data)} bytes; the limit is {MAX_IMAGE_BYTES}",
                status_code=413,
                kind="image_input_too_large",
            )
        digest = hashlib.sha256(image.data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        total += len(image.data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ImageInputError(
                f"decoded images total {total} bytes; the limit is {MAX_TOTAL_IMAGE_BYTES}",
                status_code=413,
                kind="image_input_too_large",
            )
        total_pixels += (image.width or 0) * (image.height or 0) * (image.frame_count or 0)
        if total_pixels > MAX_TOTAL_IMAGE_PIXELS:
            raise ImageInputError(
                f"decoded images total {total_pixels} pixels; "
                f"the limit is {MAX_TOTAL_IMAGE_PIXELS}",
                status_code=413,
                kind="image_input_too_large",
            )
        images.append(image)

    if not images:
        return []

    inbox = make_inbox_dir(workspace, ttl_hours=ttl_hours, storage_key=storage_key)
    saved: list[Attachment] = []
    try:
        for index, image in enumerate(images, start=1):
            digest = hashlib.sha256(image.data).hexdigest()
            extension = _VALIDATED_EXTENSIONS.get(
                image.media_type, _GENERIC_EXTENSIONS.get(image.media_type, ".bin")
            )
            prefix = "image" if image.width is not None else "attachment"
            path = inbox / f"{prefix}_{digest[:16]}{extension}"
            if not path.exists():
                _atomic_write(path, image.data)
            else:
                path.touch()
            common = {
                "path": path,
                "media_type": image.media_type,
                "sha256": digest,
                "byte_size": len(image.data),
                "ordinal": index,
            }
            if image.width is not None and image.height is not None:
                saved.append(
                    ImageAttachment(
                        **common,
                        width=image.width,
                        height=image.height,
                        frame_count=image.frame_count or 1,
                    )
                )
            else:
                saved.append(
                    Attachment(
                        **common,
                    )
                )
    except OSError as err:
        # This may be a stable conversation directory containing files from
        # earlier turns. `_atomic_write` cleans up only its own temporary file.
        raise ImageInputError(
            f"could not save image input: {err}", kind="image_input_error"
        ) from err
    return saved


def _decode_anthropic_block(block: Mapping[str, Any]) -> _InlineAttachment | None:
    if block.get("type") != "image":
        return None
    source = block.get("source")
    if not isinstance(source, Mapping):
        raise ImageInputError("Anthropic image block is missing its source")
    source_type = str(source.get("type") or "")
    if source_type == "url":
        _validated_remote_url(source.get("url"))
        return None
    if source_type != "base64":
        raise ImageInputError(f"unsupported Anthropic image source type {source_type!r}")
    data = source.get("data")
    media_type = source.get("media_type")
    if not isinstance(data, str) or not isinstance(media_type, str):
        raise ImageInputError("Anthropic base64 image requires media_type and data")
    return _decode_base64(data, media_type)


def _decode_responses_block(block: Mapping[str, Any]) -> _InlineAttachment | None:
    if block.get("type") not in {"input_image", "image_url"}:
        return None
    value = block.get("image_url")
    if isinstance(value, Mapping):
        value = value.get("url")
    if not isinstance(value, str) or not value.strip():
        raise ImageInputError("Responses image block is missing image_url")
    value = value.strip()
    if _is_remote_url(value):
        _validated_remote_url(value)
        return None
    if not value.lower().startswith("data:"):
        raise ImageInputError("Responses image_url must be an inline data URL")
    return _decode_data_url(value)


def _decode_data_url(value: str) -> _InlineAttachment:
    try:
        header, data = value.split(",", 1)
    except ValueError as err:
        raise ImageInputError("image data URL is missing its payload") from err
    if not header.lower().endswith(";base64"):
        raise ImageInputError("image data URL must use base64 encoding")
    media_type = header[5:-7]
    return _decode_base64(data, media_type)


def _decode_base64(data: str, media_type: str) -> _InlineAttachment:
    normalized = _MEDIA_TYPE_ALIASES.get(media_type.lower(), media_type.lower())
    if not _IMAGE_MEDIA_TYPE.fullmatch(normalized):
        raise ImageInputError(f"invalid image media type {media_type!r}")
    # Reject before allocating the decoded buffer. The HTTP body limit is a
    # separate outer guard; this one still protects direct callers and remains
    # correct if that service-level limit is raised.
    max_encoded_bytes = ((MAX_IMAGE_BYTES + 2) // 3) * 4
    if len(data) > max_encoded_bytes:
        raise ImageInputError(
            f"one encoded image exceeds the decoded limit of {MAX_IMAGE_BYTES} bytes",
            status_code=413,
            kind="image_input_too_large",
        )
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ImageInputError("image contains invalid base64 data") from err
    if not decoded:
        raise ImageInputError("image payload is empty")
    detected = _detect_media_type(decoded)
    if detected is not None and detected != normalized:
        raise ImageInputError(
            f"image declares {normalized!r} but its file signature is {detected!r}"
        )
    if normalized in _VALIDATED_EXTENSIONS:
        if detected != normalized:
            raise ImageInputError(
                f"image declares {normalized!r} but its file signature is unknown"
            )
        width, height, frames = _inspect_image(decoded, normalized)
        return _InlineAttachment(
            media_type=normalized,
            data=decoded,
            width=width,
            height=height,
            frame_count=frames,
        )
    return _InlineAttachment(media_type=normalized, data=decoded)


def _detect_media_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
            return "image/heic"
        if brand in {b"heif", b"heim", b"heis", b"mif1", b"msf1"}:
            return "image/heif"
    return None


def _inspect_image(data: bytes, media_type: str) -> tuple[int, int, int]:
    """Fully validate the container and enforce decoded-resource limits."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1))
            detected_media_type = Image.MIME.get(image.format or "")
            if detected_media_type and detected_media_type.lower() != media_type:
                raise ImageInputError(
                    f"image declares {media_type!r} but Pillow detected "
                    f"{detected_media_type.lower()!r}"
                )
            if width <= 0 or height <= 0:
                raise ImageInputError("image dimensions must be positive")
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise ImageInputError(
                    f"image dimensions are {width}x{height}; no side may exceed "
                    f"{MAX_IMAGE_DIMENSION}px",
                    status_code=413,
                    kind="image_input_too_large",
                )
            if frame_count > MAX_ANIMATION_FRAMES:
                raise ImageInputError(
                    f"animated image has {frame_count} frames; the limit is {MAX_ANIMATION_FRAMES}",
                    status_code=413,
                    kind="image_input_too_large",
                )
            pixels = width * height * frame_count
            if pixels > MAX_IMAGE_PIXELS:
                raise ImageInputError(
                    f"decoded image is {pixels} pixels across {frame_count} frame(s); "
                    f"the limit is {MAX_IMAGE_PIXELS}",
                    status_code=413,
                    kind="image_input_too_large",
                )
            image.verify()
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as err:
        raise ImageInputError(f"image is corrupt or unsupported: {err}") from err
    return width, height, frame_count


def _atomic_write(path: Path, data: bytes) -> None:
    """Commit an attachment without exposing a partially written file."""
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".image-", dir=path.parent, delete=False
        ) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _selected_anthropic_blocks(
    body: Mapping[str, Any], *, resuming: bool
) -> list[Mapping[str, Any]]:
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return []
    user_turns = [
        turn
        for turn in messages
        if isinstance(turn, Mapping) and str(turn.get("role") or "user") == "user"
    ]
    selected = user_turns[-1:] if resuming else user_turns
    blocks: list[Mapping[str, Any]] = []
    for turn in selected:
        _collect_image_blocks(turn.get("content"), blocks)
    return blocks


def _selected_responses_blocks(
    body: Mapping[str, Any], *, resuming: bool
) -> list[Mapping[str, Any]]:
    items = body.get("input")
    if not isinstance(items, list):
        return []
    user_items = [item for item in items if isinstance(item, Mapping) and _is_user_item(item)]
    selected = user_items[-1:] if resuming else user_items
    blocks: list[Mapping[str, Any]] = []
    for item in selected:
        _collect_image_blocks(item, blocks)
    return blocks


def _remote_urls(blocks: Sequence[Mapping[str, Any]], *, anthropic: bool) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        value: Any
        if anthropic:
            source = block.get("source")
            if not isinstance(source, Mapping) or source.get("type") != "url":
                continue
            value = source.get("url")
        else:
            value = block.get("image_url")
            if isinstance(value, Mapping):
                value = value.get("url")
            if not isinstance(value, str) or not _is_remote_url(value):
                continue
        url = _validated_remote_url(value)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if len(urls) > MAX_IMAGE_COUNT:
        raise ImageInputError(
            f"request contains {len(urls)} remote images; the limit is {MAX_IMAGE_COUNT}",
            status_code=413,
            kind="image_input_too_large",
        )
    return tuple(urls)


def _validated_remote_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImageInputError("remote image source is missing its URL")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ImageInputError("remote image URL must use http or https")
    if len(url) > MAX_REMOTE_URL_LENGTH:
        raise ImageInputError(
            f"remote image URL exceeds {MAX_REMOTE_URL_LENGTH} characters",
            status_code=413,
            kind="image_input_too_large",
        )
    return url


def _is_remote_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))
