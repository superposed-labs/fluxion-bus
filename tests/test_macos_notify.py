"""Queueing notifications for the desktop app to deliver.

`queue_throttled` exists for standing conditions — a finding stays true until
someone fixes it, so a check that runs more often than the fix would re-send the
same notification every cycle. The two failure modes it sits between: nagging
until the user mutes the channel, and going so quiet that a finding is forgotten.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from fluxion.utils import macos_notify

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="notification signal file is macOS-only"
)


def records(tmp_path) -> list[dict]:
    signal = tmp_path / macos_notify.FILENAME
    if not signal.exists():
        return []
    return [json.loads(line) for line in signal.read_text().splitlines()]


def test_queue_writes_one_record_with_extras(tmp_path):
    assert macos_notify.queue(tmp_path, "title", "body", channel="telegram") is True
    (record,) = records(tmp_path)
    assert record["title"] == "title"
    assert record["channel"] == "telegram"
    assert record["timestamp"]


def test_queue_reports_failure_instead_of_raising(tmp_path):
    """A notification that cannot be sent must not take down its caller."""
    assert macos_notify.queue(tmp_path / "no-such-dir", "t", "b") is False


def test_the_same_finding_is_not_repeated(tmp_path):
    first = macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    second = macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    assert (first, second) == (macos_notify.QUEUED, macos_notify.SUPPRESSED)
    assert len(records(tmp_path)) == 1


def test_a_changed_finding_notifies_immediately(tmp_path):
    macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    result = macos_notify.queue_throttled(tmp_path, "k", "t", "different", fingerprint="def")
    assert result == macos_notify.QUEUED
    assert len(records(tmp_path)) == 2


def test_an_unchanged_finding_is_repeated_after_the_interval(tmp_path):
    """Suppression must not become silence: an unfixed finding comes back."""
    macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    state = tmp_path / "runtime" / "notify-k.json"
    stale = datetime.now(UTC) - timedelta(hours=25)
    state.write_text(json.dumps({"fingerprint": "abc", "notified_at": stale.isoformat()}))

    result = macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    assert result == macos_notify.QUEUED


def test_clearing_lets_a_returning_finding_notify_again(tmp_path):
    macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    macos_notify.clear_throttle(tmp_path, "k")

    result = macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    assert result == macos_notify.QUEUED, "fixed then broken again is news"


def test_clearing_an_absent_state_is_not_an_error(tmp_path):
    macos_notify.clear_throttle(tmp_path, "never-notified")


def test_unreadable_state_costs_one_duplicate_not_an_exception(tmp_path):
    macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc")
    (tmp_path / "runtime" / "notify-k.json").write_text("{ truncated")

    assert macos_notify.queue_throttled(tmp_path, "k", "t", "b", fingerprint="abc") == (
        macos_notify.QUEUED
    )


def test_separate_keys_do_not_throttle_each_other(tmp_path):
    macos_notify.queue_throttled(tmp_path, "one", "t", "b", fingerprint="abc")
    result = macos_notify.queue_throttled(tmp_path, "two", "t", "b", fingerprint="abc")
    assert result == macos_notify.QUEUED
