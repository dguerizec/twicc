"""Peer CLI surface: peers / peer-message / peer-send (in-process invoker)."""

import asyncio

import pytest

from twicc.cli._drop_request import transport
from twicc.core.models import Peer, PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
from twicc.core.services.peer_tokens import mint_token
from twicc.drop_requests_watcher import execute_drop_payload
from twicc.peer import outbound
from twicc.rpc.invoker import invoke


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.peer_mutation.run_under_db_write_lock", _p)
    monkeypatch.setattr("twicc.core.services.peer_messages.run_under_db_write_lock", _p)


def _active_peer(**kw):
    defaults = dict(
        name="alice", base_url="https://alice.example.com", state=PeerState.ACTIVE,
        token_ours=mint_token(), token_theirs="their-" + "t" * 30,
    )
    defaults.update(kw)
    return Peer.objects.create(**defaults)


@pytest.mark.django_db(transaction=True)
def test_peers_lists_active_and_broken_only():
    _active_peer()
    _active_peer(name="bob", base_url="https://bob.example.com", state=PeerState.BROKEN,
                 token_ours=mint_token())
    _active_peer(name="carol", base_url="https://carol.example.com",
                 state=PeerState.PENDING_RECEIVED, token_ours=None, verification_code="123456")
    res = invoke(["peers"])
    assert res.exit_code == 0
    peers = res.result["peers"]
    assert {p["name"] for p in peers} == {"alice", "bob"}
    for p in peers:
        assert set(p) == {"id", "name", "state", "last_contact_at"}  # no base_url, no tokens, no code


@pytest.mark.django_db(transaction=True)
def test_peer_message_found_and_not_found():
    peer = _active_peer()
    PeerMessage.objects.create(
        peer=peer, direction=PeerMessageDirection.OUT, message_id="pm_cli1",
        thread_id="pm_cli1",
        payload={"text": "hello", "images": [], "documents": []},
        origin={"sent_at": "2026-07-24T12:00:00+00:00"},
        status=PeerMessageStatus.PENDING,
    )
    res = invoke(["peer-message", "pm_cli1"])
    assert res.exit_code == 0
    assert res.result["message_id"] == "pm_cli1"
    assert res.result["status"] == "pending"
    assert "payload" not in res.result  # summary only on the agent surface

    res = invoke(["peer-message", "pm_nope"])
    assert res.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_peer_send_precheck_errors():
    async def scenario(argv):
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario(["peer-send", "ghost", "Subject", "hello"]))
    assert res.exit_code == 1

    _active_peer(name="brk", base_url="https://brk.example.com", state=PeerState.BROKEN,
                 token_ours=mint_token())
    res = asyncio.run(scenario(["peer-send", "brk", "Subject", "hello"]))
    assert res.exit_code == 1

    # Title pre-check (local, before the drop-request): empty and over-cap.
    _active_peer()
    res = asyncio.run(scenario(["peer-send", "alice", "   ", "hello"]))
    assert res.exit_code == 1
    res = asyncio.run(scenario(["peer-send", "alice", "x" * 101, "hello"]))
    assert res.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_peer_send_end_to_end_in_process(monkeypatch):
    peer = _active_peer()
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append({
            "bearer": bearer,
            "message_id": message_id,
            "title": title,
            "reply_to": reply_to,
        })
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, ["peer-send", "alice", "Daily recap", "recap of the day"])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0, res.error
    assert res.result["status"] == "sent"
    assert res.result["peer_id"] == peer.id
    assert res.result["message_id"].startswith("pm_")
    assert res.result["peer_status"] == "pending"  # remote state via status_extra
    assert calls[0]["bearer"] == peer.token_theirs
    assert calls[0]["title"] == "Daily recap"
    assert calls[0]["reply_to"] == ""
    message = PeerMessage.objects.get()
    assert message.direction == PeerMessageDirection.OUT
    assert message.status == PeerMessageStatus.PENDING
    assert message.title == "Daily recap"


@pytest.mark.django_db(transaction=True)
def test_peer_send_rejected_maps_exit_3(monkeypatch):
    _active_peer()

    async def _fake_post(base_url, **kw):
        raise outbound.PeerOutboundError("ConnectError")

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, ["peer-send", "alice", "Subject", "hello"])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    # Every service failure (network included) surfaces as watcher "rejected"
    # → exit 3; the distinction lives in the error code (accepted collapse).
    assert res.exit_code == 3


@pytest.mark.django_db(transaction=True)
def test_execute_drop_payload_peer_send(monkeypatch):
    peer = _active_peer()

    async def _fake_post(base_url, **kw):
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)
    status = asyncio.run(execute_drop_payload({"peer": peer.id, "title": "Subject", "text": "hi"}, "peer:send"))
    assert status["status"] == "sent"
    assert status["peer_id"] == peer.id
    assert status["peer_status"] == "pending"
    assert "sent_at" in status
