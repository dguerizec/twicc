"""Peer relationship mutations (create / verify / accept / refuse / rename / delete).

Single source of truth for the two surfaces that mutate ``Peer``:
- the owner REST endpoints (``/api/peers/…``, human-only — no CLI/MCP surface
  for relationship management, by design), and
- the inbound instance-to-instance endpoints (``/peer/handshake/…``).

Mirrors ``share_mutation.py``: results are NamedTuples (never raise for
business-rule errors), writes run under ``run_under_db_write_lock``, broadcasts
go out AFTER the lock is released.

Activation race note (design §4.2): on requester-side rows,
``active ⇔ code_confirmed_at AND remote_accepted_at`` is split across two
writers (the inbound accept callback and the local code submission). EVERY
mutation therefore re-fetches the Peer INSIDE the write lock and evaluates
activation from the fresh fields — whichever write lands second flips the row
to ``active``. Never decide from an instance fetched before the lock.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.services.peer_tokens import mint_token, mint_verification_code, peer_base_url
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

# Failed code echoes before the code is regenerated; regenerations before the
# pending request is dropped entirely (hard guess ceiling: ≤ 15 total guesses).
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_MAX_REGENS = 3

# Junk unauthenticated requests can crowd out legitimate ones at this cap —
# acceptable: the manager UI lets the user refuse/clear pending rows at any time.
MAX_PENDING_RECEIVED = 20


class PeerError(NamedTuple):
    field: str
    code: str
    message: str


class PeerMutationResult(NamedTuple):
    success: bool
    peer_id: str | None
    errors: list[PeerError] | None


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def valid_base_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def own_display_name() -> str:
    """The name this instance advertises in handshakes: the ``peerDisplayName``
    synced setting, falling back to the hostname of our own ``peerBaseUrl``."""
    from twicc.synced_settings import read_synced_settings

    name = (read_synced_settings().get("peerDisplayName") or "").strip()
    if name:
        return name
    from twicc.core.services.public_origin import normalize_public_origin

    return normalize_public_origin(peer_base_url()).hostname or "twicc"


# ── Broadcasts ──────────────────────────────────────────────────────────────
# serialize_peer is pure attribute access (no queries), safe to call directly
# from async. ShareConsumer's broadcast whitelist silently drops every peer_*
# type — load-bearing: verification codes must never reach share viewers.

async def _broadcast(data: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {"type": "broadcast", "data": data})


async def broadcast_peer_updated(peer) -> None:
    from twicc.core.serializers import serialize_peer

    await _broadcast({"type": "peer_updated", "peer": serialize_peer(peer)})


async def broadcast_peer_removed(peer_id: str) -> None:
    await _broadcast({"type": "peer_removed", "peer_id": peer_id})


async def broadcast_peer_request_received(peer) -> None:
    from twicc.core.serializers import serialize_peer

    await _broadcast({"type": "peer_request_received", "peer": serialize_peer(peer)})


async def broadcast_peer_accepted(peer) -> None:
    from twicc.core.serializers import serialize_peer

    await _broadcast({"type": "peer_accepted", "peer": serialize_peer(peer)})


async def _emit(action: str | None, obj) -> None:
    """Dispatch one deferred broadcast decided inside a locked write."""
    if action is None:
        return
    if action == "peer_removed":
        await broadcast_peer_removed(obj)
    elif action == "peer_updated":
        await broadcast_peer_updated(obj)
    elif action == "peer_request_received":
        await broadcast_peer_request_received(obj)
    elif action == "peer_accepted":
        await broadcast_peer_accepted(obj)


# ── Owner-side mutations (REST) ─────────────────────────────────────────────

async def create_peer_and_request(*, name: str, base_url: str) -> PeerMutationResult:
    """Create a ``pending_sent`` row and POST the handshake request. On any
    outbound failure the row is deleted — the user retries the whole add."""
    from twicc.core.models import Peer, PeerState
    from twicc.peer import outbound

    own_url = peer_base_url()
    if not own_url:
        return PeerMutationResult(False, None, [PeerError(
            "base_url", "peer_host_unset", "Configure your peer address in Settings → Peers first.",
        )])
    base_url = normalize_base_url(base_url)
    if not valid_base_url(base_url):
        return PeerMutationResult(False, None, [PeerError(
            "base_url", "invalid_url", "The peer address must be an absolute http(s) URL.",
        )])
    duplicate = await sync_to_async(
        lambda: Peer.objects.filter(base_url=base_url).exclude(state=PeerState.BROKEN).exists()
    )()
    if duplicate:
        return PeerMutationResult(False, None, [PeerError(
            "base_url", "duplicate", "A peer with this address already exists.",
        )])

    token = mint_token()
    peer = Peer(
        name=(name or "").strip(),
        base_url=base_url,
        state=PeerState.PENDING_SENT,
        token_ours=token,
    )
    await run_under_db_write_lock(lambda: peer.asave(force_insert=True))

    detail = ""
    try:
        status, _body = await outbound.post_handshake_request(
            base_url, display_name=own_display_name(), own_base_url=own_url, token=token,
        )
    except outbound.PeerOutboundError as exc:
        status, detail = None, str(exc)

    # The peer's own CROSSED request can land during the outbound window and
    # flip this row to pending_received (register_incoming_request). Every
    # decision below therefore re-fetches — acting on the pre-call instance
    # would broadcast stale state or delete the peer's just-registered request.
    if status is None or status >= 400:
        def _delete_if_untouched():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return False
            if fresh.state == PeerState.PENDING_SENT and fresh.token_theirs is None:
                fresh.delete()
                return False
            return True  # crossed flip landed — keep the row (their request is real)

        kept = await run_under_db_write_lock(lambda: sync_to_async(_delete_if_untouched)())
        if not kept:
            return PeerMutationResult(False, None, [PeerError(
                "base_url", "unreachable", detail or f"http_{status}",
            )])
        # Our request failed but theirs arrived: surface the failure, the
        # incoming request lives its own life (already broadcast).
        return PeerMutationResult(False, peer.id, [PeerError(
            "base_url", "unreachable", detail or f"http_{status}",
        )])

    fresh = await sync_to_async(lambda: Peer.objects.filter(pk=peer.pk).first())()
    if fresh is None:
        return PeerMutationResult(False, None, [PeerError("peer", "not_found", "Peer no longer exists.")])
    await broadcast_peer_updated(fresh)
    logger.info("[peer_create] id=%s base_url=%s", fresh.id, base_url)
    return PeerMutationResult(True, fresh.id, None)


async def accept_peer(peer, *, name: str) -> PeerMutationResult:
    """Accept an incoming request: mint (or, after a crossed handshake, reuse)
    ``token_ours``, call back the requester, then activate locally."""
    from twicc.core.models import Peer, PeerState
    from twicc.peer import outbound

    if peer.state == PeerState.ACTIVE:
        # Idempotent no-op — happens after a crossed handshake resolved from the other side.
        return PeerMutationResult(True, peer.id, None)
    if peer.state != PeerState.PENDING_RECEIVED:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer is not pending acceptance.")])
    if peer.verified_at is None:
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "not_verified", "The requester has not confirmed the verification code yet.",
        )])

    # Reuse an existing token — after a crossed handshake the other side already
    # knows the one we minted for our own outbound request; never re-mint. When
    # minting fresh, PERSIST it before the outbound call: if the requester
    # processes the accept but our 200 is lost, the retry must present the SAME
    # token (their now-active row compares it) — a fresh mint per attempt would
    # wedge the handshake in a permanent 409/pending_received dead end.
    token = peer.token_ours
    if token is None:
        minted = mint_token()

        def _persist_token():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return None
            if fresh.token_ours:
                return fresh.token_ours
            fresh.token_ours = minted
            fresh.save(update_fields=["token_ours"])
            return minted

        token = await run_under_db_write_lock(lambda: sync_to_async(_persist_token)())
        if token is None:
            return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])

    detail = ""
    try:
        status, _body = await outbound.post_handshake_accept(
            peer.base_url, bearer=peer.token_theirs, token=token, display_name=own_display_name(),
        )
    except outbound.PeerOutboundError as exc:
        status, detail = None, str(exc)
    if status is None or status >= 400:
        # Row stays pending_received (token kept for the retry), the user retries.
        return PeerMutationResult(False, peer.id, [PeerError(
            "base_url", "unreachable", detail or f"http_{status}",
        )])

    clean_name = (name or "").strip()
    now = _now()

    def _apply():
        fresh = Peer.objects.filter(pk=peer.pk).first()
        if fresh is None:
            return None
        if clean_name:
            fresh.name = clean_name
        fresh.token_ours = token
        fresh.state = PeerState.ACTIVE
        fresh.accepted_at = now
        fresh.last_contact_at = now
        # Deliberately NOT clearing verification_code: handshake_verify stays
        # idempotent on active rows (held-accept recovery on the requester side).
        fresh.save(update_fields=["name", "token_ours", "state", "accepted_at", "last_contact_at"])
        return fresh

    fresh = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    if fresh is None:
        return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])
    await broadcast_peer_updated(fresh)
    logger.info("[peer_accept] id=%s", fresh.id)
    return PeerMutationResult(True, fresh.id, None)


async def submit_verification_code(peer, code: str) -> PeerMutationResult:
    """Requester-side: echo the out-of-band code to the peer. On success the row
    records ``code_confirmed_at`` and activates if the accept was already held."""
    from twicc.core.models import Peer, PeerState
    from twicc.peer import outbound

    code = (code or "").strip()
    # pending_received with a non-null token_ours = crossed row acting as
    # requester for its outbound leg.
    allowed = peer.state == PeerState.PENDING_SENT or (
        peer.state == PeerState.PENDING_RECEIVED and peer.token_ours
    )
    if not allowed:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer has no outbound request to verify.")])

    try:
        status, body = await outbound.post_handshake_verify(peer.base_url, bearer=peer.token_ours, code=code)
    except outbound.PeerOutboundError as exc:
        return PeerMutationResult(False, peer.id, [PeerError("code", "unreachable", str(exc))])

    if status == 200:
        now = _now()

        def _apply():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return None, False
            if fresh.code_confirmed_at is None:
                fresh.code_confirmed_at = now
            activated = False
            # Activation race note (module docstring): decide from the FRESH row.
            if fresh.state == PeerState.PENDING_SENT and fresh.remote_accepted_at is not None:
                fresh.state = PeerState.ACTIVE
                fresh.accepted_at = now
                activated = True
            fresh.save(update_fields=["code_confirmed_at", "state", "accepted_at"])
            return fresh, activated

        fresh, activated = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
        if fresh is None:
            return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])
        await broadcast_peer_updated(fresh)
        if activated:
            await broadcast_peer_accepted(fresh)
        return PeerMutationResult(True, fresh.id, None)

    remote_error = (body or {}).get("error") if status == 403 else None
    if remote_error == "bad_code":
        return PeerMutationResult(False, peer.id, [PeerError("code", "bad_code", "Wrong code — check with your peer.")])
    if remote_error == "too_many_attempts":
        return PeerMutationResult(False, peer.id, [PeerError(
            "code", "code_regenerated", "Too many attempts — the peer's code was regenerated, ask them for the new one.",
        )])
    if remote_error == "unknown_token":
        return PeerMutationResult(False, peer.id, [PeerError(
            "code", "relationship_gone",
            "The peer no longer has this pending request — ask them to check their side, or remove and re-add the peer.",
        )])
    return PeerMutationResult(False, peer.id, [PeerError(
        "code", "verify_failed", "Verification could not be completed — check the relationship with your peer.",
    )])


async def refuse_peer(peer) -> PeerMutationResult:
    """Silent local delete of a pending_received request (design: silence works)."""
    from twicc.core.models import PeerState

    if peer.state != PeerState.PENDING_RECEIVED:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer is not pending acceptance.")])
    peer_id = peer.id
    await run_under_db_write_lock(lambda: peer.adelete())
    await broadcast_peer_removed(peer_id)
    return PeerMutationResult(True, peer_id, None)


async def rename_peer(peer, name: str) -> PeerMutationResult:
    peer.name = (name or "").strip()
    await run_under_db_write_lock(lambda: peer.asave(update_fields=["name"]))
    await broadcast_peer_updated(peer)
    return PeerMutationResult(True, peer.id, None)


async def delete_peer(peer) -> PeerMutationResult:
    """Silent revocation, any state. The CASCADE deletes the peer's message
    history with it — a recorded decision (design §3.2): "history forever" holds
    for the lifetime of the relationship."""
    peer_id = peer.id
    await run_under_db_write_lock(lambda: peer.adelete())
    await broadcast_peer_removed(peer_id)
    logger.info("[peer_delete] id=%s", peer_id)
    return PeerMutationResult(True, peer_id, None)


def mark_peer_broken(peer) -> None:
    """Sync body — async callers MUST wrap it in ``sync_to_async`` under the
    write lock like every other mutation. Caller broadcasts."""
    from twicc.core.models import PeerState

    peer.state = PeerState.BROKEN
    peer.save(update_fields=["state"])


# ── Inbound-side writes (instance-to-instance endpoints) ────────────────────
# Each returns ``(http_status, response_body)`` after re-fetching inside the
# lock and emitting the decided broadcast outside it — views stay thin.

async def register_incoming_request(*, display_name: str, base_url: str, token: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/request/`` (unauthenticated)."""
    from twicc.core.models import Peer, PeerState

    base_url = normalize_base_url(base_url)

    def _apply():
        row = Peer.objects.exclude(state=PeerState.BROKEN).filter(base_url=base_url).first()
        if row is not None:
            if row.state == PeerState.PENDING_RECEIVED:
                if row.verified_at is not None:
                    # This endpoint is unauthenticated and dedups by base_url
                    # alone — a forged re-request must not strip a completed
                    # verification or swap the bound token. No mutation.
                    return 200, {}, None, None
                row.remote_display_name = display_name
                row.token_theirs = token
                row.verification_code = mint_verification_code()
                row.verification_attempts = 0
                row.verification_regens = 0
                row.save(update_fields=[
                    "remote_display_name", "token_theirs", "verification_code",
                    "verification_attempts", "verification_regens",
                ])
                return 200, {}, "peer_updated", row
            if row.state == PeerState.PENDING_SENT:
                # Crossed handshake (both users added each other): merge into
                # this row. Keep our minted token_ours — accept_peer reuses it.
                # No exemption from the code: the user verifies + accepts
                # exactly like any incoming request (design §4.2).
                row.remote_display_name = display_name
                row.token_theirs = token
                row.state = PeerState.PENDING_RECEIVED
                row.verification_code = mint_verification_code()
                row.verification_attempts = 0
                row.verification_regens = 0
                row.save(update_fields=[
                    "remote_display_name", "token_theirs", "state", "verification_code",
                    "verification_attempts", "verification_regens",
                ])
                return 200, {}, "peer_request_received", row
            # active
            return 409, {"error": "already_related"}, None, None
        if Peer.objects.filter(state=PeerState.PENDING_RECEIVED).count() >= MAX_PENDING_RECEIVED:
            return 429, {"error": "too_many_pending"}, None, None
        peer = Peer(
            state=PeerState.PENDING_RECEIVED,
            name="",
            remote_display_name=display_name,
            base_url=base_url,
            token_theirs=token,
            token_ours=None,
            verification_code=mint_verification_code(),
        )
        peer.save(force_insert=True)
        return 201, {}, "peer_request_received", peer

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body


