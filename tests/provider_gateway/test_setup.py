from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from fluxion.availability import Availability
from fluxion.provider_gateway.config import RoutingConfig
from fluxion.provider_gateway.setup import build_setup_plan, plan_to_config

# (id, input price, output price). Codex ships a lineup at one version — an
# order of magnitude apart — while Gemini's effort variants all cost the same.
_CATALOGS = {
    "claude": [
        ("fable", 10.0, 50.0),
        ("opus", 5.0, 25.0),
        ("sonnet", 3.0, 15.0),
        ("haiku", 1.0, 5.0),
    ],
    "codex": [
        ("gpt-5.6-sol", 5.0, 30.0),
        ("gpt-5.6-terra", 2.0, 12.0),
        ("gpt-5.6-luna", 0.2, 1.2),
        ("gpt-5.4-mini", 0.75, 4.5),
    ],
    "antigravity": [
        ("gemini-3.9-flash-high", 0.75, 3.75),
        ("gemini-3.10-flash-high", 0.75, 3.75),
        ("gemini-3.10-flash-medium", 0.75, 3.75),
        ("gemini-3.10-flash-low", 0.75, 3.75),
        ("gemini-3.1-pro-high", 2.0, 12.0),
    ],
}


# Antigravity is absent on purpose: it declares no runtime levels because its
# effort is part of the model id.
_EFFORTS = {
    "fable": ["low", "medium", "high", "xhigh", "max"],
    "opus": ["low", "medium", "high", "xhigh", "max"],
    "sonnet": ["low", "medium", "high", "xhigh", "max"],
    "haiku": ["low", "medium", "high", "xhigh", "max"],
    "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-5.6-terra": ["low", "medium", "high", "xhigh"],
    "gpt-5.6-luna": ["low", "medium", "high"],
    "gpt-5.4-mini": ["low", "medium", "high"],
}


def _plan(monkeypatch, installed: set[str], **kwargs) -> dict[str, Any]:
    monkeypatch.setattr(
        "fluxion.provider_gateway.setup.detect_executor",
        lambda executor, **_: (
            Availability(status="available", detail="found", path=f"/bin/{executor}")
            if executor in installed
            else Availability(status="unavailable", detail="not found")
        ),
    )
    monkeypatch.setattr(
        "fluxion.provider_gateway.setup.list_agent_models_view",
        lambda agent, **_: {
            "models": [
                {
                    "id": model,
                    "input_per_1m": input_price,
                    "output_per_1m": output_price,
                    "supported_reasoning_efforts": _EFFORTS.get(model),
                }
                for model, input_price, output_price in _CATALOGS[agent]
            ]
        },
    )
    # A stub settings object: building the real one loads `.env` into the
    # process environment, which leaks into every later test.
    settings = cast(Any, SimpleNamespace(claude_command="", antigravity_command=""))
    return build_setup_plan(settings=settings, config_file="routes.json", **kwargs)


def _by_role(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {route["role"]: route for route in plan["routes"]}


def test_setup_plan_keeps_read_only_roles_off_an_executor_that_cannot_enforce_it(monkeypatch):
    plan = _plan(monkeypatch, {"claude", "codex", "antigravity"}, worker_executor="antigravity")
    routes = _by_role(plan)

    # `agy` has no read-only mode, so a route pointing there is refused at
    # request time. It may still take the working roles.
    for role in ("explorer", "reviewer"):
        assert "local_agy" not in routes[role]["primary"]
        assert not any("local_agy" in item for item in routes[role]["fallback"])
        assert not routes[role]["warning"]
    assert routes["worker"]["primary"].startswith("local_agy:")


def test_setup_plan_never_defaults_a_role_back_to_the_calling_vendor(monkeypatch):
    plan = _plan(monkeypatch, {"claude", "codex", "antigravity"})

    for route in plan["routes"]:
        assert not route["primary"].startswith("local_codex:")
        assert not any(item.startswith("local_codex:") for item in route["fallback"])
    # Still declared, so it can be chosen deliberately later.
    assert "local_codex" in [provider["id"] for provider in plan["providers"]]
    assert any("caller" in note for note in plan["notes"])


def test_setup_plan_gives_the_working_roles_to_the_chosen_agent(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude", "antigravity"}, worker_executor="claude"))

    # Working roles take the cheap tier of whichever agent runs them.
    assert plan["worker"]["primary"] == "local_claude:haiku"
    assert plan["worker"]["fallback"] == ["local_agy:gemini-3.10-flash-high"]
    # Compaction follows Auto rather than getting its own opinion.
    assert plan["compaction"]["primary"] == plan["auto"]["primary"]
    assert plan["compaction"]["reason_code"] == "inherits_auto"


def test_setup_plan_picks_the_newest_version_in_a_family_numerically(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"antigravity"}, worker_executor="antigravity"))

    # 3.10 beats 3.9, which string ordering would get backwards, and the family
    # the vendor keeps shipping wins over the pro one-off.
    assert plan["worker"]["primary"] == "local_agy:gemini-3.10-flash-high"


