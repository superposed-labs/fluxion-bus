from fluxion.channels.wechat.context_store import ContextTokenStore


def test_context_token_store_updates_and_filters_notification_targets(tmp_path):
    store = ContextTokenStore(tmp_path)
    store.save("user-b", "token-old")
    store.save("user-a", "token-a")
    store.save("user-b", "token-new")

    assert store.notification_targets(set()) == [
        ("user-a", "token-a"),
        ("user-b", "token-new"),
    ]
    assert store.notification_targets({"user-b", "missing"}) == [
        ("user-b", "token-new"),
    ]
