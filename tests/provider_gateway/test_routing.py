from __future__ import annotations

import logging

import pytest

from fluxion.provider_gateway.capabilities import (
    TOOL_CALLING,
    ModelCapabilities,
    RequestRequirements,
)
from fluxion.provider_gateway.identity import (
    KIND_COMPACTION,
    IdentityConfidence,
    RequestIdentity,
)
from fluxion.provider_gateway.routing import (
    HEALTH_RETIRED,
    CandidateStats,
    NoRouteAvailableError,
    PolicySpec,
    Router,
    split_candidate,
)

FAST = "openai_primary:fast"
GOOD = "openai_primary:good"
CLAUDE = "anthropic_official:claude-opus"

ALL_CAPABLE = ModelCapabilities(frozenset({TOOL_CALLING}), max_context_tokens=400_000)


def identity(route_hint="auto", request_kind="turn"):
    return RequestIdentity(
        ingress="codex",
        route_key="k",
        confidence=IdentityConfidence.EXPLICIT,
        route_hint=route_hint,
        request_kind=request_kind,
    )


def build_router(**overrides):
    defaults = dict(
        policies={
            "economy": PolicySpec("economy", candidates=(FAST,), fallback=(GOOD,)),
            "quality-first": PolicySpec("quality-first", candidates=(GOOD,)),
            "balanced": PolicySpec(
                "balanced",
                candidates=(GOOD, FAST),
                weights={"quality": 0.5, "cost": 0.5},
            ),
            "compatibility-first": PolicySpec("compatibility-first", candidates=(GOOD,)),
        },
        routes={
            "explorer": "economy",
            "reviewer": "quality-first",
            "auto": "balanced",
            KIND_COMPACTION: "compatibility-first",
        },
        capabilities={FAST: ALL_CAPABLE, GOOD: ALL_CAPABLE, CLAUDE: ALL_CAPABLE},
    )
    defaults.update(overrides)
    return Router(**defaults)


def test_split_candidate_keeps_colons_in_model_names():
    """Dated snapshots and vendor-prefixed ids legitimately contain colons."""
    assert split_candidate("vertex:claude-opus-4-5@20251101:v2") == (
        "vertex",
        "claude-opus-4-5@20251101:v2",
    )


@pytest.mark.parametrize("bad", ["nocolon", ":model", "provider:", ""])
def test_split_candidate_rejects_malformed_ids(bad):
    with pytest.raises(ValueError):
        split_candidate(bad)


def test_route_hint_selects_the_policy():
    router = build_router()
    assert router.select(identity("reviewer"), RequestRequirements()).policy_id == "quality-first"
    assert router.select(identity("explorer"), RequestRequirements()).policy_id == "economy"


def test_unknown_hint_falls_back_to_the_default_policy():
    decision = build_router().select(identity("nonexistent"), RequestRequirements())
    assert decision.policy_id == "balanced"


def test_compaction_overrides_the_role_hint():
    """A compaction turn carries its sub-agent's role header; routing by it is wrong."""
    decision = build_router().select(
        identity(route_hint="reviewer", request_kind=KIND_COMPACTION), RequestRequirements()
    )
    assert decision.policy_id == "compatibility-first"


def test_sticky_route_short_circuits_scoring():
    decision = build_router().select(identity("auto"), RequestRequirements(), sticky_candidate=FAST)
    assert decision.from_sticky
    assert decision.candidate_id == FAST
    assert f"sticky={FAST}" in decision.routing_reason


def test_sticky_route_is_dropped_when_it_can_no_longer_serve():
    """Being chosen before does not make an unusable model usable now."""
    router = build_router(capabilities={FAST: ModelCapabilities(), GOOD: ALL_CAPABLE})
    decision = router.select(
        identity("auto"),
        RequestRequirements(required=frozenset({TOOL_CALLING})),
        sticky_candidate=FAST,
    )
    assert not decision.from_sticky
    assert decision.candidate_id == GOOD
    assert f"sticky-dropped={FAST}" in decision.routing_reason


def test_sticky_route_is_dropped_when_unhealthy():
    router = build_router(health_check=lambda candidate: "" if candidate != FAST else "unhealthy")
    decision = router.select(identity("auto"), RequestRequirements(), sticky_candidate=FAST)
    assert not decision.from_sticky
    assert decision.candidate_id == GOOD


def test_unknown_sticky_candidate_is_dropped():
    """A candidate removed from config must not resurrect via a stored route."""
    decision = build_router().select(
        identity("auto"), RequestRequirements(), sticky_candidate="gone:model"
    )
    assert not decision.from_sticky


def test_scoring_picks_the_highest_weighted_candidate():
    router = build_router(
        stats={
            GOOD: CandidateStats(quality=0.9, cost=0.1),
            FAST: CandidateStats(quality=0.3, cost=1.0),
        }
    )
    # weights are quality 0.5 / cost 0.5 -> GOOD 0.50, FAST 0.65
    assert router.select(identity("auto"), RequestRequirements()).candidate_id == FAST


def test_ties_break_on_declared_order_not_arbitrarily():
    """Unstable ties would drift the same request between models across restarts."""
    router = build_router()  # no stats: everything scores 0.0
    for _ in range(5):
        assert router.select(identity("auto"), RequestRequirements()).candidate_id == GOOD


def test_incapable_candidates_never_win_on_price():
    router = build_router(
        capabilities={GOOD: ALL_CAPABLE, FAST: ModelCapabilities()},
        stats={FAST: CandidateStats(cost=1.0), GOOD: CandidateStats(cost=0.0)},
    )
    decision = router.select(
        identity("auto"), RequestRequirements(required=frozenset({TOOL_CALLING}))
    )
    assert decision.candidate_id == GOOD
    assert f"filtered={FAST}" in decision.routing_reason


