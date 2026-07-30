"""Route selection: pick the provider and model for one request.

Every decision must be explainable and reproducible. A route that cannot say
why it chose what it chose is unusable in production — when a sub-agent lands on
an unexpected model, "it scored highest" is not an answer anyone can act on. So
`RouteDecision` carries a machine-readable reason list, including for the paths
that never reach scoring at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from fluxion.provider_gateway.capabilities import (
    ModelCapabilities,
    RequestRequirements,
    filter_candidates,
)
from fluxion.provider_gateway.identity import KIND_COMPACTION, RequestIdentity

log = logging.getLogger(__name__)

# Scoring dimensions. Each is normalized to 0..1 where higher is better, so a
# policy's weights compare like with like: raw dollars and milliseconds are not
# comparable, but "cheaper than the alternatives" and "faster than the
# alternatives" are.
DIMENSIONS = ("quality", "cost", "latency", "quota")

# Health verdicts. `HEALTH_OK` (the empty string) means usable; anything else is
# a short machine-readable reason that travels into `routing_reason` and into
# `NoRouteAvailableError`.
#
# A verdict rather than a boolean because the reasons are not interchangeable to
# whoever reads the decision afterwards. `HEALTH_RETIRED` means the model no
# longer exists as far as its own CLI is concerned — the configuration is now
# wrong and someone has to edit it — while a transient verdict means today's
# routing moved and tomorrow's may move back.
HEALTH_OK = ""
HEALTH_RETIRED = "retired"


def _always_healthy(candidate: str) -> str:
    return HEALTH_OK


class NoRouteAvailableError(RuntimeError):
    """Raised when no candidate can serve the request.

    Carries the per-candidate rejection reasons so the error itself explains the
    outcome rather than requiring a log dive.
    """

    def __init__(self, message: str, rejected: Mapping[str, tuple[str, ...]]):
        detail = "; ".join(f"{name}({', '.join(reasons)})" for name, reasons in rejected.items())
        super().__init__(f"{message}: {detail}" if detail else message)
        self.rejected = dict(rejected)


@dataclass(frozen=True)
class CandidateStats:
    """Normalized 0..1 scores for one candidate; higher is better on every axis."""

    quality: float = 0.0
    cost: float = 0.0
    latency: float = 0.0
    quota: float = 0.0

    def weighted(self, weights: Mapping[str, float]) -> float:
        return sum(getattr(self, name) * float(weights.get(name, 0.0)) for name in DIMENSIONS)


@dataclass(frozen=True)
class PolicySpec:
    """A named routing strategy."""

    policy_id: str
    candidates: tuple[str, ...]
    fallback: tuple[str, ...] = ()
    weights: Mapping[str, float] = field(default_factory=dict)

    def ordered_candidates(self) -> tuple[str, ...]:
        """Primary candidates first, then fallbacks, with duplicates removed.

        Order matters beyond scoring: it is the deterministic tie-break, so two
        equally-scored models always resolve the same way across restarts.
        """
        seen: set[str] = set()
        ordered: list[str] = []
        for name in (*self.candidates, *self.fallback):
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return tuple(ordered)


@dataclass(frozen=True)
class RouteDecision:
    """The chosen route plus the reasoning that produced it."""

    provider_id: str
    upstream_model: str
    policy_id: str
    routing_reason: tuple[str, ...]
    from_sticky: bool = False

    @property
    def candidate_id(self) -> str:
        return f"{self.provider_id}:{self.upstream_model}"


def split_candidate(candidate: str) -> tuple[str, str]:
    """Split a `provider:model` candidate id.

    Only the first colon separates the two — model names legitimately contain
    colons (dated snapshots, vendor-prefixed ids), and splitting on all of them
    would silently truncate the model.
    """
    provider_id, separator, model = candidate.partition(":")
    if not separator or not provider_id or not model:
        raise ValueError(f"candidate must look like 'provider:model', got {candidate!r}")
    return provider_id, model


@dataclass
class Router:
    """Applies the section 8.1 decision order.

    Sticky lookup, health, and stats are injected rather than owned, so routing
    logic stays testable without a database or live providers behind it.
    """

    policies: Mapping[str, PolicySpec]
    routes: Mapping[str, str]
    capabilities: Mapping[str, ModelCapabilities]
    stats: Mapping[str, CandidateStats] = field(default_factory=dict)
    # Returns `HEALTH_OK` for a usable candidate, or a short reason. Default:
    # everything is usable, so a router built without a health source routes
    # exactly as it did before one existed.
    health_check: Callable[[str], str] = _always_healthy
    default_policy_id: str = "balanced"

    def select(
        self,
        identity: RequestIdentity,
        requirements: RequestRequirements,
        *,
        sticky_candidate: str | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> RouteDecision:
        """Choose a route. `exclude` drops candidates already tried this request.

        Failover re-enters here rather than walking a precomputed list, so a
        retry is subject to the same capability and health filters as the first
        attempt — a fallback candidate is not exempt from being wrong.
        """
        policy = self._policy_for(identity)

        if sticky_candidate in exclude:
            sticky_candidate = None

        # A sticky route short-circuits scoring, but never the capability and
        # health filters: a model that has become unusable must not be reused
        # just because it was chosen before.
        sticky_reject_reason: tuple[str, ...] = ()
        if sticky_candidate:
            reason = self._sticky_rejection(sticky_candidate, requirements)
            sticky_reject_reason = reason or ()
            if reason is None:
                provider_id, model = split_candidate(sticky_candidate)
                return RouteDecision(
                    provider_id=provider_id,
                    upstream_model=model,
                    policy_id=policy.policy_id,
                    routing_reason=(
                        f"sticky={sticky_candidate}",
                        f"role={identity.route_hint}",
                        f"kind={identity.request_kind}",
                    ),
                    from_sticky=True,
                )

        return self._score(
            identity,
            requirements,
            policy,
            sticky_reject=sticky_candidate,
            sticky_reject_reason=sticky_reject_reason,
            exclude=exclude,
        )

    def _policy_for(self, identity: RequestIdentity) -> PolicySpec:
        """Map a request to its policy.

        Compaction overrides the role hint. Since remote compaction v2 arrives on
        the normal endpoint, a compaction turn carries whatever role header its
        sub-agent uses — routing it by that role would send a structural
        summarization job to a model picked for, say, code review prose.
        """
        if identity.request_kind == KIND_COMPACTION:
            policy_id = self.routes.get(KIND_COMPACTION, self.default_policy_id)
        else:
            policy_id = self.routes.get(identity.route_hint, self.default_policy_id)
        policy = self.policies.get(policy_id)
        if policy is None:
            raise NoRouteAvailableError(f"policy {policy_id!r} is not configured", {})
        return policy

    def _sticky_rejection(
        self, candidate: str, requirements: RequestRequirements
    ) -> tuple[str, ...] | None:
        capabilities = self.capabilities.get(candidate)
        if capabilities is None:
            return ("unknown-candidate",)
        reasons = requirements.unmet_by(capabilities)
        if reasons:
            return reasons
        verdict = self.health_check(candidate)
        if verdict:
            return (verdict,)
        return None

    def _score(
        self,
        identity: RequestIdentity,
        requirements: RequestRequirements,
        policy: PolicySpec,
        *,
        sticky_reject: str | None,
        sticky_reject_reason: tuple[str, ...] = (),
        exclude: frozenset[str] = frozenset(),
    ) -> RouteDecision:
        ordered = tuple(name for name in policy.ordered_candidates() if name not in exclude)
        eligible, rejected = filter_candidates(
            ((name, self.capabilities[name]) for name in ordered if name in self.capabilities),
            requirements,
        )
        for name in ordered:
            if name not in self.capabilities:
                rejected[name] = ("unknown-candidate",)
        for name in exclude:
            rejected[name] = ("already-tried",)

        healthy: list[str] = []
        retired: set[str] = set()
        for name in eligible:
            verdict = self.health_check(name)
            if not verdict:
                healthy.append(name)
                continue
            rejected[name] = (verdict,)
            if verdict == HEALTH_RETIRED:
                retired.add(name)
        # A sticky route can point at a model no policy still lists, so its
        # verdict has to be carried in rather than recovered from `rejected`.
        if sticky_reject and HEALTH_RETIRED in sticky_reject_reason:
            retired.add(sticky_reject)

        if not healthy:
            raise NoRouteAvailableError(
                f"no candidate satisfies policy {policy.policy_id!r}", rejected
            )

        best = self._best(healthy, ordered, policy.weights)
        provider_id, model = split_candidate(best)
        displaced = self._displaced_by(best, retired, ordered, sticky_reject)

        reason = [
            f"role={identity.route_hint}",
            f"kind={identity.request_kind}",
            f"policy={policy.policy_id}",
        ]
        if sticky_reject:
            reason.append(f"sticky-dropped={sticky_reject}")
        reason.extend(f"requires={name}" for name in sorted(requirements.required))
        # `retired=` is split out of `filtered=` rather than added alongside it:
        # a reader scanning a reason list needs to see at a glance that this
        # candidate is gone for good, not merely losing today.
        reason.extend(f"filtered={name}" for name in sorted(rejected) if name not in retired)
        reason.extend(f"retired={name}" for name in sorted(retired))
        reason.extend(f"fallback-for={name}" for name in displaced)
        reason.append("health=ok")

        if displaced:
            # Warning, per decision, not a one-off at eject time. Silent model
            # substitution is the dangerous failure mode of this whole feature:
            # a reviewer role quietly downgraded to a cheap fallback still
            # returns reviews the operator trusts at full weight. A turn here is
            # an entire agent run, so one line per turn is not a log flood — and
            # the operator has to see it on the turn it affected, not only in
            # whatever scrollback covers the moment the model was ejected.
            log.warning(
                "role=%s policy=%s served by %s because %s is no longer offered by its CLI — "
                "this answer did not come from the policy's preferred model; "
                "run `fluxion-provider check-models` and fix the routing config",
                identity.route_hint,
                policy.policy_id,
                best,
                ", ".join(displaced),
            )

        return RouteDecision(
            provider_id=provider_id,
            upstream_model=model,
            policy_id=policy.policy_id,
            routing_reason=tuple(reason),
        )

    @staticmethod
    def _displaced_by(
        best: str,
        retired: set[str],
        ordered: Sequence[str],
        sticky_reject: str | None,
    ) -> tuple[str, ...]:
        """Which retired candidates the chosen one is standing in for.

        Declared order, not score, decides this. A policy lists its candidates
        best-first and its fallbacks after them, so "ranked ahead of the winner"
        is the config's own statement that this substitution is a downgrade —
        whereas a retired candidate the policy already ranked below the winner
        changes nothing about the answer and is not worth waking anyone for.

        A retired sticky route counts unconditionally: sticking to it was the
        strongest preference there is, and it was overridden.
        """
        position = {name: index for index, name in enumerate(ordered)}
        best_position = position.get(best, len(ordered))
        displaced = {
            name
            for name in retired
            if name in position and position[name] < best_position and name != best
        }
        if sticky_reject in retired and sticky_reject != best:
            displaced.add(str(sticky_reject))
        return tuple(sorted(displaced))

    def _best(
        self,
        healthy: Sequence[str],
        ordered: Sequence[str],
        weights: Mapping[str, float],
    ) -> str:
        """Highest weighted score, ties broken by declared candidate order.

        The tie-break is what makes a decision reproducible: float scores collide
        often (an unconfigured policy scores everything 0.0), and without a
        stable secondary key the same request would drift between models across
        restarts, breaking the sticky guarantee before it is ever stored.
        """
        position = {name: index for index, name in enumerate(ordered)}
        return max(
            healthy,
            key=lambda name: (
                self.stats.get(name, CandidateStats()).weighted(weights),
                -position[name],
            ),
        )
