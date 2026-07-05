from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fluxion.config.settings.models import PROJECT_EXECUTORS, ProjectConfig


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_paths(raw: str) -> list[Path]:
    paths: list[Path] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        paths.append(Path(value).expanduser().resolve())
    return paths


def _merge_allowed_workspaces(existing: list[Path], extra: list[Path]) -> list[Path]:
    merged: list[Path] = []
    seen: set[Path] = set()
    for path in [*existing, *extra]:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        merged.append(resolved)
    return merged


def _parse_projects(*, raw: str, file_path: str) -> dict[str, ProjectConfig]:
    projects: dict[str, ProjectConfig] = {}
    projects.update(_parse_projects_file(file_path))
    projects.update(_parse_projects_env(raw))
    return projects


def _parse_projects_file(file_path: str) -> dict[str, ProjectConfig]:
    value = file_path.strip()
    if not value:
        return {}
    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return _projects_from_json(data)


def _projects_from_json(data: Any) -> dict[str, ProjectConfig]:
    projects: dict[str, ProjectConfig] = {}
    if isinstance(data, dict):
        for key, spec in data.items():
            if isinstance(spec, str):
                project = _project_from_parts(key=str(key), workspace=spec)
            elif isinstance(spec, dict):
                project = _project_from_parts(
                    key=str(spec.get("key") or key),
                    workspace=str(spec.get("workspace") or ""),
                    default_executor=str(spec.get("default_executor") or ""),
                    description=str(spec.get("description") or ""),
                )
            else:
                continue
            if project is not None:
                projects[project.key] = project
    elif isinstance(data, list):
        for spec in data:
            if not isinstance(spec, dict):
                continue
            project = _project_from_parts(
                key=str(spec.get("key") or ""),
                workspace=str(spec.get("workspace") or ""),
                default_executor=str(spec.get("default_executor") or ""),
                description=str(spec.get("description") or ""),
            )
            if project is not None:
                projects[project.key] = project
    return projects


def _parse_projects_env(raw: str) -> dict[str, ProjectConfig]:
    projects: dict[str, ProjectConfig] = {}
    for item in raw.split(","):
        value = item.strip()
        if not value or "=" not in value:
            continue
        key, rest = value.split("=", 1)
        parts = [part.strip() for part in rest.split("|") if part.strip()]
        if not parts:
            continue
        attrs: dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            attr_key, attr_value = part.split("=", 1)
            attrs[attr_key.strip()] = attr_value.strip()
        project = _project_from_parts(
            key=key.strip(),
            workspace=parts[0],
            default_executor=attrs.get("executor", attrs.get("default_executor", "")),
            description=attrs.get("description", ""),
        )
        if project is not None:
            projects[project.key] = project
    return projects


def _project_from_parts(
    *,
    key: str,
    workspace: str,
    default_executor: str = "",
    description: str = "",
) -> ProjectConfig | None:
    clean_key = key.strip()
    if not clean_key or not workspace.strip():
        return None
    clean_executor = default_executor.strip()
    if clean_executor and clean_executor not in PROJECT_EXECUTORS:
        allowed = ", ".join(sorted(PROJECT_EXECUTORS))
        raise ValueError(
            f"Unsupported default_executor for project {clean_key}: {clean_executor}. "
            f"Allowed: {allowed}"
        )
    return ProjectConfig(
        key=clean_key,
        workspace=Path(workspace).expanduser().resolve(),
        default_executor=clean_executor,
        description=description.strip(),
    )


def _resolve_data_dir(raw: str, *, workspace_root: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _first_containing_root(path: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        if _is_within(path, root):
            return root
    return None


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _parse_status_updates(raw: str) -> set[str]:
    allowed = {"RECEIVED", "QUEUED", "RUNNING", "RETRYING", "RETURNED", "FAILED", "CANCELED"}
    parsed = {x.strip().upper() for x in raw.split(",") if x.strip()}
    valid = parsed & allowed
    if not valid:
        return {"RUNNING", "RETURNED", "FAILED"}
    return valid


def _parse_channel_workspaces(raw: str) -> dict[str, Path]:
    """
    Format:
      C12345678:/abs/path/projectA,C23456789:/abs/path/projectB
    """
    mapping: dict[str, Path] = {}
    for item in raw.split(","):
        part = item.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        channel_id, path = part.split(":", 1)
        channel_id = channel_id.strip()
        value = path.strip()
        if not channel_id or not value:
            continue
        mapping[channel_id] = Path(value).expanduser().resolve()
    return mapping


def _parse_codex_usage_mode(raw: str) -> str:
    value = (raw or "").strip().lower()
    return value if value in {"auto", "live", "logs"} else "auto"


def _parse_change_detection_mode(raw: str) -> str:
    value = (raw or "").strip().lower()
    allowed = {"off", "snapshot", "force", "fsevents", "auto"}
    return value if value in allowed else "off"


def _parse_revert_capture_mode(raw: str) -> str:
    value = (raw or "").strip().lower()
    allowed = {"off", "structured", "full"}
    return value if value in allowed else "structured"


def _parse_usage_providers(raw: str) -> list[str]:
    allowed = ("claude", "codex", "antigravity")
    seen: set[str] = set()
    providers: list[str] = []
    for part in raw.split(","):
        value = part.strip().lower()
        if value in allowed and value not in seen:
            seen.add(value)
            providers.append(value)
    return providers


def _parse_enabled_executors(raw: str) -> list[str]:
    allowed = ("claude", "codex", "antigravity")
    seen: set[str] = set()
    executors: list[str] = []
    for part in raw.split(","):
        value = part.strip().lower()
        if value in allowed and value not in seen:
            seen.add(value)
            executors.append(value)
    return executors or list(allowed)


def _parse_codex_sandbox_mode(raw: str) -> str:
    value = (raw or "").strip().lower()
    allowed = {"read-only", "workspace-write", "danger-full-access"}
    if value in allowed:
        return value
    return "workspace-write"


def _parse_claude_provider(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"ollama", "custom"}:
        return "third_party"
    allowed = {"official", "third_party"}
    if value in allowed:
        return value
    return "official"


def _parse_claude_auth_mode(raw: str) -> str:
    value = (raw or "").strip().lower()
    allowed = {"login", "api_key", "auth_token", "none"}
    if value in allowed:
        return value
    return "login"
