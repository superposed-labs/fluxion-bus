"""Runtime health for configured models: skip what the CLI no longer offers.

`model_catalog` answers "is this configured id real?" for an operator running a
check. This module answers the same question for the router, on every selection,
which imposes two constraints that the on-demand check does not have.

**A routing decision must never wait on a catalog.** Reading one means spawning
`agy models` or `codex debug models`; the loaders cache, but a cache miss is
still a subprocess, and a subprocess on the request path turns a slow CLI into a
slow gateway. So nothing here queries anything during `health_check` — it reads
an immutable snapshot that a background thread replaces.

**An unknown must not read as dead.** Only a successfully fetched catalog can
eject a model. If the CLI is missing, slow, or mid-upgrade, every candidate stays
healthy and routing behaves exactly as it did before this module existed. The
opposite policy is much worse than the bug this fixes: one timed-out subprocess
would disable every model of a provider at once, turning a single broken route
into a total outage — and it would do it silently, since "nothing is healthy"
looks the same from the outside whatever caused it.

The consequence of that rule is worth stating plainly: a model ejected while its
catalog was readable comes *back* if the catalog later becomes unreadable. That
is intentional. Ejection is a fact we currently observe, not a verdict we store.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from fluxion.provider_gateway.config import (
    DEFAULT_MODEL_HEALTH_REFRESH_SECONDS,
    RoutingConfig,
)
from fluxion.provider_gateway.model_catalog import ExecutorCatalog, load_catalog
from fluxion.provider_gateway.routing import HEALTH_OK, HEALTH_RETIRED, split_candidate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthSnapshot:
    """What the last completed refresh concluded. Immutable; replaced wholesale.

    Swapping a whole snapshot rather than mutating a set is what lets readers go
    lock-free: a routing decision either sees the previous conclusion or the next
    one, never a set that is halfway through being rebuilt.
    """

    ejected: frozenset[str] = frozenset()
    # Per-executor reason a catalog read that *should* have worked did not, which
    # means that executor's models were all left healthy this cycle. Executors
    # with no catalog command at all (Claude Code) are deliberately absent: they
    # are permanently uncheckable by design, and listing them here would put a
    # standing fault next to real ones.
    unreadable: Mapping[str, str] = field(default_factory=dict)
    # 0.0 until the first refresh finishes. Before that nothing is ejected,
    # which is the same state as "everything checks out" by design — the safe
    # direction to be wrong in.
    checked_at: float = 0.0

    @property
    def checked(self) -> bool:
        return self.checked_at > 0.0


class CatalogHealth:
    """Tracks which configured candidates their own CLI no longer lists.

    Wired into `Router.health_check`. Owns a background thread because the check
    it performs cannot happen on the request path; everything the router calls is
    a snapshot read.
    """

    def __init__(
        self,
        routing: RoutingConfig,
        *,
        load: Callable[[str], ExecutorCatalog] = load_catalog,
        on_eject: Callable[[str], None] | None = None,
        interval: float = DEFAULT_MODEL_HEALTH_REFRESH_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # Only enabled providers: a disabled one routes nothing, so a stale id
        # there is a note for whoever re-enables it, not a live problem.
        self._executors: dict[str, str] = {
            f"{provider_id}:{model_id}": spec.executor.strip().lower()
            for provider_id, spec in routing.enabled_providers().items()
            for model_id in spec.models
        }
        self._load = load
        self._on_eject = on_eject
        self._interval = interval
        self._clock = clock
        self._snapshot = HealthSnapshot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── request path ─────────────────────────────────────────────────
    def health_check(self, candidate: str) -> str:
        """The router's seam. A snapshot read; never touches a CLI."""
        return HEALTH_RETIRED if candidate in self._snapshot.ejected else HEALTH_OK

    @property
    def snapshot(self) -> HealthSnapshot:
        return self._snapshot

    # ── background refresh ───────────────────────────────────────────
    def start(self) -> None:
        """Begin refreshing in the background. First refresh runs immediately.

        Immediately, but off-thread: a retired model is worth catching within
        seconds of startup, and blocking the boot on one subprocess per CLI
        would delay serving the requests that are still perfectly routable.
        """
        if self._interval <= 0 or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="provider-model-health", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ask the refresh thread to finish. Does not wait out a running fetch.

        The join is bounded because a refresh can be sitting in a 30s subprocess
        timeout, and shutdown must not inherit that wait. The thread is a daemon
        and touches nothing that outlives the process, so leaving it to expire
        on its own is safe.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)

    def _loop(self) -> None:
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - a failed refresh must not end refreshing
                log.warning("model health refresh failed; keeping previous state", exc_info=True)
            if self._stop.wait(self._interval):
                return

    def refresh(self) -> HealthSnapshot:
        """Re-read every catalog and republish the conclusion. Blocking.

        Call from a background thread or a startup path, never from a request.
        """
        catalogs: dict[str, ExecutorCatalog] = {}
        ejected: set[str] = set()
        unreadable: dict[str, str] = {}

        for candidate, executor in self._executors.items():
            if executor not in catalogs:
                catalogs[executor] = self._load(executor)
            catalog = catalogs[executor]
            if not catalog.readable:
                # The whole point of constraint one: no catalog, no verdict.
                # `supported` separates "this CLI has no catalog command" from
                # "asking failed"; only the second is something to report.
                if catalog.supported:
                    unreadable.setdefault(executor, catalog.error)
                continue
            _, model_id = split_candidate(candidate)
            if not catalog.has(model_id):
                ejected.add(candidate)

        previous = self._snapshot
        self._snapshot = HealthSnapshot(
            ejected=frozenset(ejected),
            unreadable=unreadable,
            checked_at=self._clock(),
        )
        self._announce(previous, self._snapshot)
        return self._snapshot

    def _announce(self, previous: HealthSnapshot, current: HealthSnapshot) -> None:
        """Log the transitions and drop sticky routes onto ejected models.

        Both directions are logged. A model coming back matters as much as one
        going away: it is how an operator tells "the vendor restored it" from
        "our catalog read was flaky", and only the second one needs chasing.
        """
        for candidate in sorted(current.ejected - previous.ejected):
            log.warning(
                "model %s is no longer listed by its CLI; it will be skipped at selection "
                "until the routing config is fixed. Turns whose policy has no other "
                "candidate will fail to route",
                candidate,
            )
            self._eject(candidate)
        # A catalog that cannot be read is the failure mode with no other
        # symptom. Nothing is ejected, no turn fails differently, and the whole
        # mechanism is quietly inert — the shape this takes in production is a
        # gateway started by launchd with a PATH that does not reach `agy`,
        # while `check-models` in the operator's own shell works perfectly and
        # reports everything fine. Without this line, nothing anywhere says so.
        for executor in sorted(set(current.unreadable) - set(previous.unreadable)):
            log.warning(
                "cannot read %s's model catalog (%s); its models are all left in rotation "
                "and retired ones will not be skipped until this is fixed",
                executor,
                current.unreadable[executor],
            )
        for executor in sorted(set(previous.unreadable) - set(current.unreadable)):
            log.info("%s's model catalog is readable again", executor)
        for candidate in sorted(previous.ejected - current.ejected):
            reason = (
                "its catalog can no longer be read, so it is no longer ejected"
                if self._executors.get(candidate, "") in current.unreadable
                else "its CLI lists it again"
            )
            log.info("model %s is back in selection: %s", candidate, reason)

    def _eject(self, candidate: str) -> None:
        if self._on_eject is None:
            return
        try:
            self._on_eject(candidate)
        except Exception:  # noqa: BLE001 - housekeeping must not stop the ejection
            log.warning("could not drop sticky routes for %s", candidate, exc_info=True)
