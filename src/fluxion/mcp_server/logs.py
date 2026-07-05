from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.subagent import compact_tail


def _log_progress(*, task_id: str, data_dir: Path) -> dict[str, Any]:
    candidates = [
        ("task_log", data_dir / "logs" / f"task-{task_id}.log"),
        ("executor_log", data_dir / "logs" / f"task-{task_id}.agy.log"),
        ("codex_log", data_dir / "logs" / f"task-{task_id}.codex.log"),
        ("claude_log", data_dir / "logs" / f"task-{task_id}.claude.log"),
    ]
    existing: list[tuple[str, Path, float]] = []
    for source, path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        existing.append((source, path, stat.st_mtime))
    if not existing:
        return {}
    source, path, _ = max(existing, key=lambda item: item[2])
    try:
        stat = path.stat()
        text = _read_text_tail(path, max_bytes=64_000)
    except OSError:
        return {}
    tail, truncated = compact_tail(_human_log_tail(text), max_lines=20, max_chars=4000)
    return {
        "progress_source": source,
        "recent_output_tail": tail,
        "recent_output_tail_truncated": truncated,
        "log_file": str(path),
        "log_size": stat.st_size,
        "log_updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _read_text_tail(path: Path, *, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read(max_bytes)
    return data.decode("utf-8", errors="replace")


# Antigravity (agy) runs a Go language server that emits glog chatter into the
# executor log, e.g.
#   I0613 11:48:13.820131 11683 http_helpers.go:186] URL: https://...
#   E0613 11:46:47.005054 11683 log.go:398] error getting token source: ...
# These are the executor *runtime's* internal logs (transport, cache, auth) at
# every level (I/W/E/F) — they're plumbing, not the sub-agent's task output, and
# carry nothing a caller acts on. The caller judges success/failure from the
# structured status (status / success / exit_code / summary), NOT from this tail,
# so dropping these can't mask a real failure. Only the agent's own (non-glog)
# output is kept. The full timestamped glog signature is required to match, so
# the agent merely mentioning a `foo.go:42` in prose won't be filtered.
_GLOG_NOISE_RE = re.compile(
    r"^(?:agy:\s*)?[IWEF]\d{4}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+\S+\.go:\d+\]"
)


def _is_glog_noise(line: str) -> bool:
    return bool(_GLOG_NOISE_RE.match(line))


def _human_log_tail(text: str) -> str:
    rows: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _is_glog_noise(stripped):
            continue
        if not stripped.startswith("{"):
            rows.append(raw)
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            rows.append(raw)
            continue
        body = entry.get("body")
        if body is None:
            command = entry.get("command")
            if command:
                rows.append(f"command: {command}")
            continue
        # The task log wraps each executor line as JSON, so glog transport noise
        # arrives inside `body` (not as a raw line) — filter it here too.
        if _is_glog_noise(str(body).strip()):
            continue
        stream = entry.get("stream") or "stdout"
        rows.append(f"{stream}: {body}")
    return "\n".join(rows)
