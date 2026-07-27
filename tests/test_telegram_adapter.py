from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fluxion.channels.telegram.adapter import TelegramChannelAdapter
from fluxion.channels.telegram.telegram_client import TelegramAPIError
from fluxion.core.models.result import ExecutionResult
from fluxion.renderers.markdown_renderer import MarkdownRenderer


def _make_adapter() -> TelegramChannelAdapter:
    adapter = object.__new__(TelegramChannelAdapter)
    adapter._settings = SimpleNamespace(  # noqa: SLF001
        status_updates={"RUNNING"},
        locale_mode="fixed",
        ui_locale="en",
    )
    adapter._renderer = MarkdownRenderer(soft_limit=3800)  # noqa: SLF001
    adapter._gateway = None  # noqa: SLF001
    adapter._client = None  # noqa: SLF001
    adapter._offset = None  # noqa: SLF001
    adapter._stream_lock = threading.Lock()  # noqa: SLF001
    adapter._task_streams = {}  # noqa: SLF001
    return adapter


def test_telegram_live_message_is_created_once_for_concurrent_status_and_delta():
    adapter = _make_adapter()
    context = {"chat_id": 123, "reply_to": 456}
    sent: list[str] = []
    edited: list[tuple[int, str]] = []

    def fake_send_text(*, context: dict, text: str) -> int:
        sent.append(text)
        time.sleep(0.05)
        return 99

    def fake_edit_text(*, chat_id: int | str, message_id: int, text: str) -> bool:
        edited.append((message_id, text))
        return True

    adapter._send_text = fake_send_text  # type: ignore[method-assign]  # noqa: SLF001
    adapter._edit_text = fake_edit_text  # type: ignore[method-assign]  # noqa: SLF001

    status_thread = threading.Thread(
        target=adapter.send_status,
        kwargs={
            "task_id": "task-1",
            "status": "RUNNING",
            "context": context,
            "detail": "elapsed=34s",
        },
    )
    delta_thread = threading.Thread(
        target=adapter.send_output_delta,
        args=("task-1", "partial answer", context),
    )

    status_thread.start()
    delta_thread.start()
    status_thread.join()
    delta_thread.join()

    assert len(sent) == 1
    assert edited == [(99, "partial answer")]


def test_telegram_result_edits_existing_live_message():
    adapter = _make_adapter()
    context = {"chat_id": 123, "reply_to": 456}
    sent: list[str] = []
    edited: list[tuple[int, str]] = []

    def fake_send_text(*, context: dict, text: str) -> int:
        sent.append(text)
        return 99

    def fake_edit_text(
        *, chat_id: int | str, message_id: int, text: str, attempts: int = 1
    ) -> bool:
        edited.append((message_id, text))
        return True

    adapter._send_text = fake_send_text  # type: ignore[method-assign]  # noqa: SLF001
    adapter._edit_text = fake_edit_text  # type: ignore[method-assign]  # noqa: SLF001

    adapter.send_status("task-1", "RUNNING", context, detail="elapsed=34s")
    adapter.send_result(
        "task-1",
        ExecutionResult(success=True, summary="done", stdout="", stderr="", exit_code=0),
        context,
    )

    assert len(sent) == 1
    assert edited == [(99, "done")]


def test_telegram_result_falls_back_to_new_message_when_finalize_edit_fails():
    adapter = _make_adapter()
    context = {"chat_id": 123, "reply_to": 456}
    sent: list[str] = []
    edit_attempts: list[int] = []

    def fake_send_text(*, context: dict, text: str) -> int:
        sent.append(text)
        return 99

    def fake_edit_text(
        *, chat_id: int | str, message_id: int, text: str, attempts: int = 1
    ) -> bool:
        edit_attempts.append(attempts)
        return False

    adapter._send_text = fake_send_text  # type: ignore[method-assign]  # noqa: SLF001
    adapter._edit_text = fake_edit_text  # type: ignore[method-assign]  # noqa: SLF001

    adapter.send_output_delta("task-1", "full answer", context)
    adapter.send_result(
        "task-1",
        ExecutionResult(success=True, summary="done", stdout="", stderr="", exit_code=0),
        context,
    )

    # The finalize edit is retried, then the full answer goes out as a new
    # message instead of leaving the live message stuck on a streamed prefix.
    assert edit_attempts[-1] == 3
    assert sent == ["…", "full answer"]


class _FlakyTelegramClient:
    """edit_message_text fails with a transient error N times, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def edit_message_text(self, **kwargs) -> dict:
        self.calls += 1
        if self.calls <= self.failures:
            raise TelegramAPIError("editMessageText", None, "timed out")
        return {}


def test_telegram_edit_text_retries_transient_errors(monkeypatch):
    monkeypatch.setattr("fluxion.channels.telegram.adapter.time.sleep", lambda _s: None)
    adapter = _make_adapter()
    client = _FlakyTelegramClient(failures=2)
    adapter._client = client  # noqa: SLF001

    assert adapter._edit_text(chat_id=1, message_id=2, text="hi", attempts=3)  # noqa: SLF001
    assert client.calls == 3


def test_telegram_edit_text_defaults_to_single_attempt():
    adapter = _make_adapter()
    client = _FlakyTelegramClient(failures=1)
    adapter._client = client  # noqa: SLF001

    assert not adapter._edit_text(chat_id=1, message_id=2, text="hi")  # noqa: SLF001
    assert client.calls == 1


class _ParseErrorTelegramClient:
    """Rejects HTML parse_mode edits; accepts plain-text edits."""

    def __init__(self) -> None:
        self.plain_texts: list[str] = []

    def edit_message_text(self, **kwargs) -> dict:
        if kwargs.get("parse_mode"):
            raise TelegramAPIError("editMessageText", 400, "can't parse entities")
        self.plain_texts.append(kwargs["text"])
        return {}


def test_telegram_edit_text_falls_back_to_plain_on_parse_error():
    adapter = _make_adapter()
    client = _ParseErrorTelegramClient()
    adapter._client = client  # noqa: SLF001

    assert adapter._edit_text(chat_id=1, message_id=2, text="*bad*")  # noqa: SLF001
    assert client.plain_texts == ["*bad*"]


def test_telegram_photo_and_document_keep_platform_media_types():
    adapter = _make_adapter()

    attachments = adapter._collect_attachments(  # noqa: SLF001
        {
            "photo": [
                {"file_id": "small"},
                {"file_id": "largest"},
            ],
            "document": {
                "file_id": "document",
                "file_name": "camera.heic",
                "mime_type": "image/heic",
            },
        }
    )

    assert attachments == [
        {
            "file_id": "largest",
            "name": None,
            "media_type": "image/jpeg",
        },
        {
            "file_id": "document",
            "name": "camera.heic",
            "media_type": "image/heic",
        },
    ]
