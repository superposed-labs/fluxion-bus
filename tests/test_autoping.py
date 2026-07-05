from fluxion.scheduler.autoping import get_autoping_modes, set_autoping_mode
from fluxion.scheduler.models import Action, ScheduleRule, Trigger
from fluxion.scheduler.store import ScheduleStore


def test_managed_autoping_modes_round_trip(tmp_path):
    store = ScheduleStore(tmp_path)

    assert get_autoping_modes(store) == {
        "claude": "off",
        "codex": "off",
        "antigravity": "off",
    }

    modes = set_autoping_mode(store, "codex", "both")
    assert modes["codex"] == "both"
    rules = store.load_rules()
    assert {r.trigger.window_key for r in rules} == {"5h", "7d"}
    assert all(r.managed_by == "autoping" for r in rules)
    assert all(r.policy.cooldown_sec == 600 for r in rules)

    modes = set_autoping_mode(store, "codex", "off")
    assert modes["codex"] == "off"
    assert store.load_rules() == []


def test_autoping_preserves_manual_rules(tmp_path):
    store = ScheduleStore(tmp_path)
    manual = ScheduleRule.new(
        name="manual",
        trigger=Trigger(type="quota_refresh", provider="claude", window_key="7d"),
        action=Action(type="ping", agent="claude"),
    )
    store.save_rules([manual])

    set_autoping_mode(store, "codex", "7d")
    set_autoping_mode(store, "codex", "off")

    assert [r.id for r in store.load_rules()] == [manual.id]


def test_antigravity_mode_manages_all_matching_windows(tmp_path):
    store = ScheduleStore(tmp_path)
    set_autoping_mode(store, "antigravity", "5h")

    assert {r.trigger.window_key for r in store.load_rules()} == {
        "Gemini (5h)",
        "External Models (5h)",
        "Gemini",
    }


def test_autoping_coexists_with_manual_ping(tmp_path):
    # Managed monitor rules (ACTION_NOTIFY) never ping themselves, so enabling a
    # watch on a window that already has a manual ping schedule is allowed — both
    # rules coexist and the daemon dedupes anchor pings per provider/window pool.
    store = ScheduleStore(tmp_path)
    manual = ScheduleRule.new(
        name="manual weekly ping",
        trigger=Trigger(type="quota_refresh", provider="codex", window_key="7d"),
        action=Action(type="ping", agent="codex"),
    )
    store.save_rules([manual])

    set_autoping_mode(store, "codex", "7d")

    rules = store.load_rules()
    assert manual.id in {r.id for r in rules}
    managed = [r for r in rules if r.managed_by == "autoping"]
    assert {r.trigger.window_key for r in managed} == {"7d"}


def test_setting_mode_updates_existing_managed_policy(tmp_path):
    store = ScheduleStore(tmp_path)
    set_autoping_mode(store, "codex", "7d")
    rule = store.load_rules()[0]
    rule.policy.cooldown_sec = 1800
    store.save_rules([rule])

    set_autoping_mode(store, "codex", "7d")

    assert store.load_rules()[0].policy.cooldown_sec == 600
