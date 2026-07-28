from __future__ import annotations

import json
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from fluxion.channels.feishu.adapter import FeishuChannelAdapter
from fluxion.channels.feishu.feishu_client import FeishuAPIError
from fluxion.core.models.result import ExecutionResult
from fluxion.renderers.markdown_renderer import MarkdownRenderer


def _card_text(card) -> str:
    """Pull the markdown element's text out of a Feishu card dict."""
    return card["body"]["elements"][0]["content"]


class _FakeClient:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str, str]] = []  # (receive_id, text, receive_id_type)
        self.cards: list[tuple[str, dict, str]] = []  # (receive_id, card, receive_id_type)
        self.card_patches: list[tuple[str, dict]] = []  # (message_id, card)
        self.images_sent: list[tuple[str, str]] = []  # (receive_id, image_key)
        self.files_sent: list[tuple[str, str]] = []  # (receive_id, file_key)
        self.uploaded: list[Path] = []
        self.downloads: list[dict] = []
        self._n = 0

    def send_text(self, receive_id, text, *, receive_id_type="chat_id"):  # noqa: ANN001
        self.texts.append((receive_id, text, receive_id_type))
        self._n += 1
        return f"om_sent_{self._n}"

    def send_card(self, receive_id, card, *, receive_id_type="chat_id"):  # noqa: ANN001
        self.cards.append((receive_id, card, receive_id_type))
        self._n += 1
        return f"om_sent_{self._n}"

    def patch_card(self, message_id, card):  # noqa: ANN001
        self.card_patches.append((message_id, card))

    def upload_image(self, path):  # noqa: ANN001
        self.uploaded.append(path)
        return "img_uploaded"

    def upload_file(self, path):  # noqa: ANN001
        self.uploaded.append(path)
        return "file_uploaded"

    def send_image(self, receive_id, image_key, *, receive_id_type="chat_id"):  # noqa: ANN001
        self.images_sent.append((receive_id, image_key))

    def send_file(self, receive_id, file_key, *, receive_id_type="chat_id"):  # noqa: ANN001
        self.files_sent.append((receive_id, file_key))

    def download_message_resource(self, *, message_id, file_key, resource_type, dest):  # noqa: ANN001
        self.downloads.append(
            {"message_id": message_id, "file_key": file_key, "resource_type": resource_type}
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        if resource_type == "image":
            payload = BytesIO()
            Image.new("RGB", (8, 8)).save(payload, format="PNG")
            dest.write_bytes(payload.getvalue())
        else:
            dest.write_bytes(b"fake-bytes")


class _FakeSessions:
    def get_executor_override(self, *, conversation_key, channel, user_id):  # noqa: ANN001
        return None


class _FakeGateway:
    def __init__(self) -> None:
        self._sessions = _FakeSessions()
        self.submitted: list[tuple[object, dict]] = []

    def handle_control_command(self, *, text, user_id, convo_key, channel):  # noqa: ANN001
        return None

    def submit_task(self, *, task, channel_adapter, channel_context):  # noqa: ANN001
        self.submitted.append((task, channel_context))
        return True, "ok"


def _make_adapter(
    *,
    allowed=("ou_user1",),
    allow_group=True,
    locale_mode="fixed",
    ui_locale="en",
) -> FeishuChannelAdapter:
    adapter = object.__new__(FeishuChannelAdapter)
    adapter._settings = SimpleNamespace(  # noqa: SLF001
        feishu_allowed_users=set(allowed),
        feishu_allow_group_chat=allow_group,
        feishu_default_workspace="",
        data_dir=Path("/tmp"),
        default_executor="codex",
        status_updates={"DONE", "FAILED"},
        locale_mode=locale_mode,
        ui_locale=ui_locale,
        resolve_workspace=lambda raw: Path("/tmp/ws"),
        inbox_ttl_hours=0.0,
        artifact_max_files=8,
        upload_log_on_success=False,
    )
    adapter._renderer = MarkdownRenderer()  # noqa: SLF001
    adapter._gateway = _FakeGateway()  # noqa: SLF001
    adapter._client = _FakeClient()  # noqa: SLF001
    adapter._pending_store = SimpleNamespace(  # noqa: SLF001
        record=lambda *_args, **_kw: None, remove=lambda *_args: None
    )
    adapter._lock = threading.Lock()  # noqa: SLF001
    adapter._chat_ids = {}  # noqa: SLF001
    adapter._stream_buffers = {}  # noqa: SLF001
    adapter._stream_last_patch = {}  # noqa: SLF001
    adapter._thinking_msg_ids = {}  # noqa: SLF001
    return adapter


def _event(
    content="hello",
    open_id="ou_user1",
    chat_id="oc_1",
    chat_type="p2p",
    msg_id="om_1",
    message_type="text",
    raw_content=None,
):
    message = SimpleNamespace(
        message_id=msg_id,
        chat_id=chat_id,
        chat_type=chat_type,
        message_type=message_type,
        content=raw_content if raw_content is not None else json.dumps({"text": content}),
        mentions=None,
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id))
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


