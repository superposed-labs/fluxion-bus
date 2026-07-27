"""Normalize files downloaded by IM channel adapters into typed task attachments."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from fluxion.core.models.attachment import Attachment, ImageAttachment
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

_NATIVE_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_HEIF_IMAGE_TYPES = {"image/heic", "image/heif"}
_MEDIA_TYPE_ALIASES = {"image/jpg": "image/jpeg"}
_MAX_IMAGE_DIMENSION = 16_384
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_ANIMATION_FRAMES = 100
MAX_ATTACHMENT_COUNT = 8
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 48 * 1024 * 1024


class AttachmentNormalizationError(ValueError):
    """Inbound attachments exceed a safe bound or cannot be decoded."""


@dataclass(frozen=True, kw_only=True)
class DownloadedFile:
    """A channel download plus the media type reported by the platform."""

    path: Path
    media_type: str = ""


def ensure_attachment_count(count: int) -> None:
    """Reject an oversized attachment batch before any network download."""
    if count > MAX_ATTACHMENT_COUNT:
        raise AttachmentNormalizationError(
            f"at most {MAX_ATTACHMENT_COUNT} attachments are allowed per message"
        )


def normalize_downloaded_files(
    downloaded: list[DownloadedFile],
) -> tuple[tuple[Attachment, ...], tuple[ImageAttachment, ...]]:
    """Classify IM downloads and normalize HEIC/HEIF into validated PNG images.

    The original download remains in the transient inbox for diagnostics, but
    only the normalized PNG is handed to the executor when conversion succeeds.
    Unsupported files remain generic attachments so executor policy still
    decides whether and how they can be inspected.
    """
    ensure_attachment_count(len(downloaded))

    generic: list[Attachment] = []
    images: list[ImageAttachment] = []
    total_bytes = 0
    for ordinal, item in enumerate(downloaded, start=1):
        path = item.path.resolve()
        try:
            byte_size = path.stat().st_size
        except OSError as exc:
            raise AttachmentNormalizationError("an attachment could not be read") from exc
        if byte_size > MAX_ATTACHMENT_BYTES:
            raise AttachmentNormalizationError(
                f"attachment {ordinal} exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"
            )
        total_bytes += byte_size
        if total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentNormalizationError(
                f"combined attachments exceed {MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MB"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AttachmentNormalizationError("an attachment could not be read") from exc
        detected = detect_image_media_type(data)
        declared = _normalize_media_type(item.media_type)
        media_type = detected or declared or _guess_media_type(path)

        if detected in _HEIF_IMAGE_TYPES:
            normalized = _convert_heif_to_png(path)
            if normalized is not None:
                normalized_data = normalized.read_bytes()
                width, height, frames = _inspect_image(normalized_data, "image/png")
                images.append(
                    _image_attachment(
                        normalized,
                        normalized_data,
                        media_type="image/png",
                        ordinal=ordinal,
                        width=width,
                        height=height,
                        frame_count=frames,
                    )
                )
                continue

        if detected in _NATIVE_IMAGE_TYPES:
            try:
                width, height, frames = _inspect_image(data, detected)
            except ValueError as exc:
                raise AttachmentNormalizationError(
                    f"attachment {ordinal} is not a valid supported image: {exc}"
                ) from exc
            images.append(
                _image_attachment(
                    path,
                    data,
                    media_type=detected,
                    ordinal=ordinal,
                    width=width,
                    height=height,
                    frame_count=frames,
                )
            )
            continue

        generic.append(
            Attachment(
                path=path,
                media_type=media_type,
                sha256=hashlib.sha256(data).hexdigest(),
                byte_size=len(data),
                ordinal=ordinal,
            )
        )
    return tuple(generic), tuple(images)


def copy_stream_limited(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> int:
    """Copy a download without ever buffering an unbounded response."""
    copied = 0
    while True:
        chunk = source.read(min(1024 * 1024, max_bytes - copied + 1))
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > max_bytes:
            raise AttachmentNormalizationError(
                f"attachment exceeds {max_bytes // (1024 * 1024)} MB"
            )
        destination.write(chunk)


def read_stream_limited(
    source: BinaryIO,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> bytes:
    """Read a response into memory with a strict upper bound."""
    from io import BytesIO

    destination = BytesIO()
    copy_stream_limited(source, destination, max_bytes=max_bytes)
    return destination.getvalue()


def detect_image_media_type(data: bytes) -> str | None:
    """Return an image media type from a trusted file signature."""
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


def _normalize_media_type(media_type: str) -> str:
    normalized = media_type.partition(";")[0].strip().lower()
    return _MEDIA_TYPE_ALIASES.get(normalized, normalized)


def _guess_media_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    return _normalize_media_type(guessed or "application/octet-stream")


def _image_attachment(
    path: Path,
    data: bytes,
    *,
    media_type: str,
    ordinal: int,
    width: int,
    height: int,
    frame_count: int,
) -> ImageAttachment:
    return ImageAttachment(
        path=path,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        ordinal=ordinal,
        width=width,
        height=height,
        frame_count=frame_count,
    )


def _inspect_image(data: bytes, media_type: str) -> tuple[int, int, int]:
    try:
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1))
            detected = Image.MIME.get(image.format or "")
            if detected and _normalize_media_type(detected) != media_type:
                raise ValueError(
                    f"image signature is {media_type}, but Pillow detected {detected.lower()}"
                )
            if width <= 0 or height <= 0:
                raise ValueError("image dimensions must be positive")
            if width > _MAX_IMAGE_DIMENSION or height > _MAX_IMAGE_DIMENSION:
                raise ValueError(f"image dimensions exceed {_MAX_IMAGE_DIMENSION}px")
            if frame_count > _MAX_ANIMATION_FRAMES:
                raise ValueError(f"image has more than {_MAX_ANIMATION_FRAMES} frames")
            if width * height * frame_count > _MAX_IMAGE_PIXELS:
                raise ValueError(f"decoded image exceeds {_MAX_IMAGE_PIXELS} pixels")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError(f"image is corrupt or unsupported: {exc}") from exc
    return width, height, frame_count


def _convert_heif_to_png(source: Path) -> Path | None:
    """Convert HEIC/HEIF with macOS ImageIO through ``sips`` without a shell."""
    if sys.platform != "darwin":
        return None
    sips = shutil.which("sips")
    if not sips:
        return None
    destination = source.with_name(f"{source.stem}.fluxion.png")
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp.png")
    try:
        completed = subprocess.run(
            [sips, "-s", "format", "png", str(source), "--out", str(temporary)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
            text=True,
        )
        if completed.returncode != 0 or not temporary.is_file():
            logger.warning(
                "HEIC/HEIF normalization failed for %s: %s",
                source,
                completed.stderr.strip() or f"sips exited {completed.returncode}",
            )
            return None
        # Validate before publishing the normalized file to the executor.
        _inspect_image(temporary.read_bytes(), "image/png")
        os.replace(temporary, destination)
        return destination
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.exception("HEIC/HEIF normalization failed for %s", source)
        return None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
