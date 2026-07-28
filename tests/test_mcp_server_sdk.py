"""MCP Python SDK 2.x wiring for the Fluxion sub-agent server.

These cover the parts of the SDK contract Fluxion depends on and that changed
in the 1.x -> 2.x migration: MCPServer construction, tool discovery, direct
call_tool returning a CallToolResult (not a tuple), structured_content
carrying the tool payload, ToolError on an unhandled tool exception, and a
real stdio client/server handshake.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

import pytest

from fluxion.mcp_server import server as server_mod

# The full tool surface exposed to MCP hosts. Kept explicit so an accidental
# rename or dropped registration fails here rather than in a host at runtime.
EXPECTED_TOOLS = {
    "cancel_subagent_run",
    "force_cancel_subagent_run",
    "get_fluxion_status",
    "get_project",
    "get_task_result",
    "get_task_status",
    "list_agent_models",
    "list_projects",
    "list_subagent_runs",
    "reconcile_tasks",
    "revert_subagent_run",
    "run_subagent",
}


class _Settings:
    mcp_status_max_wait_ms = 60_000

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.projects = {}

    @classmethod
    def load(cls):
        raise AssertionError("patched in test")


class _Runner:
    def __init__(self, settings):
        del settings

    def submit(self, request):
        raise AssertionError("not used")

    def cancel(self, task_id):
        raise AssertionError("not used")


@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    class _S(_Settings):
        @classmethod
        def load(cls):
            return cls(tmp_path)

    monkeypatch.setattr(server_mod, "Settings", _S)
    monkeypatch.setattr(server_mod, "SubagentRunner", _Runner)
    return server_mod.create_server()


def test_create_server_returns_mcpserver(mcp_server):
    from mcp.server import MCPServer

    assert isinstance(mcp_server, MCPServer)
    assert mcp_server.name == "Fluxion"


def test_tool_discovery_exposes_full_surface(mcp_server):
    tools = asyncio.run(mcp_server.list_tools())

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    # Every tool must carry a description; hosts show it for tool selection.
    assert all((tool.description or "").strip() for tool in tools)


def test_run_subagent_schema_keeps_documented_arguments(mcp_server):
    tools = {tool.name: tool for tool in asyncio.run(mcp_server.list_tools())}

    # input_schema is the 2.x snake_case spelling of 1.x inputSchema.
    properties = tools["run_subagent"].input_schema["properties"]
    for name in ("prompt", "agent", "project", "workspace", "profile", "mode", "model"):
        assert name in properties
    assert tools["run_subagent"].input_schema["required"] == ["prompt"]


def test_call_tool_returns_structured_call_tool_result(mcp_server):
    from mcp.types import CallToolResult

    result = asyncio.run(mcp_server.call_tool("list_projects", {}))

    # 2.x returns a CallToolResult object; 1.x returned a (content, payload) tuple.
    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    assert result.structured_content == {
        "projects": [],
        "hint": (
            "No projects are configured. You can still run_subagent by passing an "
            "absolute `workspace` path to the target repo (and profile=implement, "
            "mode=workspace-write for edits)."
        ),
    }
    # The same payload is mirrored as JSON text content for hosts that ignore
    # structured output.
    assert result.content


def test_call_tool_reports_not_found_without_raising(mcp_server, monkeypatch):
    monkeypatch.setattr(server_mod, "_find_task", lambda run_id: None)

    result = asyncio.run(mcp_server.call_tool("get_task_status", {"run_id": "missing"}))

    assert result.is_error is False
    assert result.structured_content == {"found": False, "run_id": "missing"}


def test_unhandled_tool_exception_raises_tool_error(mcp_server, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    def _boom(run_id):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(server_mod, "_find_task", _boom)

    # 2.x surfaces an unhandled tool exception as ToolError on a direct call;
    # over a session it is reported to the host as an is_error result.
    with pytest.raises(ToolError, match="store unavailable"):
        asyncio.run(mcp_server.call_tool("get_task_status", {"run_id": "t1"}))


def test_unknown_tool_raises(mcp_server):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown tool"):
        asyncio.run(mcp_server.call_tool("no_such_tool", {}))


def test_stdio_client_can_connect_and_call(tmp_path):
    """End-to-end: a real MCP stdio client drives the packaged server entry point."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    async def drive():
        data_dir = tempfile.mkdtemp(dir=tmp_path)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from fluxion.mcp_server import main; main()"],
            env={
                **os.environ,
                "FLUXION_DATA_DIR": data_dir,
                "FLUXION_WORKSPACE_ROOT": data_dir,
            },
            cwd=data_dir,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("list_projects", {})
                return init, {t.name for t in tools.tools}, result

    init, tool_names, result = asyncio.run(drive())

    assert init.server_info.name == "Fluxion"
    assert tool_names == EXPECTED_TOOLS
    assert result.is_error is False
    assert result.structured_content["projects"] == []
