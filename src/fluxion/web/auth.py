"""Optional token gate for the Fluxion UI.

When FLUXION_UI_TOKEN is unset the API is open — appropriate for the default
127.0.0.1 bind. Set the env var (and bind to LAN/VPS) to require a Bearer
token or `?token=` query param on every /api/* request.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _expected_token() -> str:
    return os.environ.get("FLUXION_UI_TOKEN", "").strip()


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token", "").strip()


async def token_guard(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    expected = _expected_token()
    if expected and request.url.path.startswith("/api/"):
        provided = _extract_token(request)
        if not provided or not secrets.compare_digest(provided, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)
