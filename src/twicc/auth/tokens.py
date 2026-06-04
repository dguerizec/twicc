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
import os
import secrets
from datetime import datetime, timezone
from typing import NamedTuple

import orjson

from twicc.paths import get_api_tokens_path

_TOKEN_PREFIX = "twicc_pat_"


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


def _write(records: list[TokenRecord]) -> None:
    path = get_api_tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tokens": [r._asdict() for r in records]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
    _write(load_tokens() + [rec])
    return token, rec


def revoke_token(token_id: str) -> bool:
    records = load_tokens()
    kept = [r for r in records if r.id != token_id]
    if len(kept) == len(records):
        return False
    _write(kept)
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
    records = load_tokens()
    changed = False
    new: list[TokenRecord] = []
    for r in records:
        if r.id == token_id:
            new.append(r._replace(last_used_at=_now_iso()))
            changed = True
        else:
            new.append(r)
    if changed:
        _write(new)
