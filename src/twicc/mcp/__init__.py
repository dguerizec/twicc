"""TwiCC's own MCP server: the CLI surface as per-session MCP tools.

The server is a Streamable-HTTP endpoint at ``/mcp`` on the backend's own
port, mounted as raw ASGI in :mod:`twicc.asgi` and started from ``run.py``
(:mod:`twicc.mcp.endpoint`). Tools are auto-generated from the Click tree
(:mod:`twicc.mcp.tools`) and executed in-process (:mod:`twicc.mcp.server`).
"""

from __future__ import annotations

import os

# Codex: force Tool Search deferral of MCP schemas (Codex loads all schemas
# eagerly below its 100-tool threshold). True = defer (clean context, full tool
# set) via the experimental ``tool_search_always_defer_mcp_tools`` feature; flip
# to False for eager loading if that under-development flag regresses on a Codex
# bump. See the MCP plan D10 / research doc §3.
TWICC_MCP_CODEX_DEFER = True


def mcp_enabled() -> bool:
    """Kill switch: ``TWICC_NO_MCP=1`` disables mount and per-session wiring."""
    return os.environ.get("TWICC_NO_MCP", "").strip().lower() not in ("1", "true", "yes")


def mcp_base_url() -> str:
    """Loopback URL agents call back to. Always local: agents run on this host."""
    port = os.environ.get("TWICC_PORT", "3500")
    return f"http://127.0.0.1:{port}/mcp"
