from __future__ import annotations

import hashlib
import json
import shlex
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.usage.history.parsing import ANTIGRAVITY_CONVERSATIONS_DIRS
from fluxion.workspace.change_set import ChangeSet, FileChange, save_change_set


@dataclass(frozen=True)
class AntigravityTrajectoryCapture:
    changed_files: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    change_set_file: str = ""
    # Highest agy `steps.idx` seen. The caller persists it as a per-session
    # high-water mark and passes it back as `since_idx` next turn, so a resumed
    # session reports only the current run's files (not the whole accumulated DB).
    # -1 means "nothing counted yet".
    max_step_idx: int = -1


@dataclass(frozen=True)
class _StructuredChange:
    path: str
    change_type: str
    old_content: str | None = None
    new_content: str | None = None
    recoverable: bool = False


def collect_antigravity_trajectory(
    *,
    session_id: str,
    workspace: Path,
    data_dir: Path,
    run_id: str,
    status: str,
    revert_capture: str = "structured",
    since_idx: int = -1,
    conversation_dirs: tuple[Path, ...] = ANTIGRAVITY_CONVERSATIONS_DIRS,
) -> AntigravityTrajectoryCapture:
    session = session_id.strip()
    if not session:
        return AntigravityTrajectoryCapture(max_step_idx=since_idx)

    db_path = find_conversation_db(session, conversation_dirs)
    if db_path is None:
        return AntigravityTrajectoryCapture(max_step_idx=since_idx)

    workspace = workspace.resolve()
    structured: OrderedDict[str, _StructuredChange] = OrderedDict()
    changed_files = OrderedDict[str, None]()
    recoverable: list[str] = []
    unrecoverable: list[str] = []
    risk_flags: list[str] = []
    shell_mutating_commands: list[tuple[str, list[str]]] = []
    max_idx = since_idx

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return AntigravityTrajectoryCapture(max_step_idx=since_idx)

    try:
        try:
            rows = connection.execute(
                "SELECT idx, step_payload FROM steps WHERE idx > ? ORDER BY idx",
                (since_idx,),
            ).fetchall()
        except sqlite3.Error:
            return AntigravityTrajectoryCapture(max_step_idx=since_idx)

        for idx_val, blob in rows:
            if isinstance(idx_val, int) and idx_val > max_idx:
                max_idx = idx_val
            if not isinstance(blob, bytes):
                continue
            for payload in extract_json_payloads(blob):
                if not isinstance(payload, dict):
                    continue
                step = _parse_payload(payload, workspace=workspace)
                if step is None:
                    continue
                if step.command_line:
                    command_paths = _paths_from_command(
                        step.command_line,
                        workspace=workspace,
                        cwd=step.cwd,
                    )
                    if _command_is_mutating(step.command_line):
                        shell_mutating_commands.append((step.command_line, command_paths))
                    for path in command_paths:
                        _record_path(changed_files, path)
                if step.path:
                    _record_path(changed_files, step.path)
                if step.structured_change is not None:
                    structured[step.structured_change.path] = step.structured_change
    finally:
        connection.close()

    structured_changed_paths = list(structured.keys())
    structured_changed_path_set = set(structured_changed_paths)
    shell_seen = False
    for command_line, command_paths in shell_mutating_commands:
        if not command_paths:
            if _command_targets_unknown(command_line):
                shell_seen = True
                break
            continue
        if any(path not in structured_changed_path_set for path in command_paths):
            shell_seen = True
            break
    if shell_seen:
        risk_flags.extend(["shell_side_effects_detected", "changed_files_may_be_incomplete"])

    structured_change_set_file = ""
    if revert_capture != "off" and structured_changed_paths:
        files = [_trajectory_file_change(change, workspace) for change in structured.values()]
        structured_changed_files = structured_changed_paths
        recoverable = [change.path for change in structured.values() if change.recoverable]
        unrecoverable = [change.path for change in structured.values() if not change.recoverable]
        change_set = ChangeSet(
            run_id=run_id,
            workspace=str(workspace),
            status=status,
            created_at=datetime.now(UTC).isoformat(),
            changed_files=structured_changed_files,
            recoverable_files=recoverable,
            unrecoverable_files=unrecoverable,
            files=files,
        )
        try:
            structured_change_set_file = str(save_change_set(data_dir, change_set))
        except Exception:
            pass

    changed_list = list(changed_files.keys())
    return AntigravityTrajectoryCapture(
        changed_files=changed_list,
        risk_flags=_dedupe(risk_flags),
        change_set_file=structured_change_set_file,
        max_step_idx=max_idx,
    )


