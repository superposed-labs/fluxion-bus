from __future__ import annotations

import pytest

from fluxion.provider_gateway.attribution import (
    BILLING_API,
    BILLING_SUBSCRIPTION,
    AttributionStore,
)
from fluxion.provider_gateway.identity import IdentityConfidence, RequestIdentity


def identity(thread_id="t1", parent="p1", **kwargs):
    return RequestIdentity(
        ingress="codex",
        route_key=f"rk-{thread_id}",
        confidence=IdentityConfidence.EXPLICIT,
        thread_id=thread_id,
        parent_thread_id=parent,
        **kwargs,
    )


@pytest.fixture
def store(tmp_path):
    s = AttributionStore(tmp_path / "attribution.db")
    yield s
    s.close()


def record(store, session="claude-1", **kwargs):
    return store.record(
        kwargs.pop("identity", identity()),
        provider_id=kwargs.pop("provider_id", "local_claude"),
        upstream_model=kwargs.pop("upstream_model", "opus"),
        billing_source=kwargs.pop("billing_source", BILLING_SUBSCRIPTION),
        executor_session_id=session,
        **kwargs,
    )


def test_a_served_turn_is_linked_to_its_agent_session(store):
    saved = record(store)
    assert saved is not None
    assert saved.executor_session_id == "claude-1"
    assert saved.thread_id == "t1"
    assert saved.parent_thread_id == "p1"


def test_no_tokens_are_stored(store):
    """The usage layer already counts this run; a second ledger would double-bill."""
    record(store)
    columns = {field for field in vars(store.list_recent()[0])}
    assert not any("token" in name for name in columns)


def test_turns_without_an_agent_session_are_skipped(store):
    """An API-backed turn is already attributed by the response itself."""
    assert record(store, session="") is None
    assert store.list_recent() == []


def test_billing_source_distinguishes_subscription_from_api(store):
    record(store, session="s1", billing_source=BILLING_SUBSCRIPTION)
    record(store, session="s2", billing_source=BILLING_API)
    sources = {item.billing_source for item in store.list_recent()}
    assert sources == {BILLING_SUBSCRIPTION, BILLING_API}


def test_every_turn_is_recorded_not_just_the_last(store):
    """A sub-thread can run many turns; pricing needs all of them."""
    record(store, session="claude-1", identity=identity(turn_id="turn-1"))
    record(store, session="claude-1", identity=identity(turn_id="turn-2"))
    assert len(store.list_recent()) == 2


def test_sessions_for_a_parent_span_all_its_subagents(store):
    record(store, session="claude-a", identity=identity("child-a", parent="parent-1"))
    record(store, session="claude-b", identity=identity("child-b", parent="parent-1"))
    record(store, session="claude-c", identity=identity("other", parent="parent-2"))

    assert sorted(store.sessions_for_parent("parent-1")) == ["claude-a", "claude-b"]


def test_repeated_turns_on_one_session_yield_one_join_key(store):
    """Pricing joins on session id; duplicates would multiply the cost."""
    record(store, session="claude-a", identity=identity(turn_id="turn-1"))
    record(store, session="claude-a", identity=identity(turn_id="turn-2"))
    assert store.sessions_for_parent("p1") == ["claude-a"]


def test_recent_is_newest_first(store):
    record(store, session="old", started_at=100.0)
    record(store, session="new", started_at=200.0)
    assert [item.executor_session_id for item in store.list_recent()] == ["new", "old"]


def test_records_survive_reopen(tmp_path):
    first = AttributionStore(tmp_path / "a.db")
    record(first)
    first.close()

    second = AttributionStore(tmp_path / "a.db")
    assert len(second.list_recent()) == 1
    second.close()


def test_route_hint_and_kind_are_kept_for_reporting(store):
    saved = record(store, identity=identity(route_hint="reviewer", request_kind="compaction"))
    assert saved.route_hint == "reviewer"
    assert saved.request_kind == "compaction"
