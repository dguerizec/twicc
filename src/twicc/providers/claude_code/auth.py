"""
Claude CLI authentication state and credentials access.

Reads OAuth credentials from the system Keychain (macOS) or
~/.claude/.credentials.json (Linux), exposes the auth state, and
broadcasts changes to connected WebSocket clients.

This module owns all credential reading. Other modules (usage.py,
auth_task.py, ASGI consumer) consume from here.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path

import orjson
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Credentials file path (cross-platform)
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

KEYCHAIN_SERVICE = "Claude Code-credentials"

# Track expiresAt values for which a token refresh has already been attempted
# (and failed), to avoid retrying the SDK call for the same stale token.
_failed_refresh_expires: set[int] = set()

# Cached (token, expiresAt) tuple. Populated by get_credentials() on first
# read, reused on subsequent calls, and invalidated by refresh_token_via_sdk()
# so we don't hit the macOS Keychain (which can prompt for authorization)
# on every usage sync.
_cached_credentials: tuple[str, int] | None = None

# Timeout for the SDK token refresh call
_TOKEN_REFRESH_TIMEOUT = 30

# Cached last known auth state (None = never checked yet)
_last_known_authenticated: bool | None = None

# Event used by the auth_task to break out of its idle (authenticated) state
# when something (manual recheck, SDK auth_failed signal, periodic flip to
# False) suggests the state should be re-checked or polling should resume.
_auth_wake_event: asyncio.Event | None = None

# Timeout for the `claude auth status --json` subprocess call.
_AUTH_STATUS_TIMEOUT = 10


def _read_credentials_from_keychain() -> dict | None:
    """Read credentials from the macOS Keychain via the ``keyring`` library."""
    try:
        import keyring
    except ImportError:
        logger.debug("keyring library not available, skipping Keychain lookup")
        return None

    try:
        account = os.environ.get("USER") or getpass.getuser()
    except Exception:
        logger.debug("Cannot determine user account for Keychain lookup")
        return None

    try:
        raw = keyring.get_password(KEYCHAIN_SERVICE, account)
    except Exception as e:
        logger.debug("Keychain read failed: %s", e)
        return None

    if not raw:
        return None

    try:
        data = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse Keychain credentials JSON: %s", e)
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_from_file() -> dict | None:
    """Read credentials from ~/.claude/.credentials.json."""
    if not CREDENTIALS_PATH.is_file():
        return None

    try:
        data = orjson.loads(CREDENTIALS_PATH.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_data() -> dict | None:
    """
    Read the full credentials dict from the appropriate storage.

    On macOS: tries the system Keychain first (via the ``keyring`` library),
    then falls back to the JSON file.
    On other platforms: reads the JSON file directly.

    Returns the parsed dict, or None if credentials cannot be read.
    """
    if sys.platform == "darwin":
        data = _read_credentials_from_keychain()
        if data is not None:
            return data

    return _read_credentials_from_file()


def get_credentials() -> tuple[str, int] | None:
    """
    Read the OAuth access token and expiresAt from credentials storage.

    Returns the cached value if available; otherwise reads from the
    Keychain (macOS) or file, caches it, and returns. The cache is
    invalidated by ``refresh_token_via_sdk()`` so a refreshed token
    is picked up on the next call.

    Returns:
        A (token, expires_at_ms) tuple, or None if not found.
    """
    global _cached_credentials

    if _cached_credentials is not None:
        return _cached_credentials

    data = _read_credentials_data()
    if data is None:
        logger.warning("No credentials found (checked %s)", "Keychain + file" if sys.platform == "darwin" else "file")
        return None

    oauth = data.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    if not token:
        logger.warning("No OAuth access token found in credentials")
        return None

    expires_at = oauth.get("expiresAt", 0)
    _cached_credentials = (token, expires_at)
    return _cached_credentials


def refresh_token_via_sdk(expires_at: int) -> bool:
    """
    Attempt to refresh the OAuth token by making a throwaway SDK call.

    The SDK automatically refreshes the stored credentials when it connects.
    We send a trivial prompt and discard the response.

    Invalidates the credentials cache before the SDK call, then re-reads
    via ``get_credentials()`` so the cache is repopulated with the
    refreshed token.

    Returns True if the token was refreshed (expiresAt changed), False otherwise.
    """
    global _cached_credentials

    if expires_at in _failed_refresh_expires:
        logger.info("Token refresh already attempted for expiresAt=%d, skipping", expires_at)
        return False

    _failed_refresh_expires.add(expires_at)

    logger.info("Attempting token refresh via SDK (current expiresAt=%d)", expires_at)

    _cached_credentials = None

    try:
        asyncio.run(_sdk_throwaway_call())
    except Exception as e:
        logger.warning("SDK token refresh call failed: %s", e)
        return False

    new_creds = get_credentials()
    new_expires_at = new_creds[1] if new_creds else 0
    if new_expires_at == expires_at:
        logger.warning("Token was not refreshed by SDK (expiresAt unchanged: %d)", expires_at)
        return False

    logger.info("Token refreshed via SDK: expiresAt %d → %d", expires_at, new_expires_at)
    return True


async def _sdk_throwaway_call() -> None:
    """Make a minimal SDK call to trigger token refresh."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

    options = ClaudeAgentOptions(
        model="haiku",
        permission_mode="default",
        extra_args={"no-session-persistence": None},
        allowed_tools=[],
        effort='low',
    )
    client = ClaudeSDKClient(options=options)

    async def _execute():
        await client.connect()
        await client.query("What model are you?")
        async for msg in client.receive_messages():
            if isinstance(msg, ResultMessage):
                break

    try:
        await asyncio.wait_for(_execute(), timeout=_TOKEN_REFRESH_TIMEOUT)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def _build_auth_message(authenticated: bool) -> dict:
    """Build the claude_auth_updated message payload."""
    return {
        "type": "claude_auth_updated",
        "authenticated": authenticated,
    }


