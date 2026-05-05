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
# and reused on subsequent calls. Invalidated by :func:`refresh_token_via_codex_cli`
# so a refreshed token is picked up on the next call.
_cached_credentials: "Credentials | None" = None

# Track ``last_refresh`` values for which a refresh has already been
# attempted (and failed), to avoid retrying the subprocess call for the
# same stale token. Mirrors the equivalent guard in the Claude Code
# refresh path.
_failed_refresh_keys: set[str] = set()

# Timeout for the ``codex login status`` subprocess call used to nudge
# the CLI into refreshing its tokens.
_TOKEN_REFRESH_TIMEOUT = 30


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

    The cache is invalidated by :func:`refresh_token_via_codex_cli` so a
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


def refresh_token_via_codex_cli(last_refresh: str) -> bool:
    """Attempt to refresh the Codex OAuth token by nudging the Codex CLI.

    Codex doesn't ship a Python SDK, so we shell out to the ``codex``
    binary on the user's PATH. The CLI has a built-in refresh-and-retry
    path on 401 and rewrites ``auth.json`` (or the keyring blob) with
    the fresh tokens. We use ``codex login status`` as the nudge — it's
    the lightest CLI command that touches the auth subsystem.

    ``last_refresh`` is the value seen on the failed call; we use it as
    a cache key so a permanently-stale token doesn't trigger an endless
    refresh loop. The cache is invalidated before the subprocess call,
    then re-read after to detect whether ``last_refresh`` actually moved
    forward — that's the success signal, since the CLI doesn't surface a
    refresh outcome on stdout.

    Returns ``True`` when ``last_refresh`` changed (refresh succeeded),
    ``False`` otherwise.
    """
    if last_refresh and last_refresh in _failed_refresh_keys:
        logger.info("Codex token refresh already attempted for last_refresh=%s, skipping", last_refresh)
        return False

    if last_refresh:
        _failed_refresh_keys.add(last_refresh)

    logger.info("Attempting Codex token refresh via 'codex login status' (current last_refresh=%s)", last_refresh)

    invalidate_credentials_cache()

    try:
        asyncio.run(_codex_status_throwaway_call())
    except Exception as e:
        logger.warning("Codex CLI refresh call failed: %s", e)
        return False

    new_creds = get_credentials()
    new_last_refresh = new_creds.last_refresh if new_creds else ""
    if new_last_refresh == last_refresh:
        logger.warning(
            "Codex token was not refreshed by CLI (last_refresh unchanged: %s)",
            last_refresh,
        )
        return False

    logger.info("Codex token refreshed via CLI: last_refresh %s → %s", last_refresh, new_last_refresh)
    return True


async def _codex_status_throwaway_call() -> None:
    """Run ``codex login status`` to nudge the CLI into refreshing tokens."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "codex", "login", "status",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise RuntimeError("'codex' binary not found on PATH") from e

    try:
        await asyncio.wait_for(proc.wait(), timeout=_TOKEN_REFRESH_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise RuntimeError(f"'codex login status' timed out after {_TOKEN_REFRESH_TIMEOUT}s")
