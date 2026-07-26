from __future__ import annotations

import sqlite3
import threading

import pytest

from fluxion.provider_gateway.identity import IdentityConfidence, RequestIdentity
from fluxion.provider_gateway.sticky import DEFAULT_TTL_SECONDS, StickyStore


def identity(route_key="rk-1", confidence=IdentityConfidence.EXPLICIT, **kwargs):
    return RequestIdentity(
        ingress=kwargs.pop("ingress", "codex"),
        route_key=route_key,
        confidence=confidence,
        **kwargs,
    )


@pytest.fixture
def store(tmp_path):
    s = StickyStore(tmp_path / "sticky.db")
    yield s
    s.close()


def test_remembered_route_is_returned(store):
    store.remember(identity(), "openai_primary", "gpt-x", "balanced")
    route = store.lookup("rk-1")
    assert route is not None
    assert route.candidate_id == "openai_primary:gpt-x"
    assert route.policy_id == "balanced"


def test_ephemeral_identities_are_never_persisted(store):
    """Pinning a guessed identity leaks one conversation's model into another."""
    result = store.remember(
        identity(confidence=IdentityConfidence.EPHEMERAL), "openai_primary", "gpt-x", "balanced"
    )
    assert result is None
    assert store.lookup("rk-1") is None


def test_inferred_identities_are_persisted(store):
    """Inferred is lower confidence, but still a real conversation handle."""
    assert store.remember(
        identity(confidence=IdentityConfidence.INFERRED), "openai_primary", "gpt-x", "balanced"
    )
    assert store.lookup("rk-1") is not None


def test_reselection_preserves_created_at(store):
    """A re-chosen route is the same route, not a new one."""
    first = store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=100.0)
    second = store.remember(identity(), "anthropic_official", "claude", "balanced", now=200.0)
    assert second.created_at == first.created_at
    assert second.last_used_at == 200.0
    assert second.candidate_id == "anthropic_official:claude"


def test_expired_route_is_not_returned_and_is_deleted(store):
    """A quiet conversation must not resurrect its old route later."""
    store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=0.0)
    assert store.lookup("rk-1", now=0.0) is not None

    beyond_ttl = DEFAULT_TTL_SECONDS + 3600
    assert store.lookup("rk-1", now=beyond_ttl) is None
    # Deleted on read, not merely filtered.
    assert store.lookup("rk-1", now=0.0) is None


def test_touch_extends_the_ttl(store):
    store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=0.0)
    store.touch("rk-1", now=100 * 3600)
    assert store.lookup("rk-1", now=DEFAULT_TTL_SECONDS + 3600) is not None


def test_ttl_can_be_disabled(tmp_path):
    store = StickyStore(tmp_path / "s.db", ttl_seconds=None)
    store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=0.0)
    assert store.lookup("rk-1", now=10**9) is not None
    store.close()


def test_purge_removes_only_expired_routes(store):
    store.remember(identity("old"), "p", "m", "balanced", now=0.0)
    store.remember(identity("fresh"), "p", "m", "balanced", now=DEFAULT_TTL_SECONDS)
    assert store.purge_expired(now=DEFAULT_TTL_SECONDS + 3600) == 1
    assert store.lookup("fresh", now=DEFAULT_TTL_SECONDS + 3600) is not None


def test_pinned_route_survives_ttl_and_purge(store):
    """A pin is a standing operator decision; TTL must not undo it."""
    store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=0.0)
    assert store.set_pinned("rk-1", True)

    beyond_ttl = 10**9
    assert store.purge_expired(now=beyond_ttl) == 0
    route = store.lookup("rk-1", now=beyond_ttl)
    assert route is not None and route.pinned


def test_unpin_restores_normal_expiry(store):
    store.remember(identity(), "openai_primary", "gpt-x", "balanced", now=0.0)
    store.set_pinned("rk-1", True)
    store.set_pinned("rk-1", False)
    assert store.lookup("rk-1", now=10**9) is None


def test_reselection_does_not_clear_a_pin(store):
    store.remember(identity(), "openai_primary", "gpt-x", "balanced")
    store.set_pinned("rk-1", True)
    route = store.remember(identity(), "anthropic_official", "claude", "balanced")
    assert route.pinned


def test_drain_provider_spares_pinned_routes(store):
    """Draining must not silently override an explicit operator decision."""
    store.remember(identity("a"), "openai_primary", "gpt-x", "balanced")
    store.remember(identity("b"), "openai_primary", "gpt-x", "balanced")
    store.remember(identity("c"), "anthropic_official", "claude", "balanced")
    store.set_pinned("b", True)

    assert store.drain_provider("openai_primary") == 1
    assert store.lookup("a") is None
    assert store.lookup("b") is not None
    assert store.lookup("c") is not None


def test_forget_reports_whether_the_route_existed(store):
    store.remember(identity(), "p", "m", "balanced")
    assert store.forget("rk-1")
    assert not store.forget("rk-1")


def test_routes_from_different_ingresses_are_listed_separately(store):
    store.remember(identity("a", ingress="codex"), "p", "m", "balanced")
    store.remember(identity("b", ingress="claude"), "p", "m", "balanced")
    assert [r.route_key for r in store.list_routes(ingress="codex")] == ["a"]
    assert len(store.list_routes()) == 2


def test_routing_reason_round_trips(store):
    store.remember(identity(), "p", "m", "balanced", routing_reason=["role=reviewer", "health=ok"])
    assert store.lookup("rk-1").routing_reason == ("role=reviewer", "health=ok")


def test_malformed_reason_does_not_make_a_route_unreadable(store, tmp_path):
    """The reason is explanatory; the route is operational."""
    store.remember(identity(), "p", "m", "balanced")
    conn = sqlite3.connect(tmp_path / "sticky.db")
    conn.execute("UPDATE sticky_routes SET routing_reason = '{not json'")
    conn.commit()
    conn.close()
    route = store.lookup("rk-1")
    assert route is not None and route.routing_reason == ()


def test_survives_reopen(tmp_path):
    """Sticky routes must outlive a gateway restart — that is the whole point."""
    first = StickyStore(tmp_path / "s.db")
    first.remember(identity(), "openai_primary", "gpt-x", "balanced")
    first.close()

    second = StickyStore(tmp_path / "s.db")
    assert second.lookup("rk-1").candidate_id == "openai_primary:gpt-x"
    second.close()


def test_corrupt_database_is_quarantined_not_fatal(tmp_path):
    """Losing routes costs re-routing; refusing to start costs every request."""
    path = tmp_path / "s.db"
    path.write_bytes(b"this is not a sqlite database at all" * 10)

    store = StickyStore(path)
    store.remember(identity(), "openai_primary", "gpt-x", "balanced")
    assert store.lookup("rk-1") is not None
    store.close()

    assert list(tmp_path.glob("s.db.corrupt-*"))


def test_newer_schema_is_refused_rather_than_downgraded(tmp_path):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=999")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="newer than this build"):
        StickyStore(path).lookup("rk-1")


def test_concurrent_writes_do_not_corrupt_state(tmp_path):
    store = StickyStore(tmp_path / "s.db")
    errors: list[Exception] = []

    def write(index: int) -> None:
        try:
            for _ in range(20):
                store.remember(identity(f"rk-{index}"), "p", f"m{index}", "balanced")
                store.lookup(f"rk-{index}")
        except Exception as err:  # pragma: no cover - only on a locking bug
            errors.append(err)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(store.list_routes()) == 4
    store.close()
