from __future__ import annotations

import subprocess
from pathlib import Path


def get_git_diff_summary(workspace: Path, *, max_chars: int = 1200) -> str:
    if not (workspace / ".git").exists():
        return "No git repository in workspace."

    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    diffstat = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--stat"],
        capture_output=True,
        text=True,
        check=False,
    )
    parts: list[str] = []
    if status.returncode == 0 and status.stdout.strip():
        status_lines = status.stdout.strip().splitlines()[:25]
        parts.append("Changed files:\n" + "\n".join(status_lines))
    if diffstat.returncode == 0 and diffstat.stdout.strip():
        parts.append("Diff stat:\n" + diffstat.stdout.strip())
    if not parts:
        return "No git diff."
    output = "\n\n".join(parts)
    if len(output) > max_chars:
        return output[: max_chars - 16] + "\n...(truncated)"
    return output
