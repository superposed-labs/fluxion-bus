"""Staleness of a local Codex model catalog override.

The distinction under test throughout: a stale *model list* silently changes what
Codex can do — a model added upstream stays invisible, a retired one stays on
offer — while a field moving upstream is only worth re-deriving for. Treating the
second as loudly as the first is what teaches a user to dismiss the daily check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluxion.provider_gateway import codex_catalog
from fluxion.provider_gateway.config import ConfigError, GatewaySettings


def entry(slug: str, **overrides) -> dict:
    base = {
        "slug": slug,
        "display_name": slug.upper(),
        "context_window": 272000,
        "base_instructions": "be helpful",
        "multi_agent_version": "v2",
    }
    base.update(overrides)
    return base


def home(
    tmp_path, *, catalog: list[dict] | None, cache: list[dict] | None, key: str = ""
) -> object:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(exist_ok=True)
    catalog_path = codex_home / "snapshot.json"
    if catalog is not None:
        catalog_path.write_text(json.dumps({"models": catalog}))
    if cache is not None:
        (codex_home / "models_cache.json").write_text(json.dumps({"models": cache}))
    pointer = key or (str(catalog_path) if catalog is not None else "")
    body = f'model_catalog_json = "{pointer}"\n' if pointer else "model = 'x'\n"
    (codex_home / "config.toml").write_text(body)
    return codex_home


def test_no_override_is_not_a_finding(tmp_path):
    codex_home = home(tmp_path, catalog=None, cache=[entry("gpt-5.6-sol")])
    assert codex_catalog.inspect(codex_home) is None
    assert codex_catalog.report("warn", codex_home) == ([], [])


def test_missing_cache_is_not_a_finding(tmp_path):
    """A Codex that has not run yet has nothing to compare against."""
    codex_home = home(tmp_path, catalog=[entry("gpt-5.6-sol")], cache=None)
    assert codex_catalog.inspect(codex_home) is None


def test_model_added_upstream_is_blocking(tmp_path):
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1")],
        cache=[entry("gpt-5.6-sol"), entry("gpt-5.7-nova")],
    )
    drift = codex_catalog.inspect(codex_home)
    assert drift.added == ("gpt-5.7-nova",)
    problems, _ = codex_catalog.report("warn", codex_home)
    assert any("gpt-5.7-nova" in problem for problem in problems)


def test_model_retired_upstream_is_blocking(tmp_path):
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1"), entry("gpt-5.5-old")],
        cache=[entry("gpt-5.6-sol")],
    )
    drift = codex_catalog.inspect(codex_home)
    assert drift.dropped == ("gpt-5.5-old",)
    problems, _ = codex_catalog.report("warn", codex_home)
    assert any("gpt-5.5-old" in problem and "retired" in problem for problem in problems)


def test_field_drift_stays_quiet(tmp_path):
    """Same model list, one field moved: a note, never a notification."""
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1", base_instructions="old text")],
        cache=[entry("gpt-5.6-sol", base_instructions="new text")],
    )
    drift = codex_catalog.inspect(codex_home)
    assert drift.field_drift == ("gpt-5.6-sol",)
    problems, notes = codex_catalog.report("warn", codex_home)
    assert problems == []
    assert any("base_instructions" not in note and "non-protocol" in note for note in notes)


def test_pin_is_read_off_the_snapshot(tmp_path):
    """No hardcoded slug list: the difference from the cache *is* the pin."""
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-terra", multi_agent_version="v1")],
        cache=[entry("gpt-5.6-terra", multi_agent_version="v2")],
    )
    drift = codex_catalog.inspect(codex_home)
    assert drift.overrides == {"gpt-5.6-terra": ("v2", "v1")}


def test_redundant_override_is_reported(tmp_path):
    """Upstream caught up: the file now maintains a difference of nothing."""
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1")],
        cache=[entry("gpt-5.6-sol", multi_agent_version="v1")],
    )
    _, notes = codex_catalog.report("warn", codex_home)
    assert any("one less thing to maintain" in note for note in notes)


def test_refresh_takes_upstream_and_keeps_the_pin(tmp_path):
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1", base_instructions="old text")],
        cache=[entry("gpt-5.6-sol", base_instructions="new text"), entry("gpt-5.7-nova")],
    )
    drift = codex_catalog.inspect(codex_home)
    codex_catalog.refresh(drift)

    written = {m["slug"]: m for m in json.loads(drift.catalog_path.read_text())["models"]}
    assert set(written) == {"gpt-5.6-sol", "gpt-5.7-nova"}
    assert written["gpt-5.6-sol"]["multi_agent_version"] == "v1"
    assert written["gpt-5.6-sol"]["base_instructions"] == "new text"
    # The old snapshot is the only record of what was pinned.
    backup = drift.catalog_path.with_suffix(drift.catalog_path.suffix + ".bak")
    assert json.loads(backup.read_text())["models"][0]["base_instructions"] == "old text"


def test_refresh_drops_a_pin_for_a_retired_model(tmp_path):
    """Re-applying it would resurrect an entry Codex can no longer use."""
    codex_home = home(
        tmp_path,
        catalog=[
            entry("gpt-5.6-sol", multi_agent_version="v1"),
            entry("gone", multi_agent_version="v1"),
        ],
        cache=[entry("gpt-5.6-sol"), entry("gone", multi_agent_version="v2")],
    )
    drift = codex_catalog.inspect(codex_home)
    # Make "gone" vanish upstream between inspection and refresh.
    (codex_home / "models_cache.json").write_text(json.dumps({"models": [entry("gpt-5.6-sol")]}))
    messages = codex_catalog.refresh(drift)

    written = {m["slug"] for m in json.loads(drift.catalog_path.read_text())["models"]}
    assert written == {"gpt-5.6-sol"}
    assert any("dropped pin for gone" in message for message in messages)


def test_refresh_mode_fixes_and_reports_instead_of_failing(tmp_path):
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1")],
        cache=[entry("gpt-5.6-sol"), entry("gpt-5.7-nova")],
    )
    problems, notes = codex_catalog.report("refresh", codex_home)
    assert problems == []
    assert any("rewrote" in note for note in notes)
    written = {m["slug"] for m in json.loads((codex_home / "snapshot.json").read_text())["models"]}
    assert written == {"gpt-5.6-sol", "gpt-5.7-nova"}


def test_off_mode_checks_nothing(tmp_path):
    codex_home = home(
        tmp_path,
        catalog=[entry("gpt-5.6-sol", multi_agent_version="v1")],
        cache=[entry("gpt-5.6-sol"), entry("gpt-5.7-nova")],
    )
    assert codex_catalog.report("off", codex_home) == ([], [])


def test_unreadable_catalog_is_a_problem_not_a_crash(tmp_path):
    codex_home = home(tmp_path, catalog=[entry("gpt-5.6-sol")], cache=[entry("gpt-5.6-sol")])
    (codex_home / "snapshot.json").write_text("{ not json")
    problems, _ = codex_catalog.report("warn", codex_home)
    assert len(problems) == 1 and "snapshot.json" in problems[0]


def test_the_models_needing_a_pin_come_from_the_cache(tmp_path):
    """Not a hardcoded list: the declaration is the server's to change."""
    codex_home = home(
        tmp_path,
        catalog=None,
        cache=[
            entry("sol", multi_agent_version="v2"),
            entry("terra", multi_agent_version="v2"),
            entry("luna", multi_agent_version="v1"),
            entry("older", multi_agent_version=None),
        ],
    )
    assert codex_catalog.models_needing_pin(codex_home) == ("sol", "terra")


