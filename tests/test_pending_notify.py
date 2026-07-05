from __future__ import annotations

import json
import sys

import pytest

from fluxion.channels.line.pending_store import PendingUserStore as LinePendingStore
from fluxion.channels.slack.pending_store import PendingUserStore as SlackPendingStore
from fluxion.channels.telegram.pending_store import PendingUserStore as TelegramPendingStore


def test_new_channel_stores_use_distinct_files(tmp_path):
    assert TelegramPendingStore(tmp_path).path.name == "telegram_pending_users.json"
    assert SlackPendingStore(tmp_path).path.name == "slack_pending_users.json"
    assert LinePendingStore(tmp_path).path.name == "line_pending_users.json"


@pytest.mark.skipif(sys.platform != "darwin", reason="notification signal file is macOS-only")
def test_record_queues_one_macos_notification_per_new_user(tmp_path):
    store = TelegramPendingStore(tmp_path)
    signal = tmp_path / "macos_notifications.jsonl"

    assert store.record("111", "first hello", notify_locale="en") is True
    assert store.record("111", "second hello", notify_locale="en") is False

    lines = [json.loads(line) for line in signal.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert "Telegram" in lines[0]["title"]
    assert "111" in lines[0]["body"]
    assert "first hello" in lines[0]["body"]
    # The desktop app routes notification clicks with these two fields.
    assert lines[0]["channel"] == "telegram"
    assert lines[0]["user_id"] == "111"


def test_record_without_locale_queues_nothing(tmp_path):
    store = SlackPendingStore(tmp_path)

    assert store.record("U123", "hi") is True

    assert not (tmp_path / "macos_notifications.jsonl").exists()
