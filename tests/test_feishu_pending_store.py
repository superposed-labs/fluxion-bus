from __future__ import annotations

from fluxion.channels.feishu.pending_store import PendingUserStore


def test_feishu_pending_user_store_records_updates_and_removes(tmp_path):
    store = PendingUserStore(tmp_path)

    store.record("ou_a", "  hello   world  ")
    store.record("ou_a", "second message")
    store.record("ou_b", "x" * 200)

    users = store.load_all()
    assert set(users) == {"ou_a", "ou_b"}
    assert users["ou_a"]["message_count"] == 2
    assert users["ou_a"]["last_message_preview"] == "second message"
    assert len(str(users["ou_b"]["last_message_preview"])) == 160

    store.remove("ou_a")

    assert set(store.load_all()) == {"ou_b"}


def test_feishu_pending_store_uses_feishu_filename(tmp_path):
    store = PendingUserStore(tmp_path)
    store.record("ou_a", "hi")
    assert (tmp_path / "feishu_pending_users.json").exists()
