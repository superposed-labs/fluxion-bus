from __future__ import annotations

import re
from pathlib import Path

from fluxion.workspace.snapshot import IGNORED_WORKSPACE_DIRS, WorkspaceDelta

PRIORITY_ARTIFACT_EXTS = {
    ".csv",
    ".xlsx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".json",
    ".txt",
    ".log",
    ".zip",
    ".html",
}

SOURCE_CODE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
}

SENSITIVE_NAME_PATTERNS = (
    re.compile(r"(^|/)\.env(\.|$)", re.IGNORECASE),
    re.compile(r"(^|/).*id_rsa.*$", re.IGNORECASE),
    re.compile(r"(^|/).*\.(pem|p12|key)$", re.IGNORECASE),
    re.compile(r"(^|/)secrets?(\.|_|$)", re.IGNORECASE),
)

TEMP_BASENAME_PREFIXES = ("~$",)
HASH_ARTIFACT_PATTERNS = (
    re.compile(r"^[0-9a-f]{32,}\.(json|txt|log)$", re.IGNORECASE),
    re.compile(r"^task-[0-9a-f-]+\.log$", re.IGNORECASE),
)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def collect_artifacts(
    *,
    workspace: Path,
    delta: WorkspaceDelta,
    max_files: int,
) -> list[str]:
    selected: list[str] = []
    for rel in delta.changed:
        path = workspace / rel
        if not path.exists() or not path.is_file():
            continue
        if _is_ignored_artifact_path(path=path, workspace=workspace):
            continue
        ext = path.suffix.lower()
        if ext in SOURCE_CODE_EXTS:
            continue
        if ext in PRIORITY_ARTIFACT_EXTS:
            selected.append(str(path.resolve()))
        if len(selected) >= max_files:
            break
    return selected


def select_uploadable_paths(
    *,
    workspace: Path,
    raw_paths: list[str],
    max_files: int,
) -> list[str]:
    selected: list[str] = []
    for raw in raw_paths:
        value = (raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (workspace / path).resolve()
        else:
            path = path.resolve()
        _add_if_uploadable(path=path, workspace=workspace, selected=selected)
        if len(selected) >= max_files:
            break
    return selected


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _add_if_uploadable(*, path: Path, workspace: Path, selected: list[str]) -> None:
    if not path.exists() or not path.is_file():
        return
    if not _is_within(path, workspace):
        return
    ext = path.suffix.lower()
    if ext not in PRIORITY_ARTIFACT_EXTS:
        return
    if ext in SOURCE_CODE_EXTS:
        return
    if any(path.name.startswith(prefix) for prefix in TEMP_BASENAME_PREFIXES):
        return
    if _is_ignored_artifact_path(path=path, workspace=workspace):
        return
    normalized = str(path.resolve())
    lowered = normalized.lower()
    if any(p.search(lowered) for p in SENSITIVE_NAME_PATTERNS):
        return
    try:
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            return
    except OSError:
        return
    if normalized not in selected:
        selected.append(normalized)


def _is_ignored_artifact_path(*, path: Path, workspace: Path) -> bool:
    try:
        rel = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return True
    if any(part in IGNORED_WORKSPACE_DIRS for part in rel.parts):
        return True
    if rel.parts[:2] == ("data", "logs"):
        return True
    return any(pattern.match(path.name) for pattern in HASH_ARTIFACT_PATTERNS)
