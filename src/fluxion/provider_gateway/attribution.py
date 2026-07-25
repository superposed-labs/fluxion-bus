"""Linking a Codex sub-agent turn to the local agent session that served it.

Deliberately *not* a second copy of the token accounting. Fluxion's usage layer
already syncs every Claude Code transcript under `~/.claude/projects`, so a run
started by this gateway is counted there like any other — writing our own entry
with the same numbers would bill the user twice for one turn.

What is missing is the link. The usage layer knows "session `abc` burned 90k
tokens"; Codex knows "sub-thread `t1` under parent `p1` did the review". This
table is the join between them: `entries.session_id` and
`attributions.executor_session_id` are the same value.

Cost per sub-agent is then a join, not a second ledger.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fluxion.provider_gateway.identity import RequestIdentity

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# Subscription quota consumed by a local agent, versus metered API tokens.
BILLING_SUBSCRIPTION = "subscription"
BILLING_API = "api"


@dataclass(frozen=True)
class Attribution:
    """One served turn, tied to whatever executed it."""

    executor_session_id: str
    ingress: str
    provider_id: str
    upstream_model: str
    billing_source: str
    route_hint: str
    request_kind: str
    thread_id: str | None = None
    parent_thread_id: str | None = None
    turn_id: str | None = None
    started_at: float = 0.0
    duration_sec: float = 0.0


class AttributionStore:
    """Append-only record of which agent session served which Codex turn."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            if str(self._db_path) != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._ensure_schema(conn)
            self._conn = conn
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                executor_session_id TEXT NOT NULL,
                ingress TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                upstream_model TEXT NOT NULL,
                billing_source TEXT NOT NULL,
                route_hint TEXT NOT NULL,
                request_kind TEXT NOT NULL,
                thread_id TEXT,
                parent_thread_id TEXT,
                turn_id TEXT,
                started_at REAL NOT NULL,
                duration_sec REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_attr_session
                ON attributions(executor_session_id);
            CREATE INDEX IF NOT EXISTS ix_attr_parent
                ON attributions(parent_thread_id, thread_id);
            """
        )
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def record(
        self,
        identity: RequestIdentity,
        *,
        provider_id: str,
        upstream_model: str,
        billing_source: str,
        executor_session_id: str = "",
        started_at: float | None = None,
        duration_sec: float = 0.0,
    ) -> Attribution | None:
        """Record one served turn.

        Turns with no executor session are skipped: an API-backed turn is
        already attributed by the response itself, and a local run that reported
        no session id gives us nothing to join on.
        """
        if not executor_session_id:
            return None

        record = Attribution(
            executor_session_id=executor_session_id,
            ingress=identity.ingress,
            provider_id=provider_id,
            upstream_model=upstream_model,
            billing_source=billing_source,
            route_hint=identity.route_hint,
            request_kind=identity.request_kind,
            thread_id=identity.thread_id,
            parent_thread_id=identity.parent_thread_id,
            turn_id=identity.turn_id,
            started_at=time.time() if started_at is None else started_at,
            duration_sec=duration_sec,
        )
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT INTO attributions (
                    executor_session_id, ingress, provider_id, upstream_model,
                    billing_source, route_hint, request_kind, thread_id,
                    parent_thread_id, turn_id, started_at, duration_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.executor_session_id,
                    record.ingress,
                    record.provider_id,
                    record.upstream_model,
                    record.billing_source,
                    record.route_hint,
                    record.request_kind,
                    record.thread_id,
                    record.parent_thread_id,
                    record.turn_id,
                    record.started_at,
                    record.duration_sec,
                ),
            )
            conn.commit()
        return record

    def sessions_for_parent(self, parent_thread_id: str) -> list[str]:
        """Agent sessions used by every sub-thread under one parent task.

        The set the usage layer should be asked about to price a whole parent
        task, including all of its sub-agents.
        """
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT DISTINCT executor_session_id FROM attributions "
                    "WHERE parent_thread_id = ? AND executor_session_id <> ''",
                    (parent_thread_id,),
                )
                .fetchall()
            )
        return [row["executor_session_id"] for row in rows]

    def list_recent(self, limit: int = 100) -> list[Attribution]:
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM attributions ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
                .fetchall()
            )
        return [_row_to_attribution(row) for row in rows]


def _row_to_attribution(row: sqlite3.Row) -> Attribution:
    return Attribution(
        executor_session_id=row["executor_session_id"],
        ingress=row["ingress"],
        provider_id=row["provider_id"],
        upstream_model=row["upstream_model"],
        billing_source=row["billing_source"],
        route_hint=row["route_hint"],
        request_kind=row["request_kind"],
        thread_id=row["thread_id"],
        parent_thread_id=row["parent_thread_id"],
        turn_id=row["turn_id"],
        started_at=row["started_at"],
        duration_sec=row["duration_sec"],
    )
