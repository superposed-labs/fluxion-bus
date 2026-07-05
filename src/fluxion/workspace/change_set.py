from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.workspace.snapshot import IGNORED_WORKSPACE_DIRS

DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_TOTAL_BYTES = 20_000_000


@dataclass(frozen=True)
class ContentSnapshotEntry:
    path: str
    exists: bool
    size: int
    mtime_ns: int
    sha256: str
    text: bool
    content: str | None = None
    skipped_reason: str = ""


@dataclass(frozen=True)
class ContentSnapshot:
    workspace: str
    created_at: str
    files: dict[str, ContentSnapshotEntry]
    captured_bytes: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class FileChange:
    path: str
    change_type: str
    old_sha256: str = ""
    new_sha256: str = ""
    old_size: int = 0
    new_size: int = 0
    old_content: str | None = None
    new_content: str | None = None
    recoverable: bool = True
    skipped_reason: str = ""
    # Populated only for change_type == "edited" (stream-derived fragment edits,
    # where the whole-file before-content was never captured). Each entry is
    # {"old": str, "new": str, "replace_all": bool}; reverting reverse-applies
    # them (new -> old) in reverse order against the current file.
    edits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ChangeSet:
    run_id: str
    workspace: str
    status: str
    created_at: str
    changed_files: list[str]
    recoverable_files: list[str]
    unrecoverable_files: list[str]
    files: list[FileChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.files)


@dataclass(frozen=True)
class RevertConflict:
    path: str
    reason: str
    expected_sha256: str = ""
    actual_sha256: str = ""


@dataclass(frozen=True)
class RevertResult:
    success: bool
    run_id: str
    workspace: str
    reverted_files: list[str]
    conflicts: list[RevertConflict]
    summary: str


