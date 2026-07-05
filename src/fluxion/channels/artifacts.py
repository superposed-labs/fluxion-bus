from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fluxion.core.models.result import ExecutionResult
from fluxion.workspace.artifact_collector import select_uploadable_paths


def channel_artifact_paths(
    *,
    result: ExecutionResult,
    context: dict,
    max_files: int,
) -> list[Path]:
    explicit = _existing_paths(result.artifacts, max_files=max_files)
    if explicit:
        return explicit

    workspace_raw = str(context.get("workspace") or "").strip()
    if not workspace_raw:
        return []
    selected = select_uploadable_paths(
        workspace=Path(workspace_raw),
        raw_paths=result.changed_files,
        max_files=max_files,
    )
    return [Path(path) for path in selected]


def upload_channel_artifacts(
    *,
    result: ExecutionResult,
    context: dict,
    max_files: int,
    upload_log_on_success: bool,
    upload_one: Callable[[Path], None],
) -> None:
    seen_names: set[str] = set()
    for path in channel_artifact_paths(result=result, context=context, max_files=max_files):
        name_key = path.name.strip().lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        upload_one(path)

    if result.log_file and (not result.success or upload_log_on_success):
        log_path = Path(result.log_file)
        if log_path.exists() and log_path.is_file():
            upload_one(log_path)


def _existing_paths(raw_paths: list[str], *, max_files: int) -> list[Path]:
    selected: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        selected.append(resolved)
        if len(selected) >= max_files:
            break
    return selected
