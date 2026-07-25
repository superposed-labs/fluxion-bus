"""Codex ingress identity extraction.

Field names mirror `codex-rs/core/src/responses_metadata.rs` at `c8957bbf0f`.
"""

from __future__ import annotations

import json

from fluxion.provider_gateway.identity import IdentityConfidence
from fluxion.provider_gateway.ingress.responses import (
    CodexResponsesIngress,
    is_compaction_request,
)
from fluxion.provider_gateway.request import RawRequest


def build(body=None, headers=None, **client_metadata):
    """Build a request whose client_metadata carries string values, as Codex sends."""
    payload = dict(body or {})
    if client_metadata:
        payload["client_metadata"] = {k.replace("__", "-"): v for k, v in client_metadata.items()}
    return CodexResponsesIngress().normalize(RawRequest.create(payload, headers))


def turn_metadata(**fields) -> str:
    return json.dumps(fields)


INGRESS = CodexResponsesIngress()


def test_canonical_blob_wins_over_flat_keys():
    """Flat keys are compatibility projections, not independent sources."""
    request = build(
        **{
            "x__codex__turn__metadata": turn_metadata(thread_id="blob-thread", request_kind="turn"),
            "thread_id": "flat-thread",
        }
    )
    assert INGRESS.identity.parse(request).thread_id == "blob-thread"


def test_flat_keys_fill_gaps_the_blob_leaves():
    request = build(
        **{
            "x__codex__turn__metadata": turn_metadata(thread_id="t1", request_kind="turn"),
            "session_id": "s1",
            "x__codex__installation__id": "i1",
        }
    )
    parsed = INGRESS.identity.parse(request)
    assert parsed.thread_id == "t1"
    assert parsed.session_id == "s1"
    assert parsed.installation_id == "i1"


def test_header_blob_is_used_when_client_metadata_is_absent():
    """Some transports drop client_metadata; the compat header still carries it."""
    request = build(
        headers={"x-codex-turn-metadata": turn_metadata(thread_id="t1", request_kind="turn")}
    )
    assert INGRESS.identity.parse(request).thread_id == "t1"


def test_malformed_blob_degrades_to_flat_keys():
    """A metadata parse failure costs routing precision, never the request."""
    request = build(**{"x__codex__turn__metadata": "{not json", "thread_id": "t1"})
    assert INGRESS.identity.parse(request).thread_id == "t1"


def test_non_object_blob_is_ignored():
    request = build(**{"x__codex__turn__metadata": "[1, 2]", "thread_id": "t1"})
    assert INGRESS.identity.parse(request).thread_id == "t1"


def test_route_hint_comes_from_the_fluxion_header():
    request = build(headers={"X-Fluxion-Route": "reviewer"})
    assert INGRESS.identity.parse(request).route_hint == "reviewer"


def test_route_hint_defaults_to_auto():
    assert INGRESS.identity.parse(build()).route_hint == "auto"


def test_thread_id_yields_explicit_identity():
    identity = INGRESS.extract_identity(
        build(**{"x__codex__turn__metadata": turn_metadata(thread_id="t1", request_kind="turn")})
    )
    assert identity.confidence is IdentityConfidence.EXPLICIT
    assert identity.is_persistable


def test_two_subagents_of_one_parent_get_different_routes():
    """The whole feature depends on sibling sub-agents routing independently."""

    def sibling(thread_id: str):
        return INGRESS.extract_identity(
            build(
                **{
                    "x__codex__turn__metadata": turn_metadata(
                        installation_id="i1",
                        parent_thread_id="parent-1",
                        thread_id=thread_id,
                        request_kind="turn",
                    )
                }
            )
        )

    assert sibling("child-a").route_key != sibling("child-b").route_key


def test_same_subthread_keeps_one_route_across_turns():
    def turn(turn_id: str):
        return INGRESS.extract_identity(
            build(
                **{
                    "x__codex__turn__metadata": turn_metadata(
                        installation_id="i1",
                        parent_thread_id="parent-1",
                        thread_id="child-a",
                        turn_id=turn_id,
                        request_kind="turn",
                    )
                }
            )
        )

    assert turn("turn-1").route_key == turn("turn-2").route_key