def test_unhealthy_candidates_are_excluded_and_reported():
    router = build_router(health_check=lambda candidate: "" if candidate != GOOD else "unhealthy")
    decision = router.select(identity("auto"), RequestRequirements())
    assert decision.candidate_id == FAST
    assert f"filtered={GOOD}" in decision.routing_reason


def retired(*candidates):
    """A health source that reports exactly these candidates as retired."""
    return lambda candidate: HEALTH_RETIRED if candidate in candidates else ""


def test_a_retired_candidate_is_skipped_and_named_as_retired():
    """Without this the router keeps selecting an id the CLI will reject."""
    router = build_router(health_check=retired(GOOD))
    decision = router.select(identity("auto"), RequestRequirements())

    assert decision.candidate_id == FAST
    assert f"retired={GOOD}" in decision.routing_reason
    # Not also under `filtered=`: a reason list that says both makes the
    # permanent case indistinguishable from an ordinary one at a glance.
    assert f"filtered={GOOD}" not in decision.routing_reason


def test_a_retired_primary_makes_the_policy_fallback_actually_fire():
    """The whole point: `fallback` covers a retired model only if selection skips it."""
    router = build_router(health_check=retired(FAST))
    decision = router.select(identity("explorer"), RequestRequirements())

    # `economy` lists FAST as its only candidate and GOOD as its fallback.
    assert decision.candidate_id == GOOD
    assert f"fallback-for={FAST}" in decision.routing_reason


def test_a_substitution_is_logged_loudly(caplog):
    """Silent downgrades produce answers the operator still trusts at full weight."""
    router = build_router(health_check=retired(FAST))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.routing"):
        router.select(identity("explorer"), RequestRequirements())

    assert FAST in caplog.text
    assert GOOD in caplog.text
    assert "no longer offered by its CLI" in caplog.text


def test_a_retired_candidate_below_the_winner_is_not_called_a_fallback(caplog):
    """`balanced` prefers GOOD, so FAST retiring changes nothing worth warning about."""
    router = build_router(health_check=retired(FAST))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.routing"):
        decision = router.select(identity("auto"), RequestRequirements())

    assert decision.candidate_id == GOOD
    assert not [name for name in decision.routing_reason if name.startswith("fallback-for=")]
    assert caplog.text == ""


def test_a_sticky_route_on_a_retired_model_is_dropped_and_reported(caplog):
    """A stored route is the strongest preference there is; overriding it must be visible."""
    router = build_router(health_check=retired(FAST))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.routing"):
        decision = router.select(identity("reviewer"), RequestRequirements(), sticky_candidate=FAST)

    assert decision.candidate_id == GOOD
    assert not decision.from_sticky
    assert f"sticky-dropped={FAST}" in decision.routing_reason
    # FAST is not a candidate of `quality-first`, so the verdict has to survive
    # the trip from the sticky check into the scoring path to be reported here.
    assert f"retired={FAST}" in decision.routing_reason
    assert f"fallback-for={FAST}" in decision.routing_reason
    assert caplog.text != ""


def test_a_policy_whose_every_candidate_retired_says_so():
    """`no route available` has to name the cause an operator can act on."""
    router = build_router(health_check=retired(GOOD))
    with pytest.raises(NoRouteAvailableError) as excinfo:
        router.select(identity("reviewer"), RequestRequirements())

    assert excinfo.value.rejected[GOOD] == (HEALTH_RETIRED,)
    assert "retired" in str(excinfo.value)


def test_fallback_is_used_when_primary_is_filtered_out():
    router = build_router(capabilities={FAST: ModelCapabilities(), GOOD: ALL_CAPABLE})
    decision = router.select(
        identity("explorer"), RequestRequirements(required=frozenset({TOOL_CALLING}))
    )
    assert decision.candidate_id == GOOD


def test_no_route_error_explains_every_rejection():
    router = build_router(capabilities={GOOD: ModelCapabilities(), FAST: ModelCapabilities()})
    with pytest.raises(NoRouteAvailableError) as excinfo:
        router.select(identity("auto"), RequestRequirements(required=frozenset({TOOL_CALLING})))
    assert excinfo.value.rejected[GOOD] == ("missing:tool_calling",)
    assert "missing:tool_calling" in str(excinfo.value)


def test_unconfigured_policy_fails_loudly():
    router = build_router(routes={"auto": "does-not-exist"})
    with pytest.raises(NoRouteAvailableError, match="not configured"):
        router.select(identity("auto"), RequestRequirements())


def test_routing_reason_is_machine_readable():
    decision = build_router().select(
        identity("reviewer"), RequestRequirements(required=frozenset({TOOL_CALLING}))
    )
    assert "role=reviewer" in decision.routing_reason
    assert "kind=turn" in decision.routing_reason
    assert "policy=quality-first" in decision.routing_reason
    assert "requires=tool_calling" in decision.routing_reason
    assert "health=ok" in decision.routing_reason


def test_policy_ordering_deduplicates_fallbacks():
    policy = PolicySpec("p", candidates=(GOOD, FAST), fallback=(GOOD, CLAUDE))
    assert policy.ordered_candidates() == (GOOD, FAST, CLAUDE)


def test_weighted_score_ignores_undeclared_dimensions():
    stats = CandidateStats(quality=1.0, cost=1.0, latency=1.0, quota=1.0)
    assert stats.weighted({"quality": 0.5}) == pytest.approx(0.5)
    assert stats.weighted({}) == pytest.approx(0.0)
