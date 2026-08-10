"""Owner-side peer management REST. Under /api/ — password-gated.

Relationship management is human-only by design (§5): these endpoints have no
CLI/RPC/MCP counterpart, so an agent can neither self-authorize a channel nor
pick an arbitrary URL to exfiltrate to.
"""

from __future__ import annotations

import orjson
from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import serialize_peer, serialize_peer_message
from twicc.core.services import peer_messages, peer_mutation


def _err_response(errors) -> JsonResponse:
    return JsonResponse({"errors": [e._asdict() for e in (errors or [])]}, status=400)


def _parse_body(request) -> dict | None:
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _load_peer(peer_id):
    from twicc.core.models import Peer

    peer = await sync_to_async(lambda: Peer.objects.filter(id=peer_id).first())()
    if peer is None:
        raise Http404("Peer not found")
    return peer


async def _load_message(pk):
    from twicc.core.models import PeerMessage

    message = await sync_to_async(
        lambda: PeerMessage.objects.select_related("peer").filter(pk=pk).first()
    )()
    if message is None:
        raise Http404("Peer message not found")
    return message


async def peers_list(request):
    """GET /api/peers/ — all states (the UI shows pending ones). POST — create + request."""
    from twicc.core.models import Peer

    if request.method == "GET":
        peers = await sync_to_async(list)(Peer.objects.all())
        return JsonResponse({"peers": [serialize_peer(p) for p in peers]})
    if request.method == "POST":
        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        result = await peer_mutation.create_peer_and_request(
            name=data.get("name") or "", base_url=data.get("base_url") or "",
        )
        if not result.success:
            return _err_response(result.errors)
        peer = await _load_peer(result.peer_id)
        return JsonResponse(serialize_peer(peer), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


async def peer_detail(request, peer_id):
    """GET / PATCH {name?, base_url?} / DELETE (silent revoke) /api/peers/<id>/."""
    peer = await _load_peer(peer_id)
    if request.method == "GET":
        return JsonResponse(serialize_peer(peer))
    if request.method == "PATCH":
        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        if "name" in data:
            result = await peer_mutation.rename_peer(peer, data.get("name") or "")
            if not result.success:
                return _err_response(result.errors)
        if "base_url" in data:
            result = await peer_mutation.update_peer_base_url(peer, data.get("base_url") or "")
            if not result.success:
                return _err_response(result.errors)
        return JsonResponse(serialize_peer(await _load_peer(peer_id)))
    if request.method == "DELETE":
        await peer_mutation.delete_peer(peer)
        return JsonResponse({"ok": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])


async def peer_verify(request, peer_id):
    """POST /api/peers/<id>/verify/ {code} — requester side submits the out-of-band code."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    result = await peer_mutation.submit_verification_code(peer, data.get("code") or "")
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_accept(request, peer_id):
    """POST /api/peers/<id>/accept/ {name}."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    result = await peer_mutation.accept_peer(peer, name=data.get("name") or "")
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_refuse(request, peer_id):
    """POST /api/peers/<id>/refuse/ — silent local delete."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    result = await peer_mutation.refuse_peer(peer)
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse({"ok": True})


async def peer_messages_list(request):
    """GET /api/peer-messages/?limit=50 — summaries, pending inbound first."""
    from twicc.core.models import PeerMessage, PeerMessageDirection, PeerMessageStatus

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        limit = min(max(int(request.GET.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50

    def _fetch():
        pending = list(PeerMessage.objects.filter(
            direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
        ))
        rest = list(PeerMessage.objects.exclude(
            direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
        )[:limit])
        return pending + rest

    messages = await sync_to_async(_fetch)()
    return JsonResponse({"messages": [serialize_peer_message(m) for m in messages]})


async def peer_message_detail(request, pk):
    """GET /api/peer-messages/<pk>/ — the full payload (the only surface that ships it)."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    message = await _load_message(pk)
    return JsonResponse(serialize_peer_message(message, include_payload=True))


async def peer_message_deliver(request, pk):
    """POST /api/peer-messages/<pk>/deliver/ — {session_id?, note?, redeliver?}.

    NOTHING is injected server-side: the message is marked delivered and the
    envelope text is returned; the UI prefills a composer with it — the
    picked existing session's draft (``session_id`` given, recorded as
    ``delivered_to_session``) or a locally-created new draft session. The
    user reviews and sends through the normal pipeline in both cases.

    ``redeliver`` (opt-in, never implicit) re-routes an already-delivered
    message to another target — the inbox's way to fix a wrong pick.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    success, envelope, errors = await peer_messages.mark_delivered(
        message, session_id=data.get("session_id") or None, note=data.get("note") or "",
        redeliver=bool(data.get("redeliver")),
    )
    if not success:
        return _err_response(errors)
    return JsonResponse({"envelope": envelope})


async def peer_message_refuse(request, pk):
    """POST /api/peer-messages/<pk>/refuse/."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    success, errors = await peer_messages.refuse_peer_message(message)
    if not success:
        return _err_response(errors)
    return JsonResponse({"ok": True})
