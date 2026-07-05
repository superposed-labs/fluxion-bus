from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from fluxion.usage.plan_prices import plan_monthly_for
from fluxion.web.deps import (
    get_settings,
    get_usage_history_service,
    get_usage_service,
)

router = APIRouter()


@router.get("/usage")
def get_usage() -> dict[str, Any]:
    # Sync route on purpose: a stale shared cache may launch the short-lived
    # collector, so FastAPI runs this in its threadpool.
    settings = get_settings()
    generated_at = datetime.now(UTC).isoformat()
    base = {"locale": settings.ui_locale, "generated_at": generated_at}
    if not settings.usage_panel_enabled:
        return {"enabled": False, "providers": [], **base}
    # Tag each provider with the official monthly price of its detected plan
    # (resolved fresh here, not cached, so a plan-price edit takes effect on
    # restart without invalidating the quota cache). Display-only.
    providers = []
    for p in get_usage_service().get_usage():
        d = p.to_dict()
        d["plan_monthly_usd"] = plan_monthly_for(p.provider, p.account_label)
        providers.append(d)
    return {"enabled": True, "providers": providers, **base}


@router.get("/usage/history")
def get_usage_history(window: str = "all") -> dict[str, Any]:
    # Token-usage analytics reconstructed from local histories — a
    # separate, local-only data path from the live quota panel above. Sync route
    # for the same reason (blocking file IO in the threadpool); the service
    # TTL-caches the transcript scan.
    settings = get_settings()
    stats = get_usage_history_service().get(window)
    return {"enabled": True, "locale": settings.ui_locale, **stats}
