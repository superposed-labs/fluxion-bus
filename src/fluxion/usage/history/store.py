"""SQLite-backed token-usage store: incremental scan state + a deduped entry
table that is aggregated in SQL.

The JSON scan cache (``service._load_cache``/``_save_cache``) had to deserialise
and re-serialise the *entire* history on every cold start and refresh, then
rebuild every :class:`UsageEntry` in Python before ``aggregate`` could run. At
~90k turns that floor was ~1.5–2s per console open regardless of how little
had changed. This store keeps the parsed turns as rows so a refresh touches only
the files that grew, and rolls the console up with ``GROUP BY`` instead of a
full in-memory pass.

The on-disk roll-up is timezone-dependent (``day``/``hour`` are local), so the
local UTC offset is recorded and the entry table is rebuilt if it changes. The
pure :func:`aggregate` path stays the source of truth for the payload shape and
the pricing rules; this module reproduces it and is pinned to it by tests.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fluxion.usage.history import pricing
from fluxion.usage.history.aggregate import (
    PROVIDER_DAY_SERIES_DAYS,
    PROVIDER_HOUR_SERIES_DAYS,
    WINDOW_DAYS,
    _Bucket,
    _streaks,
)
from fluxion.usage.history.antigravity_db import _parse_antigravity_db, _sqlite_signature
from fluxion.usage.history.entry import UsageEntry
from fluxion.usage.history.parsing import (
    _claude_line_parser,
    _codex_line_parser,
    _dedupe_codex_paths,
    _parse_incremental,
)

# Bumping this drops `entries` and `files` and reparses every transcript from
# scratch (`_ensure_schema`).
#
# **Raise it whenever a line parser's output changes**, not only when a column
# is added. `files` records the offset and scan state of everything already
# read, so an unchanged transcript is never parsed again — the corrected rule
# applies to new turns while the old rows sit in `entries` forever, and the
# aggregate stays wrong in a way no amount of restarting fixes.
#
# v4: `_codex_line_parser` stopped emitting turns served by a provider other
# than OpenAI. Shipped one commit too late — the rule landed while 61 already
# parsed phantom turns (3.75M input tokens under a model slug that does not
# exist) stayed in the table, so the fix reported success and changed nothing.
_SCHEMA_VERSION = 4


def _local(ts: datetime, tz: timezone | None) -> datetime:
    return ts.astimezone(tz) if tz is not None else ts.astimezone()


def _tz_marker(tz: timezone | None) -> int:
    """A stable key for the timezone the stored day/hour columns were computed
    in. Uses standard-time offset (not the DST-variant name) so daylight shifts
    don't trigger a rebuild but an actual relocation does."""
    if tz is not None:
        off = tz.utcoffset(None)
        return int(off.total_seconds()) if off else 0
    return -time.timezone