def test_setup_plan_spreads_roles_across_a_priced_lineup(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude", "codex"}, worker_executor="codex"))

    # sol/terra/luna are one version at three prices, so they are tiers rather
    # than the single "newest" a version comparison alone would pick. Codex is
    # calibrated with implementation at the cheap end and investigation above
    # it, which is the reverse of the default weighting.
    assert plan["worker"]["primary"] == "local_codex:gpt-5.6-luna"
    assert plan["auto"]["primary"] == "local_codex:gpt-5.6-luna"
    assert plan["compaction"]["primary"] == "local_codex:gpt-5.6-luna"
    assert plan["explorer"]["primary"] == "local_codex:gpt-5.6-terra"
    # Review takes the top of the same lineup.
    assert plan["reviewer"]["primary"] == "local_codex:gpt-5.6-sol"


def test_setup_plan_states_a_reasoning_effort_for_every_route_that_takes_one(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude", "codex"}, worker_executor="codex"))

    # The route editor always shows an effort control, so leaving the config
    # silent means the value on screen, the value stored, and the value the CLI
    # runs at can all disagree.
    # `top` resolves per model: Claude tops out at `max`, this Codex model at
    # `ultra`, and naming either would be wrong on the other.
    assert plan["worker"]["efforts"]["local_codex:gpt-5.6-luna"] == "high"
    assert plan["explorer"]["effort"] == "medium"
    # Reviewer matches what Codex's own role file declares rather than topping
    # out, so the level survives the hop to the agent that does the reviewing.
    assert plan["reviewer"]["efforts"]["local_codex:gpt-5.6-sol"] == "high"
    # Fallbacks carry the role's effort too, not just the primary.
    assert plan["reviewer"]["efforts"]["local_claude:opus"] == "high"
    assert plan["compaction"]["efforts"] == plan["auto"]["efforts"]


def test_setup_plan_leaves_effort_unset_where_it_lives_in_the_model_id(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"antigravity"}, worker_executor="antigravity"))

    # Antigravity spells effort into the id and declares no runtime levels, so
    # nothing is stored for it — that would be a setting its executor never
    # reads. It is still reported, because the difference is in how the effort
    # is delivered, not in what the user is choosing.
    assert plan["worker"]["primary"].endswith("-high")
    assert plan["worker"]["efforts"] == {}
    assert plan["worker"]["effort"] == "high"


def test_setup_plan_falls_back_to_the_nearest_effort_a_model_supports(monkeypatch):
    monkeypatch.setitem(_EFFORTS, "gpt-5.6-terra", ["low"])
    plan = _by_role(_plan(monkeypatch, {"claude", "codex"}, worker_executor="codex"))

    # Explorer asks for `medium`; a model offering only `low` gets the closest
    # thing it has rather than a level it would reject.
    assert plan["explorer"]["efforts"]["local_codex:gpt-5.6-terra"] == "low"


