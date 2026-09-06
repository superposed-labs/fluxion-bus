"""Generate a starter routing config from what is installed on this machine.

The old starter config was a literal: one provider, Claude Code, one model,
written whether or not Claude Code was present and whether or not the other
agent CLIs were. A user with three CLIs installed got a config naming one, and
a user with none got a config that failed on its first request.

What replaces it is a plan derived from detection plus each CLI's own catalog,
shown before it is written. Two rules shape the routes, and only the first is
absolute:

* Read-only roles must go to an executor that can enforce read-only. The
  gateway refuses those turns rather than run with write access, so routing
  them anywhere else produces a config that dies on its first use.
* Read-only roles prefer the agent already doing the work, and fall back to a
  different vendor from the caller. Codex is the caller — the whole install
  flow is `print-codex-config` — so where nothing else decides it, a role that
  routes back to Codex repeats the caller's blind spots and shares its quota
  window. This one yields: it shapes fallbacks rather than forbidding a
  same-vendor primary.

Model ids are never hardcoded here. Claude aliases are stable and are used by
name; every other id comes from the CLI's live catalog, picked by rules
(newest in a family, cheapest tier) so a vendor's next release needs no edit.
"""

from __future__ import annotations

import re
from typing import Any

from fluxion.availability import PROVIDERS, detect_executor
from fluxion.config.settings import Settings
from fluxion.executors.antigravity.models import EFFORT_ORDER
from fluxion.executors.registry import executor_read_only_support
from fluxion.mcp_server.model_catalog import list_agent_models_view
from fluxion.provider_gateway import codex_config
from fluxion.usage.model_identity import parse_model_name

# The gateway exists to serve this CLI's sub-agents; `print-codex-config` and
# the install sheet wire it up. "Cross-vendor" is measured against this.
CALLER_EXECUTOR = "codex"

SETUP_ROLES = ("auto", "worker", "explorer", "reviewer", "compaction")

_DEFAULT_PROVIDER_IDS = {
    "claude": "local_claude",
    "codex": "local_codex",
    "antigravity": "local_agy",
}

# Claude exposes no catalog command; its aliases are the stable surface and
# survive version bumps, which is exactly why they can be named in code.
_CLAUDE_TIERS = ("opus", "sonnet", "haiku")

_VERSIONED_FAMILY = re.compile(r"^(?P<prefix>[a-z]+)-(?P<version>\d+(?:\.\d+)*)-(?P<suffix>.+)$")

# Which end of a vendor's lineup each role belongs at.
#
# Implementation sits at the cheap end because that is where the volume is:
# across three weeks of recorded gateway traffic, `worker` was 73% of requests
# and ~88% of total runtime, so it is the tier worth spending down.
#
# Investigation sits above it because nothing checks its answer. A worker
# produces a diff, which tests, the coordinator, and the reviewer role all get
# a look at; an explorer produces a claim about the codebase that is acted on
# directly, so a wrong one propagates silently into everything built on it.
# Lifting it a tier costs almost nothing — explorer was 3% of runtime.
#
# Review takes the top tier: it is the judgement on all of the above, and the
# rarest call of the four.
_ROLE_TIERS = {
    "auto": "cheap",
    "worker": "cheap",
    "explorer": "mid",
    "reviewer": "top",
    "compaction": "cheap",
}

_EFFORT_RANK = {"max": 0, "xhigh": 1, "high": 2, "medium": 3, "low": 4, "minimal": 5}

# Strongest to weakest, used to fall back to the nearest level a model actually
# offers: Codex's lineup varies (one model has `ultra`, an older one has no
# `max`), so a level cannot simply be assumed.
_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# How hard each role should think. Written for every route rather than left
# unset: the route editor always shows an effort control, so a config that
# stores nothing means the value on screen, the value stored, and the value
# the CLI actually runs at can all differ.
#
# `top` resolves to the strongest level the chosen model offers, which differs
# per vendor (`max` for Claude, `ultra` on one Codex model). Naming the level
# instead would silently mean "second strongest" on whichever vendor ships
# another one above it.
# Reviewer stops at `high` rather than the top: that is the level Codex's own
# reviewer role file declares (`_ROLE_EFFORT` in codex_config), and carrying it
# through to the agent that does the reviewing is the whole point of setting it
# here — the Codex-side declaration governs a thread that runs no tools.
_ROLE_EFFORTS = {
    "auto": "top",
    "worker": "top",
    "explorer": "medium",
    "reviewer": "high",
    "compaction": "top",
}