def test_p2p_event_submits_task_with_context():
    adapter = _make_adapter()
    adapter._handle_message(_event())

    submitted = adapter._gateway.submitted  # noqa: SLF001
    assert len(submitted) == 1
    task, ctx = submitted[0]
    assert task.channel == "feishu"
    assert task.user_id == "ou_user1"
    assert ctx["channel_type"] == "feishu_p2p"
    assert ctx["feishu_chat_id"] == "oc_1"
    assert adapter._chat_ids[task.id] == "oc_1"  # noqa: SLF001


def test_group_event_submits_and_marks_group():
    adapter = _make_adapter()
    adapter._handle_message(_event(chat_id="oc_grp", chat_type="group"))

    _task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert ctx["channel_type"] == "feishu_group"
    assert ctx["feishu_chat_id"] == "oc_grp"


def test_group_event_ignored_when_disabled():
    adapter = _make_adapter(allow_group=False)
    adapter._handle_message(_event(chat_type="group"))
    assert adapter._gateway.submitted == []  # noqa: SLF001


def test_unsupported_message_type_ignored():
    adapter = _make_adapter()
    ev = _event(message_type="audio", raw_content=json.dumps({"file_key": "k"}))
    adapter._handle_message(ev)
    assert adapter._gateway.submitted == []  # noqa: SLF001


def test_malformed_image_content_ignored():
    # An image message whose content lacks an image_key is dropped, not submitted.
    adapter = _make_adapter()
    ev = _event(message_type="image", raw_content=json.dumps({"text": "oops"}))
    adapter._handle_message(ev)
    assert adapter._gateway.submitted == []  # noqa: SLF001


def test_image_message_downloads_and_submits(tmp_path):
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001
    ev = _event(message_type="image", raw_content=json.dumps({"image_key": "img_v2_abc"}))
    adapter._handle_message(ev)

    downloads = adapter._client.downloads  # noqa: SLF001
    assert len(downloads) == 1
    assert downloads[0] == {
        "message_id": "om_1",
        "file_key": "img_v2_abc",
        "resource_type": "image",
    }
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert task.text == ""
    assert task.attachments == ()
    assert len(task.image_attachments) == 1
    assert task.image_attachments[0].media_type == "image/png"
    assert ctx["workspace"] == str(tmp_path)


def test_file_message_downloads_with_original_name(tmp_path):
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001
    ev = _event(
        message_type="file",
        raw_content=json.dumps({"file_key": "file_v2_xyz", "file_name": "report.pdf"}),
    )
    adapter._handle_message(ev)

    assert adapter._client.downloads[0]["resource_type"] == "file"  # noqa: SLF001
    task, _ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert task.text == ""
    assert task.image_attachments == ()
    assert len(task.attachments) == 1
    assert task.attachments[0].path.name == "report.pdf"