def test_a_snapshot_keeps_every_model_and_pins_the_named_ones(tmp_path):
    """Writing only the pinned entry would leave Codex with only that model."""
    codex_home = home(
        tmp_path, catalog=None, cache=[entry("sol"), entry("luna", multi_agent_version="v1")]
    )
    written = json.loads(codex_catalog.build_snapshot(codex_home, ["sol"]))
    by_slug = {m["slug"]: m for m in written["models"]}
    assert set(by_slug) == {"sol", "luna"}
    assert by_slug["sol"]["multi_agent_version"] == "v1"
    assert by_slug["sol"]["display_name"] == "SOL", "other fields come from upstream"


def test_pinning_an_unknown_model_is_refused(tmp_path):
    codex_home = home(tmp_path, catalog=None, cache=[entry("sol")])
    with pytest.raises(codex_catalog.CatalogError, match="nope"):
        codex_catalog.build_snapshot(codex_home, ["nope"])


def test_the_config_key_goes_above_every_table():
    """TOML assigns a key after a `[table]` header to that table. Appending would
    make this `[projects."…"].model_catalog_json` — ignored, and still look right."""
    before = 'model = "x"\n\n[projects."/tmp/p"]\ntrust_level = "trusted"\n'
    after = codex_catalog.plan_config_line(before, Path("/c.json"))
    assert after.splitlines()[0] == 'model_catalog_json = "/c.json"'
    assert 'trust_level = "trusted"' in after


def test_removal_drops_only_the_key():
    """Comments around it are the user's, including notes about their own config."""
    before = (
        '# why this is here\nmodel_catalog_json = "/c.json"\nmodel = "x"\n\n'
        '[projects."/tmp/p"]\ntrust_level = "trusted"\n'
    )
    after = codex_catalog.plan_config_removal(before)
    assert "model_catalog_json" not in after
    assert "# why this is here" in after
    assert 'trust_level = "trusted"' in after


def test_removal_reports_when_there_is_nothing_to_remove():
    assert codex_catalog.plan_config_removal('model = "x"\n') is None


def test_reinstalling_replaces_the_key_instead_of_duplicating_it():
    before = 'model_catalog_json = "/old.json"\nmodel = "x"\n'
    after = codex_catalog.plan_config_line(before, Path("/new.json"))
    assert after.count("model_catalog_json") == 1
    assert "/old.json" not in after


def test_mode_typo_fails_at_load(tmp_path):
    """A misspelled opt-in must not read as the reporting default."""
    with pytest.raises(ConfigError, match="off, warn, refresh"):
        GatewaySettings.load({"FLUXION_PROVIDER_CODEX_CATALOG_DRIFT": "refersh"})
    assert GatewaySettings.load({}).codex_catalog_drift == "warn"
    assert (
        GatewaySettings.load(
            {"FLUXION_PROVIDER_CODEX_CATALOG_DRIFT": "REFRESH"}
        ).codex_catalog_drift
        == "refresh"
    )
