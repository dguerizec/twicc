"""Token/id minting + inbound token resolution for peer messaging.

Same trade-off as shares (``share_tokens.py``): 256-bit secrets stored
plaintext on an indexed column, re-checked with ``hmac.compare_digest`` so the
comparison stays constant-time. Resolution returns the peer regardless of
state — callers apply state policy.
"""

from __future__ import annotations

import hmac
import secrets

from asgiref.sync import sync_to_async


def mint_token() -> str:
    """256-bit URL-safe bearer secret (per direction, design §4.4)."""
    return secrets.token_urlsafe(32)


def mint_message_id() -> str:
    """Wire handle for a peer message, minted by the sender."""
    return "pm_" + secrets.token_hex(8)


def mint_verification_code() -> str:
    """6-digit out-of-band verification code (design §4.2 step 2)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def peer_base_url() -> str:
    """Live-read the ``peerBaseUrl`` synced setting. Empty = feature disabled."""
    from twicc.core.services.public_origin import usable_public_origin
    from twicc.synced_settings import read_synced_settings

    return usable_public_origin(read_synced_settings().get("peerBaseUrl"))


def resolve_peer(token: str):
    """Return the Peer whose ``token_ours`` matches (constant-time compare) or
    ``None``. Sync — call via ``aresolve_peer`` from async views. Does NOT apply
    state policy."""
    from twicc.core.models import Peer

    if not token:
        return None
    peer = Peer.objects.filter(token_ours=token).first()
    if peer is None:
        return None
    if not hmac.compare_digest(peer.token_ours, token):
        return None
    return peer


async def aresolve_peer(token: str):
    return await sync_to_async(resolve_peer)(token)
