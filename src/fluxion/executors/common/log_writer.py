"""Structured (JSONL) per-task log writer shared by every executor.

File layout (one JSON object per line):

    Header (always first line, no `stream` field):
        {"v":1,"task_id":...,"command":"...","started_at":"<ISO>"}

    Entries (one per captured line):
        {"ts":"<ISO>","stream":"stdout"|"stderr"|<custom>,"lvl":"...","body":"..."}

`ts` is currently set to the moment _write_log is called (effectively the
task end-time) because the existing executors collect stdout/stderr in
memory and flush at the end. Per-line wall-clock timestamps require a
streaming refactor — out of scope for this change.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

LOG_FORMAT_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Per-stream line classifiers. Each returns the level token written into
# every entry's `lvl` field. Keep the rules intentionally simple — they
# match the heuristics the web layer used to apply at read time, just
# moved to where they belong (the executor knows what command it ran).
def _classify_stdout(line: str) -> str:
    lower = line.lower()
    if "tool/" in lower or "tool.read" in lower or "tool.write" in lower:
        return "tool"
    if "running" in lower or "starting" in lower or "resumed" in lower:
        return "info"
    if line.startswith(("fluxion ", "exec ", "pnpm ", "git ", "codex ", "claude ", "agy ")):
        return "cmd"
    return "out"


def _classify_stderr(line: str) -> str:
    lower = line.lower()
    if "error" in lower or "fail" in lower:
        return "error"
    if "warn" in lower:
        return "warn"
    return "info"


_CLASSIFIERS = {
    "stdout": _classify_stdout,
    "stderr": _classify_stderr,
}


def _entries_for_stream(stream: str, text: str, ts: str) -> Iterable[dict[str, object]]:
    if not text:
        return
    classify = _CLASSIFIERS.get(stream, _classify_stdout)
    for line in text.splitlines():
        yield {"ts": ts, "stream": stream, "lvl": classify(line), "body": line}


def write_jsonl_log(
    path: Path,
    *,
    task_id: str,
    command: list[str] | None,
    stdout: str,
    stderr: str,
    started_at: str | None = None,
    extra_streams: Mapping[str, str] | None = None,
) -> str:
    """Write `path` as JSONL and return its absolute path.

    `extra_streams` carries additional named streams to log alongside
    stdout/stderr — antigravity uses it for the raw `agy --print` log.
    """
    ts = _now_iso()
    header: dict[str, object] = {
        "v": LOG_FORMAT_VERSION,
        "task_id": task_id,
        "command": " ".join(command) if command else "",
        "started_at": started_at or ts,
        "ended_at": ts,
    }
    lines: list[str] = [json.dumps(header, ensure_ascii=False)]
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        for entry in _entries_for_stream(stream, text, ts):
            lines.append(json.dumps(entry, ensure_ascii=False))
    if extra_streams:
        for stream, text in extra_streams.items():
            for entry in _entries_for_stream(stream, text, ts):
                lines.append(json.dumps(entry, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path.resolve())


def touch_live_log(path: Path) -> None:
    """Create or refresh a live executor log without adding visible output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def append_live_log(path: Path, text: str) -> None:
    """Append raw executor output to a live progress log."""
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
