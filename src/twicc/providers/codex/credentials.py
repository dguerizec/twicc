"""
Codex CLI OAuth credentials access.

Reads the ChatGPT OAuth token + account id from the Codex CLI's
credential storage so the usage fetcher can call ChatGPT's
``/backend-api/wham/usage`` endpoint with them.

The Codex CLI stores credentials in one of two places, controlled by
its ``cli_auth_credentials_store`` config (default ``file``):

- **File** (``~/.codex/auth.json``): plain JSON with the shape
  ``{"auth_mode": "chatgpt", "tokens": {"access_token": ..., "account_id": ..., "refresh_token": ...}, "last_refresh": "..."}``.
- **Keyring** (any OS): a single ``"Codex Auth"`` service entry whose
  account name is ``"cli|" + sha256(canonical(~/.codex))[:16]`` and
  whose value is the **same JSON blob** that would otherwise live in
  ``auth.json``. In keyring mode the file is removed by the CLI after
  every successful save, so a third-party reader must check the
  keyring when the file is absent.

Mirrors the surface of :mod:`twicc.providers.claude_code.auth` for the
credential half — auth-state tracking (``codex login status`` polling)
lives separately in :mod:`twicc.providers.codex.auth`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import NamedTuple

import orjson
from codex_app_server import (
    AppServerConfig,
    AskForApproval,
    AsyncCodex,
    ReasoningEffort,
    SandboxMode,
    TextInput,
)

from .bin import resolve_bundled_binary

logger = logging.getLogger(__name__)

# Default Codex home — credentials live under here whichever storage
# backend is in use.
CODEX_HOME = Path.home() / ".codex"

# File path used when ``cli_auth_credentials_store = "file"`` (default)
# or as a fallback in ``"auto"`` mode.
CREDENTIALS_PATH = CODEX_HOME / "auth.json"

# Keyring service name shared across OSes (constant ``KEYRING_SERVICE`` in
# ``codex-rs/login/src/auth/storage.rs``). The account name is computed
# per Codex home (see :func:`_compute_keyring_account`).
KEYRING_SERVICE = "Codex Auth"

# Cached parsed credentials. Populated on first ``get_credentials()`` call
# and reused on subsequent calls. Invalidated by :func:`refresh_token_via_codex_sdk`
# so a refreshed token is picked up on the next call.
_cached_credentials: "Credentials | None" = None

# Track ``last_refresh`` values for which a refresh has already been
# attempted (and failed), to avoid retrying the SDK throwaway call for
# the same stale token. Mirrors the equivalent guard in the Claude Code
# refresh path.
_failed_refresh_keys: set[str] = set()

# Timeout (seconds) for the throwaway SDK turn used to nudge the Codex
# binary into refreshing its tokens — covers the full spawn + initialize
# + thread_start + turn streaming round-trip.
_TOKEN_REFRESH_TIMEOUT = 30

# Fixed model + prompt for the throwaway turn. We don't care about the
# answer; the act of opening a thread and running one turn is what makes
# the codex-app-server binary check / refresh its OAuth tokens.
_REFRESH_MODEL = "gpt-5.4-mini"
_REFRESH_PROMPT = "What model are you?"


class Credentials(NamedTuple):
    """Codex OAuth credentials extracted from the CLI's storage.

    ``last_refresh`` is the raw string from the source (or empty when
    absent) — used purely as a cache invalidation key, never parsed.
    """
    access_token: str
    account_id: str
    last_refresh: str


def _compute_keyring_account(codex_home: Path) -> str:
    """Return the keyring account name for ``codex_home``.

    Mirrors ``compute_store_key`` in ``codex-rs/login/src/auth/storage.rs``:
    ``"cli|" + sha256(canonical_path)[:16]``. ``resolve()`` is the
    Python equivalent of Rust's ``canonicalize`` and resolves symlinks
    when possible — the Rust code falls back to the unresolved path on
    canonicalisation failure, so we mirror that with a try/except.
    """
    try:
        canonical = str(codex_home.resolve(strict=False))
    except OSError:
        canonical = str(codex_home)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"cli|{digest[:16]}"


def _read_credentials_from_keyring() -> dict | None:
    """Read the Codex auth blob from the OS keyring, or ``None`` when absent.

    Cross-platform via the ``keyring`` library (macOS Keychain, Linux
    Secret Service, Windows Credential Manager). Returns the parsed
    JSON dict — the value Codex stores is the full ``auth.json``
    payload serialised as a single string.
    """
    try:
        import keyring
    except ImportError:
        logger.debug("keyring library not available, skipping Codex keyring lookup")
        return None

    account = _compute_keyring_account(CODEX_HOME)

    try:
        raw = keyring.get_password(KEYRING_SERVICE, account)
    except Exception as e:
        logger.debug("Codex keyring read failed: %s", e)
        return None

    if not raw:
        return None

    try:
        data = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse Codex keyring credentials JSON: %s", e)
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_from_file() -> dict | None:
    """Read ``~/.codex/auth.json`` and return the parsed dict, or ``None``."""
    if not CREDENTIALS_PATH.is_file():
        return None

    try:
        data = orjson.loads(CREDENTIALS_PATH.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None


def _read_credentials_data() -> dict | None:
    """Read the full Codex credentials dict from whichever storage is in use.

    Tries the file first (the default backend); on miss, falls back to
    the keyring (which is what's used when the user has set
    ``cli_auth_credentials_store = keyring`` and the CLI has wiped the
    file). Trying file first keeps the common path cheap (no keychain
    auth prompt on macOS).
    """
    data = _read_credentials_from_file()
    if data is not None:
        return data

    return _read_credentials_from_keyring()


def get_credentials() -> Credentials | None:
    """Return the cached :class:`Credentials`, reading storage on first call.

    The cache is invalidated by :func:`refresh_token_via_codex_sdk` so a
    refreshed token is picked up on the next call without paying the
    keyring/file read cost on every usage sync.

    Returns ``None`` when:
    - no Codex credentials exist (CLI never logged in),
    - the file/keyring blob is missing the ``tokens`` block,
    - or ``access_token`` / ``account_id`` is unset (e.g. ``auth_mode``
      is ``apikey``, where the user authenticates with an API key
      instead of ChatGPT — the usage endpoint requires the OAuth tokens).
    """
    global _cached_credentials

    if _cached_credentials is not None:
        return _cached_credentials

    data = _read_credentials_data()
    if data is None:
        logger.warning("No Codex credentials found (checked file + keyring)")
        return None

    tokens = data.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")

    if not access_token or not account_id:
        logger.warning(
            "Codex credentials missing access_token or account_id (auth_mode=%s)",
            data.get("auth_mode"),
        )
        return None

    _cached_credentials = Credentials(
        access_token=access_token,
        account_id=account_id,
        last_refresh=data.get("last_refresh") or "",
    )
    return _cached_credentials


def invalidate_credentials_cache() -> None:
    """Drop the cached credentials so the next read goes back to storage.

    Call this after any external action that may have rewritten
    ``auth.json`` / the keyring blob (notably the Codex CLI refreshing
    its own tokens).
    """
    global _cached_credentials
    _cached_credentials = None


def refresh_token_via_codex_sdk(last_refresh: str) -> bool:
    """Attempt to refresh the Codex OAuth token by running a throwaway SDK turn.

    Mirrors the Claude Code refresh path: spin up a one-shot AsyncCodex
    session against the wheel-bundled binary, run an ephemeral
    ``thread.turn`` with a trivial prompt, drain the stream, and let the
    codex-app-server binary refresh-and-rewrite ``auth.json`` /
    keyring blob during its session bootstrap. We discard the answer.

    ``last_refresh`` is the value seen on the failed call; we use it as
    a cache key so a permanently-stale token doesn't trigger an endless
    refresh loop. The credential cache is invalidated before the SDK
    call, then re-read after to detect whether ``last_refresh`` actually
    moved forward — that's the success signal, since the binary doesn't
    surface a refresh outcome on its own channel.

    Returns ``True`` when ``last_refresh`` changed (refresh succeeded),
    ``False`` otherwise.
    """
    if last_refresh and last_refresh in _failed_refresh_keys:
        logger.info("Codex token refresh already attempted for last_refresh=%s, skipping", last_refresh)
        return False

    if last_refresh:
        _failed_refresh_keys.add(last_refresh)

    logger.info("Attempting Codex token refresh via throwaway SDK turn (current last_refresh=%s)", last_refresh)

    invalidate_credentials_cache()

    try:
        asyncio.run(
            asyncio.wait_for(_codex_sdk_throwaway_call(), timeout=_TOKEN_REFRESH_TIMEOUT),
        )
    except asyncio.TimeoutError:
        logger.warning("Codex SDK refresh call timed out after %ds", _TOKEN_REFRESH_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("Codex SDK refresh call failed: %s", e)
        return False

    new_creds = get_credentials()
    new_last_refresh = new_creds.last_refresh if new_creds else ""
    if new_last_refresh == last_refresh:
        logger.warning(
            "Codex token was not refreshed by SDK turn (last_refresh unchanged: %s)",
            last_refresh,
        )
        return False

    logger.info("Codex token refreshed via SDK turn: last_refresh %s → %s", last_refresh, new_last_refresh)
    return True


async def _codex_sdk_throwaway_call() -> None:
    """Run one ephemeral AsyncCodex turn against the bundled binary.

    The point is the side effect: launching the codex-app-server
    subprocess, walking its initialize handshake, and running a real
    turn forces the binary to validate its OAuth tokens against the
    upstream API. A stale ``access_token`` triggers the binary's
    built-in refresh-and-retry path, which rewrites the credentials
    store. We drain the stream so the turn completes cleanly and the
    transport closes without leaking the subprocess.
    """
    bundled_bin = resolve_bundled_binary()
    config = AppServerConfig(codex_bin=str(bundled_bin))
    async with AsyncCodex(config=config) as codex:
        thread = await codex.thread_start(
            model=_REFRESH_MODEL,
            ephemeral=True,
            sandbox=SandboxMode.danger_full_access,
            approval_policy=AskForApproval.model_validate("never"),
        )
        turn_handle = await thread.turn(
            TextInput(_REFRESH_PROMPT),
            effort=ReasoningEffort.low,
        )
        async for _event in turn_handle.stream():
            pass  # drain — we don't care about the reply, just the side effect
