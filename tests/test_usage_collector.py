from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fluxion.usage import collector
from fluxion.usage.models import ProviderUsage, UsageWindow


def _settings(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path,
        ui_locale="en",
        usage_panel_enabled=True,
        usage_refresh_sec=60,
    )


def _payload(generated_at: str) -> dict:
    return {
        "enabled": True,
        "locale": "en",
        "generated_at": generated_at,
        "providers": [
            {
                "provider": "claude",
                "status": "ok",
                "account_label": "pro",
                "windows": [
                    {
                        "key": "5h",
                        "label": "5-hour",
                        "used_percent": 25.0,
                        "resets_at": None,
                        "window_minutes": 300,
                        "remaining": None,
                        "total": None,
                    }
                ],
                "fetched_at": generated_at,
                "detail": "",
            }
        ],
    }


def test_collect_usage_payload_reuses_fresh_shared_cache(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    expected = _payload(datetime.now(UTC).isoformat())
    (tmp_path / "usage_cache.json").write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(
        collector,
        "usage_service_from_settings",
        lambda _settings: (_ for _ in ()).throw(AssertionError("must not probe")),
    )

    assert collector.collect_usage_payload(settings) == expected


def test_collect_usage_payload_force_always_probes(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []

    class FakeService:
        def get_usage(self, *, force=False, force_providers=None):
            calls.append((force, force_providers))
            return [
                ProviderUsage(
                    provider="claude",
                    status="ok",
                    windows=[UsageWindow(key="5h", label="5-hour", used_percent=10)],
                    fetched_at=datetime.now(UTC).isoformat(),
                )
            ]

        def collector_state(self):
            return {}

    monkeypatch.setattr(collector, "usage_service_from_settings", lambda _settings: FakeService())

    first = collector.collect_usage_payload(settings, force=True)
    collector.collect_usage_payload(settings, force=True)

    assert calls == [(True, None), (True, None)]
    assert "_collector_state" not in first
    assert (
        json.loads((tmp_path / "usage_cache.json").read_text(encoding="utf-8"))["enabled"] is True
    )


def _fake_service(providers: list[str]):
    class FakeService:
        def get_usage(self, *, force=False, force_providers=None):
            return [
                ProviderUsage(
                    provider=name,
                    status="ok",
                    windows=[UsageWindow(key="5h", label="5-hour", used_percent=10)],
                    fetched_at=datetime.now(UTC).isoformat(),
                )
                for name in providers
            ]

        def collector_state(self):
            return {}

    return FakeService()


def test_collect_usage_payload_retires_disabled_providers(tmp_path, monkeypatch):
    # A provider present in the old cache but absent from this run (disabled)
    # is carried under the private _retired_providers key, not dropped.
    settings = _settings(tmp_path)
    old = _payload((datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    old["providers"].append({**old["providers"][0], "provider": "codex"})
    (tmp_path / "usage_cache.json").write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(
        collector, "usage_service_from_settings", lambda _settings: _fake_service(["claude"])
    )

    public = collector.collect_usage_payload(settings, force=True)

    written = json.loads((tmp_path / "usage_cache.json").read_text(encoding="utf-8"))
    assert [p["provider"] for p in written["providers"]] == ["claude"]
    assert [p["provider"] for p in written["_retired_providers"]] == ["codex"]
    # Public consumers (scheduler, web) never see retired snapshots.
    assert "_retired_providers" not in public


def test_collect_usage_payload_restores_reenabled_provider(tmp_path, monkeypatch):
    # Once the provider probes again, its retired snapshot is promoted back
    # out and no longer duplicated under the private key.
    settings = _settings(tmp_path)
    old = _payload((datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    old["_retired_providers"] = [{**old["providers"][0], "provider": "codex"}]
    (tmp_path / "usage_cache.json").write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(
        collector,
        "usage_service_from_settings",
        lambda _settings: _fake_service(["claude", "codex"]),
    )

    collector.collect_usage_payload(settings, force=True)

    written = json.loads((tmp_path / "usage_cache.json").read_text(encoding="utf-8"))
    assert {p["provider"] for p in written["providers"]} == {"claude", "codex"}
    assert "_retired_providers" not in written


def test_retired_snapshot_survives_repeated_writes(tmp_path, monkeypatch):
    # The retired entry must persist across many cache rewrites while the
    # provider stays disabled — not just the first one after disabling.
    settings = _settings(tmp_path)
    old = _payload((datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    old["_retired_providers"] = [{**old["providers"][0], "provider": "codex"}]
    (tmp_path / "usage_cache.json").write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(
        collector, "usage_service_from_settings", lambda _settings: _fake_service(["claude"])
    )

    collector.collect_usage_payload(settings, force=True)
    collector.collect_usage_payload(settings, force=True)

    written = json.loads((tmp_path / "usage_cache.json").read_text(encoding="utf-8"))
    assert [p["provider"] for p in written["_retired_providers"]] == ["codex"]


def test_shared_usage_client_uses_fresh_cache_without_subprocess(tmp_path):
    settings = _settings(tmp_path)
    payload = _payload(datetime.now(UTC).isoformat())
    (tmp_path / "usage_cache.json").write_text(json.dumps(payload), encoding="utf-8")

    def fail_runner(*args, **kwargs):
        raise AssertionError("must not launch collector")

    usages = collector.SharedUsageClient(settings, runner=fail_runner).get_usage()

    assert usages[0].provider == "claude"
    assert usages[0].windows[0].used_percent == 25.0


def test_shared_usage_client_refreshes_stale_cache_with_short_process(tmp_path):
    settings = _settings(tmp_path)
    stale = _payload((datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    fresh = _payload(datetime.now(UTC).isoformat())
    (tmp_path / "usage_cache.json").write_text(json.dumps(stale), encoding="utf-8")
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(fresh), stderr="")

    usages = collector.SharedUsageClient(settings, runner=runner).get_usage()

    assert commands[0][0][-2:] == ["-m", "fluxion.cli.usage"]
    assert usages[0].fetched_at == fresh["providers"][0]["fetched_at"]


def test_shared_usage_client_force_passes_force_to_short_process(tmp_path):
    settings = _settings(tmp_path)
    fresh = _payload(datetime.now(UTC).isoformat())
    (tmp_path / "usage_cache.json").write_text(json.dumps(fresh), encoding="utf-8")
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(fresh), stderr="")

    collector.SharedUsageClient(settings, runner=runner).get_usage(force=True)

    assert commands[0][-1] == "--force"


def test_shared_usage_client_falls_back_to_stale_cache_on_failure(tmp_path):
    settings = _settings(tmp_path)
    stale = _payload((datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    (tmp_path / "usage_cache.json").write_text(json.dumps(stale), encoding="utf-8")

    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    usages = collector.SharedUsageClient(settings, runner=runner).get_usage()

    assert usages[0].provider == "claude"