def _trajectory_file_change(change: _StructuredChange, workspace: Path) -> FileChange:
    """Build a FileChange with the sha256/size fields the revert conflict check
    relies on. The new-state hash is read from the finalized file on disk (the
    authoritative post-run bytes), falling back to the trajectory content; the
    old-state hash is derived from the captured old content. Without these the
    'added'/'modified' conflict check compared against an empty sha and always
    blocked revert with a spurious 'file changed after the run'."""
    new_sha256, new_size = "", 0
    if change.change_type in {"added", "modified"}:
        target = workspace / change.path
        try:
            raw = target.read_bytes() if target.is_file() else None
        except OSError:
            raw = None
        if raw is None and change.new_content is not None:
            raw = change.new_content.encode("utf-8")
        if raw is not None:
            new_sha256 = hashlib.sha256(raw).hexdigest()
            new_size = len(raw)
    old_sha256, old_size = "", 0
    if change.old_content is not None:
        old_raw = change.old_content.encode("utf-8")
        old_sha256 = hashlib.sha256(old_raw).hexdigest()
        old_size = len(old_raw)
    return FileChange(
        path=change.path,
        change_type=change.change_type,
        old_sha256=old_sha256,
        new_sha256=new_sha256,
        old_size=old_size,
        new_size=new_size,
        old_content=change.old_content,
        new_content=change.new_content,
        recoverable=change.recoverable,
    )


@dataclass(frozen=True)
class _ParsedPayload:
    path: str = ""
    command_line: str = ""
    cwd: Path | None = None
    structured_change: _StructuredChange | None = None


def find_conversation_db(session_id: str, conversation_dirs: tuple[Path, ...]) -> Path | None:
    candidates = [root / f"{session_id}.db" for root in conversation_dirs]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def extract_json_payloads(blob: bytes) -> list[dict[str, Any]]:
    """Pull the JSON objects agy embeds in a protobuf `step_payload` blob.

    Shared with the live narrator, which reads the same rows while the run is
    still in flight.
    """
    text = blob.decode("utf-8", errors="ignore")
    if not text:
        return []
    decoder = json.JSONDecoder()
    payloads: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            payloads.append(value)
        index = start + max(1, end)
    return payloads


