"""The `fluxion-provider check-models` command.

It exists to be run unattended, which sets its contract: exit non-zero only for
something a human must act on, and stay quiet-but-successful when a CLI simply
could not be asked.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from fluxion.provider_gateway import cli
from fluxion.provider_gateway.model_catalog import ExecutorCatalog
from fluxion.utils import macos_notify

ROUTING = {
    "version": 1,
    "providers": [
        {
            "id": "local_agy",
            "protocol": "local_agent",
            "executor": "antigravity",
            "models": [{"id": "gemini-live"}, {"id": "gemini-retired"}],
        }
    ],
    "policies": {"balanced": {"candidates": ["local_agy:gemini-live"]}},
    "routes": {"auto": "balanced", "worker": "balanced"},
}


@pytest.fixture
def run_check(tmp_path, monkeypatch):
    """Run the command against a config file, with the catalog stubbed."""
    config_file = tmp_path / "routes.json"
    config_file.write_text(json.dumps(ROUTING), encoding="utf-8")
    settings = cli.GatewaySettings.load(
        env={
            "FLUXION_PROVIDER_CONFIG_FILE": str(config_file),
            "FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "token"),
        }
    )
    monkeypatch.setattr(cli.GatewaySettings, "load", staticmethod(lambda *a, **k: settings))
    # The command also inspects the user's Codex catalog override. Point it at an
    # empty home: without this the result would depend on whether the developer
    # running the tests happens to have one installed.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    def run(*live_models: str, error: str = "", notify: bool = False) -> int:
        monkeypatch.setattr(
            cli,
            "verify_configured_models",
            lambda routing: _verify_with(routing, *live_models, error=error),
        )
        return cli._check_models(argparse.Namespace(notify=notify))

    return run


def _records(data_dir) -> list[dict]:
    signal = data_dir / macos_notify.FILENAME
    if not signal.exists():
        return []
    return [json.loads(line) for line in signal.read_text().splitlines()]


def _verify_with(routing, *live_models: str, error: str):
    from fluxion.provider_gateway.model_catalog import verify_configured_models

    return verify_configured_models(
        routing,
        catalogs={
            "antigravity": ExecutorCatalog(
                executor="antigravity",
                model_ids=frozenset(live_models),
                error=error,
            )
        },
    )


def test_a_retired_model_fails_the_check(run_check, capsys):
    assert run_check("gemini-live") == 1
    err = capsys.readouterr().err
    assert "local_agy:gemini-retired" in err
    assert "fallback" in err, "the operator must be told a fallback will not cover this"


def test_a_healthy_config_passes(run_check):
    assert run_check("gemini-live", "gemini-retired") == 0


def test_an_unconfigured_install_passes_quietly(tmp_path, monkeypatch, capsys):
    """Most installs never set up the gateway.

    Failing here would hand them a daily alert about a feature they do not use,
    and an alert that is always firing is an alert nobody reads.
    """
    settings = cli.GatewaySettings.load(
        env={
            "FLUXION_PROVIDER_CONFIG_FILE": str(tmp_path / "absent.json"),
            "FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "token"),
        }
    )
    monkeypatch.setattr(cli.GatewaySettings, "load", staticmethod(lambda *a, **k: settings))

    assert cli._check_models(argparse.Namespace()) == 0
    captured = capsys.readouterr()
    assert "nothing to check" in captured.out
    assert captured.err == "", "an unconfigured install is not a finding"


def test_an_unreachable_catalog_does_not_fail_the_check(run_check, capsys):
    """A scheduled check that cries wolf when a CLI is slow gets muted, and then
    the real retirement goes unnoticed too."""
    assert run_check(error="`agy models` timed out after 30s") == 0
    assert "not verified" in capsys.readouterr().out


def _install_stale_override(codex_home) -> None:
    """A `model_catalog_json` snapshot that upstream has moved past."""
    codex_home.mkdir(parents=True, exist_ok=True)
    snapshot = codex_home / "snapshot.json"
    fields = {"display_name": "X", "context_window": 1, "multi_agent_version": "v1"}
    snapshot.write_text(json.dumps({"models": [{"slug": "sol", **fields}]}))
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "sol", **fields, "multi_agent_version": "v2"},
                    {"slug": "nova", **fields},
                ]
            }
        )
    )
    (codex_home / "config.toml").write_text(f'model_catalog_json = "{snapshot}"\n')


def test_a_stale_codex_catalog_fails_the_check(run_check, capsys, tmp_path, monkeypatch):
    """The whole point of hooking this onto a scheduled check: a snapshot going
    stale is silent otherwise."""
    _install_stale_override(tmp_path / "codex-home")
    assert run_check("gemini-live", "gemini-retired") == 1
    err = capsys.readouterr().err
    assert "nova" in err, "a model added upstream is invisible until the snapshot catches up"
    assert "fallback" not in err, "that advice is about routing config, not the catalog"


@pytest.mark.skipif(sys.platform != "darwin", reason="notification signal file is macOS-only")
def test_notify_names_the_subject_that_fired(run_check, tmp_path, monkeypatch):
    """The two findings are fixed in different files, so the title has to say
    which one it is rather than send the user to the wrong one."""
    monkeypatch.setattr(cli, "_data_dir", lambda: tmp_path)
    _install_stale_override(tmp_path / "codex-home")

    # Catalog stale, routing fine.
    assert run_check("gemini-live", "gemini-retired", notify=True) == 1
    (record,) = _records(tmp_path)
    assert "catalog" in record["title"]

    # A routing finding on top of it takes the title.
    macos_notify.clear_throttle(tmp_path, cli._NOTIFY_KEY)
    assert run_check("gemini-live", notify=True) == 1
    assert "retired" in _records(tmp_path)[-1]["title"]


@pytest.mark.skipif(sys.platform != "darwin", reason="notification signal file is macOS-only")
def test_a_clean_check_notifies_nothing_and_rearms(run_check, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_data_dir", lambda: tmp_path)
    _install_stale_override(tmp_path / "codex-home")
    assert run_check("gemini-live", "gemini-retired", notify=True) == 1

    # Snapshot repaired: nothing to say, and the throttle is cleared so the next
    # occurrence is not swallowed as a repeat.
    (tmp_path / "codex-home" / "config.toml").write_text("model = 'x'\n")
    assert run_check("gemini-live", "gemini-retired", notify=True) == 0
    assert len(_records(tmp_path)) == 1
    assert not (tmp_path / "runtime" / f"notify-{cli._NOTIFY_KEY}.json").exists()


def test_dotenv_reaches_the_unattended_check(tmp_path, monkeypatch, capsys):
    """A user who sets the switch in `.env` expects the daily run to honour it.

    Reading the file only where `Settings` is constructed left `check-models`
    silently on defaults.
    """
    _install_stale_override(tmp_path / "codex-home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    env_file = tmp_path / ".env"
    env_file.write_text("FLUXION_PROVIDER_CODEX_CATALOG_DRIFT=off\n")
    monkeypatch.setenv("FLUXION_ENV_FILE", str(env_file))
    monkeypatch.delenv("FLUXION_PROVIDER_CODEX_CATALOG_DRIFT", raising=False)
    config_file = tmp_path / "routes.json"
    config_file.write_text(json.dumps(ROUTING), encoding="utf-8")
    monkeypatch.setenv("FLUXION_PROVIDER_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(
        cli,
        "verify_configured_models",
        lambda routing: _verify_with(routing, "gemini-live", "gemini-retired", error=""),
    )

    assert cli.main(["check-models"]) == 0
    assert "codex model catalog" not in capsys.readouterr().out
