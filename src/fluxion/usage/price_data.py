"""Load the LLM price tables, choosing the freshest of the refreshed cache and
the bundled snapshot, and optionally refresh the cache from the shared repo.

**Local-first by design.** The loaders NEVER touch the network. Each table has
two on-disk candidates — a locally-refreshed cache file and the snapshot
bundled with this package — and `load_price_json` uses whichever carries the
newer top-level `updated_at`. The bundled snapshot is therefore a floor: an app
upgrade that ships newer prices takes effect immediately even when a stale
cache exists, and the cache only ever adds data newer than the release.

Consumers key their parsed-table caches on `price_file_stamp()`, so a rewrite
of either candidate (background refresh, `fluxion-usage --refresh-prices`, an
upgrade replacing the bundle) is picked up by running services on the next
lookup — no restart, and never any network I/O on the request path.

Source of truth: https://github.com/superposed-labs/llm-price-table
(the in-package `model_prices.json` / `plan_prices.json` are the bundled
snapshot / offline fallback.)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

_BUNDLED_DIR = Path(__file__).resolve().parent
_REPO = "superposed-labs/llm-price-table"
_DEFAULT_RAW = f"https://raw.githubusercontent.com/{_REPO}/main"

PRICE_FILES = ("model_prices.json", "plan_prices.json")
_URL_ENV = {
    "model_prices.json": "FLUXION_PRICE_MODEL_URL",
    "plan_prices.json": "FLUXION_PRICE_PLAN_URL",
}


def _cache_dir() -> Path:
    """Where a refreshed copy is stored. Tracks Fluxion's data dir
    (`FLUXION_DATA_DIR`), else the conventional `data/` under the cwd."""
    base = os.environ.get("FLUXION_DATA_DIR", "").strip()
    root = Path(base).expanduser() if base else Path("data")
    return root / "price_cache"


def _read_price_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _updated_at(data: dict[str, Any] | None) -> str:
    value = data.get("updated_at") if data else None
    return value if isinstance(value, str) else ""


def load_price_json(name: str) -> dict[str, Any] | None:
    """Return the parsed price file, choosing whichever of the refreshed cache
    and the bundled snapshot has the newer top-level `updated_at` (ISO dates,
    so string comparison orders them). The bundled snapshot wins ties — it went
    through release review — and a candidate that is missing, unparseable, or
    undated loses to a dated one. None if neither is readable (the caller
    applies its own embedded fallback). Never hits the network."""
    cached = _read_price_file(_cache_dir() / name)
    bundled = _read_price_file(_BUNDLED_DIR / name)
    if cached is None or bundled is None:
        return cached if bundled is None else bundled
    return cached if _updated_at(cached) > _updated_at(bundled) else bundled


def price_file_stamp(name: str) -> tuple[tuple[int, int] | None, ...]:
    """Cheap change token for `name`'s two candidate sources: (mtime_ns, size)
    of the cache and bundled copies, None where absent. Parsed-table caches key
    on it so any on-disk change is noticed on the next lookup at the cost of
    two stat calls."""
    stamp: list[tuple[int, int] | None] = []
    for path in (_cache_dir() / name, _BUNDLED_DIR / name):
        try:
            st = path.stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)
    return tuple(stamp)


def _url_for(name: str) -> str:
    env = _URL_ENV.get(name)
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip()
    return f"{_DEFAULT_RAW}/{name}"


def refresh(*, timeout: float = 10.0, cache_dir: Path | None = None) -> list[dict[str, Any]]:
    """Fetch the latest price files from the shared repo into the local cache.
    Explicit / opt-in only — this is the one place that uses the network. JSON
    is validated before writing, so a bad fetch can't corrupt the cache.
    Returns a per-file result list (ok / error) with absolute paths.

    `cache_dir` overrides the env-derived cache location; the CLI passes the
    settings-resolved data dir so a refresh run from any cwd lands where the
    services read."""
    cache = (cache_dir if cache_dir is not None else _cache_dir()).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for name in PRICE_FILES:
        url = _url_for(name)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (https raw URL)
                raw = resp.read().decode("utf-8")
            json.loads(raw)  # validate before persisting
            cache.mkdir(parents=True, exist_ok=True)
            (cache / name).write_text(raw, encoding="utf-8")
            results.append({"file": name, "ok": True, "url": url, "path": str(cache / name)})
        except Exception as exc:  # network/parse/io — refresh is best-effort
            results.append(
                {"file": name, "ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
            )
    return results


# ── opt-in background refresh (long-lived services only) ─────────────
# Mature-app pattern for bundled-data-that-goes-stale: ship the snapshot, and
# refresh it out-of-band in the background — never on the startup/request path,
# always best-effort with fallback. Enabled by default and disclosed (Fluxion
# already makes benign outbound calls for the quota panel, with your creds; this
# is a lighter, credential-free GET to a public repo). Set
# FLUXION_PRICE_AUTO_REFRESH=false to disable (e.g. air-gapped). This is an
# internal service concern, deliberately NOT a user-visible scheduler task.
_refresh_lock = threading.Lock()
_refresh_thread_started = False


def _auto_refresh_enabled() -> bool:
    value = os.environ.get("FLUXION_PRICE_AUTO_REFRESH", "").strip().lower()
    if not value:
        return True  # default on
    return value not in ("0", "false", "no", "off")


def _stale_after_days() -> float:
    try:
        return float(os.environ.get("FLUXION_PRICE_REFRESH_DAYS", "1"))
    except ValueError:
        return 1.0


def _is_stale() -> bool:
    """True if any cached price file is missing or older than the threshold.
    (A missing cache is treated as stale so the first refresh populates it.)"""
    threshold = _stale_after_days()
    for name in PRICE_FILES:
        try:
            age_days = (time.time() - (_cache_dir() / name).stat().st_mtime) / 86400
        except OSError:
            return True  # no cache yet
        if age_days >= threshold:
            return True
    return False


def _invalidate_loaders() -> None:
    """Drop the consumers' cached tables after a refresh. The stamp-keyed
    loaders would notice the rewritten files on their own; clearing just stops
    entries for the replaced files from lingering in memory. Lazy import avoids
    a circular dependency (history/plan_prices import this module)."""
    try:
        from fluxion.usage import plan_prices
        from fluxion.usage.history import pricing

        pricing._load_prices_for_stamp.cache_clear()
        pricing._rates_for.cache_clear()
        plan_prices._load_plan_prices_for_stamp.cache_clear()
    except Exception:
        pass


def _refresh_if_stale() -> bool:
    """One refresh cycle: refresh only when stale, and on success invalidate the
    in-process loaders. Returns True if a refresh wrote something."""
    if not _is_stale():
        return False
    if any(r.get("ok") for r in refresh()):
        _invalidate_loaders()
        return True
    return False


def start_background_refresh(*, check_interval_sec: float = 21_600.0) -> bool:
    """Start ONE daemon thread that refreshes the price cache when stale, on a
    slow cadence. Enabled by default; disable via FLUXION_PRICE_AUTO_REFRESH=false.
    Best-effort and non-blocking — it never touches startup or request latency,
    and falls back to the bundled snapshot on any failure. No-op (returns False)
    when disabled or already running. Call from long-lived services only."""
    global _refresh_thread_started
    if not _auto_refresh_enabled():
        return False
    with _refresh_lock:
        if _refresh_thread_started:
            return False
        _refresh_thread_started = True

    def _loop() -> None:
        while True:
            try:
                _refresh_if_stale()
            except Exception:
                pass  # never crash the host service
            time.sleep(max(60.0, check_interval_sec))

    threading.Thread(target=_loop, name="fluxion-price-refresh", daemon=True).start()
    return True
