"""Top-level entry points: the persisted scan cache and the console service.

``compute_usage_stats`` is the one-shot path; ``UsageHistoryService`` is the
long-lived path that holds parsed entries in memory behind a TTL and adds the
Codex account-token reconciliation. Both sit on top of parsing + aggregation.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fluxion.usage.history.aggregate import WINDOW_DAYS, aggregate
from fluxion.usage.history.parsing import (
    ANTIGRAVITY_CONVERSATIONS_DIRS,
    CLAUDE_PROJECTS_DIR,
    CODEX_SESSIONS_DIR,
    collect_antigravity_entries,
    collect_claude_entries,
    collect_codex_entries,
)
from fluxion.usage.history.store import UsageStore
from fluxion.usage.probes import CodexAccountUsage, CodexAccountUsageProbe

_CACHE_VERSION = 7


def _parse_usage_date(raw: str, fallback: date) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback - timedelta(days=1)


def _load_cache(cache_path: Path | None) -> dict[str, Any]:
    if cache_path is None or not cache_path.exists():
        return {"version": _CACHE_VERSION, "files": {}}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _CACHE_VERSION, "files": {}}
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return {"version": _CACHE_VERSION, "files": {}}
    data.setdefault("files", {})
    return data


def _save_cache(cache_path: Path | None, cache: dict[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def compute_usage_stats(
    *,
    window: str = "all",
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    sessions_dir: Path | None = None,
    antigravity_dirs: Iterable[Path] | None = None,
    cache_path: Path | None = None,
    tz: timezone | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Parse local transcripts (incrementally, via `cache_path`) and return the
    token-usage console payload for `window` ("all" | "30d" | "7d").

    Codex and Antigravity histories are included only when their corresponding
    directory arguments are given, so focused tests don't pick up live data."""
    if window not in WINDOW_DAYS:
        window = "all"
    cache = _load_cache(cache_path)
    entries = collect_claude_entries(projects_dir, cache=cache)
    if sessions_dir is not None:
        entries += collect_codex_entries(sessions_dir, cache=cache)
    if antigravity_dirs is not None:
        entries += collect_antigravity_entries(antigravity_dirs, cache=cache)
    _save_cache(cache_path, cache)
    payload = aggregate(entries, window=window, tz=tz, now=now)
    payload["generated_at"] = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    return payload


class UsageHistoryService:
    """Serves the token-usage console with a TTL on the transcript scan.

    Re-globbing every transcript on each request is wasteful, so the histories
    are synced into a persistent :class:`UsageStore` at most once per
    `refresh_sec` (re-reading only files whose mtime/size changed, and only the
    appended tail of those). The per-window roll-up runs in SQL on every call, so
    a fresh process never rebuilds the whole history in memory just to answer."""

    def __init__(
        self,
        *,
        projects_dir: Path = CLAUDE_PROJECTS_DIR,
        sessions_dir: Path = CODEX_SESSIONS_DIR,
        antigravity_dirs: Iterable[Path] = ANTIGRAVITY_CONVERSATIONS_DIRS,
        db_path: Path | str | None = None,
        refresh_sec: int = 60,
        codex_account_usage_probe: CodexAccountUsageProbe | None = None,
    ) -> None:
        self._projects_dir = projects_dir
        self._sessions_dir = sessions_dir
        self._antigravity_dirs = tuple(antigravity_dirs)
        self._refresh_sec = max(1, refresh_sec)
        self._lock = threading.Lock()
        self._store = UsageStore(db_path if db_path is not None else ":memory:")
        self._codex_account_usage_probe = codex_account_usage_probe
        self._account_usage: CodexAccountUsage | None = None
        self._account_usage_at = 0.0
        self._account_usage_loaded = False

    def _get_account_usage(self, now: float, force: bool) -> CodexAccountUsage | None:
        if self._codex_account_usage_probe is None:
            return None
        if (
            force
            or not self._account_usage_loaded
            or now - self._account_usage_at >= self._refresh_sec
        ):
            try:
                self._account_usage = self._codex_account_usage_probe.probe()
            except Exception:
                # Reconciliation is advisory; local history must remain usable
                # through network/auth/backend failures.
                pass
            self._account_usage_at = now
            self._account_usage_loaded = True
        return self._account_usage

    @staticmethod
    def _add_reconciliation(
        payload: dict[str, Any], account: CodexAccountUsage | None, window: str
    ) -> None:
        if account is None:
            payload["codex_reconciliation"] = {"status": "unavailable"}
            return
        local = next(
            (
                int(row.get("total_tokens", 0))
                for row in payload.get("by_provider", [])
                if row.get("provider") == "codex"
            ),
            0,
        )
        if window == "all":
            server = account.lifetime_tokens
        else:
            days = WINDOW_DAYS.get(window)
            today = datetime.now().astimezone().date()
            cutoff = today - timedelta(days=days - 1) if days else None
            server = (
                sum(
                    tokens
                    for raw_date, tokens in account.daily_usage_buckets.items()
                    if cutoff is None or _parse_usage_date(raw_date, cutoff) >= cutoff
                )
                if account.daily_usage_buckets
                else None
            )
        if server is None:
            payload["codex_reconciliation"] = {"status": "unavailable"}
            return
        uncovered = max(0, server - local)
        excess = max(0, local - server)
        payload["codex_reconciliation"] = {
            "status": "ok",
            "local_tokens": local,
            "server_tokens": server,
            "unclassified_tokens": uncovered,
            "excess_local_tokens": excess,
            "coverage": round(min(1.0, local / server), 4) if server > 0 else None,
            "fetched_at": account.fetched_at,
        }

    def get(self, window: str = "all", *, force: bool = False) -> dict[str, Any]:
        if window not in WINDOW_DAYS:
            window = "all"
        with self._lock:
            now = time.monotonic()
            # Reuse a recent sync — including one written by an earlier process —
            # so a freshly opened console skips the whole-filesystem walk.
            age = self._store.seconds_since_sync()
            if force or age is None or age >= self._refresh_sec:
                self._store.sync(
                    projects_dir=self._projects_dir,
                    sessions_dir=self._sessions_dir,
                    antigravity_dirs=self._antigravity_dirs,
                )
            payload = self._store.aggregate(window)
            self._add_reconciliation(payload, self._get_account_usage(now, force), window)
            payload["generated_at"] = datetime.now(UTC).isoformat()
            return payload