def _parse_payload(payload: dict[str, Any], *, workspace: Path) -> _ParsedPayload | None:
    tool_action = str(payload.get("toolAction") or payload.get("tool") or "").strip()
    tool_summary = str(payload.get("toolSummary") or payload.get("summary") or "").strip()
    description = str(payload.get("Description") or payload.get("description") or "").strip()
    command_line = str(payload.get("CommandLine") or "").strip()
    cwd = _normalize_command_cwd(str(payload.get("Cwd") or "").strip(), workspace=workspace)
    path = _normalize_path(
        str(
            payload.get("TargetFile")
            or payload.get("AbsolutePath")
            or payload.get("FilePath")
            or payload.get("Path")
            or ""
        ).strip(),
        workspace=workspace,
    )
    if command_line:
        return _ParsedPayload(path=path, command_line=command_line, cwd=cwd)

    if not path:
        return None

    lowered = tool_action.lower()
    if lowered.startswith("viewing") or lowered.startswith("listing"):
        return None

    target_content = _string_or_none(payload.get("TargetContent"))
    replacement_content = _string_or_none(payload.get("ReplacementContent"))
    code_content = _string_or_none(payload.get("CodeContent"))
    overwrite = bool(payload.get("Overwrite"))
    change_hint = f"{tool_action} {tool_summary} {description}".strip().lower()

    if target_content is not None and replacement_content is not None:
        return _ParsedPayload(
            path=path,
            structured_change=_StructuredChange(
                path=path,
                change_type="modified",
                old_content=target_content,
                new_content=replacement_content,
                recoverable=True,
            ),
        )
    if code_content is not None:
        if "delete" in change_hint and not target_content:
            change_type = "deleted"
        elif _is_creation_hint(change_hint):
            change_type = "added"
        elif target_content is not None or _is_modification_hint(change_hint):
            change_type = "modified"
        elif overwrite:
            change_type = "added"
        else:
            change_type = "added"
        return _ParsedPayload(
            path=path,
            structured_change=_StructuredChange(
                path=path,
                change_type=change_type,
                old_content=target_content,
                new_content=code_content,
                recoverable=target_content is not None or change_type == "added",
            ),
        )
    if target_content is not None and "delete" in lowered:
        return _ParsedPayload(
            path=path,
            structured_change=_StructuredChange(
                path=path,
                change_type="deleted",
                old_content=target_content,
                recoverable=True,
            ),
        )
    if "delete" in lowered:
        return _ParsedPayload(
            path=path,
            structured_change=_StructuredChange(
                path=path,
                change_type="deleted",
                recoverable=False,
            ),
        )
    return None


def _is_creation_hint(text: str) -> bool:
    return any(
        token in text
        for token in {
            "create",
            "created",
            "creating",
            "new file",
            "add file",
            "added",
        }
    )


def _is_modification_hint(text: str) -> bool:
    return any(
        token in text
        for token in {
            "modify",
            "modified",
            "modifying",
            "update",
            "updated",
            "updating",
            "edit",
            "edited",
            "editing",
            "append",
            "appended",
            "appending",
            "replace",
            "replaced",
            "replacing",
            "overwrite",
            "overwritten",
        }
    )


def _paths_from_command(
    command_line: str, *, workspace: Path, cwd: Path | None = None
) -> list[str]:
    try:
        parts = shlex.split(command_line)
    except ValueError:
        return []
    if not parts:
        return []
    command = parts[0]
    args = [part for part in parts[1:] if not part.startswith("-")]
    if command == "mv" and len(args) >= 2:
        return [
            _normalize_command_target(args[-2], workspace=workspace, cwd=cwd),
            _normalize_command_target(args[-1], workspace=workspace, cwd=cwd),
        ]
    if command == "rm" and args:
        return [_normalize_command_target(arg, workspace=workspace, cwd=cwd) for arg in args]
    if ">" in parts:
        for idx, part in enumerate(parts[:-1]):
            if part in {">", ">>"}:
                target = parts[idx + 1]
                if target:
                    return [_normalize_command_target(target, workspace=workspace, cwd=cwd)]
    return []


def _command_is_mutating(command_line: str) -> bool:
    lowered = command_line.lower()
    if any(token in lowered for token in {" > ", ">>", "mv ", "cp ", "rm ", "touch ", "tee "}):
        return True
    if (
        "sed -i" in lowered
        or "perl -pi" in lowered
        or "python -c" in lowered
        or "python3 -c" in lowered
    ):
        return True
    return False


def _command_targets_unknown(command_line: str) -> bool:
    lowered = command_line.lower()
    return any(
        token in lowered for token in {"sh -c", "bash -c", "zsh -c", "python -c", "python3 -c"}
    )


def _record_path(paths: OrderedDict[str, None], path: str) -> None:
    if path:
        paths.setdefault(path, None)


def _normalize_path(raw: str, *, workspace: Path) -> str:
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _normalize_command_cwd(raw: str, *, workspace: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _normalize_command_target(raw: str, *, workspace: Path, cwd: Path | None) -> str:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return _normalize_path(raw, workspace=workspace)
    if cwd is None:
        return _normalize_path(raw, workspace=workspace)
    try:
        return (cwd / path).resolve().relative_to(workspace).as_posix()
    except ValueError:
        return (cwd / path).resolve().as_posix()


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
