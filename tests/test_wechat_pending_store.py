from __future__ import annotations

from fluxion.channels.wechat.pending_store import PendingUserStore


def test_pending_user_store_records_updates_and_removes(tmp_path):
    store = PendingUserStore(tmp_path)

    store.record("user-a", "  hello   world  ")
    store.record("user-a", "second message")
    store.record("user-b", "x" * 200)

    users = store.load_all()
    assert set(users) == {"user-a", "user-b"}
    assert users["user-a"]["message_count"] == 2
    assert users["user-a"]["last_message_preview"] == "second message"
    assert len(str(users["user-b"]["last_message_preview"])) == 160

    store.remove("user-a")

    assert set(store.load_all()) == {"user-b"}