def test_send_result_uploads_image_artifact(tmp_path):
    adapter = _make_adapter()
    art = tmp_path / "chart.png"
    art.write_bytes(b"png")
    result = ExecutionResult(
        success=True, summary="done", stdout="", stderr="", exit_code=0, artifacts=[str(art)]
    )
    adapter.send_result("t1", result, {"feishu_chat_id": "oc_1", "workspace": str(tmp_path)})

    assert adapter._client.images_sent == [("oc_1", "img_uploaded")]  # noqa: SLF001
    assert adapter._client.uploaded == [art]  # noqa: SLF001


def test_send_result_uploads_non_image_as_file(tmp_path):
    adapter = _make_adapter()
    art = tmp_path / "notes.txt"
    art.write_text("hi")
    result = ExecutionResult(
        success=True, summary="done", stdout="", stderr="", exit_code=0, artifacts=[str(art)]
    )
    adapter.send_result("t2", result, {"feishu_chat_id": "oc_1", "workspace": str(tmp_path)})

    assert adapter._client.files_sent == [("oc_1", "file_uploaded")]  # noqa: SLF001


def test_unauthorized_user_is_denied_not_submitted(monkeypatch):
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Darwin")
    adapter = _make_adapter(allowed=("ou_someone",))
    recorded = []
    adapter._pending_store = SimpleNamespace(  # noqa: SLF001
        record=lambda user_id, preview, **_kw: recorded.append((user_id, preview)),
        remove=lambda *_args: None,
    )
    adapter._handle_message(_event(open_id="ou_intruder"))

    assert adapter._gateway.submitted == []  # noqa: SLF001
    assert recorded == [("ou_intruder", "hello")]
    texts = adapter._client.texts  # noqa: SLF001
    assert len(texts) == 1
    receive_id, text, _kind = texts[0]
    assert receive_id == "oc_1"
    assert "Rejected: user is not in allowlist" in text
    assert "FLUXION_FEISHU_ALLOWED_USERS" in text
    assert "Messaging -> Feishu -> Pending Users" in text
    assert "ou_intruder" in text


def test_unauthorized_rejection_uses_detected_locale(monkeypatch):
    monkeypatch.setattr("fluxion.channels.allowlist_messages.platform.system", lambda: "Linux")
    adapter = _make_adapter(allowed=("ou_someone",), locale_mode="auto", ui_locale="en")
    adapter._handle_message(_event(content="你好", open_id="ou_intruder"))

    text = adapter._client.texts[0][1]  # noqa: SLF001
    assert "已拒绝" in text


