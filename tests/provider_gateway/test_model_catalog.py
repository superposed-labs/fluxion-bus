"""Model-existence checking.

The distinction under test throughout: an id a readable catalog does not list is
a defect, while an id nobody could check is an unknown. Collapsing the two in
either direction is the failure mode that matters — one hides a route that will
fail every turn, the other condemns a working config because a CLI was slow.
"""

from __future__ import annotations

import json

from fluxion.provider_gateway.config import RoutingConfig
from fluxion.provider_gateway.model_catalog import (
    ExecutorCatalog,
    describe_missing,
    load_catalog,
    verify_configured_models,
)


def routing(**overrides) -> RoutingConfig:
    base = {
        "version": 1,
        "providers": [
            {
                "id": "local_agy",
                "protocol": "local_agent",
                "executor": "antigravity",
                "models": [{"id": "gemini-live"}, {"id": "gemini-retired"}],
            }
        ],
        "policies": {
            "cheap": {"candidates": ["local_agy:gemini-retired"]},
            "balanced": {"candidates": ["local_agy:gemini-live"]},
        },
        "routes": {"auto": "balanced", "worker": "cheap", "compaction": "cheap"},
    }
    base.update(overrides)
    return RoutingConfig.parse(base)


def catalog(*model_ids: str, error: str = "", supported: bool = True) -> ExecutorCatalog:
    return ExecutorCatalog(
        executor="antigravity",
        model_ids=frozenset(model_ids),
        error=error,
        supported=supported,
    )


# ── readable catalog: absence is a defect ────────────────────────────
def test_a_model_the_catalog_does_not_list_is_reported():
    verification = verify_configured_models(
        routing(), catalogs={"antigravity": catalog("gemini-live")}
    )

    assert verification.missing == ("local_agy:gemini-retired",)
    assert verification.verified == ("local_agy:gemini-live",)
    assert verification.ok is False


def test_model_ids_compare_case_insensitively():
    """The CLIs are not consistent about case, and a case mismatch is not a defect."""
    verification = verify_configured_models(
        routing(), catalogs={"antigravity": catalog("GEMINI-LIVE", "Gemini-Retired")}
    )

    assert verification.missing == ()
    assert len(verification.verified) == 2


# ── unreadable catalog: absence proves nothing ───────────────────────
def test_an_unreachable_catalog_never_condemns_a_model():
    verification = verify_configured_models(
        routing(),
        catalogs={"antigravity": catalog(error="`agy models` timed out after 30s")},
    )

    assert verification.missing == ()
    assert verification.ok is True
    assert [candidate for candidate, _reason in verification.unverified] == [
        "local_agy:gemini-live",
        "local_agy:gemini-retired",
    ]


def test_an_executor_without_a_catalog_is_a_note_not_a_problem():
    verification = verify_configured_models(
        routing(
            providers=[
                {
                    "id": "local_claude",
                    "protocol": "local_agent",
                    "executor": "claude",
                    "models": [{"id": "opus"}],
                }
            ],
            policies={"balanced": {"candidates": ["local_claude:opus"]}},
            routes={"auto": "balanced"},
        )
    )

    assert verification.missing == ()
    assert verification.ok is True
    assert verification.unverified[0][0] == "local_claude:opus"
    assert any("no catalog to check against" in note for note in verification.catalog_notes)


def test_claude_reports_no_catalog_rather_than_a_failure():
    resolved = load_catalog("claude")

    assert resolved.supported is False
    assert resolved.readable is False
    assert resolved.error


def test_an_unknown_executor_is_unverifiable_rather_than_broken():
    resolved = load_catalog("some-future-cli")

    assert resolved.supported is False
    assert resolved.readable is False


# ── scope ────────────────────────────────────────────────────────────
def test_disabled_providers_are_not_checked():
    """Their models are unreachable, so a stale id there fails nothing today."""
    verification = verify_configured_models(
        routing(
            providers=[
                {
                    "id": "local_agy",
                    "protocol": "local_agent",
                    "executor": "antigravity",
                    "enabled": False,
                    "models": [{"id": "gemini-retired"}],
                }
            ],
            policies={"balanced": {"candidates": ["local_agy:gemini-retired"]}},
            routes={"auto": "balanced"},
        ),
        catalogs={"antigravity": catalog("gemini-live")},
    )

    assert verification.missing == ()
    assert verification.verified == ()


# ── blast radius ─────────────────────────────────────────────────────
def test_the_report_names_every_role_the_dead_model_serves():
    """Without the roles, the operator cannot tell whether this matters."""
    message = describe_missing(routing(), "local_agy:gemini-retired")

    assert "compaction" in message and "worker" in message
    assert "auto" not in message


def test_a_model_no_role_reaches_says_so():
    message = describe_missing(routing(), "local_agy:orphan")

    assert "no role routes to it" in message


# ── codex catalog parsing ────────────────────────────────────────────
def _codex_catalog_from(stdout: str, monkeypatch) -> tuple[list[str], str]:
    import subprocess

    from fluxion.executors.codex import models as codex_models

    monkeypatch.setattr(codex_models, "_MODEL_CATALOG_CACHE", {})

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(codex_models.subprocess, "run", fake_run)
    return codex_models.load_codex_model_catalog("codex")


def test_codex_catalog_reads_model_slugs(monkeypatch):
    names, error = _codex_catalog_from(
        json.dumps({"models": [{"slug": "gpt-a"}, {"slug": "gpt-b"}, {"slug": "gpt-a"}]}),
        monkeypatch,
    )

    assert names == ["gpt-a", "gpt-b"]
    assert error == ""


def test_codex_catalog_reports_unparsable_output_as_an_error(monkeypatch):
    """An error here means "unknown", so it must not read as an empty catalog."""
    names, error = _codex_catalog_from("not json at all", monkeypatch)

    assert names == []
    assert "not JSON" in error


def test_codex_catalog_reports_a_missing_models_list(monkeypatch):
    names, error = _codex_catalog_from(json.dumps({"unexpected": []}), monkeypatch)

    assert names == []
    assert "no models list" in error
