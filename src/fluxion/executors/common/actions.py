"""Parsing for the ACTIONS_JSON block agents emit after their final answer.

Every executor prompts for the same trailing structure::

    FINAL_ANSWER:
    <text>
    ACTIONS_JSON:
    {"upload_files": []}

``upload_files`` is how an agent *declares* files it wants delivered — it is an
explicit request channel, not an inventory of everything the run produced. The
prompt asks for it only when the user asked for files to be sent, so an empty
list is the normal case, and files created as a side effect of the work show up
in ``changed_files`` instead.

Codex and Claude each grew their own copy of this parsing; Antigravity never got
one, so its runs silently dropped whatever the agent declared.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fluxion.workspace.artifact_collector import select_uploadable_paths


def find_last_marker(text: str, marker: str) -> re.Match[str] | None:
    """Last line that is exactly ``MARKER`` or ``MARKER:``, ignoring indent."""
    matches = list(re.finditer(rf"(?m)^\s*{re.escape(marker)}:?\s*$", text or ""))
    return matches[-1] if matches else None


def extract_actions_json(text: str) -> dict[str, Any] | None:
    """Parse the trailing ACTIONS_JSON object, unwrapping a ``` fence if present."""
    body = text or ""
    marker_match = find_last_marker(body, "ACTIONS_JSON")
    if marker_match is None:
        return None
    tail = body[marker_match.end() :].strip()
    if not tail:
        return None
    if tail.startswith("```"):
        tail = _strip_code_fence(tail)
        if not tail:
            return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(tail)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def upload_paths(actions: dict[str, Any] | None) -> list[str]:
    """The declared ``upload_files`` entries, de-duplicated, order preserved.

    Accepts both bare strings and ``{"path": ...}`` objects, since agents
    produce either.
    """
    if not isinstance(actions, dict):
        return []
    raw = actions.get("upload_files")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = ""
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get("path", "")).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(value)
    return paths


def resolve_uploads_from_text(*, text: str, workspace: Path, max_files: int) -> list[str]:
    """Declared upload paths from raw agent output, resolved against the workspace."""
    paths = upload_paths(extract_actions_json(text))
    if not paths:
        return []
    return select_uploadable_paths(
        workspace=workspace,
        raw_paths=paths,
        max_files=max_files,
    )


def _strip_code_fence(tail: str) -> str:
    lines = tail.splitlines()[1:]
    content: list[str] = []
    for line in lines:
        if line.strip().startswith("```"):
            break
        content.append(line)
    return "\n".join(content).strip()
