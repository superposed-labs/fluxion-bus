from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".angular",
    ".cache",
    "dist",
    "build",
    "coverage",
    "tmp",
    "temp",
    "scratch",
    "data",
    "cache",
    ".fluxion_inbox",
    ".claude",
    ".gemini",
    ".fluxion",
}


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class WorkspaceDelta:
    added: list[str]
    modified: list[str]
    deleted: list[str]

    @property
    def changed(self) -> list[str]:
        return self.added + self.modified


def take_snapshot(root: Path) -> dict[str, FileFingerprint]:
    snapshot: dict[str, FileFingerprint] = {}
    if not root.exists():
        return snapshot
    # Prune ignored directories while walking instead of after the fact: a plain
    # rglob descends into (and stats every file under) .git/node_modules/.venv and
    # only then discards them, which on a large workspace means walking 100k+ files
    # to keep a few thousand. Removing them from `dirnames` skips those subtrees
    # entirely. The per-file name check preserves the previous behavior of also
    # excluding a file whose own name matches an ignored token.
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_WORKSPACE_DIRS]
        for name in filenames:
            if name in IGNORED_WORKSPACE_DIRS:
                continue
            if name.startswith(".DS_Store"):
                continue
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                # Skip directories, broken symlinks, and anything unreadable —
                # os.stat follows symlinks, matching the old path.is_file() check.
                continue
            rel_path = Path(full).relative_to(root).as_posix()
            snapshot[rel_path] = FileFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return snapshot


def diff_snapshot(
    before: dict[str, FileFingerprint], after: dict[str, FileFingerprint]
) -> WorkspaceDelta:
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    added = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified = sorted(rel for rel in (before_keys & after_keys) if before[rel] != after[rel])
    return WorkspaceDelta(added=added, modified=modified, deleted=deleted)
