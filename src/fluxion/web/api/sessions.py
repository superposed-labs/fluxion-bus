from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from fluxion.web.deps import get_data_dir
from fluxion.web.services.aggregator import aggregate_sessions_cached

router = APIRouter()


@router.get("/sessions")
def list_sessions() -> dict[str, list[dict[str, Any]]]:
    return {"sessions": aggregate_sessions_cached(get_data_dir())}
