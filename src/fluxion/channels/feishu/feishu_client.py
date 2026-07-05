"""Outbound message client for Feishu (Lark), wrapping the official SDK.

The ``lark-oapi`` SDK handles tenant-access-token management, signing, and
retries internally, so this is a thin convenience layer that builds the send
requests the adapter and scheduler need — send a text/card message and patch a
card in place — and normalizes failures into :class:`FeishuAPIError`.

Text content goes on the wire as a JSON string (``{"text": "..."}``); see
:func:`fluxion.channels.feishu.content.build_text_content`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

from fluxion.channels.feishu.content import build_text_content
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)


class FeishuAPIError(RuntimeError):
    """Raised when the Feishu API returns a non-success response."""

    def __init__(self, endpoint: str, code: int | None, msg: str) -> None:
        self.endpoint = endpoint
        self.code = code
        self.msg = msg
        super().__init__(f"{endpoint} failed (code {code}): {msg}")


class FeishuClient:
    """Thin wrapper around ``lark.Client`` for sending text messages."""

    def __init__(self, app_id: str, app_secret: str) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

    def send_text(
        self, receive_id: str, text: str, *, receive_id_type: str = "chat_id"
    ) -> str | None:
        """Actively send a text message; return the created message id (if any).

        ``receive_id_type`` is ``chat_id`` for replying into a conversation
        (works for both single chats and groups) or ``open_id`` to start/continue
        a private chat with a specific user (used by the scheduler's notifications).
        The returned message id lets the caller later patch the message in place
        (e.g. replace a "thinking…" placeholder with the answer).
        """
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(build_text_content(text))
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        self._check("im.v1.message.create", response)
        data = getattr(response, "data", None)
        return getattr(data, "message_id", None) if data else None

    def send_card(
        self, receive_id: str, card: dict, *, receive_id_type: str = "chat_id"
    ) -> str | None:
        """Send an interactive card; return the created message id (if any)."""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        self._check("im.v1.message.create", response)
        data = getattr(response, "data", None)
        return getattr(data, "message_id", None) if data else None

    def patch_card(self, message_id: str, card: dict) -> None:
        """Replace an existing card message's content in place (used for streaming)."""
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.patch(request)
        self._check("im.v1.message.patch", response)

    # ------------------------------------------------------------------
    # Media — upload (outbound) and download (inbound)
    # ------------------------------------------------------------------

    def upload_image(self, path: Path) -> str:
        """Upload an image and return its ``image_key`` (for ``image`` messages)."""
        with path.open("rb") as fh:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder().image_type("message").image(fh).build()
                )
                .build()
            )
            response = self._client.im.v1.image.create(request)
        self._check("im.v1.image.create", response)
        data = getattr(response, "data", None)
        image_key = getattr(data, "image_key", None) if data else None
        if not image_key:
            raise FeishuAPIError("im.v1.image.create", None, "no image_key returned")
        return image_key

    def upload_file(self, path: Path) -> str:
        """Upload a generic file and return its ``file_key`` (for ``file`` messages)."""
        with path.open("rb") as fh:
            request = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    # "stream" is the catch-all type for arbitrary binaries; Feishu
                    # only special-cases a handful (pdf/doc/xls/ppt/mp4/opus).
                    .file_type("stream")
                    .file_name(path.name)
                    .file(fh)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.file.create(request)
        self._check("im.v1.file.create", response)
        data = getattr(response, "data", None)
        file_key = getattr(data, "file_key", None) if data else None
        if not file_key:
            raise FeishuAPIError("im.v1.file.create", None, "no file_key returned")
        return file_key

    def send_image(
        self, receive_id: str, image_key: str, *, receive_id_type: str = "chat_id"
    ) -> None:
        """Send a previously uploaded image (by ``image_key``)."""
        self._send_media(receive_id, "image", {"image_key": image_key}, receive_id_type)

    def send_file(
        self, receive_id: str, file_key: str, *, receive_id_type: str = "chat_id"
    ) -> None:
        """Send a previously uploaded file (by ``file_key``)."""
        self._send_media(receive_id, "file", {"file_key": file_key}, receive_id_type)

    def download_message_resource(
        self, *, message_id: str, file_key: str, resource_type: str, dest: Path
    ) -> None:
        """Download an inbound message's image/file resource to ``dest``.

        ``resource_type`` is ``"image"`` for image messages or ``"file"`` for
        file/audio/media messages — Feishu keys the resource fetch by the file
        key carried in the message content.
        """
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(resource_type)
            .build()
        )
        response = self._client.im.v1.message_resource.get(request)
        self._check("im.v1.message_resource.get", response)
        stream = getattr(response, "file", None)
        if stream is None:
            raise FeishuAPIError("im.v1.message_resource.get", None, "no file in response")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            out.write(stream.read())

    def _send_media(
        self, receive_id: str, msg_type: str, content: dict, receive_id_type: str
    ) -> None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(json.dumps(content, ensure_ascii=False))
                .uuid(str(uuid.uuid4()))
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        self._check("im.v1.message.create", response)

    @staticmethod
    def _check(endpoint: str, response: object) -> None:
        # The SDK responses expose ``success()`` / ``code`` / ``msg``.
        if not response.success():  # type: ignore[attr-defined]
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "") or ""
            logger.error("Feishu API %s failed: code=%s msg=%s", endpoint, code, msg)
            raise FeishuAPIError(endpoint, code, msg)
