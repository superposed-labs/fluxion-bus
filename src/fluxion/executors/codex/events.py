from __future__ import annotations

import json
import re
import shlex
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any


@dataclass(frozen=True)
class CodexEventCapture:
    is_jsonl: bool = False
    final_message: str = ""
    session_id: str = ""
    changed_files: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""


def extract_codex_json_stream_message(stdout: str) -> str:
    """Return the latest user-facing Codex JSONL agent message, if complete."""
    message = ""
    for event in _iter_jsonl_events(stdout):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "agent_message":
            continue
        text = str(item.get("text") or "").strip()
        if _has_final_answer_marker(text):
            message = text
    return message


def extract_codex_json_stream_text(stdout: str) -> str:
    """Return all agent-message text so far, with no FINAL_ANSWER gate.

    The raw-prompt counterpart to ``extract_codex_json_stream_message``; see
    ``fluxion.executors.claude.events.extract_claude_stream_text`` for why the
    gated reader cannot serve a caller that supplies its own prompt.
    """
    parts: list[str] = []
    for event in _iter_jsonl_events(stdout):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "agent_message":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def extract_codex_stream_reasoning(stdout: str) -> str:
    """The agent's visible working, from `codex exec --json`.

    Codex reports its reasoning already summarized (`ReasoningItem` is
    documented as "Agent's reasoning summary"), unlike Claude which streams raw
    thinking — so a codex-backed sub-agent reads much closer to a native one.

    Items arrive as `item.started` / `item.updated` / `item.completed` for the
    *same* id, so they are collected by id and the latest version of each wins.
    Appending on every event would repeat each command two or three times.
    """
    seen: OrderedDict[str, str] = OrderedDict()
    for event in _iter_jsonl_events(stdout):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        rendered = _describe_codex_item(item)
        if not item_id or not rendered:
            continue
        seen[item_id] = rendered
    return "\n\n".join(seen.values())


def _describe_codex_item(item: dict[str, Any]) -> str:
    """One line of working for a thread item, or "" when it is not working.

    `agent_message` is deliberately excluded: that is the answer, and it travels
    on the other channel.
    """
    kind = str(item.get("type") or "")
    if kind == "reasoning":
        return str(item.get("text") or "").strip()
    if kind == "command_execution":
        command = str(item.get("command") or "").strip().replace("\n", " ")
        return f"$ {_clip_subject(command)}" if command else ""
    if kind == "file_change":
        changes = item.get("changes")
        if not isinstance(changes, list):
            return ""
        paths = [
            str(change.get("path") or "").strip()
            for change in changes
            if isinstance(change, dict) and change.get("path")
        ]
        return "Edit " + ", ".join(_clip_subject(p, keep_tail=True) for p in paths) if paths else ""
    if kind == "web_search":
        query = str(item.get("query") or "").strip()
        return f"Search({_clip_subject(query)})" if query else ""
    if kind == "mcp_tool_call":
        server = str(item.get("server") or "").strip()
        tool = str(item.get("tool") or "").strip()
        return f"{server}.{tool}" if tool else ""
    return ""


def _clip_subject(subject: str, *, keep_tail: bool = False, limit: int = 80) -> str:
    if keep_tail and subject.startswith("/"):
        subject = "/".join(PurePath(subject).parts[-2:])
    if len(subject) <= limit:
        return subject
    return subject[: limit - 3] + "..."