def take_content_snapshot(
    root: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> ContentSnapshot:
    root = root.resolve()
    files: dict[str, ContentSnapshotEntry] = {}
    captured_bytes = 0
    truncated = False
    if not root.exists():
        return ContentSnapshot(
            workspace=str(root),
            created_at=_now(),
            files={},
            captured_bytes=0,
            truncated=False,
        )
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories before descending (same fix as take_snapshot):
        # a plain rglob descends into .git/node_modules/.venv and stats/reads every
        # file there only to discard it. Removing them from dirnames skips those
        # subtrees entirely. The per-file name check preserves the prior behavior of
        # also excluding a file whose own name matches an ignored token.
        dirnames[:] = [d for d in dirnames if d not in IGNORED_WORKSPACE_DIRS]
        for name in filenames:
            if name in IGNORED_WORKSPACE_DIRS:
                continue
            if name.startswith(".DS_Store"):
                continue
            candidates.append(Path(dirpath) / name)
    # Preserve the previous sorted-path iteration order: which files get their
    # content captured (vs truncated) under max_total_bytes depends on it.
    for path in sorted(candidates):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        rel_path = rel.as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        content: str | None = None
        skipped_reason = ""
        is_text = False
        try:
            digest_hex, head, raw_for_content = _read_file_for_snapshot(
                path,
                capture_content=(
                    stat.st_size <= max_file_bytes
                    and captured_bytes + stat.st_size <= max_total_bytes
                ),
            )
        except OSError:
            continue
        if b"\x00" in head:
            skipped_reason = "binary"
        elif stat.st_size > max_file_bytes:
            skipped_reason = f"larger than max_file_bytes={max_file_bytes}"
        elif captured_bytes + stat.st_size > max_total_bytes:
            skipped_reason = f"snapshot exceeds max_total_bytes={max_total_bytes}"
            truncated = True
        elif raw_for_content is not None:
            try:
                content = raw_for_content.decode("utf-8")
                is_text = True
                captured_bytes += stat.st_size
            except UnicodeDecodeError:
                skipped_reason = "non-utf8"
        files[rel_path] = ContentSnapshotEntry(
            path=rel_path,
            exists=True,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=digest_hex,
            text=is_text,
            content=content,
            skipped_reason=skipped_reason,
        )
    return ContentSnapshot(
        workspace=str(root),
        created_at=_now(),
        files=files,
        captured_bytes=captured_bytes,
        truncated=truncated,
    )


def build_change_set(
    *,
    run_id: str,
    workspace: Path,
    status: str,
    before: ContentSnapshot,
    after: ContentSnapshot,
) -> ChangeSet:
    before_keys = set(before.files)
    after_keys = set(after.files)
    changed_paths = sorted(
        (after_keys - before_keys)
        | (before_keys - after_keys)
        | {
            path
            for path in before_keys & after_keys
            if before.files[path].sha256 != after.files[path].sha256
        }
    )
    changes: list[FileChange] = []
    for path in changed_paths:
        old = before.files.get(path)
        new = after.files.get(path)
        if old is None and new is not None:
            changes.append(_added_change(path, new))
        elif old is not None and new is None:
            changes.append(_deleted_change(path, old))
        elif old is not None and new is not None:
            changes.append(_modified_change(path, old, new))
    recoverable = [change.path for change in changes if change.recoverable]
    unrecoverable = [change.path for change in changes if not change.recoverable]
    return ChangeSet(
        run_id=run_id,
        workspace=str(workspace.resolve()),
        status=status,
        created_at=_now(),
        changed_files=changed_paths,
        recoverable_files=recoverable,
        unrecoverable_files=unrecoverable,
        files=changes,
    )


def build_stream_change_set(
    *,
    run_id: str,
    workspace: Path,
    status: str,
    operations: list[dict[str, Any]],
) -> ChangeSet:
    """Build a best-effort ChangeSet from executor stream events, without a
    workspace snapshot.

    The executor stream tells us which files were touched and how (a file was
    created, overwritten, edited via fragments, or deleted), but never the
    whole-file *before* content. So only operations whose inverse needs no prior
    content are recoverable:

    * create / add  -> revert by deleting the freshly created file
    * edit          -> revert by reverse-applying the recorded fragments

    Overwrites, in-place updates and deletions discard the prior bytes, so they
    are recorded as unrecoverable with a hint to use FLUXION_REVERT_CAPTURE=full.
    Reads here are targeted single-file stats of the touched paths only (never a
    workspace walk), keeping this cheap enough to run by default.
    """
    workspace = workspace.resolve()
    order: list[str] = []
    state: dict[str, dict[str, Any]] = {}
    for op in operations:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip().lower()
        rel = _normalize_change_path(op.get("path"), workspace)
        if not rel or not kind:
            continue
        if rel not in state:
            state[rel] = {"created": False, "overwritten": False, "deleted": False, "edits": []}
            order.append(rel)
        entry = state[rel]
        if kind in {"create", "add"}:
            entry["created"] = True
            entry["deleted"] = False
        elif kind in {"overwrite", "update"}:
            entry["overwritten"] = True
        elif kind == "delete":
            entry["deleted"] = True
        elif kind == "edit":
            entry["edits"].extend(e for e in (op.get("edits") or []) if isinstance(e, dict))

    changes: list[FileChange] = []
    for rel in order:
        change = _stream_file_change(rel, state[rel], workspace)
        if change is not None:
            changes.append(change)

    changed_paths = [change.path for change in changes]
    recoverable = [change.path for change in changes if change.recoverable]
    unrecoverable = [change.path for change in changes if not change.recoverable]
    return ChangeSet(
        run_id=run_id,
        workspace=str(workspace),
        status=status,
        created_at=_now(),
        changed_files=changed_paths,
        recoverable_files=recoverable,
        unrecoverable_files=unrecoverable,
        files=changes,
    )


_FULL_CAPTURE_HINT = "prior content not captured; set FLUXION_REVERT_CAPTURE=full to enable revert"


def _stream_file_change(rel: str, entry: dict[str, Any], workspace: Path) -> FileChange | None:
    created = bool(entry["created"])
    overwritten = bool(entry["overwritten"])
    deleted = bool(entry["deleted"])
    edits = [e for e in entry["edits"] if _is_valid_edit(e)]

    if deleted and not created:
        # Pre-existing file removed; the bytes are gone from the stream.
        return FileChange(
            path=rel,
            change_type="deleted",
            recoverable=False,
            skipped_reason=f"file deleted; {_FULL_CAPTURE_HINT}",
        )
    if deleted and created:
        # Created and then removed within the same run: net no-op, nothing to revert.
        return None

    path = (workspace / rel).resolve()
    after = _read_after(path) if _is_within(path, workspace) else None

    if created:
        # Reverting a freshly created file just deletes it — no prior content needed.
        if after is None:
            return FileChange(
                path=rel,
                change_type="added",
                recoverable=False,
                skipped_reason="created file is no longer readable; cannot verify before revert",
            )
        sha, content, size = after
        return FileChange(
            path=rel,
            change_type="added",
            new_sha256=sha,
            new_size=size,
            new_content=content,
            recoverable=True,
        )
    if overwritten:
        return FileChange(
            path=rel,
            change_type="modified",
            recoverable=False,
            skipped_reason=f"file overwritten; {_FULL_CAPTURE_HINT}",
        )
    if edits:
        if after is None:
            return FileChange(
                path=rel,
                change_type="edited",
                edits=edits,
                recoverable=False,
                skipped_reason="edited file is no longer readable; cannot verify before revert",
            )
        sha, _content, size = after
        return FileChange(
            path=rel,
            change_type="edited",
            new_sha256=sha,
            new_size=size,
            edits=edits,
            recoverable=True,
        )
    return None


def _is_valid_edit(edit: Any) -> bool:
    return (
        isinstance(edit, dict)
        and isinstance(edit.get("old"), str)
        and isinstance(edit.get("new"), str)
    )


def _read_after(path: Path) -> tuple[str, str | None, int] | None:
    """Targeted single-file read of one touched path (post-run state)."""
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    try:
        content: str | None = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = None
    return sha, content, len(raw)


def save_change_set(data_dir: Path, change_set: ChangeSet) -> Path:
    directory = data_dir / "change_sets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{change_set.run_id}.json"
    path.write_text(
        json.dumps(asdict(change_set), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_change_set(data_dir: Path, run_id: str) -> ChangeSet | None:
    path = data_dir / "change_sets" / f"{run_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return change_set_from_dict(data)


def change_set_from_dict(data: dict[str, Any]) -> ChangeSet:
    files = [
        FileChange(
            path=str(item.get("path", "")),
            change_type=str(item.get("change_type", "")),
            old_sha256=str(item.get("old_sha256", "")),
            new_sha256=str(item.get("new_sha256", "")),
            old_size=int(item.get("old_size", 0) or 0),
            new_size=int(item.get("new_size", 0) or 0),
            old_content=item.get("old_content"),
            new_content=item.get("new_content"),
            recoverable=bool(item.get("recoverable", True)),
            skipped_reason=str(item.get("skipped_reason", "")),
            edits=[e for e in item.get("edits", []) if isinstance(e, dict)],
        )
        for item in data.get("files", [])
        if isinstance(item, dict)
    ]
    return ChangeSet(
        run_id=str(data.get("run_id", "")),
        workspace=str(data.get("workspace", "")),
        status=str(data.get("status", "")),
        created_at=str(data.get("created_at", "")),
        changed_files=[str(item) for item in data.get("changed_files", [])],
        recoverable_files=[str(item) for item in data.get("recoverable_files", [])],
        unrecoverable_files=[str(item) for item in data.get("unrecoverable_files", [])],
        files=files,
    )


def revert_change_set(data_dir: Path, run_id: str) -> RevertResult:
    change_set = load_change_set(data_dir, run_id)
    if change_set is None:
        return RevertResult(
            success=False,
            run_id=run_id,
            workspace="",
            reverted_files=[],
            conflicts=[RevertConflict(path="", reason=f"ChangeSet not found for run_id={run_id}")],
            summary="ChangeSet not found.",
        )
    workspace = Path(change_set.workspace).resolve()
    conflicts = _revert_conflicts(change_set, workspace)
    if conflicts:
        return RevertResult(
            success=False,
            run_id=run_id,
            workspace=str(workspace),
            reverted_files=[],
            conflicts=conflicts,
            summary="Revert blocked by conflicts.",
        )
    reverted: list[str] = []
    for change in reversed(change_set.files):
        path = (workspace / change.path).resolve()
        if not _is_within(path, workspace):
            conflicts.append(RevertConflict(path=change.path, reason="path escapes workspace"))
            continue
        if change.change_type == "added":
            if path.exists():
                path.unlink()
            reverted.append(change.path)
        elif change.change_type == "deleted":
            if change.old_content is None:
                conflicts.append(RevertConflict(path=change.path, reason="missing old content"))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(change.old_content, encoding="utf-8")
            reverted.append(change.path)
        elif change.change_type == "modified":
            if change.old_content is None:
                conflicts.append(RevertConflict(path=change.path, reason="missing old content"))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(change.old_content, encoding="utf-8")
            reverted.append(change.path)
        elif change.change_type == "edited":
            text = path.read_text(encoding="utf-8")
            reverted_text, ok = _reverse_apply_edits(text, change.edits)
            if not ok:
                conflicts.append(
                    RevertConflict(path=change.path, reason="edited fragment not found")
                )
                continue
            path.write_text(reverted_text, encoding="utf-8")
            reverted.append(change.path)
    if conflicts:
        return RevertResult(
            success=False,
            run_id=run_id,
            workspace=str(workspace),
            reverted_files=reverted,
            conflicts=conflicts,
            summary="Revert partially applied and then hit conflicts.",
        )
    return RevertResult(
        success=True,
        run_id=run_id,
        workspace=str(workspace),
        reverted_files=reverted,
        conflicts=[],
        summary=f"Reverted {len(reverted)} file(s).",
    )


def _added_change(path: str, new: ContentSnapshotEntry) -> FileChange:
    return FileChange(
        path=path,
        change_type="added",
        new_sha256=new.sha256,
        new_size=new.size,
        new_content=new.content,
        recoverable=True,
        skipped_reason=new.skipped_reason,
    )


def _deleted_change(path: str, old: ContentSnapshotEntry) -> FileChange:
    recoverable = old.content is not None
    return FileChange(
        path=path,
        change_type="deleted",
        old_sha256=old.sha256,
        old_size=old.size,
        old_content=old.content,
        recoverable=recoverable,
        skipped_reason="" if recoverable else old.skipped_reason or "old content not captured",
    )


def _modified_change(path: str, old: ContentSnapshotEntry, new: ContentSnapshotEntry) -> FileChange:
    recoverable = old.content is not None and new.content is not None
    reasons = [old.skipped_reason, new.skipped_reason]
    skipped_reason = "; ".join(reason for reason in reasons if reason)
    return FileChange(
        path=path,
        change_type="modified",
        old_sha256=old.sha256,
        new_sha256=new.sha256,
        old_size=old.size,
        new_size=new.size,
        old_content=old.content,
        new_content=new.content,
        recoverable=recoverable,
        skipped_reason="" if recoverable else skipped_reason or "content not captured",
    )


def _revert_conflicts(change_set: ChangeSet, workspace: Path) -> list[RevertConflict]:
    conflicts: list[RevertConflict] = []
    for change in change_set.files:
        path = (workspace / change.path).resolve()
        if not _is_within(path, workspace):
            conflicts.append(RevertConflict(path=change.path, reason="path escapes workspace"))
            continue
        if not change.recoverable:
            conflicts.append(
                RevertConflict(
                    path=change.path,
                    reason=change.skipped_reason or "change is not recoverable",
                )
            )
            continue
        if change.change_type == "added":
            if not path.exists():
                continue
            actual = _sha256_file(path)
            if actual != change.new_sha256:
                conflicts.append(
                    RevertConflict(
                        path=change.path,
                        reason="file changed after the run",
                        expected_sha256=change.new_sha256,
                        actual_sha256=actual,
                    )
                )
        elif change.change_type == "deleted":
            if path.exists():
                conflicts.append(
                    RevertConflict(path=change.path, reason="deleted file exists again")
                )
        elif change.change_type == "modified":
            if not path.exists():
                conflicts.append(
                    RevertConflict(path=change.path, reason="modified file is missing")
                )
                continue
            actual = _sha256_file(path)
            if actual != change.new_sha256:
                conflicts.append(
                    RevertConflict(
                        path=change.path,
                        reason="file changed after the run",
                        expected_sha256=change.new_sha256,
                        actual_sha256=actual,
                    )
                )
        elif change.change_type == "edited":
            if not path.exists():
                conflicts.append(RevertConflict(path=change.path, reason="edited file is missing"))
                continue
            actual = _sha256_file(path)
            if actual != change.new_sha256:
                conflicts.append(
                    RevertConflict(
                        path=change.path,
                        reason="file changed after the run",
                        expected_sha256=change.new_sha256,
                        actual_sha256=actual,
                    )
                )
                continue
            _text, ok = _reverse_apply_edits(path.read_text(encoding="utf-8"), change.edits)
            if not ok:
                conflicts.append(
                    RevertConflict(path=change.path, reason="edited fragment not found")
                )
    return conflicts


def _reverse_apply_edits(text: str, edits: list[dict[str, Any]]) -> tuple[str, bool]:
    """Undo recorded fragment edits by replacing each `new` back with `old`,
    in reverse order. Returns (text, ok); ok is False if any fragment is absent."""
    for edit in reversed(edits):
        new = edit.get("new")
        old = edit.get("old")
        if not isinstance(new, str) or not isinstance(old, str):
            return text, False
        if new == "":
            # A pure insertion (old == "" too) leaves nothing locatable to undo.
            if old == "":
                continue
            return text, False
        if new not in text:
            return text, False
        if edit.get("replace_all"):
            text = text.replace(new, old)
        else:
            text = text.replace(new, old, 1)
    return text, True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_file_for_snapshot(
    path: Path, *, capture_content: bool
) -> tuple[str, bytes, bytes | None]:
    digest = hashlib.sha256()
    head = b""
    chunks: list[bytes] | None = [] if capture_content else None
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            if not head:
                head = chunk[:4096]
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    return digest.hexdigest(), head, b"".join(chunks) if chunks is not None else None


def _normalize_change_path(raw_path: Any, workspace: Path) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ""
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        rel = path.resolve(strict=False).relative_to(workspace)
    except (ValueError, OSError):
        return ""
    value = rel.as_posix()
    if not value or value == "." or value.startswith("../"):
        return ""
    return value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()
