"""Runtime health filtering: skipping a model the CLI no longer offers.

Every test here defends one of the four rules this feature was built around —
eject only on a catalog we actually read, never query a catalog from the request
path, drop the sticky routes that pointed at an ejected model, and make the
ejection visible. The first is the one that hurts most if it regresses: treating
"catalog unreadable" as "model dead" would let a single slow subprocess take a
whole provider out of rotation, which is a larger outage than the bug this
feature fixes.
"""

from __future__ import annotations

import logging
import threading
import time

from fluxion.provider_gateway.config import RoutingConfig
from fluxion.provider_gateway.model_catalog import ExecutorCatalog
from fluxion.provider_gateway.model_health import CatalogHealth
from fluxion.provider_gateway.routing import HEALTH_OK, HEALTH_RETIRED

LIVE = "local_agy:gemini-live"
RETIRED = "local_agy:gemini-retired"
CLAUDE = "local_claude:opus"


def routing(*, agy_enabled: bool = True) -> RoutingConfig:
    return RoutingConfig.parse(
        {
            "version": 1,
            "providers": [
                {
                    "id": "local_agy",
                    "protocol": "local_agent",
                    "executor": "antigravity",
                    "enabled": agy_enabled,
                    "models": [{"id": "gemini-live"}, {"id": "gemini-retired"}],
                },
                {
                    "id": "local_claude",
                    "protocol": "local_agent",
                    "executor": "claude",
                    "models": [{"id": "opus"}],
                },
            ],
            "policies": {
                "balanced": {"candidates": ["local_agy:gemini-retired", "local_claude:opus"]}
            },
            "routes": {"auto": "balanced"},
        }
    )


def catalogs(**by_executor: ExecutorCatalog):
    """A loader over canned catalogs, recording which executors were asked."""
    asked: list[str] = []

    def load(executor: str) -> ExecutorCatalog:
        asked.append(executor)
        # An unstubbed executor stands in for Claude Code: no catalog command at
        # all, which is a permanent property and not a failure to report.
        return by_executor.get(
            executor,
            ExecutorCatalog(executor=executor, supported=False, error="no catalog command"),
        )

    load.asked = asked  # type: ignore[attr-defined]
    return load


class Switchable:
    """A loader whose answer can change between refreshes, like a real CLI's."""

    def __init__(self, catalog: ExecutorCatalog) -> None:
        self.catalog = catalog
        self.asked: list[str] = []

    def __call__(self, executor: str) -> ExecutorCatalog:
        self.asked.append(executor)
        if executor == "antigravity":
            return self.catalog
        return ExecutorCatalog(executor=executor, supported=False, error="no catalog command")


def live(*model_ids: str, executor: str = "antigravity") -> ExecutorCatalog:
    return ExecutorCatalog(executor=executor, model_ids=frozenset(model_ids))


def unreadable(reason: str = "`agy models` timed out after 30s") -> ExecutorCatalog:
    return ExecutorCatalog(executor="antigravity", error=reason)


# ── ejecting on evidence ─────────────────────────────────────────────
def test_a_model_a_readable_catalog_omits_is_ejected():
    health = CatalogHealth(routing(), load=catalogs(antigravity=live("gemini-live")))
    health.refresh()

    assert health.health_check(RETIRED) == HEALTH_RETIRED
    assert health.health_check(LIVE) == HEALTH_OK


def test_a_model_that_comes_back_is_routable_again():
    """Ejection tracks what the CLI reports now, not a verdict we keep forever."""
    load = Switchable(live("gemini-live"))
    health = CatalogHealth(routing(), load=load)
    health.refresh()

    load.catalog = live("gemini-live", "gemini-retired")
    health.refresh()

    assert health.health_check(RETIRED) == HEALTH_OK


