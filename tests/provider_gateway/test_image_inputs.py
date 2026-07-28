from __future__ import annotations

import base64
import io
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
from PIL import Image

from fluxion.core.models.attachment import Attachment, ImageAttachment
from fluxion.provider_gateway.image_inputs import (
    MAX_IMAGE_DIMENSION,
    AttachmentReferenceRedactor,
    ImageInputError,
    append_image_paths,
    materialize_anthropic_images,
    materialize_responses_images,
    prepare_image_prompt,
    redact_attachment_references,
    responses_remote_image_urls,
    rewrite_materialized_image_references,
)


def image_bytes(format: str, *, size: tuple[int, int] = (3, 2)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color=(31, 127, 223)).save(output, format=format)
    return output.getvalue()


PNG = image_bytes("PNG")
JPEG = image_bytes("JPEG")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_anthropic_base64_image_is_saved_in_the_workspace(tmp_path):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect this"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64(PNG),
                        },
                    },
                ],
            }
        ]
    }

    attachments = materialize_anthropic_images(body, workspace=tmp_path, resuming=False)

    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.path.read_bytes() == PNG
    assert attachment.path.relative_to(tmp_path).parts[0] == ".fluxion_inbox"
    assert attachment.path.stat().st_mode & 0o777 == 0o600
    assert (attachment.width, attachment.height, attachment.frame_count) == (3, 2, 1)
    assert attachment.byte_size == len(PNG)


def test_responses_data_url_is_saved_in_the_workspace(tmp_path):
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect this"},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64(JPEG)}",
                    },
                ],
            }
        ]
    }

    attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert len(attachments) == 1
    assert attachments[0].path.suffix == ".jpg"
    assert attachments[0].path.read_bytes() == JPEG


def test_a_resumed_turn_materializes_only_the_newest_users_images(tmp_path):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64(PNG),
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "seen"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64(JPEG),
                        },
                    }
                ],
            },
        ]
    }

    attachments = materialize_anthropic_images(body, workspace=tmp_path, resuming=True)

    assert len(attachments) == 1
    assert attachments[0].path.suffix == ".jpg"
    assert attachments[0].path.read_bytes() == JPEG


def test_duplicate_images_are_written_once(tmp_path):
    part = {"type": "input_image", "image_url": f"data:image/png;base64,{b64(PNG)}"}
    body = {"input": [{"role": "user", "content": [part, part]}]}

    attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert len(attachments) == 1


def test_same_conversation_reuses_the_content_addressed_file(tmp_path):
    part = {"type": "input_image", "image_url": f"data:image/png;base64,{b64(PNG)}"}
    body = {"input": [{"role": "user", "content": [part]}]}

    first = materialize_responses_images(
        body, workspace=tmp_path, resuming=False, storage_key="conversation-a"
    )
    second = materialize_responses_images(
        body, workspace=tmp_path, resuming=True, storage_key="conversation-a"
    )

    assert first[0].path == second[0].path
    assert list(first[0].path.parent.glob("image_*")) == [first[0].path]


def test_different_conversations_use_isolated_inbox_directories(tmp_path):
    part = {"type": "input_image", "image_url": f"data:image/png;base64,{b64(PNG)}"}
    body = {"input": [{"role": "user", "content": [part]}]}

    first = materialize_responses_images(
        body, workspace=tmp_path, resuming=False, storage_key="conversation-a"
    )
    second = materialize_responses_images(
        body, workspace=tmp_path, resuming=False, storage_key="conversation-b"
    )

    assert first[0].path.parent != second[0].path.parent


def test_a_failed_write_does_not_delete_earlier_conversation_images(tmp_path):
    first_part = {"type": "input_image", "image_url": f"data:image/png;base64,{b64(PNG)}"}
    first = materialize_responses_images(
        {"input": [{"role": "user", "content": [first_part]}]},
        workspace=tmp_path,
        resuming=False,
        storage_key="conversation-a",
    )
    different_png = image_bytes("PNG", size=(5, 4))
    next_part = {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{b64(different_png)}",
    }

    with (
        patch(
            "fluxion.provider_gateway.image_inputs._atomic_write",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(ImageInputError, match="could not save"),
    ):
        materialize_responses_images(
            {"input": [{"role": "user", "content": [next_part]}]},
            workspace=tmp_path,
            resuming=True,
            storage_key="conversation-a",
        )

    assert first[0].path.read_bytes() == PNG


def test_remote_urls_are_not_fetched_and_remain_available_to_the_agent(tmp_path):
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "https://example.com/private.png"}
                ],
            }
        ]
    }

    with patch("fluxion.provider_gateway.image_inputs._atomic_write") as write:
        attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert attachments == []
    assert responses_remote_image_urls(body, resuming=False) == ("https://example.com/private.png",)
    write.assert_not_called()


