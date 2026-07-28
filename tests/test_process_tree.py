"""Real-subprocess tests for the executor process-tree helpers.

These spawn actual processes on purpose: the bug they cover (a grandchild
inheriting the child's stdout write end, so the pipe never reaches EOF and the
reader join blocks forever) cannot be reproduced with mocked pipes.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from fluxion.executors.common.process import (
    drain_reader_threads,
    new_process_group,
    start_process,
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


def _alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
