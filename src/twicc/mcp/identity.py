"""Caller identity for the MCP endpoint.

A session token is self-describing and deterministic:
``twicc_mcp_<session_id>.<hmac_sha256(secret, session_id)[:32]>``. The secret
is a per-install random file in the data dir, so tokens survive backend
restarts (a hybrid tmux agent outlives the backend and must keep calling
``/mcp`` with the token baked into its launch config) and need no storage or
revocation: they only grant "act as this session on this machine", the same
authority the PID-ancestry CLI grants any local process today.

Brand-new Codex sessions are the one wrinkle: the token is minted against the
frontend draft id (the canonical id only exists once ``thread_start``
returns), so the Codex manager registers a draft→canonical alias right after
the thread starts. The alias map is process-local by design — after a backend
restart the resume path re-wires the session with a token minted against the
canonical id, and the alias is no longer needed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

from twicc import paths

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "twicc_mcp_"
_SIG_LEN = 32  # hex chars = 128 bits, ample for a local HMAC capability

_secret: bytes | None = None
_draft_aliases: dict[str, str] = {}


def _reset_for_tests() -> None:
    global _secret
    _secret = None
    _draft_aliases.clear()


def _get_secret() -> bytes:
    """Read (or create once) the per-install signing secret, cached."""
    global _secret
    if _secret is None:
        path = paths.get_mcp_secret_path()
        try:
            _secret = bytes.fromhex(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            _secret = secrets.token_bytes(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(_secret.hex())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    return _secret


def _sign(session_id: str) -> str:
    mac = hmac.new(_get_secret(), f"mcp:{session_id}".encode(), hashlib.sha256)
    return mac.hexdigest()[:_SIG_LEN]


def mint_session_token(session_id: str) -> str:
    return f"{TOKEN_PREFIX}{session_id}.{_sign(session_id)}"


def resolve_session_token(token: str) -> str | None:
    """Return the (alias-resolved) session id, or None if invalid."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    session_id, sep, sig = token.removeprefix(TOKEN_PREFIX).rpartition(".")
    if not sep or not session_id:
        return None
    if not hmac.compare_digest(sig, _sign(session_id)):
        return None
    return _draft_aliases.get(session_id, session_id)


def register_draft_alias(draft_id: str, canonical_id: str) -> None:
    """Map a Codex draft session id to the canonical id minted by thread_start."""
    if draft_id != canonical_id:
        _draft_aliases[draft_id] = canonical_id
        logger.info("MCP identity: draft %s aliased to %s", draft_id, canonical_id)
