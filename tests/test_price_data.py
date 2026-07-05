from __future__ import annotations

import json
import time

from fluxion.usage import price_data


# ── load cascade: cache → bundled → None ────────────────────────────
def test_load_prefers_cache_over_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DATA_DIR", str(tmp_path))
    cache = tmp_path / "price_cache"
    cache.mkdir()
    (cache / "model_prices.json").write_text(
        json.dumps({"version": 99, "models": {}}), encoding="utf-8"
    )
    data = price_data.load_price_json("model_prices.json")
    assert data is not None and data["version"] == 99  # the cache copy, not bundled


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
