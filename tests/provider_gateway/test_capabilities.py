from __future__ import annotations

from fluxion.provider_gateway.capabilities import (
    COMPACT,
    IMAGE_INPUT,
    PARALLEL_TOOL_CALLING,
    REASONING,
    REASONING_SUMMARY,
    STREAMING,
    TOOL_CALLING,
    ModelCapabilities,
    RequestRequirements,
    derive_requirements,
    filter_candidates,
)


def caps(*flags, context=None):
    return ModelCapabilities(flags=frozenset(flags), max_context_tokens=context)


def test_undeclared_capability_is_absent_not_assumed():
    """Forgetting a declaration costs a routing option, never correctness."""
    model = ModelCapabilities.from_config({"streaming": True})
    assert model.supports(STREAMING)
    assert not model.supports(TOOL_CALLING)


def test_capabilities_are_never_inferred_from_a_name():
    """Config is the only source; there is no name-based fallback to test."""
    assert ModelCapabilities.from_config({}).flags == frozenset()


def test_streaming_required_only_when_requested():
    assert STREAMING in derive_requirements({"stream": True}).required
    assert STREAMING not in derive_requirements({}).required


def test_tools_require_tool_calling():
    reqs = derive_requirements({"tools": [{"type": "function"}]})
    assert TOOL_CALLING in reqs.required
    assert PARALLEL_TOOL_CALLING not in reqs.required


def test_empty_tools_list_requires_nothing():
    assert TOOL_CALLING not in derive_requirements({"tools": []}).required


def test_parallel_tool_calling_requires_an_explicit_true():
    """Absent means "no opinion"; requiring it then would drop good candidates."""
    body = {"tools": [{"type": "function"}], "parallel_tool_calls": True}
    assert PARALLEL_TOOL_CALLING in derive_requirements(body).required

    body["parallel_tool_calls"] = False
    assert PARALLEL_TOOL_CALLING not in derive_requirements(body).required


def test_reasoning_summary_is_required_separately():
    plain = derive_requirements({"reasoning": {"effort": "high"}})
    assert REASONING in plain.required
    assert REASONING_SUMMARY not in plain.required

    summarized = derive_requirements({"reasoning": {"summary": "auto"}})
    assert REASONING_SUMMARY in summarized.required


def test_empty_reasoning_object_requires_nothing():
    assert REASONING not in derive_requirements({"reasoning": {}}).required


def test_nested_image_input_is_detected():
    """A shallow scan would miss images and route to a text-only model."""
    body = {
        "input": [
            {
                "type": "message",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": "data:image/png;base64,x"},
                ],
            }
        ]
    }
    assert IMAGE_INPUT in derive_requirements(body).required


def test_text_only_input_does_not_require_images():
    body = {"input": [{"type": "message", "content": [{"type": "input_text", "text": "hi"}]}]}
    assert IMAGE_INPUT not in derive_requirements(body).required


def test_compaction_requires_a_verified_model():
    """Compaction output must satisfy Codex's structural expectations."""
    assert COMPACT in derive_requirements({}, is_compaction=True).required
    assert COMPACT not in derive_requirements({}).required


def test_context_requirement_rejects_smaller_models():
    reqs = RequestRequirements(min_context_tokens=400_000)
    assert reqs.unmet_by(caps(context=200_000))
    assert not reqs.unmet_by(caps(context=400_000))


def test_unknown_context_window_is_not_treated_as_too_small():
    """An undeclared window is unknown, not zero; do not reject on a guess."""
    reqs = RequestRequirements(min_context_tokens=400_000)
    assert not reqs.unmet_by(caps(context=None))


def test_rejection_reasons_are_explainable():
    """ "No model available" with no reason is the worst production failure mode."""
    reqs = RequestRequirements(
        required=frozenset({TOOL_CALLING, IMAGE_INPUT}), min_context_tokens=400_000
    )
    reasons = reqs.unmet_by(caps(TOOL_CALLING, context=100_000))
    assert "missing:image_input" in reasons
    assert "context:100000<400000" in reasons
    assert "missing:tool_calling" not in reasons


def test_filter_splits_eligible_from_rejected():
    reqs = RequestRequirements(required=frozenset({TOOL_CALLING}))
    eligible, rejected = filter_candidates(
        [
            ("good:model", caps(TOOL_CALLING)),
            ("cheap:model", caps(STREAMING)),
        ],
        reqs,
    )
    assert eligible == ["good:model"]
    assert rejected["cheap:model"] == ("missing:tool_calling",)


def test_cheap_but_incapable_model_never_reaches_scoring():
    """The whole point of a hard filter: price must not rescue a wrong model."""
    reqs = derive_requirements({"input": [{"type": "input_image", "image_url": "x"}]})
    eligible, rejected = filter_candidates([("cheap:model", caps(STREAMING))], reqs)
    assert eligible == []
    assert "cheap:model" in rejected


def test_non_integer_context_values_are_ignored():
    for value in ("400000", True, None, 0, -1):
        assert (
            ModelCapabilities.from_config({"max_context_tokens": value}).max_context_tokens is None
        )
