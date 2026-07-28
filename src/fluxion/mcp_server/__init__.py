"""Fluxion sub-agent MCP server.

Split into submodules — threads (thread/session resolution), payloads (run
acceptance + error payloads), logs (executor-log tail cleanup), views (task
status/result views), server (MCPServer wiring). The public surface and the
internals referenced by tests are re-exported so ``from fluxion.mcp_server
import ...`` and ``fluxion.mcp_server.<name>`` keep working.
"""

from __future__ import annotations

from fluxion.mcp_server.logs import _human_log_tail, _is_glog_noise
from fluxion.mcp_server.payloads import _timed_out_still_running_payload
from fluxion.mcp_server.server import create_server, main
from fluxion.mcp_server.threads import _resolve_thread
from fluxion.mcp_server.views import (
    _status_view,
    _suggested_poll_after_sec,
    _typical_duration_sec,
)

__all__ = ["create_server", "main"]
