from __future__ import annotations

import json
import time
from pathlib import Path

from fluxion.usage import price_data


def _write_cache(tmp_path, name: str, payload: dict) -> Path:
    cache = tmp_path / "price_cache"
    cache.mkdir(exist_ok=True)
    path = cache / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bundled_updated_at(name: str) -> str:
    return json.loads((price_data._BUNDLED_DIR / name).read_text(encoding="utf-8"))["updated_at"]


# ── load: the newer of cache and bundled wins ────────────────────────
def test_load_prefers_newer_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path,
        "model_prices.json",
        {"updated_at": "9999-01-01", "marker": "cache-copy", "models": {}},
    )
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and data.get("marker") == "cache-copy"  # cache is newer → cache wins


def test_load_prefers_newer_bundled_over_stale_cache(tmp_path, monkeypatch):
    # The upgrade scenario: the app ships a bundled table newer than the local
    # cache — the stale cache must not mask it.
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path,
        "model_prices.json",
        {"updated_at": "2000-01-01", "marker": "cache-copy", "models": {}},
    )
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and "marker" not in data and "models" in data  # bundled snapshot


def test_load_tie_prefers_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path,
        "model_prices.json",
        {
            "updated_at": _bundled_updated_at("model_prices.json"),
            "marker": "cache-copy",
            "models": {},
        },
    )
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and "marker" not in data  # tie → the release-reviewed bundle


def test_load_undated_cache_loses_to_dated_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path, "model_prices.json", {"marker": "cache-copy", "models": {}}
    )  # no updated_at
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and "marker" not in data and "models" in data


def test_load_falls_back_to_bundled_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))  # empty → no cache file
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and "models" in data  # the bundled snapshot


def test_load_returns_none_for_unknown_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    assert price_data.load_price_json("nope.json") is None


def test_load_ignores_corrupt_cache_falls_through(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    cache = tmp_path / "price_cache"
    cache.mkdir()
    (cache / "model_prices.json").write_text("{ not json", encoding="utf-8")
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and "models" in data  # corrupt cache skipped → bundled


# ── file stamp: cheap change token for the cached loaders ────────────
def test_stamp_changes_when_cache_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    before = price_data.price_file_stamp("model_prices.json")
    assert before[0] is None  # no cache yet
    assert before[1] is not None  # bundled copy present
    _write_cache(tmp_path, "model_prices.json", {"updated_at": "9999-01-01", "models": {}})
    after = price_data.price_file_stamp("model_prices.json")
    assert after != before and after[0] is not None


def test_rewritten_cache_takes_effect_without_restart(tmp_path, monkeypatch):
    # Regression for the stale-GPT-5.6 defect: an external refresh rewrites the
    # cache while a service is running; the next lookup must serve the new
    # rates without anyone calling cache_clear or restarting.
    from fluxion.usage.history import pricing

    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path,
        "model_prices.json",
        {
            "updated_at": "9999-01-01",
            "models": {
                "m-test": {"rates": [{"effective_date": "2025-01-01", "in": 1.0, "out": 2.0}]}
            },
        },
    )
    assert pricing._rates_for("codex", "m-test")["in"] == 1.0
    _write_cache(
        tmp_path,
        "model_prices.json",
        {
            "updated_at": "9999-01-02",
            "models": {
                "m-test": {"rates": [{"effective_date": "2025-01-01", "in": 77.5, "out": 90.0}]}
            },
        },
    )
    assert pricing._rates_for("codex", "m-test")["in"] == 77.5


def test_plan_prices_pick_up_rewritten_cache(tmp_path, monkeypatch):
    from fluxion.usage import plan_prices

    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    _write_cache(
        tmp_path,
        "plan_prices.json",
        {"updated_at": "9999-01-01", "plans": {"codex": {"plus": 25}}},
    )
    assert plan_prices.plan_monthly_for("codex", "plus") == 25.0
    _write_cache(
        tmp_path,
        "plan_prices.json",
        {"updated_at": "9999-01-02", "plans": {"codex": {"plus": 42.5}}},
    )
    assert plan_prices.plan_monthly_for("codex", "plus") == 42.5


def test_price_basis_date_reflects_selected_source(tmp_path, monkeypatch):
    # The stats payload surfaces _load_prices()["updated_at"] as the UI's
    # "price basis date" — it must follow whichever source actually won.
    from fluxion.usage.history import pricing

    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    bundled_date = _bundled_updated_at("model_prices.json")
    _write_cache(tmp_path, "model_prices.json", {"updated_at": "2000-01-01", "models": {}})
    assert pricing._load_prices().get("updated_at") == bundled_date  # stale cache masked
    _write_cache(tmp_path, "model_prices.json", {"updated_at": "9999-01-01", "models": {}})
    assert pricing._load_prices().get("updated_at") == "9999-01-01"


