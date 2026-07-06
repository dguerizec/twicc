"""Raw-ASGI entry for /mcp: auth gate + streamable-HTTP session manager.

Mounted by twicc.asgi *in front of* Django (no middleware, no urls.py, no
SPA-catch-all involvement). Authentication is mandatory and header-based:

- ``Authorization: Bearer twicc_mcp_<sid>.<sig>`` — a per-session token
  minted at agent wiring time (twicc.mcp.identity); grants full access and
  binds caller identity (whoami / self / parent).
- ``Authorization: Bearer twicc_pat_...`` — a user-created API token
  (``twicc token create``); full access, no session identity.

Anything else → 401. Additionally, remote connections go through the same
``scope_remote_access_blocked`` gate as the WebSocket consumers (an
unprotected instance refuses non-loopback callers outright).
"""

from __future__ import annotations

import contextlib
import logging

import orjson

from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.tokens import verify_token
from twicc.mcp import mcp_enabled
from twicc.mcp.identity import TOKEN_PREFIX, resolve_session_token
from twicc.mcp.server import get_session_manager

logger = logging.getLogger(__name__)

_started = False


def _bearer(scope) -> str:
    for key, value in scope.get("headers") or ():
        if key == b"authorization":
            return value.decode("latin-1").removeprefix("Bearer ").strip()
    return ""


def _authorized(scope) -> bool:
    token = _bearer(scope)
    if token.startswith(TOKEN_PREFIX):
        return resolve_session_token(token) is not None
    if token:
        return verify_token(token) is not None
    return False


async def _plain_response(send, status: int, body: dict, *, headers=()) -> None:
    payload = orjson.dumps(body)
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), *headers],
    })
    await send({"type": "http.response.body", "body": payload})


async def handle_mcp(scope, receive, send) -> None:
    """ASGI handler for every /mcp request."""
    if scope["type"] != "http":  # pragma: no cover — router only sends http
        return
    if not mcp_enabled() or not _started:
        await _plain_response(send, 503, {"error": "MCP server not available."})
        return
    if scope_remote_access_blocked(scope):
        await _plain_response(send, 403, {"error": "Remote access is disabled."})
        return
    if not _authorized(scope):
        await _plain_response(
            send, 401, {"error": "A TwiCC MCP session token or API token is required."},
            headers=[(b"www-authenticate", b"Bearer")],
        )
        return
    # The session manager expects to own the path; it treats the mount point
    # as the endpoint regardless of the exact path value.
    await get_session_manager().handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def mcp_lifespan():
    """Run the session manager's task group (call once, from run.py or tests)."""
    global _started
    manager = get_session_manager()
    async with manager.run():
        _started = True
        logger.info("MCP server ready at /mcp")
        try:
            yield
        finally:
            _started = False


async def start_mcp_task(shutdown_event) -> None:
    """run.py background task: keep the session manager alive until shutdown."""
    if not mcp_enabled():
        logger.info("MCP server disabled (TWICC_NO_MCP)")
        return
    async with mcp_lifespan():
        await shutdown_event.wait()