def test_an_executor_with_no_catalog_command_ejects_nothing():
    """Claude Code exposes no catalog, so its aliases are unverifiable, not dead."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=live("gemini-live")))
    health.refresh()

    assert health.health_check(CLAUDE) == HEALTH_OK


def test_a_disabled_provider_is_never_consulted():
    """Its models route nothing, so a stale id there is not a live problem."""
    load = catalogs(antigravity=live("gemini-live"))
    health = CatalogHealth(routing(agy_enabled=False), load=load)
    health.refresh()

    assert "antigravity" not in load.asked
    assert health.health_check(RETIRED) == HEALTH_OK


# ── an unknown is not a death sentence ───────────────────────────────
def test_an_unreadable_catalog_leaves_every_candidate_healthy():
    """One slow subprocess must not disable a provider — worse than the bug fixed."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=unreadable()))
    health.refresh()

    assert health.health_check(RETIRED) == HEALTH_OK
    assert health.health_check(LIVE) == HEALTH_OK
    assert health.snapshot.unreadable["antigravity"].startswith("`agy models` timed out")


def test_a_catalog_that_becomes_unreadable_releases_an_earlier_ejection():
    """Deliberate: an ejection is evidence we currently hold, not a stored verdict."""
    load = Switchable(live("gemini-live"))
    health = CatalogHealth(routing(), load=load)
    health.refresh()
    assert health.health_check(RETIRED) == HEALTH_RETIRED

    load.catalog = unreadable()
    health.refresh()
    assert health.health_check(RETIRED) == HEALTH_OK


def test_nothing_is_ejected_before_the_first_refresh():
    """A gateway that has not checked yet must route exactly as it did before."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=live("gemini-live")))

    assert health.health_check(RETIRED) == HEALTH_OK
    assert health.snapshot.checked is False


# ── the request path stays off the CLI ───────────────────────────────
def test_health_check_never_reads_a_catalog():
    """A routing decision that can block on `agy models` makes a slow CLI a slow gateway."""
    load = catalogs(antigravity=live("gemini-live"))
    health = CatalogHealth(routing(), load=load)
    health.refresh()
    asked_after_refresh = len(load.asked)

    for _ in range(50):
        health.health_check(RETIRED)

    assert len(load.asked) == asked_after_refresh


def test_one_catalog_read_per_executor_not_per_candidate():
    """Two models of one provider are two candidates and one subprocess."""
    load = catalogs(antigravity=live("gemini-live"))
    CatalogHealth(routing(), load=load).refresh()

    assert load.asked.count("antigravity") == 1


# ── the consequences of ejecting ─────────────────────────────────────
def test_ejecting_a_model_drops_its_sticky_routes():
    """Left in place, those rows are re-read and re-rejected on every later turn."""
    dropped: list[str] = []
    health = CatalogHealth(
        routing(),
        load=catalogs(antigravity=live("gemini-live")),
        on_eject=dropped.append,
    )
    health.refresh()

    assert dropped == [RETIRED]


def test_sticky_routes_are_dropped_once_not_on_every_refresh():
    """The drain follows the transition; repeating it would be pointless write load."""
    dropped: list[str] = []
    health = CatalogHealth(
        routing(),
        load=catalogs(antigravity=live("gemini-live")),
        on_eject=dropped.append,
    )
    health.refresh()
    health.refresh()

    assert dropped == [RETIRED]


def test_a_failing_drain_does_not_block_the_ejection():
    """Housekeeping is secondary: the model must still be skipped at selection."""

    def explode(candidate: str) -> None:
        raise RuntimeError("sticky store is unhappy")

    health = CatalogHealth(
        routing(), load=catalogs(antigravity=live("gemini-live")), on_eject=explode
    )
    health.refresh()

    assert health.health_check(RETIRED) == HEALTH_RETIRED


def test_an_ejection_is_logged_with_what_it_means(caplog):
    """An automatic degradation nobody can see is worse than a loud failure."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=live("gemini-live")))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.model_health"):
        health.refresh()

    assert RETIRED in caplog.text
    assert "no longer listed by its CLI" in caplog.text