class UsageStore:
    """Persisted, incrementally-synced token-usage history backed by SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── connection / schema ──────────────────────────────────────────
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            if str(self._db_path) != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # Several short-lived fluxion-web processes can open this DB at once;
            # wait out a peer's write instead of erroring with "database is locked".
            conn.execute("PRAGMA busy_timeout=5000")
            self._conn = conn
            self._ensure_schema(conn)
        return self._conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != _SCHEMA_VERSION:
            conn.executescript(
                "DROP TABLE IF EXISTS entries; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;"
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, provider TEXT NOT NULL,
                mtime REAL, size INTEGER, offset INTEGER, state TEXT
            );
            CREATE TABLE IF NOT EXISTS entries (
                dedup_key TEXT PRIMARY KEY, path TEXT NOT NULL, provider TEXT NOT NULL,
                ts TEXT NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL,
                model TEXT NOT NULL, session_id TEXT NOT NULL,
                i INTEGER, o INTEGER, cc INTEGER, cc1h INTEGER, cr INTEGER,
                bi INTEGER, is_fast INTEGER, total INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_entries_day ON entries(day);
            CREATE INDEX IF NOT EXISTS ix_entries_path ON entries(path);
            """
        )
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.commit()

    # ── sync ─────────────────────────────────────────────────────────
    # On equal totals the *earlier* timestamp wins (mirrors `aggregate`): a
    # Codex fork replays the parent history with timestamps rewritten to the
    # fork instant, and only the original copy carries the real day — this also
    # keeps those rows owned by the parent file's `path`, so deleting the
    # forked rollout doesn't drop the parent's history.
    _UPSERT = """
        INSERT INTO entries
            (dedup_key, path, provider, ts, day, hour, model, session_id,
             i, o, cc, cc1h, cr, bi, is_fast, total)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(dedup_key) DO UPDATE SET
            path=excluded.path, provider=excluded.provider, ts=excluded.ts,
            day=excluded.day, hour=excluded.hour, model=excluded.model,
            session_id=excluded.session_id, i=excluded.i, o=excluded.o,
            cc=excluded.cc, cc1h=excluded.cc1h, cr=excluded.cr, bi=excluded.bi,
            is_fast=excluded.is_fast, total=excluded.total
        WHERE excluded.total > entries.total
           OR (excluded.total = entries.total AND excluded.ts < entries.ts)
    """

    def sync(
        self,
        *,
        projects_dir: Path,
        sessions_dir: Path,
        archived_sessions_dir: Path | None = None,
        antigravity_dirs: Iterable[Path],
        tz: timezone | None = None,
    ) -> None:
        """Bring the store level with the local histories, parsing only files
        that changed (appended files re-read just their new tail)."""
        if archived_sessions_dir is None:
            archived_sessions_dir = sessions_dir.parent / "archived_sessions"

        with self._lock:
            conn = self._db()
            self._reset_if_tz_changed(conn, tz)
            # One read of the scan bookkeeping up front; the per-file change check
            # is then an in-memory dict hit, not a query per file (899+ of them).
            known = {
                row[0]: row[1:]
                for row in conn.execute("SELECT path, mtime, size, offset, state FROM files")
            }
            seen: set[str] = set()
            self._sync_lines(
                conn, known, projects_dir, "*.jsonl", "claude", _claude_line_parser, tz, seen
            )
            codex_roots = [sessions_dir]
            if archived_sessions_dir:
                codex_roots.append(archived_sessions_dir)
            self._sync_lines(
                conn, known, codex_roots, "rollout-*.jsonl", "codex", _codex_line_parser, tz, seen
            )
            self._sync_antigravity(conn, known, antigravity_dirs, tz, seen)
            self._drop_missing(conn, set(known) - seen)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('synced_at',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (repr(time.time()),),
            )
            conn.commit()
            # WAL only resets its write-point on a passive checkpoint; without a
            # TRUNCATE it grew to ~50MB after the initial bulk load. Reclaim it so
            # the sidecar file doesn't shadow the DB on disk.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def seconds_since_sync(self) -> float | None:
        """Wall-clock seconds since the last completed sync by *any* process, or
        None if the store has never been synced. Lets a freshly started process
        reuse a recent sync instead of re-walking the filesystem."""
        with self._lock:
            row = self._db().execute("SELECT value FROM meta WHERE key='synced_at'").fetchone()
        if not row:
            return None
        try:
            return max(0.0, time.time() - float(row[0]))
        except (TypeError, ValueError):
            return None

    def _reset_if_tz_changed(self, conn: sqlite3.Connection, tz: timezone | None) -> None:
        marker = str(_tz_marker(tz))
        row = conn.execute("SELECT value FROM meta WHERE key='tz'").fetchone()
        if row is not None and row[0] != marker:
            # Stored local day/hour no longer match the active timezone; the
            # cheapest correct fix is to re-derive everything from scratch.
            conn.executescript("DELETE FROM entries; DELETE FROM files;")
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('tz',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (marker,),
        )

    def _upsert_entries(
        self, conn: sqlite3.Connection, path: Path, entries: list[UsageEntry], tz: timezone | None
    ) -> None:
        key = str(path)
        rows = []
        for e in entries:
            loc = _local(e.ts, tz)
            rows.append(
                (
                    e.dedup_key,
                    key,
                    e.provider,
                    e.ts.isoformat(),
                    loc.date().isoformat(),
                    loc.hour,
                    e.model,
                    e.session_id,
                    e.input_tokens,
                    e.output_tokens,
                    e.cache_creation_tokens,
                    e.cache_creation_1h_tokens,
                    e.cache_read_tokens,
                    e.billed_input_tokens_total,
                    1 if e.is_fast else 0,
                    e.total_tokens,
                )
            )
        if rows:
            conn.executemany(self._UPSERT, rows)

    def _sync_lines(
        self,
        conn: sqlite3.Connection,
        known: dict[str, Any],
        roots: Path | Iterable[Path],
        pattern: str,
        provider: str,
        parser: Any,
        tz: timezone | None,
        seen: set[str],
    ) -> None:
        if isinstance(roots, Path):
            roots = (roots,)
        else:
            roots = tuple(roots)

        paths: list[Path] = []
        for root in roots:
            if root.is_dir():
                paths.extend(root.rglob(pattern))

        if provider == "codex":
            final_paths = _dedupe_codex_paths(paths, roots[0])
            # Basename → known-path index, built once. Scanning `known` (with a
            # Path() constructed per probe) for every rollout was O(files²) —
            # ~3.5M Path.name calls for ~1.9k files, the bulk of an otherwise
            # no-op sync. final_paths carries one path per basename, so each
            # index bucket is consulted at most once and needs no maintenance
            # as `known` mutates below.
            known_by_name: dict[str, list[str]] = {}
            for key in known:
                known_by_name.setdefault(Path(key).name, []).append(key)
            for chosen in final_paths:
                chosen_key = str(chosen)
                matching_old_keys = [
                    key for key in known_by_name.get(chosen.name, ()) if key != chosen_key
                ]
                for old_key in matching_old_keys:
                    exists = conn.execute(
                        "SELECT 1 FROM files WHERE path = ?", (chosen_key,)
                    ).fetchone()
                    if exists:
                        # The selected copy already has its own scan state. Drop
                        # the duplicate rather than attaching stale-only turns to
                        # the authoritative path.
                        conn.execute("DELETE FROM entries WHERE path = ?", (old_key,))
                        conn.execute("DELETE FROM files WHERE path = ?", (old_key,))
                        known.pop(old_key, None)
                    else:
                        conn.execute(
                            "UPDATE files SET path = ? WHERE path = ?", (chosen_key, old_key)
                        )
                        conn.execute(
                            "UPDATE entries SET path = ? WHERE path = ?", (chosen_key, old_key)
                        )
                        known[chosen_key] = known.pop(old_key)

            paths = final_paths

        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            seen.add(key)
            cached = known.get(key)  # (mtime, size, offset, state)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                continue  # unchanged

            # Append-only fast path: the file only grew, so resume from the
            # recorded offset/state and parse just the appended bytes.
            if cached and cached[2] is not None and stat.st_size > cached[1]:
                start_offset = int(cached[2])
                state = json.loads(cached[3]) if cached[3] else {}
            else:
                start_offset, state = 0, {}
                conn.execute("DELETE FROM entries WHERE path=?", (key,))

            committed, tail, end_offset = _parse_incremental(path, start_offset, state, parser)
            # The tail (an unterminated final record) is re-parsed every scan; the
            # dedup_key upsert keeps that idempotent, so it is safe to insert here.
            self._upsert_entries(conn, path, committed + tail, tz)
            conn.execute(
                "INSERT INTO files(path,provider,mtime,size,offset,state) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, "
                "offset=excluded.offset, state=excluded.state",
                (key, provider, stat.st_mtime, stat.st_size, end_offset, json.dumps(state)),
            )

    def _sync_antigravity(
        self,
        conn: sqlite3.Connection,
        known: dict[str, Any],
        conversations_dirs: Iterable[Path],
        tz: timezone | None,
        seen: set[str],
    ) -> None:
        for root in conversations_dirs:
            if not root.is_dir():
                continue
            for path in root.glob("*.db"):
                key = str(path)
                seen.add(key)
                signature = json.dumps(_sqlite_signature(path))
                cached = known.get(key)  # (mtime, size, offset, state)
                if cached and cached[3] == signature:
                    continue
                conn.execute("DELETE FROM entries WHERE path=?", (key,))
                self._upsert_entries(conn, path, _parse_antigravity_db(path), tz)
                conn.execute(
                    "INSERT INTO files(path,provider,mtime,size,offset,state) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET state=excluded.state",
                    (key, "antigravity", 0, 0, None, signature),
                )

    def _drop_missing(self, conn: sqlite3.Connection, stale: set[str]) -> None:
        for path in stale:
            conn.execute("DELETE FROM entries WHERE path=?", (path,))
            conn.execute("DELETE FROM files WHERE path=?", (path,))

    # ── aggregation ──────────────────────────────────────────────────
    def aggregate(
        self, window: str = "all", *, tz: timezone | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Roll the stored turns into the console payload for `window`.

        Mirrors :func:`fluxion.usage.history.aggregate.aggregate` field-for-field
        (verified by tests) but reduces in SQL so an unchanged history is never
        re-walked in Python."""
        if window not in WINDOW_DAYS:
            window = "all"
        now = (now or datetime.now(UTC)).astimezone(UTC)
        today = _local(now, tz).date()
        days = WINDOW_DAYS.get(window)
        cutoff = (today - timedelta(days=days - 1)).isoformat() if days else None

        with self._lock:
            conn = self._db()
            where = "WHERE day >= ?" if cutoff else ""
            params = (cutoff,) if cutoff else ()

            total = _Bucket()
            by_provider: dict[str, _Bucket] = {}
            by_model: dict[str, _Bucket] = {}
            model_provider: dict[str, str] = {}
            model_cost: dict[str, float] = {}
            model_context_counts: dict[str, dict[str, int]] = {}
            by_day: dict[str, _Bucket] = {}
            total_cost = 0.0
            uncosted_tokens = 0
            total_context_counts = {"short": 0, "long": 0}
            providers: set[str] = set()

            # Cost can now vary per turn for the same (day, provider, model)
            # because long-context tiers are request-sized, so price each row at
            # the entry grain and aggregate the rest separately in SQL.
            cost_rows = conn.execute(
                f"""SELECT day, provider, model, is_fast, i, o, cc, cc1h, cr, bi
                    FROM entries {where}""",
                params,
            ).fetchall()
            for day, provider, model, is_fast, si, so, scc, scc1h, scr, sbi in cost_rows:
                bucket = UsageEntry(
                    provider=provider,
                    ts=now,
                    model=model,
                    session_id="",
                    input_tokens=si,
                    output_tokens=so,
                    cache_creation_tokens=scc,
                    cache_read_tokens=scr,
                    dedup_key="",
                    billed_input_tokens_total=sbi,
                    cache_creation_1h_tokens=scc1h,
                    is_fast=bool(is_fast),
                )
                rate = pricing._rates_for(provider, model, day, bool(is_fast))
                cost = pricing._entry_cost(bucket, rate)
                context_tier = pricing._context_tier_for_entry(bucket, rate)
                total_cost += cost
                model_cost[model] = model_cost.get(model, 0.0) + cost
                if context_tier is not None:
                    total_context_counts[context_tier] += 1
                    per_model = model_context_counts.setdefault(model, {"short": 0, "long": 0})
                    per_model[context_tier] += 1
                if rate is None:
                    uncosted_tokens += bucket.total_tokens

            grain = conn.execute(
                f"""SELECT day, provider, model,
                        SUM(i), SUM(o), SUM(cc), SUM(cc1h), SUM(cr), COUNT(*)
                    FROM entries {where}
                    GROUP BY day, provider, model""",
                params,
            ).fetchall()
            for day, provider, model, si, so, scc, _scc1h, scr, n in grain:
                providers.add(provider)
                model_provider.setdefault(model, provider)
                for sink in (
                    total,
                    by_provider.setdefault(provider, _Bucket()),
                    by_model.setdefault(model, _Bucket()),
                    by_day.setdefault(day, _Bucket()),
                ):
                    sink.input_tokens += si
                    sink.output_tokens += so
                    sink.cache_creation_tokens += scc
                    sink.cache_read_tokens += scr
                    sink.messages += n

            # Distinct session counts and the hourly histogram can't be derived
            # from the priced grain, so query them directly.
            sess_clause = (where + " AND" if where else "WHERE") + " session_id <> ''"
            total_sessions = conn.execute(
                f"SELECT COUNT(DISTINCT session_id) FROM entries {sess_clause}", params
            ).fetchone()[0]
            model_sessions = dict(
                conn.execute(
                    f"SELECT model, COUNT(DISTINCT session_id) FROM entries {sess_clause} "
                    "GROUP BY model",
                    params,
                ).fetchall()
            )
            by_hour_rows = dict(
                (h, (m, tok))
                for h, m, tok in conn.execute(
                    f"SELECT hour, COUNT(*), SUM(i + o + cc + cr) FROM entries {where} "
                    "GROUP BY hour",
                    params,
                ).fetchall()
            )

            # Query by_day for the entire history to populate the heatmap calendar
            by_day_full = {}
            for day, si, so, scc, scr, n in conn.execute(
                """SELECT day, SUM(i), SUM(o), SUM(cc), SUM(cr), COUNT(*)
                   FROM entries
                   GROUP BY day"""
            ).fetchall():
                bucket = _Bucket()
                bucket.input_tokens = si
                bucket.output_tokens = so
                bucket.cache_creation_tokens = scc
                bucket.cache_read_tokens = scr
                bucket.messages = n
                by_day_full[day] = bucket

            # Trailing per-provider day series for the notch sparkline — like
            # by_day, independent of `window` (see aggregate.py).
            provider_day_cutoff = (today - timedelta(days=PROVIDER_DAY_SERIES_DAYS - 1)).isoformat()
            by_provider_day: dict[tuple[str, str], _Bucket] = {}
            for day, provider, si, so, scc, scr, n in conn.execute(
                """SELECT day, provider, SUM(i), SUM(o), SUM(cc), SUM(cr), COUNT(*)
                   FROM entries WHERE day >= ?
                   GROUP BY day, provider""",
                (provider_day_cutoff,),
            ).fetchall():
                bucket = _Bucket()
                bucket.input_tokens = si
                bucket.output_tokens = so
                bucket.cache_creation_tokens = scc
                bucket.cache_read_tokens = scr
                bucket.messages = n
                by_provider_day[(day, provider)] = bucket

            # Provider-specific trailing-seven-day hourly activity. This is
            # deliberately independent of the requested window, just like the
            # notch's provider-day series above.
            provider_hour_cutoff = (
                today - timedelta(days=PROVIDER_HOUR_SERIES_DAYS - 1)
            ).isoformat()
            by_provider_hour = {
                (provider, hour): (messages, total_tokens or 0)
                for provider, hour, messages, total_tokens in conn.execute(
                    """SELECT provider, hour, COUNT(*), SUM(i + o + cc + cr)
                       FROM entries WHERE day >= ?
                       GROUP BY provider, hour""",
                    (provider_hour_cutoff,),
                ).fetchall()
            }

        return self._payload(
            window,
            today,
            providers,
            total,
            total_cost,
            uncosted_tokens,
            total_context_counts,
            total_sessions,
            by_provider,
            by_model,
            model_provider,
            model_cost,
            model_context_counts,
            model_sessions,
            by_day_full,
            by_provider_day,
            by_provider_hour,
            by_hour_rows,
        )

    @staticmethod
    def _payload(
        window,
        today,
        providers,
        total,
        total_cost,
        uncosted_tokens,
        total_context_counts,
        total_sessions,
        by_provider,
        by_model,
        model_provider,
        model_cost,
        model_context_counts,
        model_sessions,
        by_day_full,
        by_provider_day,
        by_provider_hour,
        by_hour_rows,
    ) -> dict[str, Any]:
        active_dates_full = {date.fromisoformat(d) for d in by_day_full}
        current_streak, longest_streak = _streaks(active_dates_full, today)

        days = WINDOW_DAYS.get(window)
        if days:
            active_days = sum(1 for d in active_dates_full if (today - d).days < days)
            span_days = days
        else:
            active_days = len(active_dates_full)
            span_days = (today - min(active_dates_full)).days + 1 if active_dates_full else 0

        peak_hour: int | None = max(
            range(24), key=lambda h: by_hour_rows.get(h, (0, 0))[0], default=0
        )
        if not by_hour_rows or by_hour_rows.get(peak_hour, (0, 0))[0] == 0:
            peak_hour = None

        top_model = max(by_model, key=lambda m: by_model[m].total_tokens, default=None)

        model_rows = [
            {
                "model": model,
                "provider": model_provider.get(model, ""),
                "messages": b.messages,
                "sessions": int(model_sessions.get(model, 0)),
                "cost": round(model_cost.get(model, 0.0), 4),
                "context_tier_breakdown": model_context_counts.get(model, {"short": 0, "long": 0}),
                **b.token_dict(),
            }
            for model, b in by_model.items()
        ]
        model_rows.sort(key=lambda row: row["total_tokens"], reverse=True)

        day_list_full = sorted(by_day_full)
        first_day = day_list_full[0] if day_list_full else None
        return {
            "window": window,
            "providers": sorted(providers),
            "prices_updated_at": pricing._load_prices().get("updated_at"),
            "totals": {
                "sessions": int(total_sessions),
                "messages": total.messages,
                "active_days": active_days,
                "span_days": span_days,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                "peak_hour": peak_hour,
                "top_model": top_model,
                "cache_hit": total.cache_hit,
                "cost": round(total_cost, 2),
                "uncosted_tokens": uncosted_tokens,
                "context_tier_breakdown": total_context_counts,
                "first_day": first_day,
                "last_day": day_list_full[-1] if day_list_full else None,
                **total.token_dict(),
            },
            "by_day": [
                {
                    "date": d,
                    "messages": by_day_full[d].messages,
                    "total_tokens": by_day_full[d].total_tokens,
                }
                for d in day_list_full
            ],
            "by_provider_day": [
                {
                    "date": d,
                    "provider": p,
                    "total_tokens": bucket.total_tokens,
                    "generated_tokens": bucket.generated_tokens,
                }
                for (d, p), bucket in sorted(by_provider_day.items())
            ],
            "by_provider_hour": [
                {
                    "provider": provider,
                    "hour": hour,
                    "messages": messages,
                    "total_tokens": total_tokens,
                }
                for (provider, hour), (messages, total_tokens) in sorted(by_provider_hour.items())
            ],
            "by_hour": [
                {
                    "hour": h,
                    "messages": by_hour_rows.get(h, (0, 0))[0],
                    "total_tokens": by_hour_rows.get(h, (0, 0))[1] or 0,
                }
                for h in range(24)
            ],
            "by_model": model_rows,
            "by_provider": [
                {"provider": provider, **bucket.token_dict()}
                for provider, bucket in sorted(by_provider.items())
            ],
        }
