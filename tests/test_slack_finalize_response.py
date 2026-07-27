"""Slack stops its streaming spinner in finalize_response (called before the
engine's workspace snapshot), and send_result must not double-finalize."""

import threading
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from fluxion.channels.slack.adapter import SlackChannelAdapter, _SlackStreamState
from fluxion.core.models.result import ExecutionResult


def _adapter() -> SlackChannelAdapter:
    adapter = object.__new__(SlackChannelAdapter)
    adapter._stream_lock = threading.Lock()
    adapter._task_streams = {}
    adapter._finalized = set()
    adapter._task_message_lock = threading.Lock()
    adapter._task_message_ts = {}
    adapter._app = MagicMock()
    adapter._renderer = MagicMock()
    adapter._renderer.render_result.return_value = "rendered"
    adapter._locale_for_context = lambda context: "en"
    adapter._task_update_chunk = lambda **kw: {"type": "task_update", **kw}
    adapter._task_update_details = lambda **kw: "details"
    adapter._markdown_chunk = lambda text: {"type": "markdown_text", "text": text}
    adapter._upload_artifacts = lambda *, result, context: None
    return adapter


def _ctx() -> dict:
    return {"channel": "C1", "thread_ts": "100.0", "user": "U1", "team": "T1"}


def _seed(adapter: SlackChannelAdapter, task_id: str) -> None:
    adapter._task_streams[task_id] = _SlackStreamState(
        ts="200.0", streamed_answer="hi", progress_started=True, phase="responding"
    )


def _ok() -> ExecutionResult:
    return ExecutionResult(success=True, summary="hi", stdout="", stderr="", exit_code=0)


def test_finalize_response_stops_stream_early():
    adapter = _adapter()
    _seed(adapter, "t1")
    adapter.finalize_response("t1", _ok(), _ctx())
    adapter._app.client.chat_stopStream.assert_called_once()
    assert "t1" in adapter._finalized
    assert "t1" not in adapter._task_streams


def test_send_result_after_finalize_does_not_double_post():
    adapter = _adapter()
    _seed(adapter, "t1")
    ctx = _ctx()
    res = _ok()
    adapter.finalize_response("t1", res, ctx)
    adapter._app.client.reset_mock()
    adapter.send_result("t1", res, ctx)
    adapter._app.client.chat_stopStream.assert_not_called()
    adapter._app.client.chat_postMessage.assert_not_called()
    assert "t1" not in adapter._finalized


def test_send_result_without_finalize_still_finalizes():
    adapter = _adapter()
    _seed(adapter, "t1")
    adapter.send_result("t1", _ok(), _ctx())
    adapter._app.client.chat_stopStream.assert_called_once()


def test_control_command_posts_mrkdwn_blocks():
    adapter = _adapter()
    adapter._gateway = MagicMock()
    adapter._gateway.handle_control_command.return_value = (
        "[Fluxion] Current Subscription Usage / Quota:\n\n"
        "*CODEX (plus) [Status: OK]*\n"
        "- Weekly: 7.0% (Resets in 6d 3h)"
    )
    say = MagicMock()

    handled = adapter._handle_control_command(
        text="usage",
        event={"channel": "C1", "thread_ts": "100.0", "user": "U1"},
        say=say,
    )

    assert handled is True
    say.assert_not_called()
    adapter._app.client.chat_postMessage.assert_called_once()
    kwargs = adapter._app.client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C1"
    assert kwargs["thread_ts"] == "100.0"
    assert kwargs["text"] == (
        "*Current Subscription Usage / Quota*\n\n"
        "*CODEX (plus) · OK*\n"
        "• Weekly: 7.0% · resets in 6d 3h"
    )
    assert kwargs["blocks"] == [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": kwargs["text"],
            },
        }
    ]


def test_slack_download_preserves_reported_media_type(monkeypatch, tmp_path):
    adapter = _adapter()
    adapter._settings = SimpleNamespace(
        inbox_ttl_hours=24,
        slack_bot_token="xoxb-test",
    )
    payload = BytesIO()
    Image.new("RGB", (8, 8)).save(payload, format="PNG")

    class _Response:
        def __init__(self):
            self._stream = BytesIO(payload.getvalue())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size=-1):
            return self._stream.read(size)

    monkeypatch.setattr(
        "fluxion.channels.slack.adapter.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    downloaded = adapter._download_files(  # noqa: SLF001
        files=[
            {
                "id": "F1",
                "name": "logo.png",
                "mimetype": "image/png",
                "url_private_download": "https://files.slack.test/logo",
            }
        ],
        workspace=tmp_path,
        event={"channel": "D1"},
        say=MagicMock(),
    )

    assert len(downloaded) == 1
    assert downloaded[0].media_type == "image/png"
    assert downloaded[0].path.read_bytes() == payload.getvalue()