# ── refresh: validates before writing, best-effort ──────────────────
class _FakeResp:
    def __init__(self, body: str):
        self._b = body.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_refresh_writes_validated_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        price_data.urllib.request,
        "urlopen",
        lambda url, timeout=10.0: _FakeResp(json.dumps({"fetched": url})),
    )
    results = price_data.refresh()
    assert all(r["ok"] for r in results)
    for name in price_data.PRICE_FILES:
        assert (tmp_path / "price_cache" / name).exists()


def test_refresh_rejects_bad_json_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        price_data.urllib.request,
        "urlopen",
        lambda url, timeout=10.0: _FakeResp("<html>not json</html>"),
    )
    results = price_data.refresh()
    assert all(not r["ok"] for r in results)
    assert not (tmp_path / "price_cache").exists()  # nothing written


def test_refresh_explicit_cache_dir_wins_and_paths_are_absolute(tmp_path, monkeypatch):
    # The CLI passes the settings-resolved dir so a refresh run from any cwd
    # lands where the services read — the env-derived location must be ignored.
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path / "env-dir"))
    monkeypatch.setattr(
        price_data.urllib.request, "urlopen", lambda url, timeout=10.0: _FakeResp("{}")
    )
    target = tmp_path / "explicit" / "price_cache"
    results = price_data.refresh(cache_dir=target)
    assert all(r["ok"] for r in results)
    for r in results:
        assert Path(r["path"]).is_absolute()
        assert Path(r["path"]).parent == target.resolve()
    assert not (tmp_path / "env-dir").exists()  # nothing leaked to the env location


def test_refresh_url_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLUXION_PRICE_MODEL_URL", "https://example.test/custom.json")
    seen = []
    monkeypatch.setattr(
        price_data.urllib.request,
        "urlopen",
        lambda url, timeout=10.0: seen.append(url) or _FakeResp("{}"),
    )
    price_data.refresh()
    assert "https://example.test/custom.json" in seen


# ── background refresher: default-on, staleness-gated, disable-able ──
def test_auto_refresh_default_on(monkeypatch):
    monkeypatch.delenv("FLUXION_PRICE_AUTO_REFRESH", raising=False)
    assert price_data._auto_refresh_enabled() is True  # default on
    monkeypatch.setenv("FLUXION_PRICE_AUTO_REFRESH", "false")
    assert price_data._auto_refresh_enabled() is False
    monkeypatch.setenv("FLUXION_PRICE_AUTO_REFRESH", "true")
    assert price_data._auto_refresh_enabled() is True


def test_background_refresh_disabled_when_false(monkeypatch):
    monkeypatch.setenv("FLUXION_PRICE_AUTO_REFRESH", "false")
    assert price_data.start_background_refresh() is False  # no thread


def test_background_refresh_starts_when_enabled(monkeypatch):
    monkeypatch.delenv("FLUXION_PRICE_AUTO_REFRESH", raising=False)  # default on
    monkeypatch.setattr(price_data, "_refresh_thread_started", False)
    monkeypatch.setattr(price_data, "_refresh_if_stale", lambda: False)  # no network
    assert price_data.start_background_refresh(check_interval_sec=3600) is True
    assert price_data.start_background_refresh() is False  # already running


def test_is_stale_missing_then_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    assert price_data._is_stale() is True  # no cache yet → stale
    cache = tmp_path / "price_cache"
    cache.mkdir()
    for name in price_data.PRICE_FILES:
        (cache / name).write_text("{}", encoding="utf-8")
    assert price_data._is_stale() is False  # just written → fresh


def test_is_stale_when_old(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLUXION_PRICE_REFRESH_DAYS", "7")
    cache = tmp_path / "price_cache"
    cache.mkdir()
    old = time.time() - 30 * 86400  # 30 days ago
    for name in price_data.PRICE_FILES:
        p = cache / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (old, old))
    assert price_data._is_stale() is True


def test_refresh_if_stale_refreshes_and_invalidates(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(price_data, "_is_stale", lambda: True)
    monkeypatch.setattr(price_data, "refresh", lambda: [{"file": "x", "ok": True}])
    called = []
    monkeypatch.setattr(price_data, "_invalidate_loaders", lambda: called.append(True))
    assert price_data._refresh_if_stale() is True
    assert called == [True]


def test_refresh_if_stale_skips_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(price_data, "_is_stale", lambda: False)
    monkeypatch.setattr(
        price_data, "refresh", lambda: (_ for _ in ()).throw(AssertionError("should not refresh"))
    )
    assert price_data._refresh_if_stale() is False