def get_last_known_authenticated() -> bool | None:
    """Return the last known auth state (None if never checked yet)."""
    return _last_known_authenticated


def get_auth_wake_event() -> asyncio.Event:
    """
    Get (lazily create) the wake event used to interrupt the auth_task's
    idle state. Set whenever the state is, or may have just become, False.
    """
    global _auth_wake_event
    if _auth_wake_event is None:
        _auth_wake_event = asyncio.Event()
    return _auth_wake_event


async def check_claude_auth_status() -> bool:
    """
    Run ``claude auth status --json`` and return ``loggedIn``.

    Calls the bundled CLI binary directly (the same one the SDK uses). This
    avoids depending on how TwiCC itself was installed (uvx, pip, etc.).

    Returns False on any failure (process error, timeout, missing field,
    invalid JSON). The CLI is the source of truth: it knows whether the
    stored token is still server-side accepted, which we cannot tell by
    just reading the credentials file.
    """
    from twicc.cli.claude import get_claude_binary

    try:
        binary = get_claude_binary()
    except SystemExit:
        # get_claude_binary() prints to stderr and raises SystemExit when
        # the bundled binary is missing — turn that into a False result here.
        logger.warning("Cannot check Claude auth status: bundled CLI not found")
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary), "auth", "status", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        logger.warning("Cannot launch Claude auth status check: %s", e)
        return False

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_AUTH_STATUS_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Claude auth status check timed out after %ds", _AUTH_STATUS_TIMEOUT)
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return False

    if proc.returncode != 0:
        logger.warning(
            "Claude auth status check failed (exit %d): %s",
            proc.returncode,
            stderr.decode(errors="replace").strip(),
        )
        return False

    try:
        data = orjson.loads(stdout)
    except orjson.JSONDecodeError as e:
        logger.warning("Claude auth status returned invalid JSON: %s", e)
        return False

    return bool(data.get("loggedIn"))


async def get_auth_message_for_connection() -> dict:
    """
    Build a claude_auth_updated message for a single client on WS connect.

    Uses the cached state populated by ``auth_task``. If the cache is still
    unknown (very first connection in a fresh process, before the auth_task
    has run its first check), runs one inline so the connecting client
    doesn't briefly see a wrong "not authenticated" state.
    """
    if _last_known_authenticated is None:
        # Populate the cache now via check_and_broadcast (which also
        # updates state and broadcasts on change).
        await check_and_broadcast()
    return _build_auth_message(bool(_last_known_authenticated))


async def check_and_broadcast(*, force: bool = False) -> bool:
    """
    Run a Claude auth status check and broadcast claude_auth_updated on change.

    When ``force`` is True, the message is always broadcast (used to answer
    a manual "Check again" request from the client, where echoing the
    current state to the requester is desirable even without a change).

    Always sets the wake event when the result is False so the auth_task
    resumes/keeps polling.

    Returns the current authenticated bool.
    """
    global _last_known_authenticated

    authenticated = await check_claude_auth_status()
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
    """
    Force the auth state to ``False`` and broadcast.

    Used when an external signal (e.g. the SDK reporting
    ``authentication_failed``) tells us the credentials are no longer
    accepted, even if a fresh ``claude auth status`` would still claim
    ``loggedIn`` (race / stale token).

    Wakes the auth_task so it resumes polling.
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