def test_setup_plan_keeps_a_second_vendor_behind_the_read_only_roles(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude", "codex"}, worker_executor="codex"))

    # Read-only roles run on the agent already in the workspace...
    for role in ("explorer", "reviewer"):
        assert plan[role]["reason_code"] == "read_only_follows_worker"
        assert plan[role]["primary"].startswith("local_codex:")
        # ...but a different vendor is what runs when it is out of quota, so
        # the independent view is one failure away rather than gone.
        assert plan[role]["fallback"] == [
            "local_claude:opus" if role == "reviewer" else "local_claude:sonnet"
        ]


def test_setup_plan_crosses_vendors_when_the_working_agent_cannot_be_read_only(monkeypatch):
    plan = _by_role(
        _plan(monkeypatch, {"claude", "codex", "antigravity"}, worker_executor="antigravity")
    )

    # `agy` cannot enforce read-only, so following the worker is not an option
    # and the choice is between Claude and the caller's own vendor.
    for role in ("explorer", "reviewer"):
        assert plan[role]["reason_code"] == "read_only_cross_vendor"
        assert plan[role]["primary"].startswith("local_claude:")


def test_setup_plan_does_not_dress_up_the_only_option_as_a_preference(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude", "antigravity"}, worker_executor="antigravity"))

    # Claude is not preferred here, it is the only agent that can enforce
    # read-only at all. Reporting a preference that was never exercised tells
    # the reader a deliberation happened when none did.
    for role in ("explorer", "reviewer"):
        assert plan[role]["reason_code"] == "read_only_only_option"


def test_setup_plan_does_not_endorse_a_route_the_gateway_will_refuse(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"antigravity"}))

    # With nothing able to enforce read-only, whichever preference picked the
    # executor is not why the route looks like this — and stating one would
    # read as an endorsement of a route that gets refused at request time.
    for role in ("explorer", "reviewer"):
        assert plan[role]["reason_code"] == "read_only_unenforceable"
        assert plan[role]["warning"]


def test_setup_plan_does_not_tier_effort_variants_that_cost_the_same(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"antigravity"}, worker_executor="antigravity"))

    # One price across high/medium/low means they are not tiers. Spending a
    # role down to `medium` would cost the same and deliver less, so every
    # role that lands here gets the strongest effort.
    for role in ("auto", "worker", "compaction", "explorer"):
        assert plan[role]["primary"] == "local_agy:gemini-3.10-flash-high"


def test_setup_plan_warns_when_no_installed_agent_can_serve_a_read_only_role(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"antigravity"}))

    for role in ("explorer", "reviewer"):
        assert plan[role]["primary"].startswith("local_agy:")
        assert "read-only" in plan[role]["warning"]
    # Working roles are unaffected and carry no warning.
    assert not plan["worker"]["warning"]


def test_setup_plan_refuses_to_invent_a_config_with_no_agent_installed(monkeypatch):
    plan = _plan(monkeypatch, set())

    assert plan["routes"] == []
    assert plan["providers"] == []
    assert plan["blockers"] and "No agent CLI" in plan["blockers"][0]


def test_setup_plan_renders_a_config_the_loader_accepts(monkeypatch):
    plan = _plan(monkeypatch, {"claude", "codex", "antigravity"})

    config = plan_to_config(plan)
    routing = RoutingConfig.parse(config, source="<generated>")

    assert routing.routes["compaction"] == routing.routes["auto"]
    assert set(routing.providers) == {"local_claude", "local_codex", "local_agy"}
    # Every candidate a policy names must be declared by its provider.
    for policy in routing.policies.values():
        for candidate in policy.ordered_candidates():
            provider_id, _, model_id = candidate.partition(":")
            assert model_id in routing.providers[provider_id].models


def test_setup_plan_falls_back_to_a_cheaper_model_on_the_same_agent(monkeypatch):
    plan = _by_role(_plan(monkeypatch, {"claude"}))

    # Claude alone: a second choice has to come from Claude, and only a
    # cheaper tier makes sense when the primary is out of quota.
    assert plan["reviewer"]["primary"] == "local_claude:opus"
    assert plan["reviewer"]["fallback"] == ["local_claude:sonnet"]
    assert plan["explorer"]["primary"] == "local_claude:sonnet"
    assert plan["explorer"]["fallback"] == ["local_claude:haiku"]
    # Nothing is cheaper than haiku, so the working roles get no fallback
    # rather than an "upgrade" that would cost more than the user chose.
    assert plan["worker"]["primary"] == "local_claude:haiku"
    assert plan["worker"]["fallback"] == []
