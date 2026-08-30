"""Outbound HTTP client for peer messaging.

Async httpx, client per call with an explicit timeout (same style as the
artifact broker proxy). Paths mirror the inbound routes in ``urls.py`` — the
wire format is ours on both sides, trailing slashes included.
"""

from __future__ import annotations

import httpx

OUTBOUND_TIMEOUT_SECONDS = 30.0

_REMOTE_ERROR_MESSAGES = {
    "already_related": "The remote instance already has a Peer relationship for this address.",
    "ambiguous_peer": "The remote instance has more than one Peer for this address.",
    "bad_state": "The remote instance cannot apply this request in its current state.",
    "invalid_payload": "The remote instance rejected the request data.",
    "reconnect_in_progress": "The remote instance already has a different reconnect request pending.",
    "too_many_pending": "The remote instance has too many pending Peer requests.",
    "unknown_token": "The remote instance rejected the Peer credentials.",
}


class PeerOutboundError(Exception):
    """Network-level failure reaching the peer (detail in str())."""


def response_error_message(body: dict, fallback: str) -> str:
    """Translate a remote wire error into user-facing text."""
    error_code = body.get("error")
    if not isinstance(error_code, str):
        return fallback
    return _REMOTE_ERROR_MESSAGES.get(error_code, fallback)


async def _post(base_url: str, path: str, json_body: dict, *, bearer: str | None) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {bearer}"} if bearer is not None else {}
    try:
        async with httpx.AsyncClient(timeout=OUTBOUND_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=json_body, headers=headers)
    except httpx.HTTPError as exc:
        raise PeerOutboundError(type(exc).__name__) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    return response.status_code, data if isinstance(data, dict) else {}


async def post_handshake_request(base_url: str, *, display_name: str, own_base_url: str, token: str) -> tuple[int, dict]:
    return await _post(
        base_url,
        "/peer/handshake/request/",
        {"display_name": display_name, "base_url": own_base_url, "token": token},
        bearer=None,
    )


async def post_handshake_cancel(base_url: str, *, bearer: str) -> tuple[int, dict]:
    return await _post(base_url, "/peer/handshake/cancel/", {}, bearer=bearer)


async def post_handshake_verify(base_url: str, *, bearer: str, code: str) -> tuple[int, dict]:
    return await _post(base_url, "/peer/handshake/verify/", {"code": code}, bearer=bearer)


async def post_handshake_accept(base_url: str, *, bearer: str, token: str, display_name: str) -> tuple[int, dict]:
    return await _post(
        base_url,
        "/peer/handshake/accept/",
        {"token": token, "display_name": display_name},
        bearer=bearer,
    )


async def post_message(
    base_url: str, *, bearer: str, message_id: str, title: str,
    reply_to: str, payload: dict, origin: dict,
) -> tuple[int, dict]:
    return await _post(
        base_url,
        "/peer/messages/",
        {
            "message_id": message_id,
            "title": title,
            "reply_to": reply_to,
            "payload": payload,
            "origin": origin,
        },
        bearer=bearer,
    )


async def post_status(base_url: str, *, bearer: str, message_id: str, status: str) -> tuple[int, dict]:
    return await _post(base_url, f"/peer/messages/{message_id}/status/", {"status": status}, bearer=bearer)
