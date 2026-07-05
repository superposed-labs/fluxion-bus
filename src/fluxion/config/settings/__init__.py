"""Application settings loaded from the environment / .env file.

Split into submodules — env (dotenv + env-file IO), models (ProjectConfig,
WorkspaceAuthorization), parsing (env-string parsers), core (the Settings
dataclass). The public surface is re-exported here so existing
``from fluxion.config.settings import ...`` imports keep working.
"""

from __future__ import annotations

from fluxion.config.settings.core import Settings
from fluxion.config.settings.env import (
    env_file_path,
    env_file_write_path,
    update_env_values,
)
from fluxion.config.settings.models import (
    PROJECT_EXECUTORS,
    ProjectConfig,
    WorkspaceAuthorization,
)

__all__ = [
    "PROJECT_EXECUTORS",
    "ProjectConfig",
    "Settings",
    "WorkspaceAuthorization",
    "env_file_path",
    "env_file_write_path",
    "update_env_values",
]
