from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from fluxion.workspace.change_set import ChangeSet, FileChange, change_set_from_dict

_HUNK_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


def load_diff_hunks(change_set_file: str) -> dict[str, list[dict[str, Any]]]:
    if not change_set_file:
        return {}
    try:
        path = Path(change_set_file).expanduser()
        data = json.loads(path.read_text(encoding="utf-8"))
        change_set = change_set_from_dict(data)
    except Exception:
        return {}
    return diff_hunks_from_change_set(change_set)


def diff_hunks_from_change_set(change_set: ChangeSet) -> dict[str, list[dict[str, Any]]]:
    hunks: dict[str, list[dict[str, Any]]] = {}
    for change in change_set.files:
        file_hunks = _file_hunks(change)
        if file_hunks:
            hunks[change.path] = file_hunks
    return hunks


def summarize_diff_hunks(
    hunks: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, int | str]]:
    stats: dict[str, dict[str, int | str]] = {}
    for path, lines in hunks.items():
        additions = sum(1 for line in lines if line.get("type") == "add")
        deletions = sum(1 for line in lines if line.get("type") == "del")
        op = "M"
        if additions and not deletions:
            op = "A"
        elif deletions and not additions:
            op = "D"
        stats[path] = {"op": op, "additions": additions, "deletions": deletions}
    return stats


def _file_hunks(change: FileChange) -> list[dict[str, Any]]:
    if change.skipped_reason:
        return [
            {
                "type": "ctx",
                "text": f"Diff not available: {change.skipped_reason}",
            }
        ]
    if change.old_content is not None or change.new_content is not None:
        old = (change.old_content or "").splitlines()
        new = (change.new_content or "").splitlines()
        return _parse_unified_diff(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
                lineterm="",
            )
        )
    if change.edits:
        return _fragment_hunks(change.edits)
    return [
        {
            "type": "ctx",
            "text": f"Diff not available for {change.change_type or 'this change'}",
        }
    ]


def _parse_unified_diff(lines: Any) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    old_line = 0
    new_line = 0
    for raw in lines:
        line = str(raw)
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if match:
                old_line = int(match.group("old"))
                new_line = int(match.group("new"))
            hunks.append({"type": "hunk", "text": line})
            continue
        if not line:
            hunks.append({"type": "ctx", "n1": old_line, "n2": new_line, "text": ""})
            old_line += 1
            new_line += 1
            continue
        prefix = line[0]
        text = line[1:]
        if prefix == "+":
            hunks.append({"type": "add", "n2": new_line, "text": text})
            new_line += 1
        elif prefix == "-":
            hunks.append({"type": "del", "n1": old_line, "text": text})
            old_line += 1
        else:
            hunks.append({"type": "ctx", "n1": old_line, "n2": new_line, "text": text})
            old_line += 1
            new_line += 1
    return hunks


def _fragment_hunks(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    for index, edit in enumerate(edits, start=1):
        old = edit.get("old")
        new = edit.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        hunks.append({"type": "hunk", "text": f"@@ edit fragment {index} @@"})
        for line in old.splitlines() or [""]:
            hunks.append({"type": "del", "text": line})
        for line in new.splitlines() or [""]:
            hunks.append({"type": "add", "text": line})
    return hunks
