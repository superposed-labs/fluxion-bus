from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _is_jsonl(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("{")
    return False


def _parse_jsonl(
    content: str, *, fold_extra_streams: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Parse the structured format written by executors/common/log_writer.py.

    First non-empty line is the header (no `stream` field, ignored here).
    Subsequent lines each carry `stream` / `lvl` / `body`.

    Streams other than stdout/stderr (antigravity's `agy` runtime log) are
    folded into stdout by default, so the UI surfaces them without needing a
    per-stream tab. Callers that must keep them apart — an MCP client, where a
    few hundred lines of runtime plumbing would bury the agent's own output —
    pass ``fold_extra_streams=False`` and read the third return value.
    """
    stdout: list[dict[str, Any]] = []
    stderr: list[dict[str, Any]] = []
    extra: dict[str, list[dict[str, Any]]] = {}
    stdout_idx = 0
    stderr_idx = 0
    for raw in content.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        stream = entry.get("stream")
        if stream is None:
            # Header line — skip silently.
            continue
        body = entry.get("body", "")
        lvl = entry.get("lvl", "out")
        if stream == "stderr":
            stderr.append({"t": stderr_idx * 100, "lvl": lvl, "body": body})
            stderr_idx += 1
        elif stream == "stdout" or fold_extra_streams:
            stdout.append({"t": stdout_idx * 100, "lvl": lvl, "body": body})
            stdout_idx += 1
        else:
            rows = extra.setdefault(str(stream), [])
            rows.append({"t": len(rows) * 100, "lvl": lvl, "body": body})
    return stdout, stderr, extra


# ── Legacy [stdout]/[stderr] format readers ────────────────────────
# Kept so we don't have to migrate the existing data/logs/task-*.log
# files; new tasks write JSONL and these branches drop out naturally.
def _parse_legacy(log_path: Path) -> tuple[list[str], list[str]]:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    current_section: str | None = None
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed == "[stdout]":
            current_section = "stdout"
            continue
        if trimmed == "[stderr]":
            current_section = "stderr"
            continue
        if line.startswith("task_id=") or line.startswith("command="):
            continue
        if current_section == "stdout":
            stdout_lines.append(line)
        elif current_section == "stderr":
            stderr_lines.append(line)
    return stdout_lines, stderr_lines


def _classify_stdout(line: str) -> str:
    lower = line.lower()
    if "tool/" in lower or "tool.read" in lower or "tool.write" in lower:
        return "tool"
    if "running" in lower or "starting" in lower or "resumed" in lower:
        return "info"
    if line.startswith(("fluxion ", "exec ", "pnpm ", "git ")):
        return "cmd"
    return "out"


def _classify_stderr(line: str) -> str:
    lower = line.lower()
    if "error" in lower or "fail" in lower:
        return "error"
    if "warn" in lower:
        return "warn"
    return "info"


def _coerce_fallback(value: Any) -> list[str]:
    if isinstance(value, str):
        return value.splitlines()
    if isinstance(value, list):
        return [str(x) for x in value]
    return []


def load_task_logs(
    log_path: Path,
    *,
    fallback_stdout: Any = "",
    fallback_stderr: Any = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """stdout/stderr for the UI, with any executor runtime log folded into stdout."""
    stdout, stderr, _extra = load_task_streams(
        log_path,
        fallback_stdout=fallback_stdout,
        fallback_stderr=fallback_stderr,
    )
    return stdout, stderr


def load_task_streams(
    log_path: Path,
    *,
    fallback_stdout: Any = "",
    fallback_stderr: Any = "",
    fold_extra_streams: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Same as ``load_task_logs`` but can keep executor runtime logs separate."""
    if log_path.exists():
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        if content and _is_jsonl(content):
            return _parse_jsonl(content, fold_extra_streams=fold_extra_streams)

    stdout_lines, stderr_lines = _parse_legacy(log_path)
    if not stdout_lines and not stderr_lines:
        stdout_lines = _coerce_fallback(fallback_stdout)
        stderr_lines = _coerce_fallback(fallback_stderr)

    formatted_stdout = [
        {"t": i * 100, "lvl": _classify_stdout(line), "body": line}
        for i, line in enumerate(stdout_lines)
    ]
    formatted_stderr = [
        {"t": i * 100, "lvl": _classify_stderr(line), "body": line}
        for i, line in enumerate(stderr_lines)
    ]
    return formatted_stdout, formatted_stderr, {}
