"""Live working notes read from agy's trajectory while a run is in flight.

`agy` prints nothing to stdout until the very end, when it dumps the whole
answer at once — so a sub-agent backed by it shows a blank window for the
entire run, however long that is. Its conversation DB, though, is written *as
the run proceeds* (measured: a new `steps` row every two or three seconds), and
every tool step embeds a JSON object carrying a human-phrased `toolAction`.
Polling that is the only way to see an agy run happening.

Unlike Claude and Codex, agy exposes no thinking text anywhere — not on stdout,
not in the trajectory. What this produces is therefore a feed of tool activity,
not a train of thought.

The table and the JSON keys are the same ones
`fluxion.workspace.antigravity_trajectory` reads after a run to derive changed
files, so nothing here deepens the coupling to agy's private schema: a format
change breaks change detection first, and louder.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from fluxion.workspace.antigravity_trajectory import extract_json_payloads

# Rows appear every couple of seconds; polling faster only burns wakeups.
POLL_INTERVAL_SEC = 0.5

_CLIP_LIMIT = 80

# Each tool surfaces twice — once when agy proposes it, once when the result
# lands — carrying byte-identical JSON both times. The pair is adjacent in
# practice, but parallel tool calls can interleave, so suppression looks back
# over a short window instead of at the previous line alone. A bounded window
# rather than a set is deliberate: re-reading the same file later in the run is
# real work and should stay visible.
_RECENT_WINDOW = 8

# Matches how the codex-backed path renders the same two kinds of step.
_SEPARATOR = "\n\n"


class TrajectoryNarrator:
    """Turns newly written trajectory steps into a stream of one-line notes.

    Stateful across polls: it holds the `steps.idx` high-water mark and the
    recently rendered lines, and hands back only what has not been sent yet.
    """

    def __init__(self, *, since_idx: int = -1) -> None:
        self._idx = since_idx
        self._recent: deque[str] = deque(maxlen=_RECENT_WINDOW)
        self._started = False

    @property
    def since_idx(self) -> int:
        return self._idx

    def poll(self, db_path: Path) -> str:
        """The delta to send, or "" when nothing new has landed."""
        rows = self._read_new_rows(db_path)
        lines: list[str] = []
        for idx_val, blob in rows:
            if isinstance(idx_val, int) and idx_val > self._idx:
                self._idx = idx_val
            if not isinstance(blob, bytes):
                continue
            for payload in extract_json_payloads(blob):
                line = describe_step(payload)
                if not line or line in self._recent:
                    continue
                self._recent.append(line)
                lines.append(line)
        if not lines:
            return ""
        delta = _SEPARATOR.join(lines)
        if self._started:
            delta = _SEPARATOR + delta
        self._started = True
        return delta

    def _read_new_rows(self, db_path: Path) -> list[tuple[Any, Any]]:
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2)
        except sqlite3.Error:
            # agy holds the DB open in WAL mode for the whole run, so a busy or
            # momentarily inconsistent read is ordinary. The rows are still
            # there next poll; a missed note is not worth failing a run over.
            return []
        try:
            return connection.execute(
                "SELECT idx, step_payload FROM steps WHERE idx > ? ORDER BY idx",
                (self._idx,),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()


def describe_step(payload: dict[str, Any]) -> str:
    """One line of working for a trajectory step, or "" when it is not one.

    agy phrases `toolAction` for display already ("Reading a.txt"), so this
    mostly picks the right field. Commands are rendered as their command line
    rather than the summary agy writes for them ("Run wc command"), because
    seeing what actually ran is the point.
    """
    if not isinstance(payload, dict):
        return ""
    command = str(payload.get("CommandLine") or "").strip().replace("\n", " ")
    if command:
        return f"$ {_clip(command)}"
    action = str(payload.get("toolAction") or "").strip().replace("\n", " ")
    return _clip(action) if action else ""


def read_max_step_idx(db_path: Path) -> int:
    """The last row already in a conversation's DB, or -1.

    A resumed conversation's DB opens holding every step of every prior turn.
    Taking this before the process starts keeps last turn's working out of this
    turn's window.
    """
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return -1
    try:
        row = connection.execute("SELECT MAX(idx) FROM steps").fetchone()
    except sqlite3.Error:
        return -1
    finally:
        connection.close()
    return row[0] if row and isinstance(row[0], int) else -1


def _clip(subject: str, limit: int = _CLIP_LIMIT) -> str:
    if len(subject) <= limit:
        return subject
    return subject[: limit - 3] + "..."
