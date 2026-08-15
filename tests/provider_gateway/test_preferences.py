from __future__ import annotations

import json
from pathlib import Path

from fluxion.provider_gateway.cli import _parse_args
from fluxion.provider_gateway.codex_config import BEGIN_MARKER, END_MARKER
from fluxion.provider_gateway.config import GatewaySettings, RoutingConfig
from fluxion.provider_gateway.preferences import preferences_state, set_route


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
