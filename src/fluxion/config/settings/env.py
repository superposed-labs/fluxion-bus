from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv_file(env_path: Path, *, override: bool = False) -> None:
    if not env_path.exists() or not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Startup uses setdefault so the real process environment wins over the
        # file; reload uses override so the file (which the settings UI writes)
        # wins, letting a daemon pick up edits without a restart.
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _load_dotenv() -> None:
    explicit_env = os.environ.get("FLUXION_ENV_FILE", "").strip()
    if explicit_env:
        _load_dotenv_file(Path(explicit_env).expanduser())
        return

    workspace_root = os.environ.get("FLUXION_WORKSPACE_ROOT", "").strip()
    candidates: list[Path] = []
    if workspace_root:
        candidates.append(Path(workspace_root).expanduser() / ".env")
    candidates.append(Path(".env"))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _load_dotenv_file(candidate)


def load_dotenv() -> None:
    """Populate the environment from the `.env` file, without a full settings load.

    `Settings.load()` does this on the way to building the whole settings object.
    Components with their own environment-backed settings — the provider gateway —
    need the file read but not the object built, and a user who put a key in
    `.env` expects every entry point to see it, not only the ones that happen to
    construct `Settings`.
    """
    _load_dotenv()


def env_file_path() -> Path | None:
    """Resolve the .env file settings are loaded from, or None if absent.
    Mirrors the precedence in _load_dotenv (explicit FLUXION_ENV_FILE first).
    Used by long-running daemons to watch the file for changes."""
    explicit_env = os.environ.get("FLUXION_ENV_FILE", "").strip()
    if explicit_env:
        path = Path(explicit_env).expanduser()
        return path if path.exists() else None
    workspace_root = os.environ.get("FLUXION_WORKSPACE_ROOT", "").strip()
    candidates: list[Path] = []
    if workspace_root:
        candidates.append(Path(workspace_root).expanduser() / ".env")
    candidates.append(Path(".env"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def env_file_write_path() -> Path:
    """Resolve the .env file to write settings to. Mirrors env_file_path()'s
    precedence but always returns a target, even when no file exists yet, so a
    first-time write lands where the loader will later read from."""
    explicit_env = os.environ.get("FLUXION_ENV_FILE", "").strip()
    if explicit_env:
        return Path(explicit_env).expanduser()
    workspace_root = os.environ.get("FLUXION_WORKSPACE_ROOT", "").strip()
    if workspace_root:
        return Path(workspace_root).expanduser() / ".env"
    return Path(".env")


def update_env_values(updates: dict[str, str]) -> Path:
    """Persist key=value settings to the .env file and the live process env.

    Mirrors the macOS app's saveEnv: existing keys are rewritten in place,
    new keys are appended, and the file is written atomically. The current
    process's os.environ is updated too so a follow-up read sees the change
    without a reload; the scheduler daemon hot-reloads .env on its own."""
    path = env_file_write_path()
    remaining = dict(updates)
    lines: list[str] = []
    if path.exists() and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                key = raw.split("=", 1)[0].strip()
                if key in remaining:
                    lines.append(f"{key}={remaining.pop(key)}")
                    continue
            lines.append(line)
    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    for key, value in updates.items():
        os.environ[key] = value
    return path