def test_running_posts_thinking_card_then_patches_result():
    adapter = _make_adapter()
    adapter._handle_message(_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    # First RUNNING posts a "thinking" placeholder card; repeated heartbeats don't.
    adapter.send_status(task.id, "RUNNING", ctx)
    adapter.send_status(task.id, "RUNNING", ctx)
    cards = adapter._client.cards  # noqa: SLF001
    assert len(cards) == 1
    assert cards[0][0] == "oc_1"
    assert adapter._thinking_msg_ids[task.id] == "om_sent_1"  # noqa: SLF001

    # The result patches the placeholder card in place, no new bubble.
    result = ExecutionResult(success=True, summary="done", stdout="", stderr="", exit_code=0)
    adapter.send_result(task.id, result, ctx)

    assert len(adapter._client.cards) == 1  # no extra send  # noqa: SLF001
    assert len(adapter._client.card_patches) == 1  # noqa: SLF001
    patched_id, patched_card = adapter._client.card_patches[0]  # noqa: SLF001
    assert patched_id == "om_sent_1"
    assert "done" in _card_text(patched_card)
    assert task.id not in adapter._thinking_msg_ids  # noqa: SLF001
    assert task.id not in adapter._chat_ids  # noqa: SLF001


def test_thinking_placeholder_follows_question_language():
    # source_text flows into channel_context, so auto-detection localizes the
    # "thinking…" placeholder to the question's language (not always English).
    adapter = _make_adapter(locale_mode="auto", ui_locale="en")
    adapter._handle_message(_event(content="你好，请帮我写个函数"))
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    assert ctx["source_text"] == "你好，请帮我写个函数"

    adapter.send_status(task.id, "RUNNING", ctx)
    card_text = _card_text(adapter._client.cards[0][1])  # noqa: SLF001
    assert "正在处理" in card_text


def test_result_card_preserves_markdown():
    # MarkdownRenderer returns the summary as raw markdown (not flattened), so the
    # Feishu card renders bold/lists/code instead of plain text.
    adapter = _make_adapter()
    adapter._handle_message(_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    result = ExecutionResult(
        success=True, summary="**bold** and `code`", stdout="", stderr="", exit_code=0
    )
    adapter.send_result(task.id, result, ctx)
    card_text = _card_text(adapter._client.cards[0][1])  # noqa: SLF001
    assert "**bold**" in card_text
    assert "`code`" in card_text


def test_streaming_deltas_patch_card_throttled():
    adapter = _make_adapter()
    adapter._handle_message(_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001
    adapter.send_status(task.id, "RUNNING", ctx)  # posts placeholder card

    # First delta is due (no prior patch); the immediate second is throttled.
    adapter.send_output_delta(task.id, "hello ", ctx)
    adapter.send_output_delta(task.id, "world", ctx)
    assert len(adapter._client.card_patches) == 1  # noqa: SLF001
    assert "hello" in _card_text(adapter._client.card_patches[0][1])  # noqa: SLF001

    # send_result always does a final un-throttled patch with the full buffer.
    result = ExecutionResult(success=True, summary="", stdout="", stderr="", exit_code=0)
    adapter.send_result(task.id, result, ctx)
    assert len(adapter._client.card_patches) == 2  # noqa: SLF001
    assert "hello world" in _card_text(adapter._client.card_patches[-1][1])  # noqa: SLF001


def test_status_then_result_sends_text_status_and_result_card():
    adapter = _make_adapter()
    adapter._handle_message(_event())
    task, ctx = adapter._gateway.submitted[0]  # noqa: SLF001

    adapter.send_status(task.id, "FAILED", ctx)  # in status_updates -> plain text bubble
    result = ExecutionResult(success=True, summary="all done", stdout="", stderr="", exit_code=0)
    adapter.send_result(task.id, result, ctx)

    # No placeholder card existed, so the result is sent as a fresh card.
    assert len(adapter._client.texts) == 1  # noqa: SLF001
    assert adapter._client.texts[0][0] == "oc_1"  # noqa: SLF001
    assert len(adapter._client.cards) == 1  # noqa: SLF001
    assert adapter._client.cards[0][0] == "oc_1"  # noqa: SLF001
    assert task.id not in adapter._chat_ids  # noqa: SLF001


def test_download_permission_error_replies_with_scope_hint(tmp_path):
    # A 99991672 "access denied" from Feishu means the app lacks a message-read
    # scope; the user should get an actionable hint, not a generic failure.
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001

    def deny(**_kwargs):
        raise FeishuAPIError("im.v1.message_resource.get", 99991672, "Access denied")

    adapter._client.download_message_resource = deny  # noqa: SLF001
    ev = _event(message_type="image", raw_content=json.dumps({"image_key": "img_v2_abc"}))
    adapter._handle_message(ev)

    assert adapter._gateway.submitted == []  # noqa: SLF001 - download failed, no task
    last_text = adapter._client.texts[-1][1]  # noqa: SLF001
    assert "im:message:readonly" in last_text
    assert "permission" in last_text.lower()


def test_download_generic_error_keeps_generic_message(tmp_path):
    adapter = _make_adapter()
    adapter._settings.resolve_workspace = lambda raw: tmp_path  # noqa: SLF001

    def boom(**_kwargs):
        raise FeishuAPIError("im.v1.message_resource.get", 50000, "server error")

    adapter._client.download_message_resource = boom  # noqa: SLF001
    ev = _event(message_type="image", raw_content=json.dumps({"image_key": "img_v2_abc"}))
    adapter._handle_message(ev)

    last_text = adapter._client.texts[-1][1]  # noqa: SLF001
    assert last_text == "Failed to download the attachment."