async def record_verification_attempt(peer_id: str, code: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/verify/`` (the requester echoes the
    out-of-band code). Constant-time compare; hard guess ceiling."""
    from twicc.core.models import Peer, PeerState

    def _apply():
        peer = Peer.objects.filter(
            pk=peer_id, state__in=[PeerState.PENDING_RECEIVED, PeerState.ACTIVE],
        ).first()
        if peer is None:
            return 403, {"error": "unknown_token"}, None, None
        if hmac.compare_digest(peer.verification_code or "", code):
            if peer.state == PeerState.ACTIVE:
                # Pure no-op: idempotent across the accept transition, so a
                # requester whose earlier verify 200 was lost can recover the
                # held accept after the acceptor already went active.
                return 200, {}, None, None
            if peer.verified_at is None:
                peer.verified_at = _now()
            peer.verification_attempts = 0
            peer.save(update_fields=["verified_at", "verification_attempts"])
            return 200, {}, "peer_updated", peer
        if peer.state == PeerState.ACTIVE:
            # Never let mismatches damage an established relationship.
            return 403, {"error": "bad_code"}, None, None
        peer.verification_attempts += 1
        if peer.verification_attempts >= VERIFICATION_MAX_ATTEMPTS:
            peer.verification_regens += 1
            if peer.verification_regens >= VERIFICATION_MAX_REGENS:
                # "5-then-regenerate" alone is an unbounded guessing loop —
                # drop the pending request entirely (silent-refusal semantics).
                dropped_id = peer.id
                peer.delete()
                return 403, {"error": "too_many_attempts"}, "peer_removed", dropped_id
            peer.verification_code = mint_verification_code()
            peer.verification_attempts = 0
            peer.save(update_fields=["verification_code", "verification_attempts", "verification_regens"])
            # The owner sees the new code live.
            return 403, {"error": "too_many_attempts"}, "peer_updated", peer
        peer.save(update_fields=["verification_attempts"])
        return 403, {"error": "bad_code"}, None, None

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body


async def apply_handshake_accept(peer_id: str, *, token: str, display_name: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/accept/`` (requester side)."""
    from twicc.core.models import Peer, PeerState

    def _apply():
        peer = Peer.objects.filter(pk=peer_id).first()
        if peer is None:
            return 403, {"error": "unknown_token"}, None, None
        now = _now()
        if peer.state == PeerState.PENDING_SENT:
            peer.token_theirs = token
            peer.remote_display_name = display_name
            if peer.code_confirmed_at is not None:
                # Honest flow: the acceptor cannot accept before our code
                # submission succeeded.
                peer.state = PeerState.ACTIVE
                peer.accepted_at = now
                peer.last_contact_at = now
                peer.save(update_fields=[
                    "token_theirs", "remote_display_name", "state", "accepted_at", "last_contact_at",
                ])
                return 200, {}, "peer_accepted", peer
            # Held accept: an accept from a stale/hijacked URL must not
            # silently open the channel (design §4.2 step 4). Activation
            # happens later inside submit_verification_code.
            peer.remote_accepted_at = now
            peer.save(update_fields=["token_theirs", "remote_display_name", "remote_accepted_at"])
            return 200, {}, "peer_updated", peer
        if peer.state == PeerState.PENDING_RECEIVED and peer.token_ours:
            # Crossed row: the data is already present; activation only ever
            # comes from the LOCAL verify + accept path on this side.
            return 200, {}, None, None
        if peer.state == PeerState.ACTIVE and hmac.compare_digest(peer.token_theirs or "", token):
            return 200, {}, None, None
        return 409, {"error": "bad_state"}, None, None

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body
