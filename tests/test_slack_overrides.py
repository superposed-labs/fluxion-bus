from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from fluxion.channels.slack.adapter import SlackChannelAdapter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage


def _adapter(manager: SessionManager) -> SlackChannelAdapter:
    adapter = object.__new__(SlackChannelAdapter)
    adapter._settings = SimpleNamespace(default_executor="codex")
    adapter._gateway = SimpleNamespace(_sessions=manager)
    return adapter


def test_slack_top_level_task_uses_channel_model_override():
    with tempfile.TemporaryDirectory() as tmp_dir:
        manager = SessionManager(storage=JsonlStorage(Path(tmp_dir)))
        manager.set_executor_override(
            conversation_key="slack:D123:D123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
        )
        manager.set_model_override(
            conversation_key="slack:D123:D123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
            model="haiku",
        )
        adapter = _adapter(manager)
        event = {"channel": "D123", "user": "U123", "ts": "1700000000.000001"}

        executor = adapter._resolve_executor_for_event(event)
        model = adapter._resolve_model_override_for_event(
            event=event,
            executor_name=executor,
        )

        assert executor == "claude"
        assert model == "haiku"


def test_slack_thread_model_override_takes_precedence_over_channel_override():
    with tempfile.TemporaryDirectory() as tmp_dir:
        manager = SessionManager(storage=JsonlStorage(Path(tmp_dir)))
        manager.set_model_override(
            conversation_key="slack:D123:D123",
            channel="slack",
            user_id="U123",
            executor_name="claude",
            model="sonnet",
        )
        manager.set_model_override(
            conversation_key="slack:D123:1700000000.000001",
            channel="slack",
            user_id="U123",
            executor_name="claude",
            model="haiku",
        )
        adapter = _adapter(manager)
        event = {
            "channel": "D123",
            "user": "U123",
            "thread_ts": "1700000000.000001",
        }

        model = adapter._resolve_model_override_for_event(
            event=event,
            executor_name="claude",
        )

        assert model == "haiku"
