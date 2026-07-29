"""Check configured model ids against what the agent CLIs actually offer.

Model ids reach the CLI verbatim (`--model` / `-m`), and nothing between this
file and the process launch knows whether the id still exists. When a vendor
retires one, the routing config keeps selecting it and every turn on that route
dies at the CLI — and the policy's `fallback` does not help, because the gateway
makes one attempt per turn by design (see `_run_local_agent` in app.py). So the
only place this can be caught is before a turn is ever routed.

The distinction this module exists to preserve: "the catalog is readable and
this id is absent" is a real defect, while "the catalog could not be read" is
an unknown. Treating the second as the first would let a CLI upgrade, a timeout,
or a missing binary condemn a perfectly good configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from fluxion.provider_gateway.config import RoutingConfig

# Executors whose CLI can list its own models, and how to ask. Both loaders
# return `(names, error)` and cache internally, so calling this per provider
# costs one subprocess per CLI per hour rather than one per candidate.
_CATALOG_LOADERS: dict[str, Callable[[], tuple[list[str], str]]] = {}


def _antigravity_catalog() -> tuple[list[str], str]:
    from fluxion.executors.antigravity.models import load_antigravity_model_catalog

    return load_antigravity_model_catalog()


def _codex_catalog() -> tuple[list[str], str]:
    from fluxion.executors.codex.models import load_codex_model_catalog

    return load_codex_model_catalog()


_CATALOG_LOADERS["antigravity"] = _antigravity_catalog
_CATALOG_LOADERS["codex"] = _codex_catalog

# Executors with no catalog command, and why. Claude Code takes aliases
# (`opus`, `sonnet`, `haiku`) that outlive individual model versions, so there
# is less to rot here — but it also means an unverifiable id is normal rather
# than suspicious, and must not be reported as a problem.
_NO_CATALOG_REASON = {
    "claude": "Claude Code exposes no model catalog command; its aliases survive version bumps",
}


@dataclass(frozen=True)
class ExecutorCatalog:
    """One CLI's live model ids, or the reason they are unknown."""

    executor: str
    model_ids: frozenset[str] = frozenset()
    error: str = ""
    # False when this executor has no catalog command at all, which is a
    # permanent property rather than a transient failure.
    supported: bool = True

    def __post_init__(self) -> None:
        """Fold ids to lowercase here rather than at each construction site.

        `verify_configured_models` accepts injected catalogs, so ids can arrive
        from anywhere. If normalization lived only in `load_catalog`, a caller
        passing a catalog straight from a CLI would have every model reported
        missing — the one false positive this whole check must never produce.
        """
        object.__setattr__(
            self,
            "model_ids",
            frozenset(item.strip().lower() for item in self.model_ids if item.strip()),
        )

    @property
    def readable(self) -> bool:
        return self.supported and not self.error and bool(self.model_ids)

    def has(self, model_id: str) -> bool:
        return model_id.strip().lower() in self.model_ids


@dataclass(frozen=True)
class ModelVerification:
    """Per-candidate outcome, split by how much it is worth acting on."""

    # `provider:model` ids a readable catalog does not list. Real defects.
    missing: tuple[str, ...] = ()
    # `provider:model` ids confirmed present.
    verified: tuple[str, ...] = ()
    # `(candidate, reason)` for ids that could not be checked either way.
    unverified: tuple[tuple[str, str], ...] = ()
    # Human-readable notes about the catalogs themselves.
    catalog_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether anything actionable was found. Unverified ids do not count."""
        return not self.missing


def load_catalog(executor: str) -> ExecutorCatalog:
    """Fetch one executor's live model ids. Never raises."""
    name = executor.strip().lower()
    if name in _NO_CATALOG_REASON:
        return ExecutorCatalog(executor=name, supported=False, error=_NO_CATALOG_REASON[name])
    loader = _CATALOG_LOADERS.get(name)
    if loader is None:
        return ExecutorCatalog(
            executor=name,
            supported=False,
            error=f"no model catalog is known for executor {name!r}",
        )
    try:
        names, error = loader()
    except Exception as exc:  # noqa: BLE001 - an unknown beats a false accusation
        return ExecutorCatalog(executor=name, error=f"{type(exc).__name__}: {exc}")
    return ExecutorCatalog(executor=name, model_ids=frozenset(names), error=error)


def verify_configured_models(
    routing: RoutingConfig,
    *,
    catalogs: Mapping[str, ExecutorCatalog] | None = None,
) -> ModelVerification:
    """Compare every enabled provider's declared models against its CLI catalog.

    Disabled providers are skipped: their models are not reachable, so a stale
    id there is a note for later rather than something to fail on now.
    """
    resolved: dict[str, ExecutorCatalog] = dict(catalogs or {})
    missing: list[str] = []
    verified: list[str] = []
    unverified: list[tuple[str, str]] = []
    notes: list[str] = []

    for provider_id, spec in sorted(routing.enabled_providers().items()):
        executor = spec.executor.strip().lower()
        if executor not in resolved:
            resolved[executor] = load_catalog(executor)
            catalog = resolved[executor]
            if catalog.readable:
                notes.append(f"{executor}: {len(catalog.model_ids)} model(s) listed by its CLI")
            elif not catalog.supported:
                # Not a fault to fix, so it must not read like one.
                notes.append(f"{executor}: no catalog to check against — {catalog.error}")
            else:
                notes.append(f"{executor}: catalog unavailable — {catalog.error}")
        catalog = resolved[executor]
        for model_id in sorted(spec.models):
            candidate = f"{provider_id}:{model_id}"
            if not catalog.readable:
                unverified.append((candidate, catalog.error))
            elif catalog.has(model_id):
                verified.append(candidate)
            else:
                missing.append(candidate)

    return ModelVerification(
        missing=tuple(missing),
        verified=tuple(verified),
        unverified=tuple(unverified),
        catalog_notes=tuple(notes),
    )


def describe_missing(routing: RoutingConfig, candidate: str) -> str:
    """Explain the blast radius of one retired model: which roles it serves.

    A bare "this id is gone" leaves the operator to work out whether it matters.
    Naming the roles turns it into a decision they can make immediately.
    """
    roles = sorted(
        role
        for role, policy_id in routing.routes.items()
        for policy in [routing.policies.get(policy_id)]
        if policy is not None and candidate in policy.ordered_candidates()
    )
    if not roles:
        return f"{candidate} is not listed by its CLI (no role routes to it)"
    return f"{candidate} is not listed by its CLI; it serves role(s): {', '.join(roles)}"
