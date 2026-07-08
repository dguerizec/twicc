"""Token minting + resolution for shares.

The token is the URL secret (O2: stored plaintext). Resolution fetches by the
indexed ``token`` column, then re-checks with ``hmac.compare_digest`` so the
comparison stays constant-time even though the column is indexed. Callers apply
the revoked/expired policy themselves (uniform 404) — this only resolves identity.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from asgiref.sync import sync_to_async


def mint_token() -> str:
    """256-bit URL-safe secret. ~43 chars, matches the ``[A-Za-z0-9_-]{20,}`` route regex."""
    return secrets.token_urlsafe(32)


def mint_share_id() -> str:
    return "shr_" + secrets.token_hex(4)


def password_fingerprint(password_hash: str) -> str:
    """Short one-way digest of a share's password hash — stored in the viewer's
    Django session so rotating the password invalidates the grant (design §7.2).
    Mirrors ``auth/session_auth.compute_fingerprint``."""
    if not password_hash:
        return ""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def resolve_share(token: str):
    """Return the Share for ``token`` (constant-time compare) or ``None``.
    Sync — call via ``sync_to_async`` from async views. Does NOT apply
    revoked/expired policy."""
    from twicc.core.models import Share

    if not token:
        return None
    share = Share.objects.filter(token=token).first()
    if share is None:
        return None
    if not hmac.compare_digest(share.token, token):
        return None
    return share


async def aresolve_share(token: str):
    return await sync_to_async(resolve_share)(token)
