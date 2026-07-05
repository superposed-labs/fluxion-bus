from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class JsonlStorage:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._tasks_path = data_dir / "tasks.jsonl"
        self._sessions_path = data_dir / "sessions.jsonl"
        self._ensure()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _ensure(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        for path in (self._tasks_path, self._sessions_path):
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def append_task_event(self, payload: dict[str, Any]) -> None:
        self._append(self._tasks_path, payload)

    def append_session_event(self, payload: dict[str, Any]) -> None:
        self._append(self._sessions_path, payload)

    def list_recent_task_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._read_tail_jsonl(self._tasks_path, limit=limit)

    def list_recent_task_summaries(self, *, limit: int = 10) -> list[dict[str, Any]]:
        events = self.list_recent_task_events(limit=500)
        latest_by_id: dict[str, dict[str, Any]] = {}
        for event in events:
            task_id = str(event.get("task_id", "")).strip()
            if not task_id:
                continue
            latest_by_id[task_id] = event
        items = sorted(
            latest_by_id.values(),
            key=lambda e: str(e.get("timestamp", "")),
            reverse=True,
        )[:limit]
        rows: list[dict[str, Any]] = []
        for item in items:
            rows.append(
                {
                    "task_id": item.get("task_id", ""),
                    "status": item.get("status", ""),
                    "timestamp": item.get("timestamp", ""),
                }
            )
        return rows

    def list_recent_session_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        return self._read_tail_jsonl(self._sessions_path, limit=limit)

    def load_latest_sessions(self, *, limit: int = 5000) -> dict[str, dict[str, Any]]:
        events = self.list_recent_session_events(limit=limit)
        latest_by_key: dict[str, dict[str, Any]] = {}
        for event in events:
            conversation_key = str(event.get("conversation_key", "")).strip()
            if not conversation_key:
                continue
            latest_by_key[conversation_key] = event
        return latest_by_key

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=_json_default)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _read_tail_jsonl(self, path: Path, *, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return []
        tail = lines[-max(1, limit) :]
        rows: list[dict[str, Any]] = []
        for line in tail:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
            except json.JSONDecodeError:
                continue
        return rows
