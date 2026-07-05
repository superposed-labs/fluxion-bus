from __future__ import annotations

import asyncio
import json

import fluxion.channels.qqbot.websocket_transport as ws_mod
from fluxion.channels.qqbot.websocket_transport import (
    INTENT_GROUP_AND_C2C,
    QQBotWebSocketTransport,
)


class _Tokens:
    def get_access_token(self) -> str:
        return "TKN"


def _make(on_event=lambda f: None, *, sandbox=False) -> QQBotWebSocketTransport:
    return QQBotWebSocketTransport(_Tokens(), on_event, sandbox=sandbox)


def test_intent_constant_is_group_and_c2c():
    assert INTENT_GROUP_AND_C2C == 1 << 25


def test_sandbox_selects_sandbox_api_base():
    assert "sandbox" in _make(sandbox=True)._api_base  # noqa: SLF001
    assert "sandbox" not in _make(sandbox=False)._api_base  # noqa: SLF001


def test_ready_frame_stores_session_and_seq():
    t = _make()
    frame = {"op": 0, "s": 1, "t": "READY", "d": {"session_id": "sess-1"}}
    assert asyncio.run(t._handle_frame(None, frame)) is True  # noqa: SLF001
    assert t._session_id == "sess-1"  # noqa: SLF001
    assert t._last_seq == 1  # noqa: SLF001


def test_dispatch_frame_invokes_on_event_and_tracks_seq():
    seen: list[dict] = []
    t = _make(on_event=seen.append)
    frame = {"op": 0, "s": 7, "t": "C2C_MESSAGE_CREATE", "d": {"id": "m1"}}
    assert asyncio.run(t._handle_frame(None, frame)) is True  # noqa: SLF001
    assert t._last_seq == 7  # noqa: SLF001
    # The event is forwarded verbatim so the adapter's _handle_event sees the same
    # {t, d} shape the webhook path produces.
    assert seen == [frame]


def test_reconnect_and_invalid_session_request_reconnect():
    t = _make()
    assert asyncio.run(t._handle_frame(None, {"op": 7})) is False  # noqa: SLF001
    assert asyncio.run(t._handle_frame(None, {"op": 9})) is False  # noqa: SLF001


def test_heartbeat_ack_is_noop_true():
    t = _make()
    assert asyncio.run(t._handle_frame(None, {"op": 11})) is True  # noqa: SLF001


def test_failing_event_handler_does_not_propagate():
    def boom(_frame):
        raise RuntimeError("handler blew up")

    t = _make(on_event=boom)
    frame = {"op": 0, "s": 3, "t": "GROUP_AT_MESSAGE_CREATE", "d": {"id": "m2"}}
    # A bad event must not break the receive loop.
    assert asyncio.run(t._handle_frame(None, frame)) is True  # noqa: SLF001


def test_fetch_gateway_url_parses_url(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"url": "wss://api.example/websocket"}).encode("utf-8")

    monkeypatch.setattr(ws_mod.urllib.request, "urlopen", lambda req, timeout=15: _Resp())
    assert _make()._fetch_gateway_url("TKN") == "wss://api.example/websocket"  # noqa: SLF001