def parse_codex_json_events(stdout: str, *, workspace: Path) -> CodexEventCapture:
    events = _load_jsonl_events(stdout)
    if not events:
        return CodexEventCapture()

    workspace = workspace.resolve()
    changed_files: OrderedDict[str, None] = OrderedDict()
    operations: list[dict[str, Any]] = []
    final_message = ""
    session_id = ""
    saw_structured_file_change = False
    saw_mutating_shell = False

    error_message = ""
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            session_id = str(event.get("thread_id") or "").strip()

        if event_type == "error":
            error_message = _clean_error_message(event.get("message")) or error_message
        elif event_type == "turn.failed":
            err = event.get("error")
            if isinstance(err, dict):
                error_message = _clean_error_message(err.get("message")) or error_message

        item = event.get("item")
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "")
        if item_type == "error":
            error_message = _clean_error_message(item.get("message")) or error_message
            continue
        if item_type == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                final_message = text
            continue

        if item_type == "file_change":
            if str(item.get("status") or "") not in {"", "completed"}:
                continue
            changes = item.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                rel_path = _normalize_workspace_path(change.get("path"), workspace=workspace)
                if rel_path:
                    saw_structured_file_change = True
                    changed_files.setdefault(rel_path, None)
                    op = _codex_op(change.get("kind"))
                    if op:
                        operations.append({"op": op, "path": rel_path})
            continue

        if item_type == "command_execution":
            command = str(item.get("command") or "")
            if _command_is_clearly_mutating(command):
                saw_mutating_shell = True

    risk_flags: list[str] = []
    if saw_mutating_shell and not saw_structured_file_change:
        risk_flags.extend(["shell_side_effects_detected", "changed_files_may_be_incomplete"])

    return CodexEventCapture(
        is_jsonl=True,
        final_message=final_message,
        session_id=session_id,
        changed_files=list(changed_files.keys()),
        risk_flags=_dedupe(risk_flags),
        operations=operations,
        error_message=error_message,
    )


def _clean_error_message(raw: Any, _depth: int = 0) -> str:
    """Extract a human-readable error from a Codex error payload. The message is
    often itself a JSON string wrapping {"error": {"message": ...}}, so unwrap a
    couple of levels and fall back to the raw text."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if _depth < 3 and text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _truncate(text)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                return _clean_error_message(err["message"], _depth + 1)
            if isinstance(data.get("message"), str):
                return _clean_error_message(data["message"], _depth + 1)
        return _truncate(text)
    return _truncate(text)


def _truncate(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _codex_op(kind: Any) -> str:
    # Codex file_change only carries the change kind, never the prior bytes, so
    # add -> revert-by-delete; update/delete are recorded but unrecoverable.
    mapping = {"add": "add", "update": "update", "delete": "delete"}
    return mapping.get(str(kind or "").strip().lower(), "update")


def _load_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return []

    events: list[dict[str, Any]] = []
    json_lines = 0
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            json_lines += 1
            events.append(value)

    # Treat stdout as Codex JSONL only when every non-empty line parsed. Mixed
    # output is usually plain executor text that happens to contain JSON.
    if json_lines != len(lines):
        return []
    return events


def _iter_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _has_final_answer_marker(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*FINAL_ANSWER:?\s*$", text or ""))


def _normalize_workspace_path(raw_path: Any, *, workspace: Path) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ""
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        resolved = path.resolve(strict=False)
        rel = resolved.relative_to(workspace)
    except Exception:
        return ""
    value = rel.as_posix()
    if not value or value.startswith("../"):
        return ""
    return value


def _command_is_clearly_mutating(command: str) -> bool:
    text = _unwrap_shell_command(command)
    if not text:
        return False
    lowered = text.lower()
    patterns = [
        r"(^|[;&|]\s*)(rm|mv|cp|touch|mkdir|rmdir|ln)\b",
        r"(^|[;&|]\s*)(sed|perl)\s+[^;&|]*\s-[^\s;&|]*i",
        r"(^|[;&|]\s*)git\s+(apply|checkout|clean|mv|rm|reset|restore)\b",
        r"(^|[;&|]\s*)apply_patch\b",
        r"(^|[;&|]\s*)python[0-9.]*\s+-c\s+['\"][^'\"]*(write_text|open\([^)]*,\s*['\"]w|unlink\()",
        r"(^|[;&|]\s*)node\s+-e\s+['\"][^'\"]*(writefile|rmSync|unlinkSync)",
    ]
    if any(re.search(pattern, lowered) for pattern in patterns):
        return True
    return bool(re.search(r"(^|[^<])>>?\s*[^&\s]", text))


def _unwrap_shell_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
    except ValueError:
        return text
    if (
        len(parts) >= 3
        and Path(parts[0]).name in {"sh", "bash", "zsh"}
        and parts[1]
        in {
            "-c",
            "-lc",
        }
    ):
        return parts[2]
    return text


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
