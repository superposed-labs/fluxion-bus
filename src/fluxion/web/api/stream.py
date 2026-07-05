from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# How often to push a no-op keepalive comment. Some proxies (Nginx default
# config, Cloudflare) cut idle long-lived HTTP connections; 15s is well
# under typical idle timeouts and invisible to the browser EventSource.
KEEPALIVE_INTERVAL_SEC = 15.0


def _format_sse(event: str, data: dict | str) -> bytes:
    body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    # Each `data:` line must be free of newlines per the SSE spec.
    encoded = body.replace("\n", "\ndata: ")
    return f"event: {event}\ndata: {encoded}\n\n".encode()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    hub = request.app.state.event_stream
    queue = hub.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        # First frame: confirm the connection is live and announce the
        # protocol version so the client can refuse incompatible servers.
        yield _format_sse("stream.hello", {"v": 1})
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL_SEC)
                except TimeoutError:
                    # SSE comment lines start with `:` and are ignored by the
                    # client — perfect for keeping the TCP/proxy chain warm.
                    yield b": keepalive\n\n"
                    continue
                yield _format_sse("task.event", event)
        finally:
            hub.unsubscribe(queue)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        # Tell common reverse proxies (Nginx, Caddy) to disable response
        # buffering so SSE frames actually reach the browser as they're
        # written instead of being held back.
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
