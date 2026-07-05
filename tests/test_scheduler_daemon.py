from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

from fluxion.scheduler import cli as scheduler_cli
from fluxion.scheduler import daemon as scheduler_daemon
from fluxion.scheduler.daemon import SchedulerDaemon
from fluxion.scheduler.models import Action, Policy, ScheduleRule, Trigger
from fluxion.scheduler.store import ScheduleStore
from fluxion.usage.models import STATUS_OK, ProviderUsage, UsageWindow


class _FakeHandle:
    def __init__(self, task_id: str) -> None:
        self.agent = "codex"
        self.task_id = task_id
        self.run_id = task_id
        self.accepted = True
        self.summary = "ok"


class _FakeRunner:
    def __init__(self) -> None:
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return _FakeHandle(f"task_{len(self.requests)}")


class _FakeUsage:
    def __init__(self) -> None:
        self.snapshot = []

    def get_usage(self, *, force: bool = False):
        return self.snapshot


def _usage(used, provider="codex", window="7d", resets_at=None):
    return [
        ProviderUsage(
            provider=provider,
            status=STATUS_OK,
            windows=[
                UsageWindow(key=window, label="Weekly", used_percent=used, resets_at=resets_at)
            ],
        )
    ]


def _daemon(tmp_path, rule, *, runner=None, usage=None, settings=None):
    store = ScheduleStore(tmp_path)
    store.save_rules(rule if isinstance(rule, list) else [rule])
    runner = runner or _FakeRunner()
    usage = usage or _FakeUsage()
    settings = settings or types.SimpleNamespace(data_dir=tmp_path)
    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)
    return daemon, store, runner, usage


