from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fluxion.availability import Availability
from fluxion.provider_gateway.cli import _parse_args
from fluxion.provider_gateway.codex_config import BEGIN_MARKER, END_MARKER
from fluxion.provider_gateway.config import GatewaySettings, RoutingConfig
from fluxion.provider_gateway.preferences import add_provider, preferences_state, set_route


def _config() -> dict:
    return {
        "version": 1,
        "providers": [
            {
                "id": "local_agy",
                "executor": "antigravity",
                "models": [{"id": "flash"}, {"id": "flash-next"}],
            },
            {
                "id": "local_claude",
                "executor": "claude",
                "models": [{"id": "haiku"}],
            },
        ],
        "policies": {
            "balanced": {
                "candidates": ["local_agy:flash"],
                "fallback": ["local_claude:haiku"],
                "weights": {"cost": 1},
            }
        },
        "routes": {
            "auto": "balanced",
            "worker": "balanced",
            "compaction": "balanced",
        },
    }


def _write(path: Path) -> None:
    path.write_text(json.dumps(_config(), indent=2) + "\n", encoding="utf-8")


def test_doctor_can_check_an_already_running_gateway():
    assert _parse_args(["doctor", "--running"]).running is True


def test_preferences_state_exposes_routes_without_live_catalogs(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    _write(path)
    monkeypatch.setattr(
        "fluxion.provider_gateway.preferences.verify_configured_models",
        lambda routing: type(
            "Verification",
            (),
            {"missing": (), "unverified": (), "catalog_notes": ()},
        )(),
    )

    state = preferences_state(
        GatewaySettings(config_file=path, token_file=tmp_path / "provider.token"),
        include_catalogs=False,
        codex_home=tmp_path / "codex",
    )

    assert state["configured"] is True
    assert state["token_available"] is False
    assert state["catalogs"] == []
    assert state["routes"][0] == {
        "role": "auto",
        "policy": "balanced",
        "candidates": ["local_agy:flash"],
        "fallback": ["local_claude:haiku"],
        "weights": {"cost": 1.0},
        "efforts": {},
        "inherits_auto": False,
    }
    assert state["routes"][-1]["role"] == "compaction"
    assert state["routes"][-1]["inherits_auto"] is True


def test_codex_state_marks_an_existing_unreadable_role_unhealthy(tmp_path):
    codex_home = tmp_path / "codex"
    agents_dir = codex_home / "agents"
    agents_dir.mkdir(parents=True)
    (codex_home / "config.toml").write_text(f"{BEGIN_MARKER}\n{END_MARKER}\n")
    for role in ("auto", "explorer", "reviewer", "worker"):
        (agents_dir / f"{role}.toml").write_text(
            'model = "gpt-x"\nmodel_provider = "fluxion_auto"\n'
        )
    (agents_dir / "worker.toml").write_text("not valid [toml")

    state = preferences_state(
        GatewaySettings(
            config_file=tmp_path / "missing-routes.json",
            token_file=tmp_path / "provider.token",
        ),
        include_catalogs=False,
        codex_home=codex_home,
    )
    worker = next(role for role in state["codex"]["roles"] if role["role"] == "worker")

    assert worker["installed"] is True
    assert worker["readable"] is False
    assert worker["error"]
    assert state["codex"]["installed"] is False


def test_preferences_state_can_skip_all_live_model_probes(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    _write(path)

    def unexpected_probe(_routing):
        raise AssertionError("local-only state must not probe executor catalogs")

    monkeypatch.setattr(
        "fluxion.provider_gateway.preferences.verify_configured_models",
        unexpected_probe,
    )
    state = preferences_state(
        GatewaySettings(config_file=path),
        include_catalogs=False,
        include_model_health=False,
        codex_home=tmp_path / "codex",
    )

    assert state["catalogs"] == []
    assert state["model_health"] == {
        "missing": [],
        "unverified": [],
        "notes": [],
    }


def test_preferences_state_parser_supports_local_only_read():
    args = _parse_args(["preferences-state", "--skip-catalogs", "--skip-model-health"])

    assert args.skip_catalogs is True
    assert args.skip_model_health is True


def test_set_route_parser_accepts_candidate_effort():
    args = _parse_args(
        [
            "set-route",
            "--role",
            "auto",
            "--candidate",
            "local_claude:opus",
            "--effort",
            "local_claude:opus=xhigh",
        ]
    )

    assert args.effort == [("local_claude:opus", "xhigh")]


def test_set_route_splits_shared_policy_and_preserves_other_roles(tmp_path):
    path = tmp_path / "routes.json"
    backup_dir = tmp_path / "data" / "backups" / "provider-routing"
    _write(path)
    path.chmod(0o640)

    backup = set_route(
        path,
        role="worker",
        candidates=["local_agy:flash-next"],
        fallback=["local_claude:haiku"],
        backup_dir=backup_dir,
    )

    assert backup.exists()
    assert backup.parent == backup_dir
    assert json.loads(backup.read_text()) == _config()
    updated = json.loads(path.read_text())
    assert updated["routes"]["worker"] == "worker"
    assert updated["routes"]["auto"] == "balanced"
    assert updated["routes"]["compaction"] == "balanced"
    assert updated["policies"]["worker"]["candidates"] == ["local_agy:flash-next"]
    assert updated["policies"]["worker"]["weights"] == {"cost": 1}
    assert path.stat().st_mode & 0o777 == 0o640
    RoutingConfig.load(path)


def test_set_route_keeps_only_the_ten_newest_backups(tmp_path):
    path = tmp_path / "routes.json"
    backup_dir = tmp_path / "data" / "backups" / "provider-routing"
    _write(path)

    for index in range(12):
        set_route(
            path,
            role="worker",
            candidates=["local_agy:flash-next" if index % 2 else "local_agy:flash"],
            fallback=["local_claude:haiku"],
            backup_dir=backup_dir,
        )

    backups = list(backup_dir.glob("routes.json.bak.*"))
    assert len(backups) == 10


def test_set_route_persists_effort_and_preserves_retained_fallback_effort(tmp_path):
    path = tmp_path / "routes.json"
    raw = _config()
    raw["policies"]["balanced"]["efforts"] = {"local_claude:haiku": "low"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    set_route(
        path,
        role="auto",
        candidates=["local_claude:haiku"],
        fallback=["local_agy:flash"],
        efforts={"local_claude:haiku": "high"},
    )

    updated = json.loads(path.read_text())
    policy = updated["policies"][updated["routes"]["auto"]]
    assert policy["efforts"] == {"local_claude:haiku": "high"}
    assert RoutingConfig.load(path).policies[updated["routes"]["auto"]].efforts == {
        "local_claude:haiku": "high"
    }


def test_set_route_keeps_compaction_inheriting_auto_when_auto_is_split(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)

    set_route(
        path,
        role="auto",
        candidates=["local_agy:flash-next"],
        fallback=[],
    )

    updated = json.loads(path.read_text())
    assert updated["routes"]["auto"] == "auto"
    assert updated["routes"]["compaction"] == "auto"
    assert updated["routes"]["worker"] == "balanced"


def test_set_route_splits_policy_named_for_role_when_another_role_shares_it(tmp_path):
    path = tmp_path / "routes.json"
    raw = _config()
    raw["policies"]["worker"] = raw["policies"].pop("balanced")
    raw["routes"] = {
        "worker": "worker",
        "explorer": "worker",
        "compaction": "worker",
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    set_route(
        path,
        role="worker",
        candidates=["local_agy:flash-next"],
        fallback=[],
    )

    updated = json.loads(path.read_text())
    assert updated["routes"]["worker"] == "worker_route"
    assert updated["routes"]["explorer"] == "worker"
    assert updated["policies"]["worker"]["candidates"] == ["local_agy:flash"]
    assert updated["policies"]["worker_route"]["candidates"] == ["local_agy:flash-next"]


def test_set_route_rejects_unknown_model_without_touching_file(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)
    before = path.read_text()

    try:
        set_route(
            path,
            role="worker",
            candidates=["local_agy:not-real"],
            fallback=[],
        )
    except ValueError as error:
        assert "not-real" in str(error)
    else:
        raise AssertionError("unknown model should fail validation")

    assert path.read_text() == before
    assert list(tmp_path.glob("*.bak.*")) == []


def _fake_detection(monkeypatch, installed: set[str]) -> None:
    """Pin CLI detection so executor state does not depend on the test machine."""
    monkeypatch.setattr(
        "fluxion.provider_gateway.preferences.detect_executor",
        lambda executor, **_: (
            Availability(status="available", detail="found", path=f"/bin/{executor}")
            if executor in installed
            else Availability(status="unavailable", detail="not found")
        ),
    )


def test_preferences_state_reports_an_installed_executor_missing_from_the_config(
    tmp_path, monkeypatch
):
    path = tmp_path / "routes.json"
    _write(path)
    _fake_detection(monkeypatch, {"claude", "antigravity", "codex"})

    state = preferences_state(
        GatewaySettings(config_file=path),
        include_catalogs=False,
        include_model_health=False,
        codex_home=tmp_path / "codex",
    )
    by_executor = {entry["executor"]: entry for entry in state["executors"]}

    # The config declares claude and antigravity but never mentions codex, which
    # is exactly the case the picker used to render as "does not exist".
    assert by_executor["claude"]["state"] == "ready"
    assert by_executor["antigravity"]["state"] == "ready"
    assert by_executor["codex"]["state"] == "available"
    assert by_executor["codex"]["provider_ids"] == []


def test_preferences_state_separates_a_missing_cli_from_a_disabled_provider(tmp_path, monkeypatch):
    raw = _config()
    raw["providers"][1]["enabled"] = False
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    _fake_detection(monkeypatch, set())

    state = preferences_state(
        GatewaySettings(config_file=path),
        include_catalogs=False,
        include_model_health=False,
        codex_home=tmp_path / "codex",
    )
    by_executor = {entry["executor"]: entry for entry in state["executors"]}

    # Switched off on purpose: not worth nagging about its absent CLI.
    assert by_executor["claude"]["state"] == "disabled"
    assert by_executor["antigravity"]["state"] == "cli_missing"
    assert by_executor["codex"]["state"] == "not_installed"


def test_preferences_state_reports_executors_before_any_config_exists(tmp_path, monkeypatch):
    _fake_detection(monkeypatch, {"antigravity"})

    state = preferences_state(
        GatewaySettings(config_file=tmp_path / "absent.json"),
        include_catalogs=False,
        codex_home=tmp_path / "codex",
    )

    assert state["configured"] is False
    assert [entry["state"] for entry in state["executors"]] == [
        "not_installed",  # claude
        "not_installed",  # codex
        "available",  # antigravity
    ]


def test_add_provider_declares_an_executor_without_changing_routes(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    _write(path)
    monkeypatch.setattr(
        "fluxion.provider_gateway.preferences.list_agent_models_view",
        lambda **_: {"models": [{"id": "gpt-expensive"}, {"id": "gpt-cheap"}]},
    )
    before = json.loads(path.read_text())

    # A stub stands in for Settings: building the real one loads `.env` into
    # the process environment, which leaks into every later test.
    result = add_provider(
        path,
        executor="codex",
        settings=cast(Any, object()),
        backup_dir=tmp_path / "backups",
    )

    updated = json.loads(path.read_text())
    assert result["provider_id"] == "local_codex"
    # Seeded from the cheapest catalog entry, which is last in price order.
    assert result["models"] == ["gpt-cheap"]
    assert updated["providers"][-1] == {
        "id": "local_codex",
        "protocol": "local_agent",
        "executor": "codex",
        "enabled": True,
        "default_workspace": "",
        "models": [{"id": "gpt-cheap", "capabilities": {}}],
    }
    # Adding an agent and giving it work are separate decisions.
    assert updated["policies"] == before["policies"]
    assert updated["routes"] == before["routes"]
    assert Path(result["backup"]).exists()
    RoutingConfig.load(path)


def test_preferences_state_reports_read_only_capability_per_executor(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    _write(path)
    _fake_detection(monkeypatch, {"claude", "antigravity", "codex"})

    state = preferences_state(
        GatewaySettings(config_file=path),
        include_catalogs=False,
        include_model_health=False,
        codex_home=tmp_path / "codex",
    )
    by_executor = {entry["executor"]: entry for entry in state["executors"]}

    assert state["read_only_roles"] == ["explorer", "reviewer"]
    # `agy` has no read-only mode, so read-only roles cannot use it. The UI
    # needs this from the backend rather than naming the executor itself.
    assert by_executor["antigravity"]["enforces_read_only"] is False
    assert by_executor["claude"]["enforces_read_only"] is True
    assert by_executor["codex"]["enforces_read_only"] is True
    assert by_executor["codex"]["default_provider_id"] == "local_codex"


def test_set_route_declares_a_provider_the_config_never_had(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)

    set_route(
        path,
        role="worker",
        candidates=["local_codex:gpt-5.6-sol"],
        fallback=[],
        add_missing_models=True,
        declare_providers={"local_codex": "codex"},
    )

    updated = json.loads(path.read_text())
    codex = next(item for item in updated["providers"] if item["id"] == "local_codex")
    assert codex["executor"] == "codex"
    assert codex["enabled"] is True
    # Seeded with what the user picked, so no default model is invented.
    assert codex["models"] == [{"id": "gpt-5.6-sol", "capabilities": {}}]
    assert updated["policies"][updated["routes"]["worker"]]["candidates"] == [
        "local_codex:gpt-5.6-sol"
    ]
    RoutingConfig.load(path)


def test_set_route_leaves_an_already_declared_provider_alone(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)

    set_route(
        path,
        role="worker",
        candidates=["local_agy:flash-next"],
        fallback=[],
        declare_providers={"local_agy": "antigravity"},
    )

    updated = json.loads(path.read_text())
    agy = [item for item in updated["providers"] if item["id"] == "local_agy"]
    assert len(agy) == 1
    assert [model["id"] for model in agy[0]["models"]] == ["flash", "flash-next"]


def test_set_route_refuses_to_declare_a_provider_for_an_unknown_executor(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)
    before = path.read_text()

    try:
        set_route(
            path,
            role="worker",
            candidates=["local_llama:whatever"],
            fallback=[],
            declare_providers={"local_llama": "llama"},
        )
    except ValueError as error:
        assert "llama" in str(error)
    else:
        raise AssertionError("an unknown executor should not be declared")

    assert path.read_text() == before


def test_set_route_parser_accepts_a_declared_provider():
    args = _parse_args(
        [
            "set-route",
            "--role",
            "worker",
            "--candidate",
            "local_agy:flash",
            "--declare-provider",
            "local_agy=antigravity",
        ]
    )

    assert args.declare_provider == [("local_agy", "antigravity")]


def test_add_provider_refuses_a_second_entry_for_the_same_executor(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)
    before = path.read_text()

    try:
        add_provider(path, executor="antigravity", models=["flash"])
    except ValueError as error:
        assert "local_agy" in str(error)
    else:
        raise AssertionError("a duplicate executor should be refused")

    assert path.read_text() == before
    assert list(tmp_path.glob("*.bak.*")) == []


def test_add_provider_reports_why_it_cannot_seed_a_model(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    _write(path)
    monkeypatch.setattr(
        "fluxion.provider_gateway.preferences.list_agent_models_view",
        lambda **_: {"models": [], "warnings": ["`codex debug models` returned nothing"]},
    )

    try:
        add_provider(path, executor="codex", settings=cast(Any, object()))
    except ValueError as error:
        assert "codex debug models" in str(error)
    else:
        raise AssertionError("an unseedable provider should fail before writing")

    assert list(tmp_path.glob("*.bak.*")) == []


def test_add_provider_parser_accepts_comma_separated_models():
    args = _parse_args(["add-provider", "--executor", "antigravity", "--models", "flash-a,flash-b"])

    assert args.executor == "antigravity"
    assert args.models == ["flash-a,flash-b"]


def test_set_route_can_declare_a_live_catalog_model(tmp_path):
    path = tmp_path / "routes.json"
    _write(path)

    set_route(
        path,
        role="worker",
        candidates=["local_agy:gemini-3.7-flash"],
        fallback=["local_claude:haiku"],
        add_missing_models=True,
    )

    updated = json.loads(path.read_text())
    agy = next(item for item in updated["providers"] if item["id"] == "local_agy")
    assert agy["models"][-1] == {
        "id": "gemini-3.7-flash",
        "capabilities": {},
    }
    assert updated["policies"]["worker"]["candidates"] == ["local_agy:gemini-3.7-flash"]
    RoutingConfig.load(path)
