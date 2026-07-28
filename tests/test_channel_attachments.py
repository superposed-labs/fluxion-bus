from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from fluxion.channels.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_COUNT,
    AttachmentNormalizationError,
    DownloadedFile,
    normalize_downloaded_files,
)
from fluxion.core.models.attachment import (
    AttachmentReferenceRedactor,
    ImageAttachment,
    redact_attachment_references,
)


def _png(path: Path) -> None:
    Image.new("RGB", (24, 16), color=(20, 40, 80)).save(path, format="PNG")


def test_png_download_becomes_a_typed_image_attachment(tmp_path):
    path = tmp_path / "upload-with-wrong-extension.bin"
    _png(path)

    generic, images = normalize_downloaded_files(
        [DownloadedFile(path=path, media_type="application/octet-stream")]
    )

    assert generic == ()
    assert len(images) == 1
    image = images[0]
    assert isinstance(image, ImageAttachment)
    assert image.path == path.resolve()
    assert image.media_type == "image/png"
    assert (image.width, image.height, image.frame_count) == (24, 16, 1)
    assert image.byte_size == path.stat().st_size


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sips") is None,
    reason="HEIC normalization uses macOS ImageIO through sips",
)
def test_heic_download_is_normalized_to_validated_png(tmp_path):
    png = tmp_path / "source.png"
    heic = tmp_path / "source.heic"
    _png(png)
    subprocess.run(
        ["sips", "-s", "format", "heic", str(png), "--out", str(heic)],
        check=True,
        capture_output=True,
        text=True,
    )

    generic, images = normalize_downloaded_files(
        [DownloadedFile(path=heic, media_type="image/heic")]
    )

    assert generic == ()
    assert len(images) == 1
    normalized = images[0]
    assert normalized.media_type == "image/png"
    assert normalized.path.name == "source.fluxion.png"
    assert normalized.path.is_file()
    assert (normalized.width, normalized.height) == (24, 16)
    assert heic.is_file()


def test_non_image_download_remains_a_generic_attachment(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    generic, images = normalize_downloaded_files(
        [DownloadedFile(path=path, media_type="text/plain; charset=utf-8")]
    )

    assert images == ()
    assert len(generic) == 1
    assert generic[0].media_type == "text/plain"
    assert generic[0].path == path.resolve()


def test_attachment_count_limit_is_enforced_before_files_are_read(tmp_path):
    downloaded = [
        DownloadedFile(path=tmp_path / f"missing-{index}.bin")
        for index in range(MAX_ATTACHMENT_COUNT + 1)
    ]

    with pytest.raises(AttachmentNormalizationError, match="at most 8"):
        normalize_downloaded_files(downloaded)


def test_attachment_byte_limit_is_enforced_from_file_metadata(tmp_path):
    path = tmp_path / "too-large.bin"
    with path.open("wb") as stream:
        stream.truncate(MAX_ATTACHMENT_BYTES + 1)

    with pytest.raises(AttachmentNormalizationError, match="exceeds 20 MB"):
        normalize_downloaded_files([DownloadedFile(path=path)])


def test_corrupt_native_image_gets_a_stable_user_facing_error(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")

    with pytest.raises(AttachmentNormalizationError, match="not a valid supported image"):
        normalize_downloaded_files([DownloadedFile(path=path, media_type="image/png")])


def test_attachment_reference_redaction_handles_split_stream_chunks(tmp_path):
    path = tmp_path / ".fluxion_inbox" / "abc" / "private-name.png"
    path.parent.mkdir(parents=True)
    _png(path)
    _, images = normalize_downloaded_files([DownloadedFile(path=path, media_type="image/png")])
    redactor = AttachmentReferenceRedactor(images, workspace=tmp_path)

    first = redactor.feed("I inspected .fluxion_inbox/abc/private-")
    second = redactor.feed("name.png and it is blue.")
    output = first + second + redactor.flush()

    assert "private-name.png" not in output
    assert ".fluxion_inbox" not in output
    assert "the attached file" in output
    assert (
        redact_attachment_references(
            f"Opened {path}",
            images,
            workspace=tmp_path,
        )
        == "Opened the attached file"
    )
