"""
Codex CLI authentication state.

Runs ``codex login status`` against the wheel-bundled Codex binary to
determine whether the user is logged into Codex. The exit code is the
source of truth (0 = logged in, 1 = not logged in).

Mirrors the surface of ``providers.claude_code.auth`` so the auth_task
and the WS handler can stay structurally identical between providers.
"""

from __future__ import annotations

import asyncio
import logging

from channels.layers import get_channel_layer

from .bin import resolve_bundled_binary

logger = logging.getLogger(__name__)

# Cached last known auth state (None = never checked yet).
_last_known_authenticated: bool | None = None

# Event used by the auth_task to break out of its idle (authenticated) state
# when something (manual recheck, periodic flip to False) suggests the state
# should be re-checked or polling should resume.
_auth_wake_event: asyncio.Event | None = None

# Timeout for the ``codex login status`` subprocess call.
_AUTH_STATUS_TIMEOUT = 10


def _build_auth_message(authenticated: bool) -> dict:
    """Build the ``codex:auth_updated`` message payload."""
    return {
        "type": "codex:auth_updated",
        "authenticated": authenticated,
    }


def get_last_known_authenticated() -> bool | None:
    """Return the last known auth state (None if never checked yet)."""
    return _last_known_authenticated


def get_auth_wake_event() -> asyncio.Event:
    """Get (lazily create) the wake event used to interrupt the auth_task's idle state."""
    global _auth_wake_event
    if _auth_wake_event is None:
        _auth_wake_event = asyncio.Event()
    return _auth_wake_event


async def check_auth_status() -> bool:
    """Run ``codex login status`` on the bundled binary and return whether exit code is 0.

    The Codex CLI uses the exit code as the source of truth (0 = logged in,
    1 = not logged in). We don't parse stdout — only the return code.

    Returns ``False`` on any failure (process spawn error, timeout, non-zero
    exit code, missing bundled binary).
    """
    try:
        binary = str(resolve_bundled_binary())
    except FileNotFoundError as e:
        logger.warning("Cannot check Codex auth status: %s", e)
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "login", "status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception as e:
        logger.warning("Cannot launch Codex auth status check: %s", e)
        return False

    try:
        await asyncio.wait_for(proc.wait(), timeout=_AUTH_STATUS_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Codex auth status check timed out after %ds", _AUTH_STATUS_TIMEOUT)
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return False

    return proc.returncode == 0


async def get_auth_message_for_connection() -> dict:
    """Build a ``codex:auth_updated`` message for a single client on WS connect.

    Uses the cached state populated by ``auth_task``. If the cache is still
    unknown (very first connection in a fresh process), runs one inline so
    the connecting client doesn't briefly see a wrong "not authenticated"
    state.
    """
    if _last_known_authenticated is None:
        await check_and_broadcast()
    return _build_auth_message(bool(_last_known_authenticated))


async def check_and_broadcast(*, force: bool = False) -> bool:
    """Run a Codex auth status check and broadcast ``codex:auth_updated`` on change.

    When ``force`` is True, the message is always broadcast (used to answer
    a manual "Check again" request from the client).

    Always sets the wake event when the result is False so the auth_task
    resumes/keeps polling.
    """
    global _last_known_authenticated

    authenticated = await check_auth_status()
    changed = authenticated != _last_known_authenticated
    _last_known_authenticated = authenticated

    if changed or force:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": _build_auth_message(authenticated),
            },
        )

    if not authenticated:
        get_auth_wake_event().set()

    return authenticated


async def mark_unauthenticated_and_broadcast() -> None:
    """Force the auth state to ``False`` and broadcast.

    Mirrors :func:`providers.claude_code.auth.mark_unauthenticated_and_broadcast`,
    which is called from the Claude message loop on
    ``AssistantMessage.error == "authentication_failed"``. The Codex
    equivalent is called from :meth:`CodexAgent._handle_stream_event`
    when :meth:`CodexAgent._is_unauthorized_error` matches an incoming
    ``ErrorNotification``. The matching covers three paths because Codex
    upstream surfaces auth failures inconsistently:

    1. ``CodexErrorInfoValue.unauthorized`` (``RefreshTokenFailed``).
    2. ``HttpConnectionFailed`` / ``ResponseStreamConnectionFailed``
       variants with ``http_status_code in {401, 403}``.
    3. ``"status 40[13]"`` substring in the message (covers
       ``CodexErr::UnexpectedStatus(401)`` which falls through to
       ``Other`` — the typical session-resume-on-expired-token case).

    Without this fast path, an expired token would only surface through
    the next ``codex login status`` poll (up to 30s later).
    """
    global _last_known_authenticated

    changed = _last_known_authenticated is not False
    _last_known_authenticated = False

    if changed:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": _build_auth_message(False),
            },
        )

    get_auth_wake_event().set()
