"""Process-tree lifecycle helpers shared by the CLI executors.

Every executor launches an agent CLI and reads its stdout/stderr from reader
threads. Two things about that arrangement are unsafe unless handled here:

1. The CLI spawns its own children (language servers, `flutter run`, simulator
   tooling). Terminating only the direct child leaves those alive.
2. Those grandchildren inherit the child's stdout/stderr *write* ends. If one
   outlives the child, the pipe never reaches EOF, so an unbounded
   ``reader.join()`` blocks forever — wedging the worker thread, the task's
   status and the workspace lock for good.

Launching each CLI in its own process group makes both tractable: the group is
the unit we terminate, and killing it closes every inherited write end.

The group is not the whole story. An agent CLI that runs its terminal commands
in a pty — Antigravity does — starts each one with ``setsid``, because that is
how a process acquires a controlling terminal. Those commands are therefore in
their *own* session from birth, and a group kill never reaches them: `flutter
test` and its `flutter_tester` children survived every cancel and were left
reparented to launchd. Ancestry can't find them afterwards either, since the
parent that linked them to us is exactly what the kill removed.

So descendants are also tracked *while the run is alive* (DescendantTracker),
and cleanup sweeps that recorded set after the group kill. Each entry is
identified by pid plus process start time, so a recycled pid is never signaled.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# How long the readers get to drain on their own once the child has exited.
# Normal drains finish in milliseconds; this only has to cover a slow flush.
READER_DRAIN_GRACE_SEC = 10.0
# After killing the process group, how long to wait for the now-closed pipes to
# push the readers out of readline().
READER_DRAIN_POST_KILL_SEC = 5.0

_TERM_WAIT_SEC = 3.0
_KILL_WAIT_SEC = 2.0

# How often the descendant tracker re-reads the process table. Each sample is
# one `ps` call for the whole run, so this is cheap; it only has to be short
# enough that a command started shortly before a cancel is already recorded.
_TRACK_INTERVAL_SEC = 2.0
# Grace for a swept escapee between SIGTERM and SIGKILL.
_SWEEP_TERM_WAIT_SEC = 2.0


@dataclass(frozen=True)
class ProcessInfo:
    """One row of the process table.

    ``started_at`` is the process's start time as reported by ``ps``; together
    with the pid it identifies a process across time, which matters because the
    pid alone is reused.
    """

    pid: int
    ppid: int
    pgid: int
    started_at: str
    command: str


@dataclass
class CleanupReport:
    """What termination left behind.

    ``verified`` is the claim the caller cares about: nothing this run started
    is still running. It is False when a process survived SIGKILL or when the
    platform gave us no way to look.
    """

    verified: bool = True
    remaining: list[dict[str, Any]] = field(default_factory=list)
    swept: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "remaining": self.remaining,
            "swept": self.swept,
        }


def list_processes() -> dict[int, ProcessInfo]:
    """The current process table, keyed by pid. Empty if ps is unavailable."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("process table read failed", exc_info=True)
        return {}
    table: dict[int, ProcessInfo] = {}
    for line in completed.stdout.splitlines():
        # lstart is five whitespace-separated fields ("Wed Jul 8 21:43:47 2026")
        # and the command is everything after them, spaces included.
        parts = line.split(None, 9)
        if len(parts) < 10:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if parts[3].startswith("Z"):
            # A zombie is an exit status its parent hasn't collected yet, not a
            # running process. Counting one as a survivor would report a cleanup
            # as incomplete for as long as the reaping took.
            continue
        table[pid] = ProcessInfo(
            pid=pid,
            ppid=ppid,
            pgid=pgid,
            started_at=" ".join(parts[4:9]),
            command=parts[9],
        )
    return table


def descendants_of(root_pid: int, table: Mapping[int, ProcessInfo]) -> dict[int, ProcessInfo]:
    """Every transitive child of ``root_pid`` in ``table`` (root excluded)."""
    children: dict[int, list[ProcessInfo]] = {}
    for info in table.values():
        children.setdefault(info.ppid, []).append(info)
    found: dict[int, ProcessInfo] = {}
    queue = list(children.get(root_pid, ()))
    while queue:
        info = queue.pop()
        if info.pid in found or info.pid == root_pid:
            continue
        found[info.pid] = info
        queue.extend(children.get(info.pid, ()))
    return found


