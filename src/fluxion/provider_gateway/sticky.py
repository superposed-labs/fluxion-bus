"""Durable sticky routes: keep one conversation on one model.

Backed by SQLite because these routes are not derivable from anything else. The
usage store next door rebuilds itself from provider logs when its schema moves,
so it can drop and recreate; losing a sticky route instead means a live
conversation silently changes model mid-flight. Schema changes here migrate.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fluxion.provider_gateway.identity import RequestIdentity

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 3

# 90 days, matching FLUXION_PROVIDER_STICKY_TTL_HOURS' default.
#
# What expiry costs is a conversation's continuity, so the window has to cover
# how long people actually leave one alone. A week does not: coming back to a
# thread after a fortnight is ordinary, and the row is a few hundred bytes, so
# nothing is saved by forgetting it sooner.
#
# Not unbounded, though `0` disables expiry for anyone who wants that. The agent
# session a row points at lives in the CLI's own storage and is cleaned up on
# its own schedule; past some age the pointer outlives its target, and resuming
# a session that is no longer there fails in a stranger way than starting cold.
DEFAULT_TTL_SECONDS = 90 * 24 * 3600


@dataclass(frozen=True)
class StickyRoute:
    """A remembered provider/model choice for one conversation."""

    route_key: str
    ingress: str
    provider_id: str
    upstream_model: str
    policy_id: str
    route_hint: str
    identity_confidence: str
    created_at: float
    last_used_at: float
    expires_at: float | None
    pinned: bool = False
    # The local agent session this sub-thread used last time. Lets a follow-up
    # turn resume that session instead of starting an agent with no memory of
    # the work it just did. Empty for API-backed routes.
    executor_session_id: str = ""
    # Where the local agent ran. Codex reports the workspace only when it spawns
    # a sub-agent; every follow-up turn on that sub-thread arrives with none, so
    # without this the second turn has nowhere to run. Empty for API-backed
    # routes and for ingresses that never report one.
    workspace: str = ""
    thread_id: str | None = None
    parent_thread_id: str | None = None
    routing_reason: tuple[str, ...] = ()

    @property
    def candidate_id(self) -> str:
        return f"{self.provider_id}:{self.upstream_model}"

    def is_expired(self, now: float) -> bool:
        # A pinned route is a standing operator decision; TTL must not undo it.
        if self.pinned or self.expires_at is None:
            return False
        return self.expires_at <= now


class StickyStore:
    """Thread-safe SQLite-backed sticky route storage."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        ttl_seconds: float | None = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── connection / schema ──────────────────────────────────────────
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        if str(self._db_path) != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = self._open()
            self._ensure_schema(conn)
            return conn
        except sqlite3.DatabaseError as err:
            # A corrupt file must not take the gateway down with it: losing
            # sticky routes costs re-routing, refusing to start costs every
            # request. Quarantine rather than delete so the file can be examined.
            quarantined = self._quarantine(err)
            if quarantined is None:
                raise
            conn = self._open()
            self._ensure_schema(conn)
            return conn

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # The gateway and the web UI can both hold this open; wait out a peer's
        # write rather than failing the request with "database is locked".
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _quarantine(self, err: Exception) -> Path | None:
        if str(self._db_path) == ":memory:":
            return None
        path = Path(self._db_path)
        if not path.exists():
            return None
        target = path.with_suffix(f"{path.suffix}.corrupt-{int(time.time())}")
        path.rename(target)
        # WAL sidecars belong to the quarantined file; leaving them behind would
        # let SQLite replay them onto the fresh database.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
            if sidecar.exists():
                sidecar.rename(Path(f"{target}{suffix}"))
        log.warning("sticky store at %s was unreadable (%s); moved to %s", path, err, target)
        return target

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sticky_routes (
                route_key TEXT PRIMARY KEY,
                ingress TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                upstream_model TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                route_hint TEXT NOT NULL,
                identity_confidence TEXT NOT NULL,
                thread_id TEXT,
                parent_thread_id TEXT,
                routing_reason TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                expires_at REAL,
                pinned INTEGER NOT NULL DEFAULT 0,
                executor_session_id TEXT NOT NULL DEFAULT '',
                workspace TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_sticky_expires ON sticky_routes(expires_at);
            CREATE INDEX IF NOT EXISTS ix_sticky_parent ON sticky_routes(ingress, parent_thread_id);
            """
        )
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version and version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"sticky store schema v{version} is newer than this build supports "
                f"(v{_SCHEMA_VERSION}); refusing to downgrade"
            )
        # Migrations, applied in order. Never drop the table: sticky routes
        # cannot be reconstructed from any other source, so a schema change has
        # to preserve the rows rather than start over.
        if version and version < _SCHEMA_VERSION:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(sticky_routes)").fetchall()
            }
            for column in ("executor_session_id", "workspace"):
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE sticky_routes ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── read / write ─────────────────────────────────────────────────
    def lookup(self, route_key: str, *, now: float | None = None) -> StickyRoute | None:
        """Return the remembered route, or None when absent or expired.

        An expired row is deleted on read rather than merely ignored, so a
        conversation that goes quiet past its TTL does not leave a row that
        later reappears as a surprise route.
        """
        now = time.time() if now is None else now
        with self._lock:
            row = (
                self._db()
                .execute("SELECT * FROM sticky_routes WHERE route_key = ?", (route_key,))
                .fetchone()
            )
            if row is None:
                return None
            route = _row_to_route(row)
            if route.is_expired(now):
                self._db().execute("DELETE FROM sticky_routes WHERE route_key = ?", (route_key,))
                self._db().commit()
                return None
            return route

    def remember(
        self,
        identity: RequestIdentity,
        provider_id: str,
        upstream_model: str,
        policy_id: str,
        *,
        routing_reason: Sequence[str] = (),
        executor_session_id: str = "",
        workspace: str = "",
        now: float | None = None,
    ) -> StickyRoute | None:
        """Persist a route choice. Returns None when the identity is not persistable.

        Refusing ephemeral identities here rather than at the call site makes the
        rule impossible to forget: pinning a route to an identity we guessed at
        would leak one conversation's model choice into an unrelated one.
        """
        if not identity.is_persistable:
            return None

        now = time.time() if now is None else now
        expires_at = None if self._ttl_seconds is None else now + self._ttl_seconds
        reason_json = json.dumps(list(routing_reason))

        with self._lock:
            conn = self._db()
            # Preserve created_at and the pin flag across re-selection: a route
            # that gets re-chosen is the same route, and an operator pin must
            # survive the conversation continuing.
            conn.execute(
                """
                INSERT INTO sticky_routes (
                    route_key, ingress, provider_id, upstream_model, policy_id,
                    route_hint, identity_confidence, thread_id, parent_thread_id,
                    routing_reason, created_at, last_used_at, expires_at, pinned,
                    executor_session_id, workspace
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(route_key) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    upstream_model = excluded.upstream_model,
                    policy_id = excluded.policy_id,
                    route_hint = excluded.route_hint,
                    identity_confidence = excluded.identity_confidence,
                    routing_reason = excluded.routing_reason,
                    last_used_at = excluded.last_used_at,
                    expires_at = excluded.expires_at,
                    -- Keep the previous session when a turn reports none, so a
                    -- failed run does not erase a resumable agent session.
                    executor_session_id = CASE
                        WHEN excluded.executor_session_id <> '' THEN excluded.executor_session_id
                        ELSE sticky_routes.executor_session_id
                    END,
                    -- Same rule, and the reason this column exists: the turn
                    -- that reports no workspace is exactly the one that needs
                    -- the remembered one.
                    workspace = CASE
                        WHEN excluded.workspace <> '' THEN excluded.workspace
                        ELSE sticky_routes.workspace
                    END
                """,
                (
                    identity.route_key,
                    identity.ingress,
                    provider_id,
                    upstream_model,
                    policy_id,
                    identity.route_hint,
                    identity.confidence.value,
                    identity.thread_id,
                    identity.parent_thread_id,
                    reason_json,
                    now,
                    now,
                    expires_at,
                    executor_session_id,
                    workspace,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sticky_routes WHERE route_key = ?", (identity.route_key,)
            ).fetchone()
        return _row_to_route(row)

    def touch(self, route_key: str, *, now: float | None = None) -> None:
        """Extend a route's life because it was just used.

        Called after a successful turn, not on lookup: a route that is looked up
        and then rejected by capability filtering has not been used, and
        renewing it would keep a dead route alive indefinitely.
        """
        now = time.time() if now is None else now
        expires_at = None if self._ttl_seconds is None else now + self._ttl_seconds
        with self._lock:
            conn = self._db()
            conn.execute(
                "UPDATE sticky_routes SET last_used_at = ?, expires_at = ? WHERE route_key = ?",
                (now, expires_at, route_key),
            )
            conn.commit()

    def forget(self, route_key: str) -> bool:
        """Drop a route (provider drain, manual reset). Returns whether it existed."""
        with self._lock:
            conn = self._db()
            deleted = conn.execute(
                "DELETE FROM sticky_routes WHERE route_key = ?", (route_key,)
            ).rowcount
            conn.commit()
        return bool(deleted)

    def set_pinned(self, route_key: str, pinned: bool) -> bool:
        """Pin or unpin a route. A pinned route ignores TTL and drain."""
        with self._lock:
            conn = self._db()
            updated = conn.execute(
                "UPDATE sticky_routes SET pinned = ? WHERE route_key = ?",
                (1 if pinned else 0, route_key),
            ).rowcount
            conn.commit()
        return bool(updated)

    def drain_provider(self, provider_id: str) -> int:
        """Forget every unpinned route on a provider; returns how many were dropped.

        Pinned routes survive so that draining a provider cannot silently
        override an explicit operator decision.
        """
        with self._lock:
            conn = self._db()
            dropped = conn.execute(
                "DELETE FROM sticky_routes WHERE provider_id = ? AND pinned = 0",
                (provider_id,),
            ).rowcount
            conn.commit()
        return dropped

    def drain_model(self, provider_id: str, upstream_model: str) -> int:
        """Forget every unpinned route on one model; returns how many were dropped.

        The per-model counterpart to `drain_provider`, for when one model of a
        healthy provider is ejected rather than the whole provider. Without it
        those rows survive their model: every turn on those conversations looks
        one up, has it rejected by the health filter, and adds another
        `sticky-dropped=` line to a reason list that is supposed to explain
        unusual decisions. Dropping the row makes the next turn an ordinary
        selection instead.

        Pinned routes survive, on the same grounds as a provider drain: a pin is
        an explicit operator decision, and this is an automatic process.
        """
        with self._lock:
            conn = self._db()
            dropped = conn.execute(
                "DELETE FROM sticky_routes "
                "WHERE provider_id = ? AND upstream_model = ? AND pinned = 0",
                (provider_id, upstream_model),
            ).rowcount
            conn.commit()
        return dropped

    def purge_expired(self, *, now: float | None = None) -> int:
        """Delete expired, unpinned routes; returns how many were removed."""
        now = time.time() if now is None else now
        with self._lock:
            conn = self._db()
            removed = conn.execute(
                "DELETE FROM sticky_routes "
                "WHERE pinned = 0 AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).rowcount
            conn.commit()
        return removed

    def list_routes(self, *, ingress: str | None = None) -> list[StickyRoute]:
        """All stored routes, newest use first. For the Web UI and `provider routes`."""
        query = "SELECT * FROM sticky_routes"
        params: tuple[str, ...] = ()
        if ingress:
            query += " WHERE ingress = ?"
            params = (ingress,)
        query += " ORDER BY last_used_at DESC"
        with self._lock:
            rows = self._db().execute(query, params).fetchall()
        return [_row_to_route(row) for row in rows]


def _row_to_route(row: sqlite3.Row) -> StickyRoute:
    return StickyRoute(
        route_key=row["route_key"],
        ingress=row["ingress"],
        provider_id=row["provider_id"],
        upstream_model=row["upstream_model"],
        policy_id=row["policy_id"],
        route_hint=row["route_hint"],
        identity_confidence=row["identity_confidence"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        pinned=bool(row["pinned"]),
        executor_session_id=row["executor_session_id"] or "",
        workspace=row["workspace"] or "",
        thread_id=row["thread_id"],
        parent_thread_id=row["parent_thread_id"],
        routing_reason=tuple(_load_reason(row["routing_reason"])),
    )


def _load_reason(value: str | None) -> list[str]:
    """Decode the reason list, tolerating anything unexpected.

    A malformed reason blob must not make a valid route unreadable — the reason
    is explanatory, the route is operational.
    """
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []
