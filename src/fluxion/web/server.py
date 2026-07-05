from __future__ import annotations

import argparse
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from fluxion.config.settings import Settings
from fluxion.subagent import SubagentRunner
from fluxion.usage import price_data
from fluxion.web.api import executors as executors_api
from fluxion.web.api import logs as logs_api
from fluxion.web.api import monitor as monitor_api
from fluxion.web.api import schedules as schedules_api
from fluxion.web.api import sessions as sessions_api
from fluxion.web.api import stream as stream_api
from fluxion.web.api import tasks as tasks_api
from fluxion.web.api import usage as usage_api
from fluxion.web.auth import token_guard
from fluxion.web.deps import get_data_dir
from fluxion.web.services.event_stream import TaskEventStream

STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = STATIC_DIR / "index.html"


class ImmutableStaticFiles(StaticFiles):
    """Serve content-hashed build assets with a long immutable cache.

    Vite emits ``assets/index-<hash>.js`` style filenames, so the bytes
    behind a given URL never change — they can be cached forever.
    """

    def file_response(self, *args, **kwargs) -> object:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.load()
    stream = TaskEventStream(get_data_dir())
    await stream.start()
    app.state.event_stream = stream
    app.state.subagent_settings = settings
    app.state.subagent_settings_fingerprint = tasks_api.executor_settings_fingerprint(settings)
    app.state.subagent_runner = SubagentRunner(settings)
    # Best-effort background refresh of the shared price tables (enabled by
    # default; FLUXION_PRICE_AUTO_REFRESH=false to disable). Never blocks; falls
    # back to the bundled snapshot. Internal plumbing — not a scheduler task.
    price_data.start_background_refresh()
    try:
        yield
    finally:
        await stream.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Fluxion UI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(BaseHTTPMiddleware, dispatch=token_guard)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(tasks_api.router, prefix="/api")
    app.include_router(sessions_api.router, prefix="/api")
    app.include_router(logs_api.router, prefix="/api")
    app.include_router(stream_api.router, prefix="/api")
    app.include_router(usage_api.router, prefix="/api")
    app.include_router(executors_api.router, prefix="/api")
    app.include_router(schedules_api.router, prefix="/api")
    app.include_router(monitor_api.router, prefix="/api")

    if _INDEX_HTML.exists():
        app.mount(
            "/assets",
            ImmutableStaticFiles(directory=STATIC_DIR / "assets"),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # Any non-API route falls through to the SPA shell. The shell and
            # non-hashed root files must always revalidate (no-cache), otherwise
            # the browser keeps serving a stale index.html that points at an old
            # bundle hash after a rebuild. The hashed /assets/* are immutable.
            no_cache = {"Cache-Control": "no-cache"}
            target = STATIC_DIR / full_path
            if full_path and target.is_file():
                # Guard against path traversal via the catch-all param.
                resolved = target.resolve()
                if resolved.is_relative_to(STATIC_DIR.resolve()):
                    return FileResponse(resolved, headers=no_cache)
            return FileResponse(_INDEX_HTML, headers=no_cache)

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fluxion self-hosted UI server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765)")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only)",
    )
    args = parser.parse_args()

    if not _INDEX_HTML.exists():
        print(
            "[warn] Frontend bundle not found at",
            STATIC_DIR,
            "\n        Run `cd web && npm install && npm run build` to populate it.",
        )

    if args.host != "127.0.0.1" and not os.environ.get("FLUXION_UI_TOKEN", "").strip():
        # Refuse to expose an unauthenticated API beyond loopback. The console
        # serves task summaries, logs, and schedule/env controls, so binding to a
        # LAN/VPS address without a token would leave all of that wide open.
        print(
            f"[error] Refusing to bind to {args.host} without FLUXION_UI_TOKEN — the API "
            "(logs, schedules, env writes) would be open to anyone who can reach this port.\n"
            '        Set a token first:  export FLUXION_UI_TOKEN="$(openssl rand -hex 16)"\n'
            "        (Loopback-only `--host 127.0.0.1` needs no token.)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    import uvicorn

    uvicorn.run(
        "fluxion.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
