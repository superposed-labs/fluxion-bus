"""Tail-and-publish event stream for task lifecycle changes.

Architecture rationale: the Slack gateway and the web server run as
**separate processes** that share only the JSONL files under ``data/``.
An in-process pub/sub bus would not bridge them. Instead this module
tails ``tasks.jsonl`` from the web process — whenever the file grows,
new event lines are parsed and broadcast to every connected SSE client.

Trade-offs:
- Latency floor ≈ POLL_INTERVAL_SEC (default 500ms). Way better than
  the 5s frontend polling it replaces; not "real time" in the strict
  sense but indistinguishable to a human.
- Gateway needs zero changes — the file is the source of truth.
- Survives gateway restarts: on resume the tail just continues from
  the saved offset.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

POLL_INTERVAL_SEC = 0.5
# Cap how many events a slow client can backlog before we drop the
# oldest. 1024 events ≈ tens of seconds of normal traffic; if a client
# can't keep up beyond that it has probably stalled.
SUBSCRIBER_QUEUE_MAX = 1024


class TaskEventStream:
    """Background tail of `tasks.jsonl` with fan-out to async subscribers."""

    def __init__(self, data_dir: Path) -> None:
        self._tasks_path = data_dir / "tasks.jsonl"
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._offset = 0
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        """Begin tailing. Anchors offset at end-of-file so historical
        events don't replay to new subscribers — clients hydrate from
        `/api/tasks` first and only need events from "now"."""
        if self._tasks_path.exists():
            self._offset = self._tasks_path.stat().st_size
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="fluxion-event-stream")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._poll_once()
            except Exception:
                # Tail loop must never die — log-and-continue would be
                # the spot once we have a logger.
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=POLL_INTERVAL_SEC)
            except TimeoutError:
                continue

    async def _poll_once(self) -> None:
        if not self._tasks_path.exists():
            return
        size = self._tasks_path.stat().st_size
        if size < self._offset:
            # File was truncated/rotated — reset and re-anchor.
            self._offset = size
            return
        if size == self._offset:
            return

        await asyncio.to_thread(self._drain_new_lines)

    def _drain_new_lines(self) -> None:
        with self._tasks_path.open("r", encoding="utf-8") as f:
            f.seek(self._offset)
            for raw in f:
                # Don't advance offset past a partially-written line; the
                # next poll will pick it up complete.
                if not raw.endswith("\n"):
                    break
                self._offset += len(raw.encode("utf-8"))
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                self._broadcast(event)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop the oldest and try again.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
