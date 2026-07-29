"""The `fluxion-provider check-models` command.

It exists to be run unattended, which sets its contract: exit non-zero only for
something a human must act on, and stay quiet-but-successful when a CLI simply
could not be asked.
"""

from __future__ import annotations

import argparse
import json

import pytest

from fluxion.provider_gateway import cli
from fluxion.provider_gateway.model_catalog import ExecutorCatalog

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

    def run(*live_models: str, error: str = "") -> int:
        monkeypatch.setattr(
            cli,
            "verify_configured_models",
            lambda routing: _verify_with(routing, *live_models, error=error),
        )
        return cli._check_models(argparse.Namespace())

    return run


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