def test_unvalidated_image_format_falls_back_to_a_generic_attachment(tmp_path):
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00example-payload"
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/heic;base64,{b64(heic)}",
                    }
                ],
            }
        ]
    }

    attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert len(attachments) == 1
    assert type(attachments[0]) is Attachment
    assert attachments[0].media_type == "image/heic"
    assert attachments[0].path.suffix == ".heic"
    assert attachments[0].path.read_bytes() == heic


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sips") is None,
    reason="HEIC normalization uses macOS ImageIO through sips",
)
def test_real_heic_protocol_input_is_normalized_to_png(tmp_path):
    source = tmp_path / "source.png"
    heic = tmp_path / "source.heic"
    source.write_bytes(PNG)
    subprocess.run(
        ["sips", "-s", "format", "heic", str(source), "--out", str(heic)],
        check=True,
        capture_output=True,
        text=True,
    )
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/heic;base64,{b64(heic.read_bytes())}",
                    }
                ],
            }
        ]
    }

    attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert len(attachments) == 1
    assert isinstance(attachments[0], ImageAttachment)
    assert attachments[0].media_type == "image/png"
    assert attachments[0].path.name.endswith(".fluxion.png")


def test_unknown_image_subtype_uses_a_non_executable_bin_extension(tmp_path):
    payload = b"vendor-specific-image-container"
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/x-vendor;base64,{b64(payload)}",
                    }
                ],
            }
        ]
    }

    attachments = materialize_responses_images(body, workspace=tmp_path, resuming=False)

    assert attachments[0].path.suffix == ".bin"
    assert attachments[0].path.read_bytes() == payload


def test_non_image_media_type_is_still_refused(tmp_path):
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:application/pdf;base64,{b64(b'%PDF')}",
                    }
                ],
            }
        ]
    }

    with pytest.raises(ImageInputError, match="invalid image media type"):
        materialize_responses_images(body, workspace=tmp_path, resuming=False)


def test_invalid_base64_is_refused(tmp_path):
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "not-base64!",
                        },
                    }
                ],
            }
        ]
    }

    with pytest.raises(ImageInputError, match="invalid base64"):
        materialize_anthropic_images(body, workspace=tmp_path, resuming=False)


def test_declared_type_must_match_the_file_signature(tmp_path):
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64(PNG)}",
                    }
                ],
            }
        ]
    }

    with pytest.raises(ImageInputError, match="declares 'image/jpeg'"):
        materialize_responses_images(body, workspace=tmp_path, resuming=False)


def test_a_valid_signature_with_a_corrupt_container_is_refused(tmp_path):
    corrupt = b"\x89PNG\r\n\x1a\nnot-a-real-png"
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64(corrupt)}",
                    }
                ],
            }
        ]
    }

    with pytest.raises(ImageInputError, match="corrupt or unsupported"):
        materialize_responses_images(body, workspace=tmp_path, resuming=False)


def test_an_image_side_over_the_dimension_limit_is_refused(tmp_path):
    oversized = image_bytes("PNG", size=(MAX_IMAGE_DIMENSION + 1, 1))
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64(oversized)}",
                    }
                ],
            }
        ]
    }

    with pytest.raises(ImageInputError, match="no side may exceed") as error:
        materialize_responses_images(body, workspace=tmp_path, resuming=False)
    assert error.value.status_code == 413


def attachment(path, *, ordinal=1):
    return ImageAttachment(
        path=path,
        media_type="image/png",
        sha256="abc",
        byte_size=len(PNG),
        width=3,
        height=2,
        ordinal=ordinal,
    )


def generic_attachment(path, *, ordinal=1):
    return Attachment(
        path=path,
        media_type="image/heic",
        sha256="def",
        byte_size=12,
        ordinal=ordinal,
    )


