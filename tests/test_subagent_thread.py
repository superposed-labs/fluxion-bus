"""Thread resolution for MCP sub-agent runs.

Default is isolation: an explicit ``thread`` is the continuation handle (pass the
same value to resume); with no thread each call gets a fresh unique id so
independent runs never resume.
"""

from __future__ import annotations

import importlib

mcp_server = importlib.import_module("fluxion.mcp_server")
_resolve_thread = mcp_server._resolve_thread


def test_explicit_thread_wins():
    assert _resolve_thread("my-thread") == "my-thread"


def test_explicit_thread_is_stable_across_calls():
    assert _resolve_thread("refactor-auth") == _resolve_thread("refactor-auth")


def test_empty_thread_is_fresh_and_unique():
    a = _resolve_thread("")
    b = _resolve_thread("")
    assert a != b
    assert a.startswith("mcp-fresh:")
    assert b.startswith("mcp-fresh:")


def test_blank_thread_counts_as_unspecified():
    assert _resolve_thread("   ").startswith("mcp-fresh:")


def test_host_session_id_no_longer_forces_continuity(monkeypatch):
    # Regression for P0#2: a host session id must NOT make independent runs share
    # a session (that conflated "same main-agent session" with "same agy convo").
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    a = _resolve_thread("")
    b = _resolve_thread("")
    assert a != b
    assert "abc-123" not in a
