"""Workspace authorization, management, and task approval storage.

The environment remains a source of authority for backwards compatibility.  The
JSON file managed by the desktop app is an additional source, not a migration of
or replacement for the legacy settings.  Authorization is intentionally resolved
from a fresh snapshot for every submission so a long-running MCP or Web process
does not need to be restarted after a permission edit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fluxion.config.settings.models import (
    PROJECT_EXECUTORS,
    ProjectConfig,
    WorkspaceAuthorization,
)
from fluxion.utils import macos_notify

try:
    import fcntl
except ImportError:  # pragma: no cover - Fluxion desktop targets macOS.
    fcntl = None

WORKSPACE_ACCESS_CONFIG_VERSION = 1
# A task approval is bounded by the task lifecycle.  The expiry remains a
# recovery guard for a crashed client or a machine that was powered off while
# a task was active; it should not interrupt a normal long-running task.
DEFAULT_TASK_GRANT_TTL_SEC = 24 * 60 * 60
# Keep the old export for integrations that imported this name before task
# scoped approvals were introduced.
DEFAULT_ONE_TIME_GRANT_TTL_SEC = DEFAULT_TASK_GRANT_TTL_SEC
WORKSPACE_NOTIFICATION_COOLDOWN_SEC = 60
# A request only leaves "pending" through a human decision (or expiry), so a
# caller waiting on one polls the store instead of holding the file lock.
REQUEST_WAIT_POLL_INTERVAL_SEC = 0.5
# Statuses a caller can act on immediately: the user granted access and the
# original task can be retried.
RETRYABLE_REQUEST_STATUSES = frozenset({"approved", "project-allowed"})
READ_ONLY = "read-only"
READ_WRITE = "read-write"
VALID_ACCESS_MODES = {READ_ONLY, READ_WRITE}

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}


def _shared_store_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonicalize_workspace(raw: str | Path, *, base: Path | None = None) -> Path:
    """Resolve a workspace without following authorization checks.

    ``Path.resolve(strict=False)`` canonicalizes existing symlinks and all
    symlink components before the path is compared with a configured root.  It
    also gives deterministic paths for a not-yet-existing target, which is
    useful for returning a safe, actionable rejection.
    """

    value = Path(raw).expanduser()
    if not value.is_absolute():
        value = (base or Path.cwd()) / value
    return value.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _first_containing_root(path: Path, roots: Iterable[Path]) -> Path | None:
    matches = [root for root in roots if _is_within(path, root)]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.parts))


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _path_values(settings: object, name: str) -> list[Path]:
    raw = getattr(settings, name, ())
    if isinstance(raw, (str, Path)):
        raw = str(raw).split(",")
    if not isinstance(raw, Iterable):
        return []
    result: list[Path] = []
    for value in raw:
        if value is None or not str(value).strip():
            continue
        try:
            result.append(canonicalize_workspace(str(value)))
        except (OSError, RuntimeError, ValueError):
            continue
    return result


def _env_path_values(name: str) -> list[Path]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return []
    result: list[Path] = []
    for value in raw.split(","):
        if not value.strip():
            continue
        try:
            result.append(canonicalize_workspace(value.strip()))
        except (OSError, RuntimeError, ValueError):
            continue
    return result


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = canonicalize_workspace(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _safe_text(value: object, *, max_length: int = 500) -> str:
    return str(value or "").strip()[:max_length]


@dataclass(frozen=True)
class WorkspaceAccessEntry:
    """One effective row shown by the desktop and Web management surfaces."""

    id: str
    key: str
    path: Path
    access: str
    source: str
    status: str = "active"
    default_executor: str = ""
    description: str = ""
    sources: tuple[str, ...] = ()
    app_entry_id: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "path": str(self.path),
            # workspace is retained as a readable alias for older UI clients.
            "workspace": str(self.path),
            "access": self.access,
            "source": self.source,
            "sources": list(self.sources or ((self.source,) if self.source else ())),
            "status": self.status,
            "default_executor": self.default_executor,
            "description": self.description,
            "app_entry_id": self.app_entry_id or None,
            "managed": bool(self.app_entry_id),
        }

    def to_config_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "key": self.key,
            "path": str(self.path),
            "access": self.access,
            "default_executor": self.default_executor,
            "description": self.description,
        }


@dataclass(frozen=True)
class WorkspaceAccessRequest:
    request_id: str
    client_id: str
    path: Path
    mode: str
    status: str
    created_at: str
    expires_at: str
    approved_at: str = ""
    consumed_at: str = ""
    last_notified_at: str = ""
    authorization_grant_id: str = ""
    task_scope_id: str = ""
    task_id: str = ""
    activated_at: str = ""
    released_at: str = ""
    project_entry_id: str = ""
    project_allowed_at: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "authorization_request_id": self.request_id,
            "request_id": self.request_id,
            "client_id": self.client_id,
            "path": str(self.path),
            "workspace": str(self.path),
            "mode": self.mode,
            "status": self.status,
            "pending": self.status == "pending",
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "approved_at": self.approved_at or None,
            "consumed_at": self.consumed_at or None,
            "last_notified_at": self.last_notified_at or None,
            "authorization_grant_id": self.authorization_grant_id or None,
            "authorization_scope": "task",
            "task_scope_id": self.task_scope_id or None,
            "task_id": self.task_id or None,
            "activated_at": self.activated_at or None,
            "released_at": self.released_at or None,
            "project_entry_id": self.project_entry_id or None,
            "project_allowed_at": self.project_allowed_at or None,
        }


@dataclass(frozen=True)
class _Grant:
    path: Path
    modes: frozenset[str]
    source: str
    policy: str
    key: str = ""
    default_executor: str = ""
    app_entry_id: str = ""


@dataclass(frozen=True)
class _Snapshot:
    settings: object
    entries: tuple[WorkspaceAccessEntry, ...]
    grants: tuple[_Grant, ...]
    legacy_entries: tuple[WorkspaceAccessEntry, ...]
    denied: tuple[Path, ...]
    workspace_root: Path
    data_dir: Path
    trusted_roots: tuple[Path, ...]
    workspace_discovery: bool
    runtime_context: dict[str, Any]


class WorkspaceAccessStore:
    """Versioned 0600 JSON stores with atomic replacement."""

    def __init__(
        self,
        data_dir: Path,
        *,
        config_path: Path | None = None,
        requests_path: Path | None = None,
    ) -> None:
        resolved_data_dir = canonicalize_workspace(data_dir)
        self.data_dir = resolved_data_dir
        self.config_path = config_path or resolved_data_dir / "config" / "workspace_access.json"
        self.requests_path = (
            requests_path or resolved_data_dir / "config" / "workspace_access_requests.json"
        )
        self._lock_path = self.config_path.parent / ".workspace_access.lock"
        self._lock = _shared_store_lock(self._lock_path)

    @contextmanager
    def transaction(self):
        """Serialize read-modify-write operations across services and processes."""

        with self._lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self._lock_path,
                os.O_CREAT | os.O_RDWR,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            try:
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def load_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read(self.config_path)
        if (
            payload.get("version", WORKSPACE_ACCESS_CONFIG_VERSION)
            != WORKSPACE_ACCESS_CONFIG_VERSION
        ):
            return []
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return (
            [item for item in entries if isinstance(item, dict)]
            if isinstance(entries, list)
            else []
        )

    def save_entries(self, entries: Iterable[dict[str, Any]]) -> None:
        payload = {
            "version": WORKSPACE_ACCESS_CONFIG_VERSION,
            "entries": [dict(entry) for entry in entries],
        }
        with self._lock:
            self._atomic_write(self.config_path, payload)

    def load_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read(self.requests_path)
        if (
            payload.get("version", WORKSPACE_ACCESS_CONFIG_VERSION)
            != WORKSPACE_ACCESS_CONFIG_VERSION
        ):
            return []
        requests = payload.get("requests") if isinstance(payload, dict) else None
        return (
            [item for item in requests if isinstance(item, dict)]
            if isinstance(requests, list)
            else []
        )

    def save_requests(self, requests: Iterable[dict[str, Any]]) -> None:
        payload = {
            "version": WORKSPACE_ACCESS_CONFIG_VERSION,
            "requests": [dict(request) for request in requests],
        }
        with self._lock:
            self._atomic_write(self.requests_path, payload)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, stat.S_IRWXU)
        except OSError:
            pass
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


class WorkspaceAccessService:
    """Resolve current workspace permissions and manage App-owned entries."""

    def __init__(
        self,
        settings: object,
        *,
        settings_loader: Callable[[], object] | None = None,
        store: WorkspaceAccessStore | None = None,
        clock: Callable[[], datetime] | None = None,
        notification_queue: Callable[[WorkspaceAccessRequest], object] | None = None,
        one_time_ttl_sec: int = DEFAULT_ONE_TIME_GRANT_TTL_SEC,
    ) -> None:
        self._settings = settings
        if settings_loader is None:
            candidate = getattr(settings, "reload", None)
            settings_loader = candidate if callable(candidate) else None
        self._settings_loader = settings_loader
        data_dir = canonicalize_workspace(getattr(settings, "data_dir", Path("data")))
        self.store = store or WorkspaceAccessStore(data_dir)
        self._clock = clock or _now
        self._notification_queue = notification_queue or self._queue_notification
        self.one_time_ttl_sec = max(1, int(one_time_ttl_sec))
        self._lock = threading.RLock()

    @property
    def config_path(self) -> Path:
        return self.store.config_path

    def _current_settings(self) -> object:
        if self._settings_loader is None:
            return self._settings
        try:
            current = self._settings_loader()
        except Exception:
            # A malformed unrelated setting must not turn a workspace denial
            # into an authorization bypass. Keep the last valid snapshot.
            return self._settings
        return current

    def _snapshot(self, settings: object | None = None) -> _Snapshot:
        current = settings or self._current_settings()
        workspace_root = canonicalize_workspace(getattr(current, "workspace_root", Path.cwd()))
        data_dir = canonicalize_workspace(getattr(current, "data_dir", self.store.data_dir))
        denied = tuple(_dedupe_paths(_path_values(current, "denied_workspaces")))
        app_entries = tuple(self._load_app_entries())
        legacy_entries, legacy_grants = self._legacy(current, workspace_root, data_dir)
        app_grants = tuple(
            _Grant(
                path=entry.path,
                modes=frozenset(
                    {READ_ONLY, "workspace-write"} if entry.access == READ_WRITE else {READ_ONLY}
                ),
                source="app",
                policy="app",
                key=entry.key,
                default_executor=entry.default_executor,
                app_entry_id=entry.id,
            )
            for entry in app_entries
        )
        trusted_roots = tuple(_dedupe_paths(_path_values(current, "trusted_workspace_roots")))
        runtime_context = {
            "workspace_root": str(workspace_root),
            "data_dir": str(data_dir),
            "development_repository": str(workspace_root)
            if (workspace_root / ".git").exists()
            else None,
            "installed_package_root": str(Path(__file__).resolve().parents[1]),
            "config_path": str(self.store.config_path),
            "legacy_sources": [
                "FLUXION_PROJECTS",
                "FLUXION_PROJECTS_FILE",
                "FLUXION_ALLOWED_WORKSPACES",
                "FLUXION_WRITE_ALLOWED_WORKSPACES",
                "FLUXION_TRUSTED_WORKSPACE_ROOTS",
                "FLUXION_DENIED_WORKSPACES",
            ],
        }
        return _Snapshot(
            settings=current,
            entries=app_entries,
            grants=(*app_grants, *legacy_grants),
            legacy_entries=tuple(legacy_entries),
            denied=denied,
            workspace_root=workspace_root,
            data_dir=data_dir,
            trusted_roots=trusted_roots,
            workspace_discovery=bool(getattr(current, "workspace_discovery", False)),
            runtime_context=runtime_context,
        )

    def _load_app_entries(self) -> list[WorkspaceAccessEntry]:
        entries: list[WorkspaceAccessEntry] = []
        for raw in self.store.load_entries():
            try:
                entry_id = _safe_text(raw.get("id"), max_length=128)
                key = _safe_text(raw.get("key"), max_length=128)
                raw_path = _safe_text(raw.get("path") or raw.get("workspace"))
                if not raw_path:
                    continue
                path = canonicalize_workspace(raw_path)
                access = _safe_text(raw.get("access"), max_length=32)
                if not entry_id or not _REQUEST_ID_RE.fullmatch(entry_id):
                    continue
                if not key or not _KEY_RE.fullmatch(key) or access not in VALID_ACCESS_MODES:
                    continue
                executor = _safe_text(raw.get("default_executor"), max_length=32).lower()
                if executor and executor not in PROJECT_EXECUTORS:
                    executor = ""
                entries.append(
                    WorkspaceAccessEntry(
                        id=entry_id,
                        key=key,
                        path=path,
                        access=access,
                        source="app",
                        status=self._entry_status(path, (), access),
                        default_executor=executor,
                        description=_safe_text(raw.get("description")),
                        sources=("app",),
                        app_entry_id=entry_id,
                    )
                )
            except (OSError, RuntimeError, ValueError):
                continue
        return entries

    @staticmethod
    def _entry_status(path: Path, denied: Iterable[Path], access: str) -> str:
        if _first_containing_root(path, denied) is not None:
            return "denied"
        if not path.exists() or not path.is_dir():
            return "missing"
        if access == READ_ONLY:
            return "read-only"
        return "active"

    def _legacy(
        self, settings: object, workspace_root: Path, data_dir: Path
    ) -> tuple[list[WorkspaceAccessEntry], tuple[_Grant, ...]]:
        entries: list[WorkspaceAccessEntry] = []
        grants: list[_Grant] = []
        seen: set[tuple[str, str]] = set()

        def add(
            path: Path,
            *,
            source: str,
            policy: str,
            access: str,
            modes: frozenset[str],
            key: str = "",
            default_executor: str = "",
            status: str = "active",
        ) -> None:
            canonical = canonicalize_workspace(path)
            marker = (str(canonical), source)
            if marker in seen:
                return
            seen.add(marker)
            row_key = key or f"legacy-{hashlib.sha256(str(canonical).encode()).hexdigest()[:12]}"
            entry_id = (
                f"legacy-{hashlib.sha256((source + str(canonical)).encode()).hexdigest()[:16]}"
            )
            entries.append(
                WorkspaceAccessEntry(
                    id=entry_id,
                    key=row_key,
                    path=canonical,
                    access=access,
                    source=source,
                    status=status,
                    default_executor=default_executor,
                    sources=(source,),
                )
            )
            grants.append(
                _Grant(
                    path=canonical,
                    modes=modes,
                    source=source,
                    policy=policy,
                    key=key,
                    default_executor=default_executor,
                )
            )

        projects = getattr(settings, "projects", {})
        if isinstance(projects, dict):
            for key, project in projects.items():
                if not isinstance(project, ProjectConfig):
                    continue
                add(
                    project.workspace,
                    source=f"legacy:FLUXION_PROJECTS:{project.key or key}",
                    policy="project",
                    access=READ_WRITE,
                    modes=frozenset({READ_ONLY, "workspace-write"}),
                    key=project.key or str(key),
                    default_executor=project.default_executor,
                )

        raw_allowed = _env_path_values("FLUXION_ALLOWED_WORKSPACES")
        configured_allowed = _path_values(settings, "allowed_workspaces")
        # A Settings instance may have been constructed before an environment
        # edit (or may be a lightweight settings object supplied by an
        # integration).  Keep both representations effective rather than
        # letting a stale process environment replace the instance's legacy
        # values.  ``add`` deduplicates identical path/source pairs.
        if raw_allowed:
            allowed_values = [(path, "legacy:FLUXION_ALLOWED_WORKSPACES") for path in raw_allowed]
        else:
            allowed_values = []
        if configured_allowed:
            configured_source = (
                "legacy:FLUXION_ALLOWED_WORKSPACES"
                if raw_allowed or any(path != workspace_root for path in configured_allowed)
                else "legacy:FLUXION_WORKSPACE_ROOT"
            )
            allowed_values.extend((path, configured_source) for path in configured_allowed)
        if not allowed_values:
            allowed_values = [(workspace_root, "legacy:FLUXION_WORKSPACE_ROOT")]
        for path, source in allowed_values:
            add(
                path,
                source=source,
                policy="allowed-workspace",
                access=READ_WRITE,
                modes=frozenset({READ_ONLY, "workspace-write"}),
            )

        raw_write_allowed = _env_path_values("FLUXION_WRITE_ALLOWED_WORKSPACES")
        write_allowed = [
            (path, "legacy:FLUXION_WRITE_ALLOWED_WORKSPACES")
            for path in [*raw_write_allowed, *_path_values(settings, "write_allowed_workspaces")]
        ]
        for path, source in write_allowed:
            add(
                path,
                source=source,
                policy="write-allowed-workspace",
                access=READ_WRITE,
                modes=frozenset({"workspace-write"}),
            )

        discovery = bool(getattr(settings, "workspace_discovery", False))
        raw_trusted = _env_path_values("FLUXION_TRUSTED_WORKSPACE_ROOTS")
        trusted_values = [
            (path, "legacy:FLUXION_TRUSTED_WORKSPACE_ROOTS")
            for path in [*raw_trusted, *_path_values(settings, "trusted_workspace_roots")]
        ]
        for path, source in trusted_values:
            add(
                path,
                source=source,
                policy="trusted-git-read",
                access=READ_ONLY,
                # Trusted roots are discovery boundaries, not direct grants.
                # Authorization below still requires discovery to be enabled
                # and the requested path itself to be a Git repository root.
                modes=frozenset(),
                status="discovery" if discovery else "trusted",
            )
        raw_denied = _env_path_values("FLUXION_DENIED_WORKSPACES")
        denied_values = [
            (path, "legacy:FLUXION_DENIED_WORKSPACES")
            for path in [*raw_denied, *_path_values(settings, "denied_workspaces")]
        ]
        for path, source in denied_values:
            add(
                path,
                source=source,
                policy="denied",
                access=READ_ONLY,
                modes=frozenset(),
                status="denied",
            )
        return entries, tuple(grants)

    def resolve_project(self, project_key: str | None) -> ProjectConfig | None:
        key = (project_key or "").strip()
        if not key:
            return None
        snapshot = self._snapshot()
        project = self._find_legacy_project(snapshot.settings, key)
        if project is not None:
            return project
        app_entry = next((entry for entry in snapshot.entries if entry.key == key), None)
        if app_entry is not None:
            return ProjectConfig(
                key=app_entry.key,
                workspace=app_entry.path,
                default_executor=app_entry.default_executor,
                description=app_entry.description,
            )
        allowed = sorted(
            {
                *(str(project_key) for project_key in getattr(snapshot.settings, "projects", {})),
                *(entry.key for entry in snapshot.entries),
            }
        )
        raise ValueError(
            f"Unknown Fluxion project: {key}. Configured projects: "
            f"{', '.join(allowed) or '(none configured)'}"
        )

    @staticmethod
    def _find_legacy_project(settings: object, key: str) -> ProjectConfig | None:
        if not key:
            return None
        projects = getattr(settings, "projects", {})
        return projects.get(key) if isinstance(projects, dict) else None

    def authorize_run_workspace(
        self,
        *,
        raw_workspace: str | None,
        project_key: str | None = None,
        mode: str = READ_ONLY,
        client_id: str = "local",
        authorization_request_id: str | None = None,
        task_scope_id: str | None = None,
        request_if_denied: bool = True,
    ) -> WorkspaceAuthorization:
        if mode not in {READ_ONLY, "workspace-write"}:
            raise ValueError(
                f"Unsupported workspace access mode: {mode}. Allowed: read-only, workspace-write"
            )
        normalized_client = self._validate_request_value(client_id, "client_id")
        supplied_request_id = self._validate_optional_request_id(authorization_request_id)
        normalized_task_scope_id = self._validate_optional_request_id(task_scope_id)
        snapshot = self._snapshot()
        normalized_project_key = (project_key or "").strip()
        legacy_project = self._find_legacy_project(snapshot.settings, normalized_project_key)
        app_project = next(
            (entry for entry in snapshot.entries if entry.key == normalized_project_key),
            None,
        )
        if normalized_project_key and legacy_project is None and app_project is None:
            allowed = sorted(
                {
                    *(str(key) for key in getattr(snapshot.settings, "projects", {})),
                    *(entry.key for entry in snapshot.entries),
                }
            )
            raise ValueError(
                f"Unknown Fluxion project: {normalized_project_key}. Configured projects: "
                f"{', '.join(allowed) or '(none configured)'}"
            )
        project = legacy_project or (
            ProjectConfig(
                key=app_project.key,
                workspace=app_project.path,
                default_executor=app_project.default_executor,
                description=app_project.description,
            )
            if app_project is not None
            else None
        )
        if project is None:
            path = self._resolve_path(raw_workspace, snapshot.workspace_root)
        else:
            project_root = canonicalize_workspace(project.workspace)
            value = (raw_workspace or "").strip()
            path = (
                project_root
                if not value or value == "."
                else self._resolve_path(value, project_root)
            )
            if not _is_within(path, project_root):
                return self._denied(
                    path,
                    mode,
                    "project-boundary",
                    f"Workspace for project {project.key} must be inside {project_root}",
                    client_id=normalized_client,
                )

        authorization = self._authorize_path(
            snapshot,
            path,
            mode,
            # Legacy projects retain their historical implicit read/write
            # grant. App-managed projects flow through their configured grant
            # so a read-only entry cannot be escalated by addressing its key.
            project=legacy_project,
            client_id=normalized_client,
        )
        if authorization.allowed:
            return authorization
        if not request_if_denied or authorization.policy in {
            "missing",
            "denied",
            "project-boundary",
        }:
            return authorization

        request, request_state = self._request_for_denial(
            path=path,
            mode=mode,
            client_id=normalized_client,
            supplied_request_id=supplied_request_id,
            task_scope_id=normalized_task_scope_id,
            denied=snapshot.denied,
        )
        if request_state == "consumed":
            return self._denied(
                path,
                mode,
                "one-time-consumed",
                "This task authorization has already ended. Request approval again.",
                client_id=normalized_client,
                request=request,
            )
        if request_state == "active":
            return self._denied(
                path,
                mode,
                "one-time-in-use",
                "This task authorization is already attached to an active task.",
                client_id=normalized_client,
                request=request,
            )
        if request_state == "denied":
            return self._denied(
                path,
                mode,
                "one-time-denied",
                "The user declined this workspace authorization request.",
                client_id=normalized_client,
                request=request,
            )
        if request_state == "expired":
            return self._denied(
                path,
                mode,
                "one-time-expired",
                "This task authorization has expired. Request approval again.",
                client_id=normalized_client,
                request=request,
            )
        if request_state == "mismatch":
            return self._denied(
                path,
                mode,
                "one-time-mismatch",
                "The task authorization does not match this exact workspace, mode, or client.",
                client_id=normalized_client,
                request=request,
            )
        # A caller normally retries the same tool invocation after the user
        # approves its notification.  Do not require it to understand and
        # replay Fluxion's internal request id: _request_for_denial already
        # matched this approved row by the exact client, canonical path, and
        # mode when no id was supplied.  An explicitly supplied id remains a
        # stronger correlation check and mismatches are rejected above.
        if request.status == "approved" and (
            not supplied_request_id or supplied_request_id == request.request_id
        ):
            if normalized_task_scope_id:
                active_request = self._activate_request(
                    request.request_id,
                    path,
                    mode,
                    normalized_client,
                    normalized_task_scope_id,
                )
                if active_request is not None:
                    return WorkspaceAuthorization(
                        allowed=True,
                        reason="Workspace allowed by a task-scoped App approval",
                        policy="one-time",
                        workspace=path,
                        access=READ_WRITE if mode == "workspace-write" else READ_ONLY,
                        source="one-time",
                        authorization_request_id=active_request.request_id,
                        client_id=normalized_client,
                        authorization_grant_id=active_request.authorization_grant_id,
                        authorization_scope="task",
                        authorization_expires_at=active_request.expires_at,
                    )
                return self._denied(
                    path,
                    mode,
                    "one-time-in-use",
                    "This task authorization is already attached to an active task.",
                    client_id=normalized_client,
                    request=request,
                )
            if self._consume_request(request.request_id, path, mode, normalized_client):
                return WorkspaceAuthorization(
                    allowed=True,
                    reason="Workspace allowed by a one-time App approval",
                    policy="one-time",
                    workspace=path,
                    access=READ_WRITE if mode == "workspace-write" else READ_ONLY,
                    source="one-time",
                    authorization_request_id=request.request_id,
                    client_id=normalized_client,
                )
            return self._denied(
                path,
                mode,
                "one-time-consumed",
                "This task authorization has already ended. Request approval again.",
                client_id=normalized_client,
                request=request,
            )
        return self._denied(
            path,
            mode,
            authorization.policy,
            authorization.reason,
            client_id=normalized_client,
            request=request,
        )

    @staticmethod
    def _resolve_path(raw_workspace: str | None, base: Path) -> Path:
        return canonicalize_workspace(raw_workspace or base, base=base)

    def _authorize_path(
        self,
        snapshot: _Snapshot,
        path: Path,
        mode: str,
        *,
        project: ProjectConfig | None,
        client_id: str,
    ) -> WorkspaceAuthorization:
        denied = _first_containing_root(path, snapshot.denied)
        if denied is not None:
            return self._denied(
                path,
                mode,
                "denied",
                f"Workspace is denied by FLUXION_DENIED_WORKSPACES: {denied}",
                client_id=client_id,
            )
        if not path.exists() or not path.is_dir():
            return self._denied(
                path,
                mode,
                "missing",
                f"Workspace does not exist or is not a directory: {path}",
                client_id=client_id,
            )
        autoping_dir = canonicalize_workspace(snapshot.data_dir / "autoping_workspace")
        if _is_within(path, autoping_dir):
            return WorkspaceAuthorization(
                allowed=True,
                reason="Workspace is the managed Auto Ping workspace",
                policy="autoping",
                workspace=path,
                access=READ_WRITE if mode == "workspace-write" else READ_ONLY,
                source="autoping",
                client_id=client_id,
            )
        if project is not None:
            return WorkspaceAuthorization(
                allowed=True,
                reason=f"Workspace allowed by registered project: {project.key}",
                policy="project",
                workspace=path,
                access=READ_WRITE if mode == "workspace-write" else READ_ONLY,
                source=f"legacy:project:{project.key}",
                client_id=client_id,
            )
        matching = [
            grant
            for grant in snapshot.grants
            if mode in grant.modes and _is_within(path, grant.path)
        ]
        if matching:
            grant = max(matching, key=lambda item: len(item.path.parts))
            return WorkspaceAuthorization(
                allowed=True,
                reason=f"Workspace allowed by {grant.source}: {grant.path}",
                policy=grant.policy,
                workspace=path,
                access=READ_WRITE if "workspace-write" in grant.modes else READ_ONLY,
                source=grant.source,
                client_id=client_id,
                default_executor=grant.default_executor,
            )
        if mode == "workspace-write":
            return self._denied(
                path,
                mode,
                "write-not-authorized",
                (
                    "Workspace-write runs require a registered project, an App workspace, "
                    "FLUXION_ALLOWED_WORKSPACES, or FLUXION_WRITE_ALLOWED_WORKSPACES. "
                    f"Requested workspace: {path}"
                ),
                client_id=client_id,
            )
        trusted = _first_containing_root(path, snapshot.trusted_roots)
        if trusted is not None:
            if snapshot.workspace_discovery and _is_git_repo(path):
                return WorkspaceAuthorization(
                    allowed=True,
                    reason=f"Read-only workspace allowed by trusted Git root: {trusted}",
                    policy="trusted-git-read",
                    workspace=path,
                    access=READ_ONLY,
                    source="legacy:trusted_workspace_roots",
                    client_id=client_id,
                )
            return self._denied(
                path,
                mode,
                "trusted-root-not-discovered",
                (
                    "Workspace is under FLUXION_TRUSTED_WORKSPACE_ROOTS but is not an "
                    "allowed Git repo for discovery. Set FLUXION_WORKSPACE_DISCOVERY=true "
                    f"and use a Git repository root. Requested workspace: {path}"
                ),
                client_id=client_id,
            )
        roots = (
            ", ".join(str(grant.path) for grant in snapshot.grants if grant.modes and grant.path)
            or "(none)"
        )
        return self._denied(
            path,
            mode,
            "not-authorized",
            f"Workspace is not authorized: {path}. Configured roots: {roots}.",
            client_id=client_id,
        )

    @staticmethod
    def _denied(
        path: Path,
        mode: str,
        policy: str,
        reason: str,
        *,
        client_id: str = "",
        request: WorkspaceAccessRequest | None = None,
    ) -> WorkspaceAuthorization:
        return WorkspaceAuthorization(
            allowed=False,
            reason=reason,
            policy=policy,
            workspace=path,
            access=READ_WRITE if mode == "workspace-write" else READ_ONLY,
            source="",
            authorization_request_id=request.request_id if request else "",
            pending=request.status == "pending" if request else False,
            pending_status=request.status if request else "",
            client_id=client_id,
            authorization_grant_id=request.authorization_grant_id if request else "",
            authorization_scope="task" if request else "",
            authorization_expires_at=request.expires_at if request else "",
        )

    @staticmethod
    def _validate_request_value(value: str | None, field: str) -> str:
        normalized = _safe_text(value, max_length=128)
        if not normalized or not _REQUEST_ID_RE.fullmatch(normalized):
            raise ValueError(f"Invalid {field}; use 1-128 letters, digits, '.', '_', ':', or '-'.")
        return normalized

    def _validate_optional_request_id(self, value: str | None) -> str:
        normalized = _safe_text(value, max_length=128)
        if not normalized:
            return ""
        return self._validate_request_value(normalized, "authorization_request_id")

    def _request_for_denial(
        self,
        *,
        path: Path,
        mode: str,
        client_id: str,
        supplied_request_id: str,
        task_scope_id: str,
        denied: Iterable[Path],
    ) -> tuple[WorkspaceAccessRequest, str]:
        now = self._clock().astimezone(UTC)
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            changed = self._expire_rows(rows, now)
            matching: dict[str, Any] | None = None
            mismatch: dict[str, Any] | None = None
            for row in rows:
                request_id = _safe_text(row.get("request_id"), max_length=128)
                raw_row_path = _safe_text(row.get("path"))
                if not request_id or not raw_row_path:
                    continue
                try:
                    row_path = canonicalize_workspace(raw_row_path)
                except (OSError, RuntimeError, ValueError):
                    continue
                if supplied_request_id and request_id == supplied_request_id:
                    mismatch = row
                    if (
                        _safe_text(row.get("client_id"), max_length=128) == client_id
                        and row_path == path
                        and _safe_text(row.get("mode"), max_length=32) == mode
                    ):
                        matching = row
                    break
                if not supplied_request_id and (
                    _safe_text(row.get("client_id"), max_length=128) == client_id
                    and row_path == path
                    and _safe_text(row.get("mode"), max_length=32) == mode
                    and _safe_text(row.get("status"), max_length=32)
                    in {"pending", "approved", "active"}
                ):
                    matching = row
                    break
            if supplied_request_id and mismatch is not None and matching is None:
                request = self._request_from_row(mismatch)
                if changed:
                    self.store.save_requests(rows)
                return request, "mismatch"
            if matching is not None:
                request = self._request_from_row(matching)
                if request.status in {"consumed", "project-allowed"}:
                    if changed:
                        self.store.save_requests(rows)
                    return request, "consumed"
                if request.status == "active":
                    if changed:
                        self.store.save_requests(rows)
                    return request, "active"
                if request.status == "denied":
                    # A refusal is a decision, not a transient failure. Never
                    # re-notify for it, and report it as terminal so a caller
                    # stops retrying instead of reading it as "not yet".
                    if changed:
                        self.store.save_requests(rows)
                    return request, "denied"
                if _parse_time(request.expires_at) and _parse_time(request.expires_at) <= now:
                    if changed:
                        self.store.save_requests(rows)
                    return request, "expired"
                if request.status == "approved":
                    if changed:
                        self.store.save_requests(rows)
                    return request, "approved"
                notified_at = _parse_time(request.last_notified_at)
                if (
                    notified_at is None
                    or (now - notified_at).total_seconds() >= WORKSPACE_NOTIFICATION_COOLDOWN_SEC
                ):
                    matching["last_notified_at"] = _iso(now)
                    changed = True
                    notify = True
                else:
                    notify = False
                if changed:
                    self.store.save_requests(rows)
                request = self._request_from_row(matching)
                if notify:
                    self._notify(request)
                return request, "pending"

            # A supplied request id that does not match any stored row is
            # rejected rather than used to seed a new request.  Request ids
            # are always server-generated so callers cannot inject arbitrary
            # identifiers into the request store.
            if supplied_request_id:
                if changed:
                    self.store.save_requests(rows)
                synthetic = WorkspaceAccessRequest(
                    request_id=supplied_request_id,
                    client_id=client_id,
                    path=path,
                    mode=mode,
                    status="not-found",
                    created_at="",
                    expires_at="",
                )
                return synthetic, "mismatch"

            request_id = f"war-{uuid4().hex}"
            request = {
                "request_id": request_id,
                "client_id": client_id,
                "path": str(path),
                "mode": mode,
                "status": "pending",
                "created_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=self.one_time_ttl_sec)),
                "approved_at": "",
                "consumed_at": "",
                "last_notified_at": _iso(now),
                "authorization_grant_id": "",
                "task_scope_id": task_scope_id,
                "task_id": "",
                "activated_at": "",
                "released_at": "",
                "project_entry_id": "",
                "project_allowed_at": "",
            }
            rows.append(request)
            self.store.save_requests(rows)
            parsed = self._request_from_row(request)
            self._notify(parsed)
            return parsed, "pending"

    def _expire_rows(self, rows: list[dict[str, Any]], now: datetime) -> bool:
        changed = False
        for row in rows:
            if _safe_text(row.get("status"), max_length=32) not in {
                "pending",
                "approved",
                "active",
            }:
                continue
            expires = _parse_time(row.get("expires_at"))
            if expires is not None and expires <= now:
                row["status"] = "expired"
                changed = True
        return changed

    @staticmethod
    def _request_from_row(row: dict[str, Any]) -> WorkspaceAccessRequest:
        raw_path = _safe_text(row.get("path"))
        if not raw_path:
            raise ValueError("Workspace access request is missing its path.")
        return WorkspaceAccessRequest(
            request_id=_safe_text(row.get("request_id"), max_length=128),
            client_id=_safe_text(row.get("client_id"), max_length=128),
            path=canonicalize_workspace(raw_path),
            mode=_safe_text(row.get("mode"), max_length=32),
            status=_safe_text(row.get("status"), max_length=32),
            created_at=_safe_text(row.get("created_at")),
            expires_at=_safe_text(row.get("expires_at")),
            approved_at=_safe_text(row.get("approved_at")),
            consumed_at=_safe_text(row.get("consumed_at")),
            last_notified_at=_safe_text(row.get("last_notified_at")),
            authorization_grant_id=_safe_text(row.get("authorization_grant_id"), max_length=128),
            task_scope_id=_safe_text(row.get("task_scope_id"), max_length=128),
            task_id=_safe_text(row.get("task_id"), max_length=128),
            activated_at=_safe_text(row.get("activated_at")),
            released_at=_safe_text(row.get("released_at")),
            project_entry_id=_safe_text(row.get("project_entry_id"), max_length=128),
            project_allowed_at=_safe_text(row.get("project_allowed_at")),
        )

    def _consume_request(
        self,
        request_id: str,
        path: Path,
        mode: str,
        client_id: str,
    ) -> bool:
        with self._lock, self.store.transaction():
            # Re-read deny roots at the moment the grant is consumed. A deny
            # edit wins even if it races with an approval retry.
            if _first_containing_root(path, self._snapshot().denied) is not None:
                return False
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) != request_id:
                    continue
                request = self._request_from_row(row)
                expires = _parse_time(request.expires_at)
                if (
                    request.status != "approved"
                    or request.client_id != client_id
                    or request.path != path
                    or request.mode != mode
                    or (expires is not None and expires <= now)
                ):
                    return False
                row["status"] = "consumed"
                row["consumed_at"] = _iso(now)
                self.store.save_requests(rows)
                return True
        return False

    def _activate_request(
        self,
        request_id: str,
        path: Path,
        mode: str,
        client_id: str,
        task_scope_id: str,
    ) -> WorkspaceAccessRequest | None:
        """Claim an approved request for one concrete task.

        Approval and task admission are separate operations because the user
        acts in Notification Center while the caller retries later.  Turning
        the approved row into ``active`` under the store lock prevents two
        retries from using the same task approval concurrently.  The runner
        binds the returned grant to the actual Task id immediately after it
        creates the task.
        """
        with self._lock, self.store.transaction():
            if _first_containing_root(path, self._snapshot().denied) is not None:
                return None
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) != request_id:
                    continue
                request = self._request_from_row(row)
                expires = _parse_time(request.expires_at)
                if (
                    request.status != "approved"
                    or request.client_id != client_id
                    or request.path != path
                    or request.mode != mode
                ):
                    return None
                if expires is not None and expires <= now:
                    row["status"] = "expired"
                    self.store.save_requests(rows)
                    return None
                grant_id = f"wag-{uuid4().hex}"
                row["status"] = "active"
                row["authorization_grant_id"] = grant_id
                row["task_scope_id"] = task_scope_id
                row["task_id"] = ""
                row["activated_at"] = _iso(now)
                row["released_at"] = ""
                self.store.save_requests(rows)
                return self._request_from_row(row)
        return None

    def bind_task_authorization(self, grant_id: str, task_id: str) -> bool:
        """Bind an active approval claim to the task that will execute it."""
        grant_id = self._validate_request_value(grant_id, "authorization_grant_id")
        task_id = self._validate_request_value(task_id, "task_id")
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            for row in rows:
                if _safe_text(row.get("authorization_grant_id"), max_length=128) != grant_id:
                    continue
                request = self._request_from_row(row)
                if request.status != "active" or (request.task_id and request.task_id != task_id):
                    return False
                row["task_id"] = task_id
                self.store.save_requests(rows)
                return True
        return False

    def abort_task_authorization(self, grant_id: str, *, task_id: str = "") -> bool:
        """Return a claim to the approved state if task submission failed."""
        grant_id = self._validate_request_value(grant_id, "authorization_grant_id")
        normalized_task_id = self._validate_request_value(task_id, "task_id") if task_id else ""
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            for row in rows:
                if _safe_text(row.get("authorization_grant_id"), max_length=128) != grant_id:
                    continue
                request = self._request_from_row(row)
                if request.status != "active" or (
                    normalized_task_id and request.task_id and request.task_id != normalized_task_id
                ):
                    return False
                row["status"] = "approved"
                row["authorization_grant_id"] = ""
                row["task_scope_id"] = ""
                row["task_id"] = ""
                row["activated_at"] = ""
                row["released_at"] = _iso(now)
                self.store.save_requests(rows)
                return True
        return False

    def complete_task_authorization(self, grant_id: str, *, task_id: str = "") -> bool:
        """Retire a task approval as soon as its task publishes a result."""
        grant_id = self._validate_request_value(grant_id, "authorization_grant_id")
        normalized_task_id = self._validate_request_value(task_id, "task_id") if task_id else ""
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            for row in rows:
                if _safe_text(row.get("authorization_grant_id"), max_length=128) != grant_id:
                    continue
                request = self._request_from_row(row)
                if request.status != "active" or (
                    normalized_task_id and request.task_id and request.task_id != normalized_task_id
                ):
                    return False
                row["status"] = "consumed"
                row["consumed_at"] = _iso(now)
                row["released_at"] = _iso(now)
                self.store.save_requests(rows)
                return True
        return False

    def approve_request(
        self,
        request_id: str,
        *,
        path: str | Path | None = None,
        mode: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = self._validate_request_value(request_id, "authorization_request_id")
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) != request_id:
                    continue
                request = self._request_from_row(row)
                if path is not None and canonicalize_workspace(path) != request.path:
                    return {**request.to_public_dict(), "approved": False, "status": "mismatch"}
                if mode is not None and mode != request.mode:
                    return {**request.to_public_dict(), "approved": False, "status": "mismatch"}
                if client_id is not None and client_id != request.client_id:
                    return {**request.to_public_dict(), "approved": False, "status": "mismatch"}
                expires = _parse_time(request.expires_at)
                if expires is not None and expires <= now:
                    row["status"] = "expired"
                    self.store.save_requests(rows)
                    return {
                        **self._request_from_row(row).to_public_dict(),
                        "approved": False,
                        "status": "expired",
                    }
                if _first_containing_root(request.path, self._snapshot().denied) is not None:
                    row["status"] = "denied"
                    self.store.save_requests(rows)
                    return {
                        **self._request_from_row(row).to_public_dict(),
                        "approved": False,
                        "status": "denied",
                    }
                if request.status in {"active", "project-allowed"}:
                    return {**request.to_public_dict(), "approved": False, "status": request.status}
                if request.status in {"consumed", "denied", "expired"}:
                    return {**request.to_public_dict(), "approved": False, "status": request.status}
                row["status"] = "approved"
                row["approved_at"] = _iso(now)
                self.store.save_requests(rows)
                return {"approved": True, **self._request_from_row(row).to_public_dict()}
        return {"approved": False, "status": "not-found", "authorization_request_id": request_id}

    def deny_request(self, request_id: str) -> dict[str, Any]:
        request_id = self._validate_request_value(request_id, "authorization_request_id")
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) != request_id:
                    continue
                current = self._request_from_row(row)
                if current.status in {"active", "project-allowed", "consumed", "expired"}:
                    return {
                        **current.to_public_dict(),
                        "denied": False,
                        "status": current.status,
                    }
                row["status"] = "denied"
                self.store.save_requests(rows)
                return {"denied": True, **self._request_from_row(row).to_public_dict()}
        return {"denied": False, "status": "not-found", "authorization_request_id": request_id}

    def allow_request_as_project(
        self,
        request_id: str,
        *,
        path: str | Path | None = None,
        mode: str | None = None,
        client_id: str | None = None,
        access: str | None = None,
        key: str = "",
        default_executor: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Turn one exact request into a persistent App-managed entry.

        This is deliberately one locked operation from the caller's point of
        view.  Notification Center can therefore offer a direct “allow this
        project” action without leaving an approved request behind or making
        the next retry race a separate ``add`` and ``approve`` call.
        """
        request_id = self._validate_request_value(request_id, "authorization_request_id")
        normalized_client = (
            self._validate_request_value(client_id, "client_id") if client_id is not None else None
        )
        if mode is not None and mode not in {READ_ONLY, "workspace-write"}:
            raise ValueError(
                f"Unsupported workspace access mode: {mode}. Allowed: read-only, workspace-write"
            )
        if access is not None and access not in VALID_ACCESS_MODES:
            raise ValueError("access must be read-only or read-write")

        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            now = self._clock().astimezone(UTC)
            request_row: dict[str, Any] | None = None
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) == request_id:
                    request_row = row
                    break
            if request_row is None:
                return {
                    "project_allowed": False,
                    "approved": False,
                    "status": "not-found",
                    "authorization_request_id": request_id,
                }

            request = self._request_from_row(request_row)
            requested_path = canonicalize_workspace(path) if path is not None else request.path
            requested_mode = mode or request.mode
            if (
                requested_path != request.path
                or requested_mode != request.mode
                or (normalized_client is not None and normalized_client != request.client_id)
            ):
                return {**request.to_public_dict(), "project_allowed": False, "status": "mismatch"}

            if request.status == "project-allowed":
                existing_entry = next(
                    (
                        entry
                        for entry in self._load_app_entries()
                        if entry.id == request.project_entry_id or entry.path == request.path
                    ),
                    None,
                )
                if existing_entry is not None:
                    return {
                        "project_allowed": True,
                        "approved": True,
                        "entry": existing_entry.to_public_dict(),
                        "request": request.to_public_dict(),
                        **request.to_public_dict(),
                    }

            # Do not rewrite an active task's claim into a project entry from a
            # stale notification. The task owns this authorization until its
            # terminal result; a later click can be retried after it ends.
            if request.status == "active":
                return {
                    **request.to_public_dict(),
                    "project_allowed": False,
                    "approved": False,
                    "status": "active",
                }

            expires = _parse_time(request.expires_at)
            if expires is not None and expires <= now:
                request_row["status"] = "expired"
                self.store.save_requests(rows)
                return {
                    **self._request_from_row(request_row).to_public_dict(),
                    "project_allowed": False,
                    "status": "expired",
                }
            if _first_containing_root(request.path, self._snapshot().denied) is not None:
                request_row["status"] = "denied"
                self.store.save_requests(rows)
                return {
                    **self._request_from_row(request_row).to_public_dict(),
                    "project_allowed": False,
                    "status": "denied",
                }
            if request.status in {"denied", "expired", "consumed"}:
                return {
                    **request.to_public_dict(),
                    "project_allowed": False,
                    "status": request.status,
                }

            requested_access = access or (
                READ_WRITE if requested_mode == "workspace-write" else READ_ONLY
            )
            canonical = self._validate_entry(request.path, requested_access, default_executor)
            entries = self._load_app_entries()
            current = next((entry for entry in entries if entry.path == canonical), None)

            if current is None:
                normalized_key = self._project_entry_key(key, canonical, entries)
                entry_id = f"app-{uuid4().hex}"
                entry = WorkspaceAccessEntry(
                    id=entry_id,
                    key=normalized_key,
                    path=canonical,
                    access=requested_access,
                    source="app",
                    status="active",
                    default_executor=default_executor,
                    description=_safe_text(description),
                    sources=("app",),
                    app_entry_id=entry_id,
                )
                next_entries = [*entries, entry]
            else:
                next_access = (
                    READ_WRITE
                    if current.access == READ_WRITE or requested_access == READ_WRITE
                    else READ_ONLY
                )
                next_executor = default_executor or current.default_executor
                next_description = _safe_text(description) or current.description
                entry = WorkspaceAccessEntry(
                    id=current.id,
                    key=current.key,
                    path=current.path,
                    access=next_access,
                    source="app",
                    status="active",
                    default_executor=next_executor,
                    description=next_description,
                    sources=("app",),
                    app_entry_id=current.id,
                )
                next_entries = [entry if item.id == current.id else item for item in entries]

            # Persist the project before closing the request.  If a process is
            # interrupted between the two atomic files, retrying this command
            # is idempotent because the existing entry is found by path.
            self.store.save_entries([item.to_config_dict() for item in next_entries])
            request_row["status"] = "project-allowed"
            request_row["approved_at"] = request_row.get("approved_at") or _iso(now)
            request_row["project_entry_id"] = entry.id
            request_row["project_allowed_at"] = _iso(now)
            request_row["released_at"] = _iso(now)
            self.store.save_requests(rows)
            return {
                "project_allowed": True,
                "approved": True,
                "entry": entry.to_public_dict(),
                "request": self._request_from_row(request_row).to_public_dict(),
                **self._request_from_row(request_row).to_public_dict(),
            }

    def get_request(self, request_id: str) -> dict[str, Any]:
        """Return the public view of one authorization request.

        Expiry is applied on read so a caller polling a request that was never
        answered eventually observes "expired" instead of a stale "pending".
        """
        request_id = self._validate_request_value(request_id, "authorization_request_id")
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            if self._expire_rows(rows, self._clock().astimezone(UTC)):
                self.store.save_requests(rows)
            for row in rows:
                if _safe_text(row.get("request_id"), max_length=128) != request_id:
                    continue
                try:
                    return {"found": True, **self._request_from_row(row).to_public_dict()}
                except (OSError, RuntimeError, ValueError):
                    break
        return {
            "found": False,
            "authorization_request_id": request_id,
            "request_id": request_id,
            "status": "not-found",
            "pending": False,
        }

    def wait_for_request(
        self,
        request_id: str,
        *,
        timeout_sec: float = 0.0,
        poll_interval: float = REQUEST_WAIT_POLL_INTERVAL_SEC,
    ) -> dict[str, Any]:
        """Block until the request leaves "pending", or the wait elapses.

        The wait is a bounded poll rather than a condition variable because the
        decision is written by the desktop app in a different process. Sleeping
        happens outside the store lock so an approval can land while we wait.
        """
        view = self.get_request(request_id)
        if timeout_sec <= 0:
            return view
        deadline = time.monotonic() + timeout_sec
        interval = max(0.01, float(poll_interval))
        while view.get("status") == "pending":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
            view = self.get_request(request_id)
        return view

    def list_requests(self) -> list[dict[str, Any]]:
        with self._lock, self.store.transaction():
            rows = self.store.load_requests()
            changed = self._expire_rows(rows, self._clock().astimezone(UTC))
            if changed:
                self.store.save_requests(rows)
            result: list[dict[str, Any]] = []
            for row in rows:
                try:
                    result.append(self._request_from_row(row).to_public_dict())
                except (OSError, RuntimeError, ValueError):
                    continue
            return result

    def create_entry(
        self,
        *,
        path: str | Path,
        key: str = "",
        access: str = READ_WRITE,
        default_executor: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        canonical = self._validate_entry(path, access, default_executor)
        normalized_key = self._entry_key(key, canonical)
        with self._lock, self.store.transaction():
            entries = self._load_app_entries()
            if any(entry.key == normalized_key for entry in entries):
                raise ValueError(f"Workspace key already exists: {normalized_key}")
            if self._find_legacy_project(self._current_settings(), normalized_key) is not None:
                raise ValueError(
                    f"Workspace key already exists in legacy projects: {normalized_key}"
                )
            entry_id = f"app-{uuid4().hex}"
            entry = WorkspaceAccessEntry(
                id=entry_id,
                key=normalized_key,
                path=canonical,
                access=access,
                source="app",
                status="active",
                default_executor=default_executor,
                description=_safe_text(description),
                sources=("app",),
                app_entry_id=entry_id,
            )
            self.store.save_entries([item.to_config_dict() for item in [*entries, entry]])
            return entry.to_public_dict()

    def update_entry(
        self,
        entry_id: str,
        *,
        path: str | Path | None = None,
        key: str | None = None,
        access: str | None = None,
        default_executor: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        entry_id = self._validate_request_value(entry_id, "entry_id")
        with self._lock, self.store.transaction():
            entries = self._load_app_entries()
            current = next((entry for entry in entries if entry.id == entry_id), None)
            if current is None:
                raise KeyError(f"App workspace entry not found: {entry_id}")
            next_path = canonicalize_workspace(path) if path is not None else current.path
            next_access = access or current.access
            next_executor = (
                default_executor if default_executor is not None else current.default_executor
            )
            self._validate_entry(next_path, next_access, next_executor)
            next_key = self._entry_key(key if key is not None else current.key, next_path)
            if any(entry.id != entry_id and entry.key == next_key for entry in entries):
                raise ValueError(f"Workspace key already exists: {next_key}")
            if self._find_legacy_project(self._current_settings(), next_key) is not None:
                raise ValueError(f"Workspace key already exists in legacy projects: {next_key}")
            updated = WorkspaceAccessEntry(
                id=current.id,
                key=next_key,
                path=next_path,
                access=next_access,
                source="app",
                status="active",
                default_executor=next_executor,
                description=_safe_text(
                    description if description is not None else current.description
                ),
                sources=("app",),
                app_entry_id=current.id,
            )
            self.store.save_entries(
                [
                    item.to_config_dict() if item.id != entry_id else updated.to_config_dict()
                    for item in entries
                ]
            )
            return updated.to_public_dict()

    def delete_entry(self, entry_id: str) -> dict[str, Any]:
        entry_id = self._validate_request_value(entry_id, "entry_id")
        with self._lock, self.store.transaction():
            entries = self._load_app_entries()
            current = next((entry for entry in entries if entry.id == entry_id), None)
            if current is None:
                raise KeyError(f"App workspace entry not found: {entry_id}")
            remaining = [entry for entry in entries if entry.id != entry_id]
            self.store.save_entries([item.to_config_dict() for item in remaining])
            snapshot = self._snapshot()
            remaining_authorization = self._remaining_authorization(snapshot, current.path)
            permission_still_effective = remaining_authorization is not None
            return {
                "deleted": True,
                "id": entry_id,
                "path": str(current.path),
                "removed_access": current.access,
                "permission_still_effective": permission_still_effective,
                "remaining_access": (
                    remaining_authorization.access if remaining_authorization is not None else ""
                ),
                "remaining_source": (
                    remaining_authorization.source if remaining_authorization is not None else ""
                ),
                "remaining_policy": (
                    remaining_authorization.policy if remaining_authorization is not None else ""
                ),
                "remaining_root": self._remaining_authorization_root(
                    snapshot,
                    current.path,
                    remaining_authorization,
                ),
                "message": (
                    "App entry removed; permission remains effective through another "
                    "configuration source."
                    if permission_still_effective
                    else "App workspace entry removed."
                ),
            }

    def _remaining_authorization(
        self,
        snapshot: _Snapshot,
        path: Path,
    ) -> WorkspaceAuthorization | None:
        """Return the strongest permission still effective after an App entry is removed."""
        for mode in ("workspace-write", READ_ONLY):
            authorization = self._authorize_path(
                snapshot,
                path,
                mode,
                project=None,
                client_id="settings",
            )
            if authorization.allowed:
                return authorization
        return None

    @staticmethod
    def _remaining_authorization_root(
        snapshot: _Snapshot,
        path: Path,
        authorization: WorkspaceAuthorization | None,
    ) -> str:
        if authorization is None:
            return ""
        if authorization.policy == "trusted-git-read":
            root = _first_containing_root(path, snapshot.trusted_roots)
            return str(root) if root is not None else ""
        matching = [
            grant
            for grant in snapshot.grants
            if grant.source == authorization.source
            and _is_within(path, grant.path)
            and (READ_ONLY in grant.modes or "workspace-write" in grant.modes)
        ]
        if not matching:
            return ""
        return str(max(matching, key=lambda item: len(item.path.parts)).path)

    def list_workspaces(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        grouped: dict[Path, list[WorkspaceAccessEntry]] = {}
        for entry in [*snapshot.legacy_entries, *snapshot.entries]:
            grouped.setdefault(entry.path, []).append(entry)
        rows: list[WorkspaceAccessEntry] = []
        for path, entries in grouped.items():
            app = next((entry for entry in entries if entry.app_entry_id), None)
            sources = tuple(
                dict.fromkeys(
                    source for entry in entries for source in entry.sources or (entry.source,)
                )
            )
            denied = _first_containing_root(path, snapshot.denied) is not None
            missing = not path.exists() or not path.is_dir()
            access = (
                READ_WRITE if any(entry.access == READ_WRITE for entry in entries) else READ_ONLY
            )
            status = "denied" if denied else ("missing" if missing else "active")
            if not app and any(entry.status == "discovery" for entry in entries):
                status = "discovery"
            source = ", ".join(sources)
            rows.append(
                WorkspaceAccessEntry(
                    id=app.id
                    if app
                    else f"legacy-{hashlib.sha256(str(path).encode()).hexdigest()[:16]}",
                    key=app.key
                    if app
                    else next((entry.key for entry in entries if entry.key), path.name),
                    path=path,
                    access=access,
                    source=source,
                    status=status,
                    default_executor=app.default_executor
                    if app
                    else next(
                        (entry.default_executor for entry in entries if entry.default_executor), ""
                    ),
                    description=app.description if app else "",
                    sources=sources,
                    app_entry_id=app.id if app else "",
                )
            )
        autoping_path = canonicalize_workspace(snapshot.data_dir / "autoping_workspace")
        if autoping_path.exists() and autoping_path not in grouped:
            rows.append(
                WorkspaceAccessEntry(
                    id=f"legacy-{hashlib.sha256(str(autoping_path).encode()).hexdigest()[:16]}",
                    key="autoping",
                    path=autoping_path,
                    access=READ_WRITE,
                    source="autoping",
                    status="active",
                    sources=("autoping",),
                )
            )
        rows.sort(key=lambda entry: (entry.path.as_posix().lower(), entry.key.lower()))
        return {
            "version": WORKSPACE_ACCESS_CONFIG_VERSION,
            "config_path": str(self.store.config_path),
            "workspaces": [entry.to_public_dict() for entry in rows],
            "runtime_context": snapshot.runtime_context,
            "pending_requests": self.list_requests(),
        }

    def _validate_entry(self, path: str | Path, access: str, default_executor: str) -> Path:
        canonical = canonicalize_workspace(path)
        if not canonical.exists() or not canonical.is_dir():
            raise ValueError(f"Workspace does not exist or is not a directory: {canonical}")
        if access not in VALID_ACCESS_MODES:
            raise ValueError("access must be read-only or read-write")
        if default_executor and default_executor not in PROJECT_EXECUTORS:
            raise ValueError(f"Unsupported default executor: {default_executor}")
        return canonical

    @staticmethod
    def _entry_key(key: str, path: Path) -> str:
        normalized = _safe_text(key, max_length=128)
        if not normalized:
            normalized = f"workspace-{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
        if not _KEY_RE.fullmatch(normalized):
            raise ValueError("key must use letters, digits, '.', '-', or '_'")
        return normalized

    def _project_entry_key(
        self,
        key: str,
        path: Path,
        entries: Iterable[WorkspaceAccessEntry],
    ) -> str:
        """Choose a readable path-based key while keeping key collisions safe."""
        used = {entry.key for entry in entries}
        candidate = _safe_text(key, max_length=128) or path.name
        if candidate and _KEY_RE.fullmatch(candidate):
            if (
                candidate not in used
                and self._find_legacy_project(self._current_settings(), candidate) is None
            ):
                return candidate
            if key:
                raise ValueError(f"Workspace key already exists: {candidate}")
        generated = self._entry_key("", path)
        if (
            generated in used
            or self._find_legacy_project(self._current_settings(), generated) is not None
        ):
            raise ValueError(f"Workspace key already exists: {generated}")
        return generated

    def _notify(self, request: WorkspaceAccessRequest) -> None:
        try:
            self._notification_queue(request)
        except Exception:
            # Authorization remains visible in the store even if Notification
            # Center is unavailable.
            return

    def _queue_notification(self, request: WorkspaceAccessRequest) -> bool:
        return macos_notify.queue(
            self.store.data_dir,
            "Workspace access requested",
            f"{request.mode}: {request.path}",
            kind="workspace_access_request",
            authorization_request_id=request.request_id,
            client_id=request.client_id,
            workspace=str(request.path),
            mode=request.mode,
        )
