"""Read-only view of the cross-process workspace write locks.

The locks themselves are flocks held by a running process (see
``GatewayCore._acquire_workspace_file_lock``); the OS releases them when a
holder dies, so there is nothing to clean up by hand. What was missing is the
ability to see who holds one — which is why each holder stamps its identity into
the lock file.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from fluxion.core.runtime_registry import pid_alive

LOCK_DIR = Path(tempfile.gettempdir()) / "fluxion-locks"


def list_workspace_locks(lock_dir: Path | None = None) -> dict[str, Any]:
    """Workspace locks that are actually held right now.

    One file exists per workspace ever locked and they outlive their runs, so
    listing all of them would bury the two or three that matter. Only held locks
    are returned; the rest are counted.
    """
    directory = lock_dir or LOCK_DIR
    if not directory.is_dir():
        return {"held": [], "released_lock_files": 0}
    held: list[dict[str, Any]] = []
    released = 0
    for path in sorted(directory.glob("*.lock")):
        if not _is_held(path):
            released += 1
            continue
        holder = _read_holder(path)
        pid = (holder or {}).get("pid")
        held.append(
            {
                "lock_file": str(path),
                "workspace": (holder or {}).get("workspace"),
                "holder": holder,
                "holder_alive": pid_alive(pid) if isinstance(pid, int) else None,
            }
        )
    return {"held": held, "released_lock_files": released}


def prune_released_locks(lock_dir: Path | None = None) -> int:
    """Delete lock files nobody holds. Returns how many were removed.

    Taking the lock non-blocking first is what makes this safe: a file we can
    lock is a file no run depends on, and we keep holding it across the unlink.
    """
    directory = lock_dir or LOCK_DIR
    if fcntl is None or not directory.is_dir():
        return 0
    removed = 0
    for path in sorted(directory.glob("*.lock")):
        try:
            handle = open(path, "a+", encoding="utf-8")
        except OSError:
            continue
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
    return removed


def _is_held(path: Path) -> bool:
    """Whether some process currently holds this lock."""
    if fcntl is None:
        return False
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        # We got it, so nobody else had it. Release without touching the stamp.
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        handle.close()


def _read_holder(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
