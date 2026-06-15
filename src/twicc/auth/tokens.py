"""API token store for the RPC API.

Tokens authenticate ``/rpc/`` calls (HTTP Bearer). A token is a high-entropy
random string; only its SHA-256 digest is stored. A fast digest is the right
choice here (not PBKDF2): a 256-bit random token is not brute-forceable, so
the slow KDF that protects low-entropy passwords buys nothing and would cost
N×600k iterations per request. Lookup is O(1) with a constant-time compare.

The store is a JSON file in the data dir, ``chmod 600``. The request-time
accessors are mtime-cached so a token created by the CLI (a separate process)
takes effect in the running backend without a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import NamedTuple

import orjson

from twicc.atomic_json import locked_json_file
from twicc.paths import get_api_tokens_path

_TOKEN_PREFIX = "twicc_pat_"

# Empty on-disk store shape. ``locked_json_file`` deep-copies the default when
# the file is absent, so this module-level literal is never mutated.
_EMPTY_STORE = {"version": 1, "tokens": []}


class TokenRecord(NamedTuple):
    id: str
    name: str
    digest: str
    created_at: str
    last_used_at: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _read_raw() -> dict:
    path = get_api_tokens_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, ValueError, OSError):
        return {"version": 1, "tokens": []}


def load_tokens() -> list[TokenRecord]:
    """Read the store fresh from disk (used by the CLI for read-modify-write)."""
    out: list[TokenRecord] = []
    for t in _read_raw().get("tokens", []):
        try:
            out.append(
                TokenRecord(
                    id=t["id"],
                    name=t.get("name", ""),
                    digest=t["digest"],
                    created_at=t.get("created_at", ""),
                    last_used_at=t.get("last_used_at"),
                )
            )
        except (KeyError, TypeError):
            continue
    return out


# --- mtime cache for the request hot path -------------------------------

_cache: dict = {"mtime": object(), "records": []}


def load_tokens_cached() -> list[TokenRecord]:
    """Read the store with an mtime cache (used per request by the middleware)."""
    path = get_api_tokens_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        _cache["records"] = load_tokens()
        _cache["mtime"] = mtime
    return _cache["records"]


# --- public API ----------------------------------------------------------

def create_token(name: str) -> tuple[str, TokenRecord]:
    """Mint a token; return ``(plaintext, record)``. Only the digest is stored."""
    token = generate_token()
    rec = TokenRecord(
        id="tok_" + secrets.token_hex(4),
        name=name,
        digest=digest_token(token),
        created_at=_now_iso(),
        last_used_at=None,
    )
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        txn.data.setdefault("version", 1)
        txn.data.setdefault("tokens", []).append(rec._asdict())
        txn.write()
    return token, rec


def revoke_token(token_id: str) -> bool:
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        tokens = txn.data.get("tokens", [])
        kept = [t for t in tokens if not (isinstance(t, dict) and t.get("id") == token_id)]
        if len(kept) == len(tokens):
            return False
        txn.data["tokens"] = kept
        txn.write()
        return True


def has_tokens() -> bool:
    return bool(load_tokens_cached())


def verify_token(token: str) -> TokenRecord | None:
    """Return the matching record (constant-time compare) or ``None``."""
    if not token:
        return None
    candidate = digest_token(token)
    match: TokenRecord | None = None
    for rec in load_tokens_cached():
        if hmac.compare_digest(candidate, rec.digest):
            match = rec
    return match


def touch_last_used(token_id: str) -> None:
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        changed = False
        for t in txn.data.get("tokens", []):
            if isinstance(t, dict) and t.get("id") == token_id:
                t["last_used_at"] = _now_iso()
                changed = True
        if changed:
            txn.write()