# Read-only roles that run on the working agent when it can enforce read-only,
# rather than reaching for a different vendor.
#
# Crossing vendors for review buys an independent view — a second vendor does
# not share the first's blind spots — but it costs a second subscription's
# quota on every review, and the top tier of the agent already in the workspace
# is a real reviewer. Cross-vendor stays the preference when the working agent
# cannot enforce read-only at all, and remains the reviewer's fallback, so the
# other vendor is still what runs when the first is out of quota.
_FOLLOWS_WORKER_READ_ONLY = ("explorer", "reviewer")


def parse_model_family(model_id: str) -> tuple[str, tuple[int, ...], str] | None:
    """Split ``gemini-3.7-flash-high`` into its family, version and variant.

    Shared with the preferences state, which uses it to notice that a route is
    pinned to a version the vendor has since superseded. Versions come back as
    number tuples so ``3.10`` compares above ``3.9``.
    """
    match = _VERSIONED_FAMILY.match(model_id)
    if not match:
        return None
    version = tuple(int(part) for part in match.group("version").split("."))
    return match.group("prefix"), version, match.group("suffix")


def model_lineup(models: list[dict[str, Any]]) -> list[str]:
    """A vendor's current models, most capable first.

    Exposed for the UI, which otherwise had to guess tiers from codenames —
    matching ``terra``/``sol``/``luna`` as substrings, which silently stops
    working the moment a vendor names its next generation something else.
    """
    normalized = [
        {
            "id": model.get("id"),
            "input": model.get("input", model.get("input_per_1m")),
            "output": model.get("output", model.get("output_per_1m")),
        }
        for model in models
        if model.get("id")
    ]
    return _current_lineup(normalized)