def test_paths_are_appended_to_a_text_prompt(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    image.parent.mkdir(parents=True)

    prompt = append_image_paths("inspect it", [attachment(image)], workspace=tmp_path)

    assert prompt.startswith("inspect it")
    assert ".fluxion_inbox/abc/image_1.png" in prompt
    assert "(image/png, 3x2)" in prompt


def test_an_image_only_turn_gets_an_instruction(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    image.parent.mkdir(parents=True)

    prompt = append_image_paths("", [attachment(image)], workspace=tmp_path)

    assert "without a text instruction" in prompt
    assert ".fluxion_inbox/abc/image_1.png" in prompt


def test_codex_absolute_image_path_is_rewritten_everywhere(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    original = "/Users/user/Downloads/ChatGPT Image 2026.png"
    prompt = (
        f'请查看图片。图片路径：{original}\n<image name=[Image #1] path="{original}">\n</image>'
    )

    rewritten = rewrite_materialized_image_references(
        prompt,
        [attachment(image)],
        workspace=tmp_path,
    )

    assert original not in rewritten
    assert "图片路径：[Attached image 1]" in rewritten
    assert rewritten.count("[Attached image 1]") == 2
    assert ".fluxion_inbox" not in rewritten
    assert "<image" not in rewritten


def test_multiple_codex_image_paths_map_to_their_matching_attachments(tmp_path):
    first = tmp_path / ".fluxion_inbox" / "abc" / "first.png"
    second = tmp_path / ".fluxion_inbox" / "abc" / "second.png"
    prompt = (
        '<image name=[Image #1] path="/tmp/source-one.png"></image>\n'
        '<image name=[Image #2] path="/tmp/source-two.png"></image>'
    )

    rewritten = rewrite_materialized_image_references(
        prompt,
        [attachment(first), attachment(second, ordinal=2)],
        workspace=tmp_path,
    )

    assert "/tmp/source-one.png" not in rewritten
    assert "/tmp/source-two.png" not in rewritten
    assert "[Attached image 1]" in rewritten
    assert "[Attached image 2]" in rewritten
    assert ".fluxion_inbox" not in rewritten


def test_file_bridge_manifest_is_added_after_source_paths_are_rewritten(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    original = "/Users/user/Downloads/source.png"
    prompt = f'{original}\n<image name=[Image #1] path="{original}"></image>'

    prepared = prepare_image_prompt(
        prompt,
        [attachment(image)],
        workspace=tmp_path,
        native=False,
    )

    assert original not in prepared
    assert prepared.count(".fluxion_inbox/abc/image_1.png") == 1
    assert "Internal attachment file(s)" in prepared
    assert "Do not mention file names, paths" in prepared


def test_native_prompt_contains_no_internal_attachment_path(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    original = "/Users/user/Downloads/source.png"
    prompt = f'{original}\n<image name=[Image #1] path="{original}"></image>'

    prepared = prepare_image_prompt(
        prompt,
        [attachment(image)],
        workspace=tmp_path,
        native=True,
    )

    assert original not in prepared
    assert ".fluxion_inbox" not in prepared
    assert "image_1.png" not in prepared
    assert prepared.count("[Attached image 1]") == 2


def test_only_executor_native_images_skip_the_file_manifest(tmp_path):
    native = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    fallback = tmp_path / ".fluxion_inbox" / "abc" / "attachment_2.heic"

    prepared = prepare_image_prompt(
        "inspect both",
        [attachment(native), generic_attachment(fallback, ordinal=2)],
        workspace=tmp_path,
        native_attachments=[attachment(native)],
    )

    assert "image_1.png" not in prepared
    assert ".fluxion_inbox/abc/attachment_2.heic" in prepared
    assert "(image/heic)" in prepared


def test_complete_output_redacts_absolute_relative_and_basename_references(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    references = f"{image} | {image.relative_to(tmp_path)} | {image.name}"

    redacted = redact_attachment_references(
        references,
        [attachment(image)],
        workspace=tmp_path,
    )

    assert ".fluxion_inbox" not in redacted
    assert "image_1.png" not in redacted
    assert redacted == "attached image | attached image | attached image"


def test_streaming_redactor_catches_a_path_split_at_every_chunk_boundary(tmp_path):
    image = tmp_path / ".fluxion_inbox" / "abc" / "image_1.png"
    text = f"Observed `{image.relative_to(tmp_path)}` successfully."

    for boundary in range(len(text) + 1):
        redactor = AttachmentReferenceRedactor([attachment(image)], workspace=tmp_path)
        output = redactor.feed(text[:boundary])
        output += redactor.feed(text[boundary:])
        output += redactor.flush()
        assert output == "Observed `attached image` successfully."