class DescendantTracker:
    """Records the run's descendants while their ancestry is still readable.

    Sampling has to happen during the run: a command that escaped into its own
    session is orphaned the moment the CLI dies, and from then on nothing
    connects it back to the run. The tracker keeps the union of everything it
    ever saw, and stops on its own once the root process is gone so a caller
    that never terminates explicitly doesn't leak the thread.
    """

    def __init__(self, root_pid: int, *, interval_sec: float = _TRACK_INTERVAL_SEC) -> None:
        self._root_pid = root_pid
        self._interval_sec = interval_sec
        self._lock = threading.Lock()
        self._tracked: dict[int, ProcessInfo] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def tracked(self) -> dict[int, ProcessInfo]:
        with self._lock:
            return dict(self._tracked)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop,
            name=f"fluxion-proctrack-{self._root_pid}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def sample(self) -> None:
        """Read the table once, now. Called right before a kill so a command
        started since the last interval is still caught."""
        table = list_processes()
        found = descendants_of(self._root_pid, table)
        with self._lock:
            self._tracked.update(found)

    def _loop(self) -> None:
        while not self._stop.is_set():
            table = list_processes()
            found = descendants_of(self._root_pid, table)
            with self._lock:
                self._tracked.update(found)
            if table and self._root_pid not in table:
                # The CLI is gone and reaped; anything it left is already in
                # _tracked, and further sampling would find nothing new.
                return
            self._stop.wait(self._interval_sec)


def sweep_escaped_processes(tracked: Mapping[int, ProcessInfo]) -> CleanupReport:
    """Kill tracked processes that outlived the group kill.

    Escapees lead their own session, so each one is signaled as a group — that
    takes its own children (``flutter test`` -> ``flutter_tester``) with it in
    one step.
    """
    report = CleanupReport()
    if not tracked or not new_process_group():
        return report
    escaped = _live_survivors(tracked)
    if not escaped:
        return report
    survivors = escaped
    for info in survivors.values():
        _signal_escapee(info, signal.SIGTERM)
    deadline = time.monotonic() + _SWEEP_TERM_WAIT_SEC
    while time.monotonic() < deadline:
        survivors = _live_survivors(survivors)
        if not survivors:
            break
        time.sleep(0.1)
    for info in survivors.values():
        _signal_escapee(info, signal.SIGKILL)
    time.sleep(0.1)
    remaining = _live_survivors(survivors)
    report.swept = [
        {"pid": info.pid, "command": info.command}
        for pid, info in escaped.items()
        if pid not in remaining
    ]
    report.remaining = [{"pid": info.pid, "command": info.command} for info in remaining.values()]
    report.verified = not report.remaining
    if report.remaining:
        logger.warning(
            "process cleanup incomplete: %s survived SIGKILL",
            [row["pid"] for row in report.remaining],
        )
    return report


def _live_survivors(candidates: Mapping[int, ProcessInfo]) -> dict[int, ProcessInfo]:
    """Candidates still running as the same process (not a recycled pid)."""
    table = list_processes()
    live: dict[int, ProcessInfo] = {}
    for pid, info in candidates.items():
        current = table.get(pid)
        if current is None or current.started_at != info.started_at:
            continue
        live[pid] = current
    return live