def test_a_catalog_that_cannot_be_read_is_reported(caplog):
    """The one failure with no other symptom.

    Nothing is ejected and no turn behaves differently, so a gateway whose PATH
    does not reach the CLI runs with this whole mechanism inert — while
    `check-models` from a normal shell reports everything fine.
    """
    health = CatalogHealth(routing(), load=catalogs(antigravity=unreadable()))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.model_health"):
        health.refresh()

    assert "cannot read antigravity's model catalog" in caplog.text


def test_an_executor_with_no_catalog_command_is_not_reported_as_a_fault(caplog):
    """Claude Code has no catalog by design; warning about it every cycle is crying wolf."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=live("gemini-live")))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.model_health"):
        health.refresh()

    assert "claude" not in caplog.text
    assert "claude" not in health.snapshot.unreadable


def test_an_unreadable_catalog_is_reported_once_not_every_cycle(caplog):
    """A refresh every 10 minutes would otherwise fill the log with one standing fault."""
    health = CatalogHealth(routing(), load=catalogs(antigravity=unreadable()))
    with caplog.at_level(logging.WARNING, logger="fluxion.provider_gateway.model_health"):
        health.refresh()
        health.refresh()

    assert caplog.text.count("cannot read antigravity's model catalog") == 1


# ── background refresh ───────────────────────────────────────────────
def test_the_background_thread_refreshes_and_stops():
    """Refreshing has to happen somewhere, and it cannot be the request path."""
    refreshed = threading.Event()

    def load(executor: str) -> ExecutorCatalog:
        refreshed.set()
        return live("gemini-live")

    health = CatalogHealth(routing(), load=load, interval=0.01)
    health.start()
    try:
        assert refreshed.wait(timeout=5.0)
    finally:
        health.stop()

    assert health.health_check(RETIRED) == HEALTH_RETIRED


def test_a_zero_interval_disables_the_background_refresh():
    """The operator's switch for "leave my routing table exactly as configured"."""
    load = catalogs(antigravity=live("gemini-live"))
    health = CatalogHealth(routing(), load=load, interval=0)
    health.start()
    try:
        assert load.asked == []
    finally:
        health.stop()


def test_a_refresh_that_raises_does_not_end_the_loop():
    """A transient failure must not silently retire the whole health check."""
    calls: list[int] = []

    def load(executor: str) -> ExecutorCatalog:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("catalog exploded")
        return live("gemini-live")

    health = CatalogHealth(routing(), load=load, interval=0.01)
    health.start()
    try:
        deadline = time.monotonic() + 5.0
        while not health.snapshot.checked and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        health.stop()

    assert health.snapshot.checked
    assert health.health_check(RETIRED) == HEALTH_RETIRED


def test_the_app_lifespan_runs_the_refresher():
    """The seam where this feature silently becomes dead code.

    Everything else here tests `CatalogHealth` directly, so dropping the two
    lines that start and stop it in `create_app` would leave a gateway that
    never checks a catalog and never ejects anything — with the whole suite
    still green.
    """
    from fastapi.testclient import TestClient

    from fluxion.provider_gateway.app import GatewayContext, create_app
    from fluxion.provider_gateway.auth import TokenAuthenticator
    from fluxion.provider_gateway.routing import Router
    from fluxion.provider_gateway.sticky import StickyStore

    refreshed = threading.Event()

    def load(executor: str) -> ExecutorCatalog:
        refreshed.set()
        return live("gemini-live")

    health = CatalogHealth(routing(), load=load, interval=0.01)
    context = GatewayContext(
        router=Router(policies={}, routes={}, capabilities={}, health_check=health.health_check),
        sticky=StickyStore(":memory:"),
        authenticator=TokenAuthenticator("t"),
        model_health=health,
    )

    with TestClient(create_app(context)) as client:
        assert refreshed.wait(timeout=5.0)
        assert client.get("/healthz").status_code == 200

    assert health.health_check(RETIRED) == HEALTH_RETIRED
    # Shutdown has to stop it too: a thread left shelling out to a CLI after the
    # app is gone is exactly what the lifespan's `finally` exists to prevent.
    refreshed.clear()
    assert not refreshed.wait(timeout=0.2)
