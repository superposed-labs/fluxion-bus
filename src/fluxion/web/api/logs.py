from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from fluxion.web.deps import get_data_dir

router = APIRouter()

_TASK_ID_RE = re.compile(r"^[0-9a-f-]+$")


@router.get("/logs/{task_id}")
def get_task_log(task_id: str) -> dict[str, Any]:
    if not _TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task ID")
    log_path = get_data_dir() / "logs" / f"task-{task_id}.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"task_id": task_id, "log": "", "error": str(exc)}
    return {"task_id": task_id, "log": content}