def _signal_escapee(info: ProcessInfo, sig: int) -> None:
    if info.pid <= 1 or info.pid == os.getpid():
        return
    try:
        own_group = os.getpgrp()
    except OSError:  # pragma: no cover - defensive
        own_group = -1
    try:
        if info.pgid > 1 and info.pgid != own_group:
            os.killpg(info.pgid, sig)
        else:
            # Sharing our group means it never escaped the session, so signal
            # the process alone — killing that group would take Fluxion down.
            os.kill(info.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return


def new_process_group() -> bool:
    """Whether children can be launched into their own process group.

    POSIX only; on Windows the child stays in the caller's group and
    termination falls back to the direct child.
    """
    return hasattr(os, "killpg") and hasattr(os, "getpgid")


def start_process(command: Sequence[str], **kwargs: object) -> subprocess.Popen[str]:
    """Launch an agent CLI in its own process group.

    Use this instead of ``subprocess.Popen`` so the group id is captured at
    spawn time: ``os.getpgid(pid)`` stops working once the child is reaped,
    which is exactly when descendants that outlived it still need sweeping.
    """
    kwargs.setdefault("start_new_session", new_process_group())
    proc = subprocess.Popen(command, **kwargs)  # type: ignore[call-overload]
    pid = getattr(proc, "pid", None)
    if kwargs.get("start_new_session") and isinstance(pid, int):
        # start_new_session makes the child a session and group leader, so its
        # group id is its pid — no getpgid call that could race the exit.
        proc.fluxion_pgid = pid  # type: ignore[attr-defined]
        tracker = DescendantTracker(pid)
        tracker.start()
        proc.fluxion_tracker = tracker  # type: ignore[attr-defined]
    return proc


def terminate_process_tree(proc: subprocess.Popen[str]) -> CleanupReport:
    """Stop the child, its process group, and anything that escaped the group.

    Escalates SIGTERM -> SIGKILL. Falls back to terminating just the direct
    child when the process group can't be resolved (already reaped, or a
    platform without process groups). Returns what the cleanup left behind.
    """
    tracker = getattr(proc, "fluxion_tracker", None)
    if tracker is not None:
        # Last look while the tree is intact: a command started since the last
        # interval is still attached to the child until the kill below.
        tracker.sample()
    _terminate_group(proc)
    if tracker is None:
        return CleanupReport()
    tracker.stop()
    return sweep_escaped_processes(tracker.tracked)


def _terminate_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        # The child is gone, but descendants it left behind may still be in its
        # group holding the inherited pipes open. Killing a group needs a pgid,
        # and after reaping we can no longer resolve one from the pid, so this
        # is best-effort — _signal_group is a no-op if the group is gone.
        _signal_group(proc, signal.SIGKILL)
        return
    if _signal_group(proc, signal.SIGTERM):
        try:
            proc.wait(timeout=_TERM_WAIT_SEC)
        except Exception:
            pass
    else:
        try:
            proc.terminate()
            proc.wait(timeout=_TERM_WAIT_SEC)
        except Exception:
            pass
    if proc.poll() is not None:
        # The child is down; sweep any descendants that ignored SIGTERM.
        _signal_group(proc, signal.SIGKILL)
        return
    if not _signal_group(proc, signal.SIGKILL):
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=_KILL_WAIT_SEC)
    except Exception:
        pass


def drain_reader_threads(
    threads: Sequence[threading.Thread],
    *,
    proc: subprocess.Popen[str],
    grace_sec: float = READER_DRAIN_GRACE_SEC,
    post_kill_sec: float = READER_DRAIN_POST_KILL_SEC,
) -> bool:
    """Join the stdout/stderr readers without ever blocking forever.

    Returns True when both readers finished (the pipes reached EOF), False when
    they had to be abandoned. A False return means some descendant is still
    holding the write end after a group kill; the collected output is whatever
    arrived by then, and the daemon threads die with the process.
    """
    if _join_all(threads, grace_sec):
        stop_tracking(proc)
        return True
    # Still blocked in readline() well after the child exited: something in its
    # process group inherited the write end. Killing the group closes it.
    logger.warning(
        "pipe readers still blocked %.0fs after child exit; killing process group",
        grace_sec,
    )
    terminate_process_tree(proc)
    if _join_all(threads, post_kill_sec):
        return True
    logger.warning("abandoning pipe readers: write end still held after group kill")
    return False


def stop_tracking(proc: subprocess.Popen[str]) -> None:
    """Stop sampling descendants for a run that ended on its own.

    The tracker also stops by itself once the child leaves the process table;
    this just makes the common path immediate instead of one interval late.
    """
    tracker = getattr(proc, "fluxion_tracker", None)
    if tracker is not None:
        tracker.stop()


def _join_all(threads: Sequence[threading.Thread], timeout: float) -> bool:
    """Join every thread within one shared deadline, not one each."""
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            return False
    return True


def _signal_group(proc: subprocess.Popen[str], sig: int) -> bool:
    """Signal the child's whole process group. False if that wasn't possible."""
    if not new_process_group():
        return False
    pgid = getattr(proc, "fluxion_pgid", None)
    if pgid is None:
        # Not launched through start_process (or no new session): the group is
        # only resolvable while the child is still unreaped.
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError, AttributeError, TypeError):
            return False
    # Refuse to signal our own group: that would take down Fluxion itself if a
    # child was ever launched without start_new_session.
    try:
        if pgid == os.getpgrp():
            return False
    except OSError:
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True
