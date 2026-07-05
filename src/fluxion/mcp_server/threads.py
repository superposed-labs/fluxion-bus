"""Thread resolution for MCP sub-agent runs."""

from __future__ import annotations

import uuid


def _resolve_thread(explicit: str) -> str:
    """Resolve the thread that scopes executor session reuse.

    An explicit non-empty ``thread`` is the continuation handle: pass the same
    value across calls to resume the same executor session (reuses the agent's
    context, saving tokens). With no explicit thread we mint a fresh unique id
    per call, so each run is isolated by default — independent tasks never
    resume and therefore cannot reconcile/clobber the workspace or inherit stale
    context. (Mirrors Codex CLI, where resume is an explicit opt-in and a plain
    invocation starts fresh.)
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    return f"mcp-fresh:{uuid.uuid4().hex}"
