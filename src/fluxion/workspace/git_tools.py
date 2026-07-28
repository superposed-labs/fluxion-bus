from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_SEC = 30


def get_git_diff_summary(workspace: Path, *, max_chars: int = 1200) -> str:
    if not (workspace / ".git").exists():
        return "No git repository in workspace."

    # Timeouts on purpose: this runs on the path that finalizes a task, so a
    # git call that hangs (index.lock held by another process, a stalled
    # filesystem) would keep the run non-terminal and its workspace locked.
    status = _run_git(workspace, "status", "--porcelain")
    diffstat = _run_git(workspace, "diff", "--stat")
    if status is None and diffstat is None:
        return "Git summary unavailable (git did not respond in time)."
    parts: list[str] = []
    if status is not None and status.returncode == 0 and status.stdout.strip():
        status_lines = status.stdout.strip().splitlines()[:25]
        parts.append("Changed files:\n" + "\n".join(status_lines))
    if diffstat is not None and diffstat.returncode == 0 and diffstat.stdout.strip():
        parts.append("Diff stat:\n" + diffstat.stdout.strip())
    if not parts:
        return "No git diff."
    output = "\n\n".join(parts)
    if len(output) > max_chars:
        return output[: max_chars - 16] + "\n...(truncated)"
    return output


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run one read-only git command, or None if it timed out or wouldn't start."""
    try:
        return subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
