"""WebSocket gateway transport for the QQ official bot.

An alternative to the webhook transport that connects *out* to QQ's WebSocket
gateway instead of receiving inbound HTTPS callbacks. This suits Fluxion's
local-first deployment: no public address, no tunnel, and no callback-signature
handshake — authentication is the same ``getAppAccessToken`` flow the send path
already uses (proven reachable from the host).

Protocol (QQ v2 gateway), mirroring the documented opcodes:

* ``GET /gateway`` (REST) returns the ``wss://`` URL.
* Server sends ``op:10`` Hello with ``heartbeat_interval``.
* We send ``op:2`` Identify with ``QQBot {access_token}`` and the intents bitmask.
* Server sends ``op:0`` Dispatch ``READY`` (carries ``session_id``); thereafter
  ``op:0`` events with ``t``/``d``/``s``.
* We send ``op:1`` Heartbeat every interval with the last seq; server ACKs ``op:11``.
* ``op:7`` Reconnect / ``op:9`` Invalid Session ask us to re-establish.

Dispatched events share the exact ``{"t", "d"}`` shape the webhook handler
produces, so they are fed straight back into the adapter's ``_handle_event`` —
the receive *and* send logic is identical across transports.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import websockets

from fluxion.channels.qqbot.qqbot_client import API_BASE_PROD, API_BASE_SANDBOX
from fluxion.channels.qqbot.token_manager import QQBotTokenManager
from fluxion.utils.logger import get_logger

logger = get_logger(__name__)

# Opcodes.
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Intent bitmask for single-chat (C2C) + group @-messages. The QQ gateway only
# delivers events whose intent bit we subscribe to here.
INTENT_GROUP_AND_C2C = 1 << 25

_MAX_BACKOFF_SEC = 30.0


class QQBotWebSocketTransport:
    """Maintains a resilient connection to the QQ WebSocket gateway."""

    def __init__(
        self,
        token_manager: QQBotTokenManager,
        on_event: Callable[[dict[str, Any]], None],
        *,
        sandbox: bool = False,
        intents: int = INTENT_GROUP_AND_C2C,
    ) -> None:
        self._tokens = token_manager
        self._on_event = on_event
        self._api_base = (API_BASE_SANDBOX if sandbox else API_BASE_PROD).rstrip("/")
        self._intents = intents
        self._stop = False
        self._last_seq: int | None = None
        self._session_id: str | None = None

    def run_forever(self) -> None:
        """Blocking entry point — run the asyncio reconnect loop on this thread."""
        try:
            asyncio.run(self._run_loop())
        except Exception:  # noqa: BLE001 - never let the channel thread die silently
            logger.exception("QQ bot WebSocket transport stopped unexpectedly")

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                await self._connect_once()
                backoff = 1.0  # a clean session resets the backoff
            except Exception:  # noqa: BLE001 - reconnect on any failure
                logger.exception("QQ bot WebSocket session ended; will reconnect")
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SEC)

    async def _connect_once(self) -> None:
        token = self._tokens.get_access_token()
        ws_url = self._fetch_gateway_url(token)
        logger.info("Connecting to QQ bot WebSocket gateway %s", ws_url)

        async with websockets.connect(ws_url, max_size=None) as ws:
            hello = json.loads(await ws.recv())
            interval = float(hello.get("d", {}).get("heartbeat_interval", 30000)) / 1000.0

            await ws.send(
                json.dumps(
                    {
                        "op": OP_IDENTIFY,
                        "d": {
                            "token": f"QQBot {token}",
                            "intents": self._intents,
                            "shard": [0, 1],
                            "properties": {},
                        },
                    }
                )
            )

            heartbeat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                async for raw in ws:
                    if not await self._handle_frame(ws, json.loads(raw)):
                        break  # op:7 / op:9 — drop the socket and reconnect
            finally:
                heartbeat.cancel()

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._last_seq}))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - a failed heartbeat just ends the session
            logger.debug("QQ bot heartbeat send failed", exc_info=True)

    async def _handle_frame(self, ws: Any, frame: dict[str, Any]) -> bool:
        """Process one gateway frame. Returns False to trigger a reconnect."""
        op = frame.get("op")

        if op == OP_DISPATCH:
            if frame.get("s") is not None:
                self._last_seq = frame["s"]
            event_type = frame.get("t")
            if event_type == "READY":
                self._session_id = (frame.get("d") or {}).get("session_id")
                logger.info("QQ bot WebSocket ready (session %s)", self._session_id)
                return True
            if event_type == "RESUMED":
                logger.info("QQ bot WebSocket resumed")
                return True
            # Hand the dispatch to the shared adapter logic off the event loop so
            # blocking work (task submission, HTTP sends) never stalls heartbeats.
            await asyncio.get_running_loop().run_in_executor(None, self._dispatch_safe, frame)
            return True

        if op == OP_HEARTBEAT_ACK:
            return True
        if op in (OP_RECONNECT, OP_INVALID_SESSION):
            logger.info("QQ bot WebSocket asked to reconnect (op=%s)", op)
            return False
        # op:10 Hello only arrives first and is handled in _connect_once.
        return True

    def _dispatch_safe(self, frame: dict[str, Any]) -> None:
        try:
            self._on_event(frame)
        except Exception:  # noqa: BLE001 - one bad event must not kill the loop
            logger.exception("QQ bot WebSocket event handler failed")

    # ------------------------------------------------------------------
    # REST: gateway URL
    # ------------------------------------------------------------------

    def _fetch_gateway_url(self, token: str) -> str:
        req = urllib.request.Request(
            f"{self._api_base}/gateway",
            headers={"Authorization": f"QQBot {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"QQ /gateway failed: HTTP {exc.code} {detail}") from exc
        url = data.get("url")
        if not url:
            raise RuntimeError(f"QQ /gateway returned no url: {data}")
        return url