def _codex_5h(used, *, span_hours=5.0):
    """Codex 5h usage. span_hours≈5 => drifting/idle; small => anchored."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    reset = (now + timedelta(hours=span_hours)).isoformat()
    return [
        ProviderUsage(
            provider="codex",
            status=STATUS_OK,
            fetched_at=now.isoformat(),
            windows=[
                UsageWindow(
                    key="5h",
                    label="5-hour",
                    used_percent=used,
                    resets_at=reset,
                    window_minutes=300,
                ),
                UsageWindow(
                    key="7d",
                    label="Weekly",
                    used_percent=used,
                    resets_at=reset,
                    window_minutes=10080,
                ),
            ],
        )
    ]


def _burst_settings(tmp_path, *, max_attempts=3):
    return types.SimpleNamespace(
        data_dir=tmp_path,
        autoping_max_attempts=max_attempts,
    )


def test_scheduler_cli_disabled_does_not_run_daemon(tmp_path, monkeypatch):
    class _Settings:
        data_dir = tmp_path
        scheduler_enabled = False
        scheduler_tick_sec = 0

        def validate(self, *, require_slack=False):
            return None

    monkeypatch.setattr(sys, "argv", ["fluxion-scheduler", "--once"])
    monkeypatch.setattr(scheduler_cli.Settings, "load", staticmethod(lambda: _Settings()))

    def _unexpected_daemon(*args, **kwargs):
        raise AssertionError("disabled scheduler should not construct the daemon")

    monkeypatch.setattr(scheduler_cli, "SchedulerDaemon", _unexpected_daemon)

    scheduler_cli.main()


def test_quota_refresh_fires_once_on_edge(tmp_path):
    rule = ScheduleRule.new(
        name="r",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="7d"),
        action=Action(type="ping", agent="claude"),
        policy=Policy(cooldown_sec=0),
    )
    daemon, store, runner, usage = _daemon(tmp_path, rule)

    # Claude is not an anchor-burst provider, so it keeps the single-fire path.
    # Tick 1: high usage only establishes a baseline — no fire.
    usage.snapshot = _usage(90.0, provider="claude")
    daemon.tick()
    assert runner.requests == []

    # Tick 2: the cliff is an edge — fire exactly once.
    usage.snapshot = _usage(1.0, provider="claude")
    daemon.tick()
    assert len(runner.requests) == 1

    # Tick 3: still low — the edge was consumed, do not refire.
    usage.snapshot = _usage(1.0, provider="claude")
    daemon.tick()
    assert len(runner.requests) == 1

    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0]["accepted"] is True
    assert runs[0]["task_id"] == "task_1"
    assert runs[0]["action_type"] == "ping"


def test_quota_refresh_ping_targets_provider_when_agent_auto(tmp_path):
    rule = ScheduleRule.new(
        name="r",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="7d"),
        action=Action(type="ping", agent="auto"),
        policy=Policy(cooldown_sec=0),
    )
    daemon, store, runner, usage = _daemon(tmp_path, rule)
    usage.snapshot = _usage(90.0, provider="claude")
    daemon.tick()
    usage.snapshot = _usage(0.0, provider="claude")
    daemon.tick()
    assert len(runner.requests) == 1
    assert runner.requests[0].agent == "claude"  # auto resolved to the provider
    assert runner.requests[0].mode == "read-only"


def test_cron_fires_and_records_run(tmp_path):
    rule = ScheduleRule.new(
        name="c",
        trigger=Trigger(type="cron", cron="* * * * *", timezone="UTC"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0, catch_up="skip"),
    )
    daemon, store, runner, _ = _daemon(tmp_path, rule)
    daemon.tick()
    assert len(runner.requests) == 1
    assert store.list_runs()[0]["trigger_reason"].startswith("cron")


def test_disabled_rule_never_fires(tmp_path):
    rule = ScheduleRule.new(
        name="c",
        trigger=Trigger(type="cron", cron="* * * * *", timezone="UTC"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0),
        enabled=False,
    )
    daemon, store, runner, _ = _daemon(tmp_path, rule)
    daemon.tick()
    assert runner.requests == []


def test_state_dropped_for_removed_rule(tmp_path):
    rule = ScheduleRule.new(
        name="c",
        trigger=Trigger(type="cron", cron="0 0 1 1 *", timezone="UTC"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(),
    )
    daemon, store, runner, _ = _daemon(tmp_path, rule)
    daemon.tick()
    assert rule.id in store.load_state()
    store.save_rules([])  # rule deleted via CRUD
    daemon.tick()
    assert rule.id not in store.load_state()


def test_quota_exhausted_defers_schedule(tmp_path):
    from datetime import datetime, timedelta

    rule = ScheduleRule.new(
        name="c",
        trigger=Trigger(type="cron", cron="* * * * *", timezone="UTC"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0),
    )
    daemon, store, runner, usage = _daemon(tmp_path, rule)

    # Provider is exhausted (100.0%) and resets in the future (10 minutes from now)
    future_reset = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    usage.snapshot = _usage(100.0, provider="codex", resets_at=future_reset)

    # Tick: cron trigger matches, but should defer because codex is exhausted
    daemon.tick()
    assert runner.requests == []

    # Reset last_eval_at to allow the cron trigger to fire again on the second tick
    daemon._state[rule.id].last_eval_at = None

    # Now provider is not exhausted (reset has passed, resets_at is in the past)
    past_reset = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    usage.snapshot = _usage(100.0, provider="codex", resets_at=past_reset)
    daemon.tick()
    assert len(runner.requests) == 1


def test_daemon_only_loads_persisted_rules(tmp_path):
    store = ScheduleStore(tmp_path)
    rule = ScheduleRule.new(
        name="managed elsewhere",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="7d"),
        action=Action(type="ping", agent="codex"),
    )
    store.save_rules([rule])
    runner = _FakeRunner()
    usage = _FakeUsage()
    settings = types.SimpleNamespace(data_dir=tmp_path)
    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)
    rules = daemon._refresh_rules()
    assert [r.id for r in rules] == [rule.id]


def test_daemon_slack_notification_on_quota_refresh(tmp_path, monkeypatch):
    store = ScheduleStore(tmp_path)
    rule = ScheduleRule.new(
        name="Test Quota Reset",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="5h"),
        action=Action(type="ping", agent="claude"),
        policy=Policy(cooldown_sec=0),
    )
    store.save_rules([rule])
    runner = _FakeRunner()
    usage = _FakeUsage()

    # Track sent messages
    sent_messages = []

    class FakeClient:
        def __init__(self, token):
            pass

        def chat_postMessage(self, channel, text, blocks=None):
            sent_messages.append((channel, text, blocks))

    # Mock slack_sdk WebClient
    import sys

    sys.modules["slack_sdk"] = types.ModuleType("slack_sdk")
    sys.modules["slack_sdk"].WebClient = FakeClient

    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        menu_slack_notify_refresh=True,
        scheduler_slack_channel="C999999",
        slack_bot_token="xoxb-fake",
        slack_channel_workspaces={"C999999": tmp_path},
    )

    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)

    # Establish baseline
    usage.snapshot = _usage(90.0, provider="claude", window="5h")
    daemon.tick()
    assert sent_messages == []

    # Trigger quota reset (cliff edge)
    usage.snapshot = _usage(10.0, provider="claude", window="5h")
    daemon.tick()

    # Should have triggered and sent Slack notification
    assert len(sent_messages) == 1
    channel, text, blocks = sent_messages[0]
    assert channel == "C999999"
    assert "Test Quota Reset" in text
    assert "quota_refresh claude/5h" in text
    assert blocks is not None
    assert len(blocks) == 4


def test_daemon_slack_notification_dm_fallback(tmp_path):
    store = ScheduleStore(tmp_path)
    rule = ScheduleRule.new(
        name="Test DM Fallback",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="5h"),
        action=Action(type="ping", agent="claude"),
        policy=Policy(cooldown_sec=0),
    )
    store.save_rules([rule])
    runner = _FakeRunner()
    usage = _FakeUsage()

    sent_messages = []
    conversations_list_called = False
    conversations_open_called = []

    class FakeClientSuccess:
        def __init__(self, token):
            pass

        def conversations_list(self, types):
            nonlocal conversations_list_called
            conversations_list_called = True
            raise Exception("missing_scope error")

        def conversations_open(self, users):
            conversations_open_called.append(users)
            return {"ok": True, "channel": {"id": f"D_{users}"}}

        def chat_postMessage(self, channel, text, blocks=None):
            sent_messages.append((channel, text, blocks))

    import sys

    sys.modules["slack_sdk"] = types.ModuleType("slack_sdk")
    sys.modules["slack_sdk"].WebClient = FakeClientSuccess

    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        menu_slack_notify_refresh=True,
        scheduler_slack_channel="",
        slack_bot_token="xoxb-fake",
        slack_allowed_users={"U12345"},
        slack_channel_workspaces={"C999999": tmp_path},
    )

    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)

    # Establish baseline
    usage.snapshot = _usage(90.0, provider="claude", window="5h")
    daemon.tick()
    assert sent_messages == []

    # Trigger quota reset (cliff edge)
    usage.snapshot = _usage(10.0, provider="claude", window="5h")
    daemon.tick()

    # Assertions
    assert conversations_list_called is True
    assert conversations_open_called == ["U12345"]
    assert len(sent_messages) == 1
    channel, text, blocks = sent_messages[0]
    assert channel == "D_U12345"
    assert "Test DM Fallback" in text
    assert blocks is not None


def test_daemon_slack_notification_dm_fallback_failure(tmp_path):
    store = ScheduleStore(tmp_path)
    rule = ScheduleRule.new(
        name="Test DM Fallback Failure",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="5h"),
        action=Action(type="ping", agent="claude"),
        policy=Policy(cooldown_sec=0),
    )
    store.save_rules([rule])
    runner = _FakeRunner()
    usage = _FakeUsage()

    sent_messages = []
    conversations_list_called = False
    conversations_open_called = []

    class FakeClientFailure:
        def __init__(self, token):
            pass

        def conversations_list(self, types):
            nonlocal conversations_list_called
            conversations_list_called = True
            raise Exception("missing_scope error")

        def conversations_open(self, users):
            conversations_open_called.append(users)
            raise Exception("open_error")

        def chat_postMessage(self, channel, text, blocks=None):
            sent_messages.append((channel, text, blocks))

    import sys

    sys.modules["slack_sdk"] = types.ModuleType("slack_sdk")
    sys.modules["slack_sdk"].WebClient = FakeClientFailure

    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        menu_slack_notify_refresh=True,
        scheduler_slack_channel="",
        slack_bot_token="xoxb-fake",
        slack_allowed_users={"U12345"},
        slack_channel_workspaces={"C999999": tmp_path},
    )

    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)

    # Establish baseline
    usage.snapshot = _usage(90.0, provider="claude", window="5h")
    daemon.tick()
    assert sent_messages == []

    # Trigger quota reset (cliff edge)
    usage.snapshot = _usage(10.0, provider="claude", window="5h")
    daemon.tick()

    # Assertions
    assert conversations_list_called is True
    assert conversations_open_called == ["U12345"]
    assert len(sent_messages) == 1
    channel, text, blocks = sent_messages[0]
    assert channel == "U12345"  # Direct user ID routing fallback
    assert "Test DM Fallback Failure" in text
    assert blocks is not None


def test_daemon_wechat_notification_on_quota_refresh(tmp_path, monkeypatch):
    from fluxion.channels.wechat.context_store import ContextTokenStore
    from fluxion.channels.wechat.credential_store import CredentialStore
    from fluxion.channels.wechat.ilink_client import ILinkClient
    from fluxion.channels.wechat.models import Credentials

    store = ScheduleStore(tmp_path)
    rule = ScheduleRule.new(
        name="Test WeChat Reset",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="7d"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0),
    )
    store.save_rules([rule])
    CredentialStore(tmp_path).save(
        Credentials(bot_token="token", ilink_bot_id="bot", baseurl="https://example.test")
    )
    context_store = ContextTokenStore(tmp_path)
    context_store.save("allowed-user", "allowed-token")
    context_store.save("other-user", "other-token")

    sent_messages = []

    def fake_send_message(self, *, to_user_id, context_token, items):
        sent_messages.append((to_user_id, context_token, items[0].text_item.text))

    monkeypatch.setattr(ILinkClient, "send_message", fake_send_message)
    runner = _FakeRunner()
    usage = _FakeUsage()
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        menu_wechat_notify_refresh=True,
        wechat_allowed_users={"allowed-user"},
    )
    daemon = SchedulerDaemon(settings, store=store, usage=usage, runner=runner, tick_sec=60)

    usage.snapshot = _usage(90.0, provider="codex", window="7d")
    daemon.tick()
    usage.snapshot = _usage(10.0, provider="codex", window="7d")
    daemon.tick()

    assert len(sent_messages) == 1
    user_id, context_token, text = sent_messages[0]
    assert user_id == "allowed-user"
    assert context_token == "allowed-token"
    assert "Test WeChat Reset" in text
    assert "quota_refresh codex/7d" in text


def _reload_settings(tmp_path, **overrides):
    base = dict(data_dir=tmp_path)
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_hot_reload_applies_env_change(tmp_path):
    """Editing .env between ticks still swaps non-schedule settings in place."""
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("")
    store = ScheduleStore(tmp_path)
    runner = _FakeRunner()
    usage = _FakeUsage()

    current = {"s": _reload_settings(tmp_path)}
    daemon = SchedulerDaemon(
        current["s"],
        store=store,
        usage=usage,
        runner=runner,
        tick_sec=60,
        env_path=env_file,
        settings_loader=lambda: current["s"],
    )

    current["s"] = _reload_settings(tmp_path, default_executor="claude")
    st = env_file.stat()
    os.utime(env_file, (st.st_atime + 10, st.st_mtime + 10))

    daemon.tick()  # reload happens at the top of the tick

    assert daemon._settings is current["s"]


def test_hot_reload_skipped_when_mtime_unchanged(tmp_path):
    """A stable .env never triggers a reload, so an injected loader is untouched."""
    env_file = tmp_path / ".env"
    env_file.write_text("")
    store = ScheduleStore(tmp_path)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return _reload_settings(tmp_path)

    daemon = SchedulerDaemon(
        _reload_settings(tmp_path),
        store=store,
        usage=_FakeUsage(),
        runner=_FakeRunner(),
        tick_sec=60,
        env_path=env_file,
        settings_loader=loader,
    )
    daemon.tick()
    daemon.tick()
    assert calls["n"] == 0  # mtime never moved → loader never invoked


def test_hot_reload_drops_runner_on_executor_change(tmp_path):
    """An executor-relevant setting change drops the cached runner so the next
    fire rebuilds executors with the new provider/credentials."""
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("")
    store = ScheduleStore(tmp_path)

    s1 = _reload_settings(tmp_path, claude_provider="third_party")
    s2 = _reload_settings(tmp_path, claude_provider="official")
    current = {"s": s1}

    # No injected runner → daemon owns it and may rebuild it.
    daemon = SchedulerDaemon(
        s1,
        store=store,
        usage=_FakeUsage(),
        tick_sec=60,
        env_path=env_file,
        settings_loader=lambda: current["s"],
    )
    daemon._runner = _FakeRunner()  # pretend a runner was built lazily

    current["s"] = s2
    st = env_file.stat()
    os.utime(env_file, (st.st_atime + 10, st.st_mtime + 10))
    daemon.tick()

    assert daemon._runner is None  # rebuilt lazily on next fire


def _codex_5h_ping_rule():
    return ScheduleRule.new(
        name="c5",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="5h"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0),
    )


def test_anchor_burst_fires_one_per_tick_until_cap(tmp_path):
    daemon, store, runner, usage = _daemon(
        tmp_path, _codex_5h_ping_rule(), settings=_burst_settings(tmp_path, max_attempts=3)
    )
    usage.snapshot = _codex_5h(90.0)  # baseline
    daemon.tick()
    assert runner.requests == []

    usage.snapshot = _codex_5h(1.0)  # cliff edge -> arm + ping #1
    daemon.tick()
    assert len(runner.requests) == 1
    assert runner.requests[0].session_policy == "auto"  # resume within event
    assert "ping-codex" in runner.requests[0].task_name

    for _ in range(6):  # still drifting -> one ping/tick, capped at 3
        usage.snapshot = _codex_5h(1.0)
        daemon.tick()
    assert len(runner.requests) == 3

    # all burst pings share one event-scoped thread (session reuse)
    threads = {r.thread for r in runner.requests}
    assert len(threads) == 1


def test_anchor_burst_stops_when_anchored(tmp_path):
    daemon, store, runner, usage = _daemon(
        tmp_path, _codex_5h_ping_rule(), settings=_burst_settings(tmp_path)
    )
    usage.snapshot = _codex_5h(90.0)
    daemon.tick()
    usage.snapshot = _codex_5h(1.0)  # edge -> ping #1
    daemon.tick()
    assert len(runner.requests) == 1

    usage.snapshot = _codex_5h(1.0, span_hours=1.0)  # now anchored -> stop
    daemon.tick()
    usage.snapshot = _codex_5h(1.0, span_hours=1.0)
    daemon.tick()
    assert len(runner.requests) == 1


def test_anchor_burst_coalesces_5h_and_7d(tmp_path):
    rule_5h = _codex_5h_ping_rule()
    rule_7d = ScheduleRule.new(
        name="c7",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="7d"),
        action=Action(type="ping", agent="codex"),
        policy=Policy(cooldown_sec=0),
    )
    daemon, store, runner, usage = _daemon(
        tmp_path, [rule_5h, rule_7d], settings=_burst_settings(tmp_path)
    )
    usage.snapshot = _codex_5h(90.0)
    daemon.tick()
    usage.snapshot = _codex_5h(1.0)  # both 5h & 7d edges fire same tick
    daemon.tick()
    assert len(runner.requests) == 1  # one pool -> one ping, not two


def test_antigravity_anchor_ping_uses_live_pool_model(tmp_path, monkeypatch):
    settings = types.SimpleNamespace(data_dir=tmp_path, antigravity_command="/bin/agy")
    daemon, _store, runner, _usage = _daemon(tmp_path, _codex_5h_ping_rule(), settings=settings)

    def fake_select(*, pool_key, command):
        assert command == "/bin/agy"
        if pool_key == "antigravity:gemini":
            return "Gemini 3.5 Flash (Low)"
        if pool_key == "antigravity:external":
            return "GPT-OSS 120B (Medium)"
        return None

    monkeypatch.setattr(scheduler_daemon, "select_antigravity_ping_model", fake_select)

    assert daemon._submit_anchor_ping("antigravity:gemini", "antigravity", "evt1")
    assert daemon._submit_anchor_ping("antigravity:external", "antigravity", "evt1")

    assert [request.model for request in runner.requests] == [
        "Gemini 3.5 Flash (Low)",
        "GPT-OSS 120B (Medium)",
    ]


def test_antigravity_anchor_ping_skips_when_live_pool_model_unavailable(tmp_path, monkeypatch):
    settings = types.SimpleNamespace(data_dir=tmp_path, antigravity_command="/bin/agy")
    daemon, store, runner, _usage = _daemon(tmp_path, _codex_5h_ping_rule(), settings=settings)
    monkeypatch.setattr(
        scheduler_daemon,
        "select_antigravity_ping_model",
        lambda *, pool_key, command: None,
    )

    assert daemon._submit_anchor_ping("antigravity:external", "antigravity", "evt1")

    assert runner.requests == []
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0]["accepted"] is False
    assert "no live Antigravity model" in runs[0]["error"]


def _monitor_rule():
    return ScheduleRule.new(
        name="m",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="5h"),
        action=Action(type="notify", agent="codex"),
        policy=Policy(cooldown_sec=0),
    )


def test_monitor_rule_never_pings_when_global_autoping_off(tmp_path):
    # Monitor rule + global Auto Ping off (default): notify only, never ping.
    daemon, store, runner, usage = _daemon(tmp_path, _monitor_rule())

    usage.snapshot = _codex_5h(90.0)
    daemon.tick()  # baseline
    usage.snapshot = _codex_5h(1.0)
    daemon.tick()  # reset edge -> notify only
    assert runner.requests == []

    usage.snapshot = _codex_5h(1.0)
    daemon.tick()
    assert runner.requests == []
    assert daemon._anchors == {}


def test_monitor_rule_pings_when_global_autoping_on(tmp_path):
    # Monitor rule + global Auto Ping on: notify AND anchor-burst.
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        autoping_enabled=True,
        autoping_max_attempts=3,
    )
    daemon, store, runner, usage = _daemon(tmp_path, _monitor_rule(), settings=settings)

    usage.snapshot = _codex_5h(90.0)
    daemon.tick()
    usage.snapshot = _codex_5h(1.0)
    daemon.tick()  # edge -> notify + ping #1
    assert len(runner.requests) == 1
    assert daemon._anchors != {}  # burst armed
    assert runner.requests[0].session_policy == "auto"


def _credit(cid, *, status="available", expires_at="2027-01-01T00:00:00Z"):
    return {
        "id": cid,
        "status": status,
        "granted_at": "2026-06-01T00:00:00Z",
        "expires_at": expires_at,
    }


def _codex_resets(credits):
    """Codex snapshot carrying per-credit identity (the new detection model)."""
    avail = [c for c in credits if c.get("status") == "available"]
    return [
        ProviderUsage(
            provider="codex",
            status=STATUS_OK,
            fetched_at=datetime.now(UTC).isoformat(),
            windows=[],
            resets={"count": len(avail), "expiries": [], "credits": credits},
        )
    ]


def _grant_mocks(daemon, monkeypatch):
    import unittest.mock

    mocks = {
        name: unittest.mock.MagicMock()
        for name in (
            "_notify_slack",
            "_notify_telegram",
            "_notify_qqbot",
            "_notify_feishu",
            "_notify_wechat",
            "_notify_line",
        )
    }
    for name, m in mocks.items():
        monkeypatch.setattr(daemon, name, m)
    return mocks


def test_codex_credit_grant_notifications(tmp_path, monkeypatch):
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_grant=True,
        menu_slack_notify_refresh=True,
        menu_telegram_notify_refresh=True,
        menu_qqbot_notify_refresh=True,
        menu_feishu_notify_refresh=True,
        menu_wechat_notify_refresh=True,
        menu_line_notify_refresh=True,
    )
    daemon, _store, _runner, usage = _daemon(tmp_path, [], settings=settings)
    m = _grant_mocks(daemon, monkeypatch)

    # Tick 1: two pre-existing credits seed the baseline silently.
    usage.snapshot = _codex_resets([_credit("c1"), _credit("c2")])
    daemon.tick()
    m["_notify_slack"].assert_not_called()
    assert daemon._seen_credit_ids == {"c1", "c2"}

    # Tick 2: three new ids appear -> one grant alert, delta 3, 5 available.
    usage.snapshot = _codex_resets(
        [_credit("c1"), _credit("c2"), _credit("c3"), _credit("c4"), _credit("c5")]
    )
    daemon.tick()

    m["_notify_slack"].assert_called_once()
    m["_notify_telegram"].assert_called_once()
    m["_notify_qqbot"].assert_called_once()
    m["_notify_feishu"].assert_called_once()
    m["_notify_wechat"].assert_called_once()
    m["_notify_line"].assert_called_once()

    msg_args = m["_notify_slack"].call_args[0][0]
    assert "Codex granted 3 reset credits" in msg_args
    assert "5 now available" in msg_args

    # Tick 3: nothing new -> no further alert.
    daemon.tick()
    m["_notify_slack"].assert_called_once()


def test_codex_credit_grant_detects_through_masking(tmp_path, monkeypatch):
    """A grant that lands the same poll a credit is consumed (so the available
    count is unchanged) must still be detected — the whole point of id-keying."""
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_grant=True,
        menu_slack_notify_refresh=True,
    )
    daemon, _store, _runner, usage = _daemon(tmp_path, [], settings=settings)
    m = _grant_mocks(daemon, monkeypatch)

    # Seed baseline: 2 available credits.
    usage.snapshot = _codex_resets([_credit("c1"), _credit("c2")])
    daemon.tick()
    m["_notify_slack"].assert_not_called()

    # c1 is consumed (drops off the list) while c3 is granted -> available
    # count stays 2, but c3 is new, so a grant must still fire.
    usage.snapshot = _codex_resets([_credit("c2"), _credit("c3")])
    daemon.tick()
    m["_notify_slack"].assert_called_once()
    assert "Codex granted 1 reset credit" in m["_notify_slack"].call_args[0][0]


def test_codex_credit_grant_persists_across_restart(tmp_path, monkeypatch):
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_grant=True,
        menu_slack_notify_refresh=True,
    )

    # Daemon A seeds the baseline with c1, then exits.
    daemon_a, _s, _r, usage_a = _daemon(tmp_path, [], settings=settings)
    m_a = _grant_mocks(daemon_a, monkeypatch)
    usage_a.snapshot = _codex_resets([_credit("c1")])
    daemon_a.tick()
    m_a["_notify_slack"].assert_not_called()

    # Daemon B (restart, same data_dir) loads the persisted seen-id set.
    daemon_b, _s2, _r2, usage_b = _daemon(tmp_path, [], settings=settings)
    m_b = _grant_mocks(daemon_b, monkeypatch)
    assert daemon_b._seen_credit_ids == {"c1"}

    # The already-seen credit must NOT re-fire after restart...
    usage_b.snapshot = _codex_resets([_credit("c1")])
    daemon_b.tick()
    m_b["_notify_slack"].assert_not_called()

    # ...but a genuinely new credit does.
    usage_b.snapshot = _codex_resets([_credit("c1"), _credit("c2")])
    daemon_b.tick()
    m_b["_notify_slack"].assert_called_once()


def test_codex_credit_expiry_notifications(tmp_path, monkeypatch):
    from datetime import timedelta

    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_expiry=True,
        menu_slack_notify_refresh=True,
        menu_telegram_notify_refresh=True,
        menu_qqbot_notify_refresh=True,
        menu_feishu_notify_refresh=True,
        menu_wechat_notify_refresh=True,
        menu_line_notify_refresh=True,
    )
    daemon, _store, _runner, usage = _daemon(tmp_path, [], settings=settings)
    m = _grant_mocks(daemon, monkeypatch)

    now = datetime.now(UTC)
    soon = (now + timedelta(hours=12)).isoformat()
    far = (now + timedelta(hours=48)).isoformat()
    snap = _codex_resets([_credit("soon", expires_at=soon), _credit("far", expires_at=far)])

    # Tick 1: only the ≤24h credit alerts, once on every channel.
    usage.snapshot = snap
    daemon.tick()
    m["_notify_slack"].assert_called_once()
    m["_notify_telegram"].assert_called_once()
    m["_notify_qqbot"].assert_called_once()
    m["_notify_feishu"].assert_called_once()
    m["_notify_wechat"].assert_called_once()
    m["_notify_line"].assert_called_once()

    msg_args = m["_notify_slack"].call_args[0][0]
    assert "Fluxion Credit Expiry" in msg_args
    assert (now + timedelta(hours=12)).strftime("%b %d") in msg_args

    # Tick 2: same credit ids -> deduped by id, no new alert.
    usage.snapshot = _codex_resets(
        [_credit("soon", expires_at=soon), _credit("far", expires_at=far)]
    )
    daemon.tick()
    m["_notify_slack"].assert_called_once()
    m["_notify_telegram"].assert_called_once()


def test_codex_credit_expiry_ignores_macos_default_off_platform(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(scheduler_daemon.sys, "platform", "linux")
    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_expiry=True,
    )
    daemon, _store, _runner, usage = _daemon(tmp_path, [], settings=settings)

    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    usage.snapshot = _codex_resets([_credit("soon", expires_at=soon)])
    daemon.tick()

    assert daemon._notified_expiry_ids == set()
    assert not (tmp_path / "macos_notifications.jsonl").exists()


def test_codex_credit_expiry_respects_toggle(tmp_path, monkeypatch):
    import unittest.mock
    from datetime import timedelta

    settings = types.SimpleNamespace(
        data_dir=tmp_path,
        notify_credit_expiry=False,
        menu_slack_notify_refresh=True,
    )
    daemon, _store, _runner, usage = _daemon(tmp_path, [], settings=settings)

    mock_slack = unittest.mock.MagicMock()
    monkeypatch.setattr(daemon, "_notify_slack", mock_slack)

    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    usage.snapshot = _codex_resets([_credit("c1", expires_at=soon)])
    daemon.tick()
    mock_slack.assert_not_called()
