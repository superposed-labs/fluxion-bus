from __future__ import annotations

import pytest

from fluxion.provider_gateway.identity import (
    IdentityConfidence,
    RequestIdentity,
    derive_route_key,
)


def test_route_key_is_stable_for_same_input():
    a = derive_route_key("codex", ["install-1", "parent-1", "thread-1"])
    b = derive_route_key("codex", ["install-1", "parent-1", "thread-1"])
    assert a == b


def test_route_key_separates_ingresses():
    """Two runtimes minting the same id must not share a sticky route."""
    codex = derive_route_key("codex", ["thread-1"])
    claude = derive_route_key("claude", ["thread-1"])
    assert codex != claude


def test_route_key_encodes_missing_parts_positionally():
    """("a", None) and (None, "a") are different identities, not the same one."""
    left = derive_route_key("codex", ["a", None])
    right = derive_route_key("codex", [None, "a"])
    assert left != right


def test_route_key_resists_delimiter_forgery():
    """A value containing the delimiter must not be able to spoof another key."""
    forged = derive_route_key("codex", ["a\x1fb", None])
    genuine = derive_route_key("codex", ["a", "b"])
    assert forged != genuine


def test_route_key_rejects_all_empty_parts():
    with pytest.raises(ValueError):
        derive_route_key("codex", [None, "", None])


def test_route_key_rejects_empty_ingress():
    with pytest.raises(ValueError):
        derive_route_key("", ["thread-1"])


@pytest.mark.parametrize(
    ("confidence", "persistable"),
    [
        (IdentityConfidence.EXPLICIT, True),
        (IdentityConfidence.INFERRED, True),
        (IdentityConfidence.EPHEMERAL, False),
    ],
)
def test_ephemeral_identities_are_not_persistable(confidence, persistable):
    identity = RequestIdentity(
        ingress="codex",
        route_key=derive_route_key("codex", ["thread-1"]),
        confidence=confidence,
    )
    assert identity.is_persistable is persistable


def test_identity_requires_ingress_and_route_key():
    with pytest.raises(ValueError):
        RequestIdentity(ingress="", route_key="abc", confidence=IdentityConfidence.EXPLICIT)
    with pytest.raises(ValueError):
        RequestIdentity(ingress="codex", route_key="", confidence=IdentityConfidence.EXPLICIT)


def test_trace_fields_hash_installation_id():
    """Installation id identifies a user; it may correlate but must not display."""
    identity = RequestIdentity(
        ingress="codex",
        route_key=derive_route_key("codex", ["thread-1"]),
        confidence=IdentityConfidence.EXPLICIT,
        installation_id="install-secret",
        thread_id="thread-1",
    )
    trace = identity.trace_fields()
    assert "install-secret" not in str(trace)
    assert trace["installation_hash"]
    assert trace["identity_confidence"] == "explicit"


def test_trace_fields_omit_installation_hash_when_absent():
    identity = RequestIdentity(
        ingress="claude",
        route_key=derive_route_key("claude", ["session-1"]),
        confidence=IdentityConfidence.INFERRED,
    )
    assert identity.trace_fields()["installation_hash"] is None


def test_parent_thread_id_absence_is_normal():
    """Runtimes without parent/child threads still produce a valid identity."""
    identity = RequestIdentity(
        ingress="claude",
        route_key=derive_route_key("claude", ["session-1"]),
        confidence=IdentityConfidence.INFERRED,
        session_id="session-1",
    )
    assert identity.parent_thread_id is None
    assert identity.is_persistable
