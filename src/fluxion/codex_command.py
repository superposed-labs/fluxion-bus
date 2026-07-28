from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

CODEX_COMMAND_CANDIDATES = (
    "~/.local/bin/codex",
    "~/bin/codex",
    "/usr/local/bin/codex",
    "/opt/homebrew/bin/codex",
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
)


def resolve_command(command: str, candidates: Iterable[str]) -> str | None:
    resolved = shutil.which(command)
    if resolved:
        return resolved
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def resolve_codex_command() -> str | None:
    return resolve_command("codex", CODEX_COMMAND_CANDIDATES)
