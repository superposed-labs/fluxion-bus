"""Real-subprocess tests for the executor process-tree helpers.

These spawn actual processes on purpose: the bug they cover (a grandchild
inheriting the child's stdout write end, so the pipe never reaches EOF and the
reader join blocks forever) cannot be reproduced with mocked pipes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from fluxion.executors.common.process import (
    DescendantTracker,
    ProcessInfo,
    descendants_of,
    drain_reader_threads,
    list_processes,
    new_process_group,
    start_process,
    sweep_escaped_processes,
    terminate_process_tree,
)

pytestmark = pytest.mark.skipif(
    not new_process_group(), reason="process groups unavailable on this platform"
)


def _spawn(script: str) -> subprocess.Popen[str]:
    return start_process(
        ["/bin/sh", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _start_readers(proc: subprocess.Popen[str]) -> tuple[list[str], tuple[threading.Thread, ...]]:
    """Mirror the executors' reader-thread arrangement."""
    collected: list[str] = []

    def read(pipe) -> None:
        for chunk in iter(pipe.readline, ""):
            collected.append(chunk)

    threads = tuple(
        threading.Thread(target=read, args=(pipe,), daemon=True)
        for pipe in (proc.stdout, proc.stderr)
    )
    for thread in threads:
        thread.start()
    return collected, threads


def test_drain_returns_when_grandchild_holds_the_pipe() -> None:
    # The child exits immediately but leaves a background process holding the
    # inherited stdout write end — exactly what `agy` did with flutter/dart.
    proc = _spawn("(sleep 30) & echo hello; exit 0")
    collected, threads = _start_readers(proc)
    proc.wait(timeout=10)

    started = time.monotonic()
    drained = drain_reader_threads(threads, proc=proc, grace_sec=0.5, post_kill_sec=5.0)
    elapsed = time.monotonic() - started

    # Killing the process group closes the inherited write end, so the readers
    # do reach EOF — the call reports a clean drain rather than abandonment.
    assert drained is True
    assert elapsed < 5
    assert "".join(collected).strip() == "hello"
    for thread in threads:
        assert not thread.is_alive()


def test_drain_gives_up_when_the_holder_escapes_the_group() -> None:
    # A holder that puts itself in its own session survives the group kill.
    # The point is that we still return instead of blocking forever.
    escape = (
        f"{sys.executable} -c "
        '"import subprocess,sys;'
        f"subprocess.Popen([{sys.executable!r},'-c','import time;time.sleep(30)'],"
        "start_new_session=True);"
        "print('hi', flush=True)\""
    )
    proc = _spawn(escape)
    collected, threads = _start_readers(proc)
    proc.wait(timeout=30)

    started = time.monotonic()
    drained = drain_reader_threads(threads, proc=proc, grace_sec=0.5, post_kill_sec=1.0)
    elapsed = time.monotonic() - started

    assert drained is False
    assert elapsed < 10
    assert "hi" in "".join(collected)


def test_terminate_process_tree_kills_descendants() -> None:
    proc = _spawn("sleep 30 & echo $!; wait")
    assert proc.stdout is not None
    grandchild = int(proc.stdout.readline().strip())

    terminate_process_tree(proc)

    assert proc.poll() is not None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _alive(grandchild):
        time.sleep(0.05)
    assert not _alive(grandchild), "grandchild survived the process-group kill"


_ESCAPE_SCRIPT = (
    "import subprocess,sys,time;"
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
    "start_new_session=True);"
    "print(p.pid, flush=True);"
    "time.sleep(60)"
)


def test_terminate_kills_a_command_that_escaped_into_its_own_session() -> None:
    """The reported failure: `agy` runs terminal commands in a pty, so each one
    is a session leader from birth and the process-group kill never reaches it.
    Those commands (flutter test / flutter_tester) outlived every cancel."""
    proc = _spawn(f'{sys.executable} -c "{_ESCAPE_SCRIPT}"')
    assert proc.stdout is not None
    escapee = int(proc.stdout.readline().strip())
    assert _alive(escapee)

    report = terminate_process_tree(proc)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(escapee):
        time.sleep(0.05)
    assert not _alive(escapee), "escapee survived the sweep"
    assert report.verified is True
    assert report.remaining == []
    assert escapee in [row["pid"] for row in report.swept]


def test_sweep_is_a_no_op_without_anything_tracked() -> None:
    report = sweep_escaped_processes({})

    assert report.verified is True
    assert report.remaining == []
    assert report.swept == []


def test_sweep_leaves_a_recycled_pid_alone() -> None:
    """pid alone doesn't identify a process. A tracked entry whose start time no
    longer matches is a different process — signaling it would be a stray kill
    of whatever the OS handed the number to next."""
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        recycled = {
            live.pid: ProcessInfo(
                pid=live.pid,
                ppid=os.getpid(),
                pgid=os.getpgrp(),
                started_at="Wed Jan 1 00:00:00 2020",  # not this process's start time
                command="something that used to hold this pid",
            )
        }

        report = sweep_escaped_processes(recycled)

        assert report.swept == []
        assert report.remaining == []
        assert live.poll() is None, "an unrelated process holding a reused pid was killed"
    finally:
        live.kill()
        live.wait(timeout=10)


def test_sweep_of_a_process_in_our_own_group_spares_the_group() -> None:
    """A tracked process that never left our session must be signaled alone:
    killing its group would kill Fluxion itself."""
    same_group = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        table = list_processes()
        info = table[same_group.pid]
        assert info.pgid == os.getpgrp(), "expected the child to share our process group"

        report = sweep_escaped_processes({same_group.pid: info})

        assert same_group.wait(timeout=10) is not None
        assert report.verified is True
        assert [row["pid"] for row in report.swept] == [same_group.pid]
    finally:
        if same_group.poll() is None:  # pragma: no cover - only on failure
            same_group.kill()
            same_group.wait(timeout=10)


def test_tracker_remembers_descendants_after_their_parent_is_gone() -> None:
    proc = _spawn(f'{sys.executable} -c "{_ESCAPE_SCRIPT}"')
    assert proc.stdout is not None
    escapee = int(proc.stdout.readline().strip())
    tracker = getattr(proc, "fluxion_tracker", None)
    assert isinstance(tracker, DescendantTracker)
    tracker.sample()

    try:
        # Once the group dies the escapee is reparented to init, and no ancestry
        # walk can attribute it to this run any more — only the record can.
        terminate_process_tree(proc)

        assert escapee in tracker.tracked
        assert descendants_of(proc.pid, list_processes()) == {}
    finally:
        if _alive(escapee):  # pragma: no cover - only on failure
            os.kill(escapee, 9)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