def test_falls_back_to_session_then_parent_window():
    session_only = INGRESS.extract_identity(build(**{"session_id": "s1"}))
    assert session_only.confidence is IdentityConfidence.EXPLICIT

    parent_window = INGRESS.extract_identity(
        build(**{"x__codex__parent__thread__id": "p1", "x__codex__window__id": "w1"})
    )
    assert parent_window.confidence is IdentityConfidence.EXPLICIT
    assert parent_window.route_key != session_only.route_key


def test_identity_without_any_handle_is_ephemeral():
    identity = INGRESS.extract_identity(build(headers={"x-codex-window-id": "w1"}))
    assert identity.confidence is IdentityConfidence.EPHEMERAL
    assert not identity.is_persistable


def test_memory_requests_are_never_persistable():
    """Codex marks memory requests as carrying no stable turn identity."""
    identity = INGRESS.extract_identity(
        build(**{"x__codex__turn__metadata": turn_metadata(thread_id="t1", request_kind="memory")})
    )
    assert identity.request_kind == "memory"
    assert identity.confidence is IdentityConfidence.EPHEMERAL


def test_unknown_request_kind_is_treated_as_absent():
    """A future Codex kind must not become an unconfigured routing key."""
    identity = INGRESS.extract_identity(
        build(
            **{
                "x__codex__turn__metadata": turn_metadata(
                    thread_id="t1", request_kind="something_new"
                )
            }
        )
    )
    assert identity.request_kind == "turn"


def test_subagent_header_does_not_affect_the_route_key():
    """`x-openai-subagent` is a request class, not a per-agent id."""

    def with_subagent(value: str | None):
        metadata = {"thread_id": "t1", "request_kind": "turn"}
        body = {"x__codex__turn__metadata": json.dumps(metadata)}
        if value:
            body["x__openai__subagent"] = value
        return INGRESS.extract_identity(build(**body)).route_key

    assert with_subagent("collab_spawn") == with_subagent(None)


def test_installation_id_is_not_exposed_in_traces():
    identity = INGRESS.extract_identity(
        build(
            **{
                "x__codex__turn__metadata": turn_metadata(
                    installation_id="install-secret", thread_id="t1", request_kind="turn"
                )
            }
        )
    )
    assert "install-secret" not in str(identity.trace_fields())


def test_compaction_detected_from_request_kind():
    """Since remote_compaction_v2 became the default, compaction has no own endpoint."""
    request = build(
        **{"x__codex__turn__metadata": turn_metadata(thread_id="t1", request_kind="compaction")}
    )
    identity = INGRESS.extract_identity(request)
    assert is_compaction_request(request, identity)


def test_compaction_detected_from_trigger_item_without_metadata():
    """The input marker survives even when the metadata blob is not sent."""
    request = build(
        body={
            "input": [
                {"type": "message", "content": "hi"},
                {"type": "compaction_trigger"},
            ]
        }
    )
    identity = INGRESS.extract_identity(request)
    assert is_compaction_request(request, identity)


def test_ordinary_turn_is_not_compaction():
    request = build(
        body={"input": [{"type": "message", "content": "hi"}]},
        **{"x__codex__turn__metadata": turn_metadata(thread_id="t1", request_kind="turn")},
    )
    identity = INGRESS.extract_identity(request)
    assert not is_compaction_request(request, identity)


def test_compaction_detection_tolerates_odd_input_shapes():
    for value in (None, "text", [], ["str"], [{"no_type": 1}]):
        request = build(body={"input": value})
        identity = INGRESS.extract_identity(request)
        assert not is_compaction_request(request, identity)


def test_blank_values_are_treated_as_missing():
    request = build(**{"thread_id": "   ", "session_id": "s1"})
    parsed = INGRESS.identity.parse(request)
    assert parsed.thread_id is None
    assert parsed.session_id == "s1"
