from __future__ import annotations

import pytest

from fluxion.provider_gateway.request import NormalizedRequest, RawRequest


def test_unknown_fields_are_preserved_verbatim():
    """A field we do not model must survive to the upstream adapter untouched."""
    raw = RawRequest.create({"model": "gpt-x", "brand_new_field": {"a": 1}})
    assert raw.body["brand_new_field"] == {"a": 1}
    assert raw.unknown_fields() == frozenset({"brand_new_field"})


def test_known_fields_are_not_reported_as_unknown():
    raw = RawRequest.create({"model": "gpt-x", "stream": True, "tools": []})
    assert raw.unknown_fields() == frozenset()


def test_headers_are_case_insensitive():
    raw = RawRequest.create({}, {"X-Fluxion-Route": "reviewer"})
    assert raw.header("x-fluxion-route") == "reviewer"
    assert raw.header("X-FLUXION-ROUTE") == "reviewer"


def test_body_is_read_only():
    raw = RawRequest.create({"model": "gpt-x"})
    with pytest.raises(TypeError):
        raw.body["model"] = "tampered"  # type: ignore[index]


def test_body_is_snapshotted_from_caller_dict():
    """Later mutation of the caller's dict must not change a parsed request."""
    source = {"model": "gpt-x"}
    raw = RawRequest.create(source)
    source["model"] = "changed"
    assert raw.body["model"] == "gpt-x"


def test_normalized_view_extracts_routing_inputs():
    raw = RawRequest.create(
        {
            "model": "gpt-x",
            "stream": True,
            "client_metadata": {"x-codex-turn-metadata": "{}"},
            "metadata": {"user_id": "u1"},
        }
    )
    request = NormalizedRequest.from_raw(raw)
    assert request.model == "gpt-x"
    assert request.stream is True
    assert request.client_metadata["x-codex-turn-metadata"] == "{}"
    assert request.metadata["user_id"] == "u1"


def test_stream_defaults_to_false():
    request = NormalizedRequest.from_raw(RawRequest.create({"model": "gpt-x"}))
    assert request.stream is False


def test_malformed_metadata_degrades_to_empty_not_error():
    """A bad metadata value costs a routing hint, never the whole request."""
    request = NormalizedRequest.from_raw(
        RawRequest.create({"model": "gpt-x", "metadata": "not-a-mapping"})
    )
    assert request.metadata == {}


def test_blank_model_normalizes_to_none():
    request = NormalizedRequest.from_raw(RawRequest.create({"model": ""}))
    assert request.model is None


def test_normalized_request_still_reaches_raw_body():
    """The router works off the view; the adapter still needs everything else."""
    raw = RawRequest.create({"model": "gpt-x", "future_field": 1})
    request = NormalizedRequest.from_raw(raw)
    assert request.raw.body["future_field"] == 1
