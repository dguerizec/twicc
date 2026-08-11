"""The lowlevel MCP server: tool listing + in-process command dispatch.

One ``Server`` instance serves every agent session; per-call identity comes
from the ``Authorization`` header of the underlying HTTP request (available
via ``request_context.request`` on the streamable-HTTP transport) and is
bound into two ContextVars before the command runs in a worker thread:

- ``whoami.forced_session_id`` — makes ``self``/``parent``/``whoami``/
  ``spawned_by`` auto-fill resolve to the calling session;
- ``transport.backend_loop`` — routes mutations straight to the drop-request
  service handlers on this event loop instead of the drop-file dance.

The tool result is the same envelope as ``POST /rpc/<command>``:
``{"exit_code": int, "result": ..., "error": ...}`` — returned as MCP
structured content. Non-zero exit codes are data, not MCP errors (parity with
the CLI/skills contract agents already know).
"""

from __future__ import annotations

import asyncio
import logging

import orjson
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from twicc.cli._drop_request import transport
from twicc.cli._drop_request.whoami import forced_session_id
from twicc.mcp.identity import resolve_session_token
from twicc.mcp.tools import iter_mcp_tools, tools_by_name
from twicc.rpc.generator import render_argv
from twicc.rpc.views import _run_invoke

logger = logging.getLogger(__name__)


class UnknownToolError(Exception):
    pass


INSTRUCTIONS = """\
These tools are the TwiCC CLI (`twicc <command>`), one tool per command; the
`twicc-*` skills document the same surface in depth. Results are the CLI's
JSON wrapped in {"exit_code", "result", "error"} — exit_code 0 is success,
non-zero maps to the exit codes the skills document (3 rejected, 4 failed,
5 timeout, ...).

You are reading this because the TwiCC MCP server is connected, so its whole
tool set is available to you — but most schemas are deferred (all of them on
Codex, all but a handful on Claude Code). A tool missing from your visible tool
list is therefore not a missing tool: search your full tool list for the one you
need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex) instead of falling back
to the `twicc` shell CLI.

Conventions:
- Session-targeting parameters accept `self` (your own session) and/or
  `parent` (the session that spawned you) where their parameter description
  says so; the connection carries the identity needed to resolve them,
  so `whoami` works and `create_session` records you as the spawner.
- Always pass absolute paths (directories, attachments): tools execute inside
  the TwiCC backend, whose working directory is not yours.
- Keep `*_wait` timeouts <= 300 seconds; poll again rather than exceeding them.
- Catalogues (models, presets, providers) drift: fetch them live with `info`.
"""


async def dispatch_tool(name: str, arguments: dict, *, session_id: str | None) -> dict:
    """Execute one tool call in-process; returns the RPC-style envelope."""
    spec = tools_by_name().get(name)
    if spec is None:
        raise UnknownToolError(name)
    argv = render_argv(spec, arguments)
    loop = asyncio.get_running_loop()
    tok_sid = forced_session_id.set(session_id)
    tok_loop = transport.backend_loop.set(loop)
    try:
        result = await asyncio.to_thread(_run_invoke, argv)
    finally:
        transport.backend_loop.reset(tok_loop)
        forced_session_id.reset(tok_sid)
    envelope = {"exit_code": result.exit_code, "result": result.result, "error": result.error}
    # Normalize to plain JSON-native types, exactly as the CLI (``_output.emit_json``)
    # and the ``/rpc/`` view (``views._json``) do. Command results carry orjson-native
    # objects (``datetime`` timestamps, ...) the MCP SDK would otherwise hand to stdlib
    # ``json.dumps`` (lowlevel/server.py), which raises "Object of type datetime is not
    # JSON serializable" and surfaces as a tool error. The orjson round-trip gives the
    # SDK the same ISO-string shape agents already get from the CLI/skills path.
    return orjson.loads(orjson.dumps(envelope))


def _session_id_from_request() -> str | None:
    """Caller identity from the HTTP Authorization header, if session-bound."""
    ctx = _server.request_context
    request = getattr(ctx, "request", None)
    if request is None:
        return None
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    return resolve_session_token(token)


_server: Server = Server("twicc", instructions=INSTRUCTIONS)


@_server.list_tools()
async def _list_tools() -> list[mcp_types.Tool]:
    return iter_mcp_tools()


@_server.call_tool()
async def _call_tool(name: str, arguments: dict) -> dict:
    session_id = _session_id_from_request()
    try:
        return await dispatch_tool(name, arguments, session_id=session_id)
    except UnknownToolError:
        raise ValueError(f"Unknown tool: {name}")
    except Exception:
        logger.exception("MCP tool %r failed (arguments=%r)", name, arguments)
        raise


_session_manager: StreamableHTTPSessionManager | None = None


def get_session_manager() -> StreamableHTTPSessionManager:
    """Process-wide singleton; created lazily, run by twicc.mcp.endpoint."""
    global _session_manager
    if _session_manager is None:
        _session_manager = StreamableHTTPSessionManager(
            app=_server,
            json_response=True,
            stateless=True,
            # The Bearer token is the real gate (endpoint.py); Host/Origin
            # validation would only break worktree ports and tunnels.
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            ),
        )
    return _session_manager
