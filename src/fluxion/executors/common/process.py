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
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# How long the readers get to drain on their own once the child has exited.
# Normal drains finish in milliseconds; this only has to cover a slow flush.
READER_DRAIN_GRACE_SEC = 10.0
# After killing the process group, how long to wait for the now-closed pipes to
# push the readers out of readline().
READER_DRAIN_POST_KILL_SEC = 5.0

_TERM_WAIT_SEC = 3.0
_KILL_WAIT_SEC = 2.0


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
    return proc


def terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Stop the child and every descendant still in its process group.

    Escalates SIGTERM -> SIGKILL. Falls back to terminating just the direct
    child when the process group can't be resolved (already reaped, or a
    platform without process groups).
    """
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