def detect_agents(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    """Probe every agent CLI and read its catalog.

    The expensive half of planning — it shells out to `agy models` and
    `codex debug models` — and the half that does not depend on any choice the
    user makes. Kept separate so several plans can be derived from one probe.
    """
    app_settings = settings or Settings.load()
    read_only_support = executor_read_only_support()

    detected: dict[str, dict[str, Any]] = {}
    for executor in PROVIDERS:
        configured_command = ""
        if executor == "claude":
            configured_command = app_settings.claude_command
        elif executor == "antigravity":
            configured_command = app_settings.antigravity_command
        probe = detect_executor(executor, configured_command=configured_command)
        installed = probe.status == "available"
        catalog_models: list[dict[str, Any]] = []
        catalog_warnings: list[str] = []
        if installed:
            view = list_agent_models_view(agent=executor, project="", settings=app_settings)
            # Prices come along because they are the tier signal: a vendor
            # lineup at one version (Codex's sol/terra/luna) is told apart from
            # effort variants of one model (Gemini's high/medium/low) by
            # whether the entries differ in price at all.
            catalog_models = _routable_models(view)
            catalog_warnings = list(view.get("warnings") or [])
        detected[executor] = {
            "executor": executor,
            "installed": installed,
            "path": probe.path,
            "detail": probe.detail,
            "enforces_read_only": read_only_support.get(executor, False),
            "catalog_ids": [model["id"] for model in catalog_models],
            "catalog_models": catalog_models,
            "catalog_warnings": catalog_warnings,
        }
    return detected


def build_setup_plan(
    *,
    settings: Settings | None = None,
    config_file: str = "",
    config_exists: bool = False,
    worker_executor: str = "",
    detected: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the config that would be written, without writing anything."""
    read_only_roles = codex_config.read_only_roles()
    detected = detected if detected is not None else detect_agents(settings)

    installed_executors = [name for name in PROVIDERS if detected[name]["installed"]]
    worker_executor = (worker_executor or "").strip().lower()
    if worker_executor not in installed_executors:
        worker_executor = _default_worker_executor(installed_executors)

    notes: list[str] = []
    blockers: list[str] = []
    if not installed_executors:
        blockers.append(
            "No agent CLI was found on this Mac. Install Claude Code, Codex, or "
            "Antigravity first — a routing config that names none of them cannot "
            "serve a request."
        )
        return {
            "config_file": config_file,
            "config_exists": config_exists,
            "caller_executor": CALLER_EXECUTOR,
            "worker_executor": "",
            "worker_options": [],
            "executors": [detected[name] for name in PROVIDERS],
            "providers": [],
            "routes": [],
            "blockers": blockers,
            "notes": notes,
        }

    routes: list[dict[str, Any]] = []
    used: dict[str, list[str]] = {}

    for role in SETUP_ROLES:
        if role == "compaction":
            # Compaction is not a Codex role; it follows Auto unless a user
            # splits it later, and inheriting keeps that relationship visible.
            auto = next(item for item in routes if item["role"] == "auto")
            routes.append(
                {
                    "role": role,
                    "primary": auto["primary"],
                    "fallback": list(auto["fallback"]),
                    "efforts": dict(auto["efforts"]),
                    "effort": auto["effort"],
                    "reason_code": "inherits_auto",
                    "reason": "Follows Auto.",
                    "warning": "",
                }
            )
            continue

        needs_read_only = role in read_only_roles
        eligible = [
            name
            for name in installed_executors
            if not needs_read_only or detected[name]["enforces_read_only"]
        ]
        warning = ""
        if needs_read_only and not eligible:
            # Every installed CLI would be refused for this role. Write the best
            # available anyway and say so: a role left out of `routes` silently
            # falls through to the default policy, which is harder to notice.
            eligible = list(installed_executors)
            warning = (
                "No installed agent can enforce read-only, so this role will be "
                "refused at request time. Install Claude Code or Codex to fix it."
            )

        primary_executor, reason_code, reason = _pick_executor(
            role=role,
            eligible=eligible,
            worker_executor=worker_executor,
            needs_read_only=needs_read_only,
        )
        if warning:
            # Nothing here can honour the role. Whatever preference picked the
            # executor, that is not why this route looks the way it does, and
            # reporting one would read as an endorsement of a route the
            # gateway is going to refuse.
            reason_code = "read_only_unenforceable"
            reason = "No installed agent can enforce read-only for this role."
        primary = _candidate(primary_executor, role, detected, used)
        fallback: list[str] = []
        candidate = _fallback_candidate(
            role=role,
            primary=primary,
            primary_executor=primary_executor,
            eligible=eligible,
            detected=detected,
            used=used,
        )
        if candidate:
            fallback.append(candidate)

        efforts = _efforts_for([primary, *fallback], role, detected)
        routes.append(
            {
                "role": role,
                "primary": primary,
                "fallback": fallback,
                "efforts": efforts,
                # What the primary will run at. Populated for every agent, even
                # the one whose effort is part of the model id and so needs no
                # entry above: the difference is in how it is delivered, not in
                # what the user is choosing.
                "effort": efforts.get(primary) or _effort_in_model_id(primary),
                "reason_code": reason_code,
                "reason": reason,
                "warning": warning,
            }
        )

    providers = []
    for executor in installed_executors:
        models = used.get(executor) or _seed_for(executor, detected)
        if not models:
            notes.append(
                f"{executor}: its CLI is installed but reported no models, so it is "
                "declared without one and cannot be routed to yet."
            )
            continue
        providers.append(
            {
                "id": _DEFAULT_PROVIDER_IDS.get(executor, f"local_{executor}"),
                "executor": executor,
                "models": models,
            }
        )

    if CALLER_EXECUTOR in installed_executors and not any(
        route["primary"].startswith(f"{_DEFAULT_PROVIDER_IDS[CALLER_EXECUTOR]}:")
        for route in routes
    ):
        notes.append(
            "Codex is declared but no role defaults to it: it is the caller, so "
            "routing back to it would share one vendor's blind spots and quota."
        )

    return {
        "config_file": config_file,
        "config_exists": config_exists,
        "caller_executor": CALLER_EXECUTOR,
        "worker_executor": worker_executor,
        "worker_options": installed_executors,
        "executors": [detected[name] for name in PROVIDERS],
        "providers": providers,
        "routes": routes,
        "blockers": blockers,
        "notes": notes,
    }


def build_setup_plans(
    *,
    settings: Settings | None = None,
    config_file: str = "",
    config_exists: bool = False,
    worker_executor: str = "",
) -> dict[str, Any]:
    """The chosen plan, plus one for every other agent the user could pick.

    Switching the working-roles agent changes only the derivation, never the
    catalogs, so re-probing for each choice would make the UI wait seconds to
    recompute something it already had the inputs for. One probe, N plans.
    """
    detected = detect_agents(settings)
    plan = build_setup_plan(
        config_file=config_file,
        config_exists=config_exists,
        worker_executor=worker_executor,
        detected=detected,
    )
    plan["alternatives"] = [
        {key: alternative[key] for key in ("worker_executor", "providers", "routes", "notes")}
        for option in plan["worker_options"]
        if option != plan["worker_executor"]
        for alternative in [
            build_setup_plan(
                config_file=config_file,
                config_exists=config_exists,
                worker_executor=option,
                detected=detected,
            )
        ]
    ]
    return plan


def plan_to_config(plan: dict[str, Any]) -> dict[str, Any]:
    """Render a plan as the routing config JSON it describes."""
    providers = [
        {
            "id": provider["id"],
            "protocol": "local_agent",
            "executor": provider["executor"],
            "enabled": True,
            "default_workspace": "",
            "models": [{"id": model_id, "capabilities": {}} for model_id in provider["models"]],
        }
        for provider in plan["providers"]
    ]

    policies: dict[str, Any] = {}
    routes: dict[str, str] = {}
    for route in plan["routes"]:
        role = route["role"]
        if route["reason_code"] == "inherits_auto":
            routes[role] = routes["auto"]
            continue
        policy: dict[str, Any] = {"candidates": [route["primary"]]}
        if route["fallback"]:
            policy["fallback"] = list(route["fallback"])
        if route.get("efforts"):
            policy["efforts"] = dict(route["efforts"])
        policies[role] = policy
        routes[role] = role

    return {
        "version": 1,
        "providers": providers,
        "policies": policies,
        "routes": routes,
    }


def _default_worker_executor(installed: list[str]) -> str:
    for preferred in ("antigravity", "claude", "codex"):
        if preferred in installed:
            return preferred
    return installed[0] if installed else ""


def _efforts_for(
    candidates: list[str],
    role: str,
    detected: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Reasoning effort for each candidate that takes one as a runtime option.

    Antigravity carries effort inside the model id and declares no runtime
    levels, so it gets no entry here — writing one would store a setting its
    executor never reads.
    """
    wanted = _ROLE_EFFORTS.get(role, "medium")
    efforts: dict[str, str] = {}
    for candidate in candidates:
        provider_id, _, model_id = candidate.partition(":")
        if not model_id:
            continue
        executor = next(
            (
                name
                for name, default_id in _DEFAULT_PROVIDER_IDS.items()
                if default_id == provider_id
            ),
            "",
        )
        supported = _supported_efforts(executor, model_id, detected)
        if not supported:
            continue
        efforts[candidate] = _nearest_effort(wanted, supported)
    return efforts


def _effort_in_model_id(candidate: str) -> str:
    """The effort an id carries as a suffix, as Antigravity's do."""
    suffix = candidate.rpartition(":")[2].rsplit("-", 1)[-1].lower()
    return suffix if suffix in _EFFORT_ORDER else ""


def _supported_efforts(
    executor: str,
    model_id: str,
    detected: dict[str, dict[str, Any]],
) -> list[str]:
    catalog = detected.get(executor, {}).get("catalog_models") or []
    entry = next((model for model in catalog if model.get("id") == model_id), None)
    return [str(level).lower() for level in (entry or {}).get("efforts") or []]


def _nearest_effort(wanted: str, supported: list[str]) -> str:
    """The supported level closest to the one this role asked for."""
    ranked_all = [level for level in _EFFORT_ORDER if level in supported]
    if wanted == "top":
        return ranked_all[-1] if ranked_all else supported[0]
    if wanted in supported:
        return wanted
    if wanted not in _EFFORT_ORDER:
        return supported[0]
    target = _EFFORT_ORDER.index(wanted)
    ranked = [level for level in supported if level in _EFFORT_ORDER]
    if not ranked:
        return supported[0]
    return min(ranked, key=lambda level: abs(_EFFORT_ORDER.index(level) - target))


def _pick_executor(
    *,
    role: str,
    eligible: list[str],
    worker_executor: str,
    needs_read_only: bool,
) -> tuple[str, str, str]:
    if not needs_read_only:
        if worker_executor in eligible:
            return (
                worker_executor,
                "main_agent",
                "Your main agent.",
            )
        return eligible[0], "only_available", "The only installed agent for this role."

    if len(eligible) == 1:
        # Say so plainly rather than dressing up the only option as a choice:
        # the rules below would all "prefer" it, and reporting a preference
        # that was never exercised misleads anyone reading the plan.
        return (
            eligible[0],
            "read_only_only_option",
            "The only installed agent that can enforce read-only.",
        )

    if role in _FOLLOWS_WORKER_READ_ONLY and worker_executor in eligible:
        return (
            worker_executor,
            "read_only_follows_worker",
            "Read-only, and runs on the agent already doing the work.",
        )

    # Read-only role: prefer an eligible executor that is not the caller's
    # vendor, so a reviewer does not repeat the caller's blind spots.
    cross_vendor = [name for name in eligible if name != CALLER_EXECUTOR]
    if cross_vendor:
        return (
            cross_vendor[0],
            "read_only_cross_vendor",
            "Enforces read-only, and is a different vendor from the caller.",
        )
    return (
        eligible[0],
        "read_only_only_option",
        "The only installed agent that can enforce read-only.",
    )


def _fallback_candidate(
    *,
    role: str,
    primary: str,
    primary_executor: str,
    eligible: list[str],
    detected: dict[str, dict[str, Any]],
    used: dict[str, list[str]],
) -> str:
    """A second choice for this role, or none.

    The caller's own vendor is excluded here as well as from primaries. A
    fallback is still a default this generator chose, and defaulting any role
    back to the caller shares the blind spots and the quota window that routing
    out to another agent was meant to avoid.
    """
    others = [name for name in eligible if name != primary_executor and name != CALLER_EXECUTOR]
    for preferred in ("claude", "antigravity", "codex"):
        if preferred not in others:
            continue
        candidate = _candidate(preferred, role, detected, used)
        if candidate and candidate != primary:
            return candidate

    # Nothing else is eligible. A cheaper model on the same executor is still a
    # real second choice when the primary is out of quota — but only cheaper:
    # an "upgrade" on failure would quietly cost more than the user chose.
    if primary_executor != "claude":
        return ""
    primary_model = primary.partition(":")[2]
    tiers = list(_CLAUDE_TIERS)
    if primary_model not in tiers:
        return ""
    cheaper = tiers[tiers.index(primary_model) + 1 :]
    if not cheaper:
        return ""
    model = cheaper[0]
    used.setdefault("claude", [])
    if model not in used["claude"]:
        used["claude"].append(model)
    return f"{_DEFAULT_PROVIDER_IDS['claude']}:{model}"


def _candidate(
    executor: str,
    role: str,
    detected: dict[str, dict[str, Any]],
    used: dict[str, list[str]],
) -> str:
    model = _pick_model(executor, role, detected)
    if not model:
        return ""
    declared = used.setdefault(executor, [])
    if model not in declared:
        declared.append(model)
    provider_id = _DEFAULT_PROVIDER_IDS.get(executor, f"local_{executor}")
    return f"{provider_id}:{model}"


def _pick_model(executor: str, role: str, detected: dict[str, dict[str, Any]]) -> str:
    catalog = detected[executor].get("catalog_models") or []
    if executor == "claude":
        # Claude's aliases are its tier ladder, so it uses the same role map as
        # every other vendor rather than a second table that has to be kept in
        # agreement with it.
        return _CLAUDE_TIERS[_tier_index(role, len(_CLAUDE_TIERS))]
    if not catalog:
        return ""
    ranked = _current_lineup(catalog)
    if not ranked:
        return str(catalog[-1]["id"])
    return ranked[_tier_index(role, len(ranked))]


def _tier_index(role: str, count: int) -> int:
    """Where in a tier ladder this role belongs, given how many rungs it has."""
    tier = _ROLE_TIERS.get(role, "mid")
    if tier == "top":
        return 0
    if tier == "cheap":
        return count - 1
    return (count - 1) // 2


def _current_lineup(catalog: list[dict[str, Any]]) -> list[str]:
    """This vendor's newest models, ordered most capable first.

    Ids like ``gemini-3.7-flash-high`` or ``gpt-5.6-terra`` carry a version
    between a prefix and a variant. Versions are compared numerically, not as
    strings where ``3.10`` would sort below ``3.9``, so the next release is
    picked up without an edit here — the point of never naming one.

    Ordering within that version is by price, because price is what separates a
    lineup (Codex ships sol, terra and luna at one version, an order of
    magnitude apart) from effort variants of a single model (Gemini's flash
    high/medium/low all cost the same). When prices are equal there are no
    tiers to choose between, and every role gets the strongest effort.
    """
    parsed: list[tuple[str, tuple[int, ...], str, dict[str, Any]]] = []
    for model in catalog:
        match = _VERSIONED_FAMILY.match(str(model["id"]))
        if not match:
            continue
        version = tuple(int(part) for part in match.group("version").split("."))
        parsed.append((match.group("prefix"), version, match.group("suffix"), model))
    if not parsed:
        return []

    # The prefix with the most entries is the vendor's own line; a catalog can
    # also list other vendors' models (Antigravity offers Claude and GPT ids).
    prefixes: dict[str, int] = {}
    for prefix, _, _, _ in parsed:
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    main_prefix = max(prefixes, key=lambda key: prefixes[key])

    newest = max(version for prefix, version, _, _ in parsed if prefix == main_prefix)
    current = [
        (suffix, model)
        for prefix, version, suffix, model in parsed
        if prefix == main_prefix and version == newest
    ]

    prices = {_price_of(model) for _, model in current}
    if len(prices) == 1:
        # One price across the whole version means these are not tiers, so
        # there is nothing for a role to choose between. Collapse to the
        # strongest effort and let every role have it — spending a role down to
        # `medium` would buy nothing back.
        current.sort(key=lambda item: (_EFFORT_RANK.get(item[0].rsplit("-", 1)[-1], 99), item[0]))
        return [str(current[0][1]["id"])]

    current.sort(key=lambda item: (-_price_of(item[1]), item[0]))
    return [str(model["id"]) for _, model in current]


def _routable_models(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a model catalog into entries a route can be pinned to.

    A route stores a launchable model id. Where effort is a runtime flag the
    product id is itself launchable and the efforts ride along as a choice; where
    it is fused into the id (Antigravity) the product row is not launchable, so
    the published variants are listed individually and each already carries its
    effort.
    """
    fused = str(view.get("effort_encoding") or "") == "model_id_suffix"
    routable: list[dict[str, Any]] = []
    for model in view.get("models", []):
        base = {
            "input": model.get("input_per_1m"),
            "output": model.get("output_per_1m"),
        }
        if not fused:
            routable.append(
                {
                    "id": str(model["id"]),
                    **base,
                    # Present only where effort is a runtime option.
                    "efforts": model.get("supported_reasoning_efforts"),
                }
            )
            continue
        variants = model.get("variants")
        if not variants:
            # No effort axis: the product row is the launchable id (agy's Claude
            # entries). Dropping these would silently shrink the routable set.
            routable.append({"id": str(model["id"]), **base, "efforts": None})
            continue
        for effort in model.get("supported_reasoning_efforts") or []:
            model_id = str(variants.get(str(effort)) or "")
            if model_id:
                routable.append({"id": model_id, **base, "efforts": None})
    return routable


def _price_of(model: dict[str, Any]) -> float:
    """Output price leads: it is where lineup tiers differ most."""
    return float(model.get("output") or 0.0) * 1000 + float(model.get("input") or 0.0)


def _seed_for(executor: str, detected: dict[str, dict[str, Any]]) -> list[str]:
    """A declared model for an executor no role routes to."""
    if executor == "claude":
        return ["sonnet"]
    catalog_models = detected[executor]["catalog_models"]
    if not catalog_models:
        return []
    # The cheapest model. Effort variants of one model are priced identically, so
    # the tie-break picks the cheapest effort rather than whichever sorted last.
    cheapest = min(catalog_models, key=lambda model: (_price_of(model), _seed_effort_rank(model)))
    return [str(cheapest["id"])]


def _seed_effort_rank(model: dict[str, Any]) -> int:
    effort = parse_model_name("antigravity", str(model.get("id") or "")).effort
    return EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else len(EFFORT_ORDER)
