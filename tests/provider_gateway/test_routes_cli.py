"""The `fluxion-provider routes` command.

Its job is to answer one operator question — "will the next turn keep its
context?" — which the route alone does not answer.
"""

from __future__ import annotations


def _run_routes(tmp_path, *, prune: bool = False) -> None:
    import argparse

    from fluxion.provider_gateway import cli

    settings = cli.GatewaySettings.load(env={"FLUXION_PROVIDER_TOKEN_FILE": str(tmp_path / "t")})
    original = cli.GatewaySettings.load
    cli.GatewaySettings.load = staticmethod(lambda *a, **k: settings)
    try:
        cli._routes(argparse.Namespace(prune=prune))
    finally:
        cli.GatewaySettings.load = original


# ── routes listing ───────────────────────────────────────────────────
def _sticky_with(tmp_path, **overrides):
    from fluxion.provider_gateway.identity import IdentityConfidence, RequestIdentity
    from fluxion.provider_gateway.sticky import StickyStore

    store = StickyStore(tmp_path / "sticky.db")
    identity = RequestIdentity(
        ingress=overrides.pop("ingress", "anthropic"),
        route_key=overrides.pop("route_key", "k" * 64),
        confidence=IdentityConfidence.EXPLICIT,
    )
    store.remember(
        identity,
        "local_claude",
        "haiku",
        "p",
        executor_session_id=overrides.pop("executor_session_id", "sess-1"),
    )
    return store


def test_routes_reports_whether_the_agent_session_survives(tmp_path, capsys):
    """The route and the agent session are different facts, and only the second
    decides whether the next turn keeps its context."""
    _sticky_with(tmp_path, executor_session_id="sess-1").close()
    _sticky_with(tmp_path, route_key="c" * 64, executor_session_id="").close()

    _run_routes(tmp_path)
    out = capsys.readouterr().out
    assert "resumable" in out
    assert "cold" in out


def test_routes_names_the_ingress(tmp_path, capsys):
    """Two protocols share the table; a row that does not say which is ambiguous."""
    _sticky_with(tmp_path, ingress="anthropic").close()

    _run_routes(tmp_path)
    assert "anthropic" in capsys.readouterr().out


def test_prune_removes_only_expired_routes(tmp_path, capsys):
    """Nothing expires on its own, so a long-lived install accumulates forever."""
    _sticky_with(tmp_path).close()

    _run_routes(tmp_path, prune=True)
    assert "removed 0 expired route(s)" in capsys.readouterr().out

    _run_routes(tmp_path)
    assert "resumable" in capsys.readouterr().out, "an unexpired route must survive"
