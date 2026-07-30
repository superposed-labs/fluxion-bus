"""Queue a notification for the macOS desktop app to deliver.

Every Fluxion notification reaches Notification Center the same way: a record is
appended to `macos_notifications.jsonl` in the data directory, the desktop app's
cache watcher picks it up, delivers it through `UNUserNotificationCenter`, and
truncates the file. That indirection is what makes the notification look like it
comes from Fluxion — icon, app name, grouping with the rest. A process that calls
`osascript display notification` itself gets Notification Center's generic script
styling instead, attributed to whatever ran the script.

No-op off macOS, so the file never accumulates records with no consumer.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

FILENAME = "macos_notifications.jsonl"

QUEUED = "queued"
SUPPRESSED = "suppressed"
FAILED = "failed"


def queue(data_dir: Path, title: str, body: str, **extra: object) -> bool:
    """Append one notification record. Returns whether it was written.

    Errors are reported to the caller rather than raised: a notification that
    cannot be queued must not take down the work that wanted to send it.
    """
    if sys.platform != "darwin":
        return False
    record = {
        "title": title,
        "body": body,
        **extra,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        with open(data_dir / FILENAME, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def _state_path(data_dir: Path, key: str) -> Path:
    return data_dir / "runtime" / f"notify-{key}.json"


def queue_throttled(
    data_dir: Path,
    key: str,
    title: str,
    body: str,
    *,
    fingerprint: str,
    repeat_after_hours: float = 24.0,
) -> str:
    """Notify about a *standing* condition without repeating it every check.

    A finding that needs a human stays true until that human acts, so a check
    running more often than the fix does would re-send the same notification
    every cycle — which is how a channel gets muted, taking the next real finding
    with it. `fingerprint` identifies the finding: a changed one always notifies,
    an unchanged one waits out `repeat_after_hours` so it is not forgotten either.

    Notification history lives beside the other runtime state and is disposable:
    losing it costs one duplicate notification, which is why nothing here fails
    the caller over an unreadable or unwritable state file.
    """
    if sys.platform != "darwin":
        return SUPPRESSED
    state_file = _state_path(data_dir, key)
    now = datetime.now(UTC)
    try:
        state = json.loads(state_file.read_text())
        last_seen = state.get("fingerprint")
        notified_at = datetime.fromisoformat(state["notified_at"])
    except (OSError, ValueError, KeyError, TypeError):
        last_seen, notified_at = None, None

    if last_seen == fingerprint and notified_at is not None:
        age_hours = (now - notified_at).total_seconds() / 3600
        if age_hours < repeat_after_hours:
            return SUPPRESSED

    if not queue(data_dir, title, body, fingerprint=fingerprint):
        return FAILED
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"fingerprint": fingerprint, "notified_at": now.isoformat()})
        )
    except OSError:
        pass
    return QUEUED


def clear_throttle(data_dir: Path, key: str) -> None:
    """Forget the last notification, so the condition notifies again if it returns.

    Called when a check comes back clean: without this, a finding that is fixed
    and then reappears within `repeat_after_hours` would be silently swallowed.
    """
    try:
        _state_path(data_dir, key).unlink()
    except OSError:
        pass
