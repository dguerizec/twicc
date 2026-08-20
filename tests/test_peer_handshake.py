"""Peer handshake: inbound endpoints, verification, mutations, owner REST."""

import asyncio

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.models import Peer, PeerState
from twicc.core.services import peer_mutation
from twicc.core.services.peer_tokens import mint_token
from twicc.peer import inbound_views, outbound


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.peer_mutation.run_under_db_write_lock", _p)
    monkeypatch.setattr("twicc.core.services.peer_messages.run_under_db_write_lock", _p)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    inbound_views._handshake_attempts.clear()
    inbound_views._verify_attempts.clear()
    yield
    inbound_views._handshake_attempts.clear()
    inbound_views._verify_attempts.clear()


@pytest.fixture
def peer_host(monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"peerBaseUrl": "https://me.example.com"},
    )


@pytest.fixture
def broadcasts(monkeypatch):
    events = []

    async def _record(data):
        events.append(data)

    monkeypatch.setattr("twicc.core.services.peer_mutation._broadcast", _record)
    monkeypatch.setattr("twicc.core.services.peer_messages._broadcast", _record)
    return events


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _post(client, path, body, *, bearer=None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return _run(client.post(path, data=orjson.dumps(body), content_type="application/json", headers=headers))


def _request_body(**overrides):
    body = {
        "display_name": "alice",
        "base_url": "https://alice.example.com",
        "token": "tok-" + "a" * 40,
    }
    body.update(overrides)
    return body


# ── Feature gate + auth (phase 3) ───────────────────────────────────────────

def test_all_endpoints_404_when_feature_disabled(client, transactional_db, monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: {"peerBaseUrl": ""})
    for path in (
        "/peer/handshake/request/",
        "/peer/handshake/verify/",
        "/peer/handshake/accept/",
        "/peer/messages/",
        "/peer/messages/pm_x/status/",
    ):
        res = _post(client, path, {})
        assert res.status_code == 404, path


def test_all_endpoints_404_when_peer_origin_is_invalid(client, transactional_db, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"peerBaseUrl": "ftp://me.example.com"},
    )
    for path in (
        "/peer/handshake/request/",
        "/peer/handshake/verify/",
        "/peer/handshake/accept/",
        "/peer/messages/",
        "/peer/messages/pm_x/status/",
    ):
        res = _post(client, path, {})
        assert res.status_code == 404, path


def test_authed_endpoints_403_on_bad_or_absent_bearer(client, transactional_db, peer_host):
    for path in ("/peer/handshake/accept/", "/peer/messages/", "/peer/messages/pm_x/status/"):
        res = _post(client, path, {"anything": 1})
        assert res.status_code == 403, path
        res = _post(client, path, {"anything": 1}, bearer="nope")
        assert res.status_code == 403, path


# ── handshake_request (phase 4) ─────────────────────────────────────────────

def test_handshake_request_creates_pending_row_with_code(client, transactional_db, peer_host, broadcasts):
    res = _post(client, "/peer/handshake/request/", _request_body())
    assert res.status_code == 201
    peer = Peer.objects.get()
    assert peer.state == PeerState.PENDING_RECEIVED
    assert peer.remote_display_name == "alice"
    assert peer.base_url == "https://alice.example.com"
    assert peer.token_theirs == "tok-" + "a" * 40
    assert peer.token_ours is None
    assert len(peer.verification_code) == 6 and peer.verification_code.isdigit()
    assert [e["type"] for e in broadcasts] == ["peer_request_received"]
    # The broadcast (owner UI) carries the code on pending_received rows.
    assert broadcasts[0]["peer"]["verification_code"] == peer.verification_code


def test_handshake_request_invalid_payloads(client, transactional_db, peer_host):
    assert _post(client, "/peer/handshake/request/", {}).status_code == 400
    assert _post(client, "/peer/handshake/request/", _request_body(base_url="not-a-url")).status_code == 400
    assert _post(client, "/peer/handshake/request/", _request_body(token="")).status_code == 400


def test_non_object_json_is_400_not_500(client, transactional_db, peer_host):
    # Valid JSON that is not an object must be invalid_payload — these
    # endpoints are (partly) pre-auth, a crash would be a free 500.
    for body in ([], "x", 5):
        res = _post(client, "/peer/handshake/request/", body)
        assert res.status_code == 400, body


def test_handshake_request_dedup_remints_code(client, transactional_db, peer_host, broadcasts):
    _post(client, "/peer/handshake/request/", _request_body())
    peer = Peer.objects.get()
    old_code = peer.verification_code
    peer.verification_attempts = 3
    peer.save(update_fields=["verification_attempts"])
    res = _post(client, "/peer/handshake/request/", _request_body(token="tok-" + "b" * 40))
    assert res.status_code == 200
    peer.refresh_from_db()
    assert Peer.objects.count() == 1
    assert peer.token_theirs == "tok-" + "b" * 40
    assert peer.verification_attempts == 0
    # A fresh code was minted (a random collision with old_code is possible at
    # 1e-6, so only the shape is asserted).
    assert len(peer.verification_code) == 6 and peer.verification_code.isdigit()
    assert broadcasts[-1]["type"] == "peer_updated"


def test_handshake_request_dedup_no_mutation_when_verified(client, transactional_db, peer_host, broadcasts):
    _post(client, "/peer/handshake/request/", _request_body())
    peer = Peer.objects.get()
    peer.verified_at = peer.created_at
    peer.save(update_fields=["verified_at"])
    original_token = peer.token_theirs
    original_code = peer.verification_code
    res = _post(client, "/peer/handshake/request/", _request_body(token="tok-" + "e" * 40))
    assert res.status_code == 200
    peer.refresh_from_db()
    # A forged re-request must not strip the verification or swap the bound token.
    assert peer.token_theirs == original_token
    assert peer.verification_code == original_code
    assert peer.verified_at is not None


def test_handshake_request_409_on_active(client, transactional_db, peer_host):
    Peer.objects.create(
        name="alice", base_url="https://alice.example.com", state=PeerState.ACTIVE,
        token_ours=mint_token(), token_theirs="t",
    )
    res = _post(client, "/peer/handshake/request/", _request_body())
    assert res.status_code == 409
    assert orjson.loads(res.content)["error"] == "already_related"


def test_handshake_request_pending_cap(client, transactional_db, peer_host, monkeypatch):
    monkeypatch.setattr(peer_mutation, "MAX_PENDING_RECEIVED", 1)
    _post(client, "/peer/handshake/request/", _request_body())
    res = _post(client, "/peer/handshake/request/", _request_body(base_url="https://bob.example.com"))
    assert res.status_code == 429
    assert orjson.loads(res.content)["error"] == "too_many_pending"


def test_handshake_request_rate_limit(client, transactional_db, peer_host):
    for i in range(5):
        _post(client, "/peer/handshake/request/", _request_body(base_url=f"https://p{i}.example.com"))
    res = _post(client, "/peer/handshake/request/", _request_body(base_url="https://p9.example.com"))
    assert res.status_code == 429
    assert res.headers["Retry-After"]


def test_handshake_request_413_on_oversized_body(client, transactional_db, peer_host):
    res = _post(client, "/peer/handshake/request/", _request_body(display_name="x" * (70 * 1024)))
    assert res.status_code == 413


# ── handshake_verify (phase 4) ──────────────────────────────────────────────

def _make_pending_received(**kw):
    defaults = {
        "name": "", "remote_display_name": "alice", "base_url": "https://alice.example.com",
        "state": PeerState.PENDING_RECEIVED, "token_theirs": "tok-" + "a" * 40,
        "verification_code": "123456",
    }
    defaults.update(kw)
    return Peer.objects.create(**defaults)


def test_verify_unknown_token(client, transactional_db, peer_host):
    _make_pending_received()
    res = _post(client, "/peer/handshake/verify/", {"code": "123456"}, bearer="wrong-token")
    assert res.status_code == 403
    assert orjson.loads(res.content)["error"] == "unknown_token"


def test_verify_invalid_code_shape(client, transactional_db, peer_host):
    peer = _make_pending_received()
    for bad in ("", "12345", "1234567", "abcdef", 123456):
        res = _post(client, "/peer/handshake/verify/", {"code": bad}, bearer=peer.token_theirs)
        assert res.status_code == 400


def test_verify_wrong_code_increments_attempts(client, transactional_db, peer_host):
    peer = _make_pending_received()
    res = _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    assert res.status_code == 403
    assert orjson.loads(res.content)["error"] == "bad_code"
    peer.refresh_from_db()
    assert peer.verification_attempts == 1
    assert peer.verified_at is None


def test_verify_fifth_mismatch_regenerates_code(client, transactional_db, peer_host, broadcasts):
    peer = _make_pending_received()
    for _ in range(4):
        _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    res = _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    assert res.status_code == 403
    assert orjson.loads(res.content)["error"] == "too_many_attempts"
    peer.refresh_from_db()
    assert peer.verification_regens == 1
    assert peer.verification_attempts == 0
    assert broadcasts[-1]["type"] == "peer_updated"


def test_verify_third_regen_deletes_row(client, transactional_db, peer_host, broadcasts, monkeypatch):
    monkeypatch.setattr(inbound_views, "_VERIFY_MAX_ATTEMPTS", 1000)
    peer = _make_pending_received()
    for _ in range(15):
        peer.refresh_from_db()
        wrong = "000000" if peer.verification_code != "000000" else "000001"
        res = _post(client, "/peer/handshake/verify/", {"code": wrong}, bearer=peer.token_theirs)
    assert res.status_code == 403
    assert orjson.loads(res.content)["error"] == "too_many_attempts"
    assert not Peer.objects.filter(pk=peer.pk).exists()
    assert broadcasts[-1]["type"] == "peer_removed"


def test_verify_rate_limit(client, transactional_db, peer_host):
    peer = _make_pending_received()
    for _ in range(10):
        _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    res = _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    assert res.status_code == 429


def test_verify_correct_code_sets_verified_at(client, transactional_db, peer_host, broadcasts):
    peer = _make_pending_received(verification_attempts=2)
    res = _post(client, "/peer/handshake/verify/", {"code": "123456"}, bearer=peer.token_theirs)
    assert res.status_code == 200
    peer.refresh_from_db()
    assert peer.verified_at is not None
    assert peer.verification_attempts == 0
    assert broadcasts[-1]["type"] == "peer_updated"


def test_verify_noop_on_active_row(client, transactional_db, peer_host, broadcasts):
    # Real active row (held-accept recovery path), NOT a monkeypatch.
    peer = _make_pending_received(state=PeerState.ACTIVE, token_ours=mint_token())
    res = _post(client, "/peer/handshake/verify/", {"code": "123456"}, bearer=peer.token_theirs)
    assert res.status_code == 200
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert broadcasts == []


def test_verify_mismatch_on_active_row_never_mutates(client, transactional_db, peer_host):
    peer = _make_pending_received(state=PeerState.ACTIVE, token_ours=mint_token())
    res = _post(client, "/peer/handshake/verify/", {"code": "000000"}, bearer=peer.token_theirs)
    assert res.status_code == 403
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert peer.verification_attempts == 0


# ── submit_verification_code service (requester side) ───────────────────────

def _make_pending_sent(**kw):
    defaults = {
        "name": "bob", "base_url": "https://bob.example.com",
        "state": PeerState.PENDING_SENT, "token_ours": mint_token(),
    }
    defaults.update(kw)
    return Peer.objects.create(**defaults)


def _patch_verify_response(monkeypatch, status, body=None, *, network_error=False):
    async def _fake(base_url, *, bearer, code):
        if network_error:
            raise outbound.PeerOutboundError("ConnectError")
        return status, body or {}
    monkeypatch.setattr("twicc.peer.outbound.post_handshake_verify", _fake)


def test_submit_code_success_sets_code_confirmed(transactional_db, peer_host, broadcasts, monkeypatch):
    peer = _make_pending_sent()
    _patch_verify_response(monkeypatch, 200)
    result = _run(peer_mutation.submit_verification_code(peer, "123456"))
    assert result.success
    peer.refresh_from_db()
    assert peer.code_confirmed_at is not None
    assert peer.state == PeerState.PENDING_SENT


def test_submit_code_success_with_held_accept_activates(transactional_db, peer_host, broadcasts, monkeypatch):
    from django.utils import timezone as djtz

    peer = _make_pending_sent(remote_accepted_at=djtz.now(), token_theirs="their-token")
    _patch_verify_response(monkeypatch, 200)
    result = _run(peer_mutation.submit_verification_code(peer, "123456"))
    assert result.success
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert peer.accepted_at is not None
    assert [e["type"] for e in broadcasts] == ["peer_updated", "peer_accepted"]


@pytest.mark.parametrize("status,body,expected_code", [
    (403, {"error": "bad_code"}, "bad_code"),
    (403, {"error": "too_many_attempts"}, "code_regenerated"),
    (403, {"error": "unknown_token"}, "relationship_gone"),
    (404, {}, "verify_failed"),
    (500, {}, "verify_failed"),
])
def test_submit_code_error_mapping(transactional_db, peer_host, monkeypatch, status, body, expected_code):
    peer = _make_pending_sent()
    _patch_verify_response(monkeypatch, status, body)
    result = _run(peer_mutation.submit_verification_code(peer, "123456"))
    assert not result.success
    assert result.errors[0].code == expected_code


def test_submit_code_network_error(transactional_db, peer_host, monkeypatch):
    peer = _make_pending_sent()
    _patch_verify_response(monkeypatch, 0, network_error=True)
    result = _run(peer_mutation.submit_verification_code(peer, "123456"))
    assert not result.success
    assert result.errors[0].code == "unreachable"


def test_submit_code_bad_state(transactional_db, peer_host):
    peer = _make_pending_received()  # no token_ours → no outbound leg
    result = _run(peer_mutation.submit_verification_code(peer, "123456"))
    assert not result.success
    assert result.errors[0].code == "bad_state"


# ── accept_peer service ─────────────────────────────────────────────────────

def _patch_accept_response(monkeypatch, status=200, *, network_error=False, calls=None):
    async def _fake(base_url, *, bearer, token, display_name):
        if calls is not None:
            calls.append({"base_url": base_url, "bearer": bearer, "token": token})
        if network_error:
            raise outbound.PeerOutboundError("ConnectError")
        return status, {}
    monkeypatch.setattr("twicc.peer.outbound.post_handshake_accept", _fake)


def test_accept_peer_rejected_before_verification(transactional_db, peer_host, monkeypatch):
    peer = _make_pending_received()
    _patch_accept_response(monkeypatch)
    result = _run(peer_mutation.accept_peer(peer, name="alice"))
    assert not result.success
    assert result.errors[0].code == "not_verified"


def test_accept_peer_success(transactional_db, peer_host, broadcasts, monkeypatch):
    from django.utils import timezone as djtz

    peer = _make_pending_received(verified_at=djtz.now())
    calls = []
    _patch_accept_response(monkeypatch, calls=calls)
    result = _run(peer_mutation.accept_peer(peer, name="alice"))
    assert result.success
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert peer.name == "alice"
    assert peer.token_ours  # freshly minted
    assert peer.verification_code == "123456"  # NOT cleared (held-accept recovery)
    assert calls[0]["bearer"] == peer.token_theirs
    assert calls[0]["token"] == peer.token_ours


def test_accept_peer_unreachable_keeps_pending(transactional_db, peer_host, monkeypatch):
    from django.utils import timezone as djtz

    peer = _make_pending_received(verified_at=djtz.now())
    _patch_accept_response(monkeypatch, network_error=True)
    result = _run(peer_mutation.accept_peer(peer, name="alice"))
    assert not result.success
    assert result.errors[0].code == "unreachable"
    peer.refresh_from_db()
    assert peer.state == PeerState.PENDING_RECEIVED
    # The minted token IS persisted before the outbound call: a retry must
    # present the SAME token (the requester may have processed the accept even
    # though our 200 was lost), or the handshake wedges permanently.
    assert peer.token_ours is not None
    first_token = peer.token_ours
    calls = []
    _patch_accept_response(monkeypatch, calls=calls)
    result = _run(peer_mutation.accept_peer(peer, name="alice"))
    assert result.success
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert peer.token_ours == first_token
    assert calls[0]["token"] == first_token


def test_accept_peer_idempotent_on_active(transactional_db, peer_host):
    peer = _make_pending_received(state=PeerState.ACTIVE, token_ours=mint_token())
    result = _run(peer_mutation.accept_peer(peer, name="x"))
    assert result.success


# ── create_peer_and_request service ─────────────────────────────────────────

def _patch_request_response(monkeypatch, status=200, *, network_error=False):
    async def _fake(base_url, *, display_name, own_base_url, token):
        if network_error:
            raise outbound.PeerOutboundError("ConnectTimeout")
        return status, {}
    monkeypatch.setattr("twicc.peer.outbound.post_handshake_request", _fake)


def test_create_peer_success(transactional_db, peer_host, broadcasts, monkeypatch):
    _patch_request_response(monkeypatch)
    result = _run(peer_mutation.create_peer_and_request(name="bob", base_url="https://bob.example.com/"))
    assert result.success
    peer = Peer.objects.get()
    assert peer.state == PeerState.PENDING_SENT
    assert peer.base_url == "https://bob.example.com"  # normalized, no trailing slash
    assert peer.token_ours
    assert broadcasts[-1]["type"] == "peer_updated"


def test_create_peer_unreachable_deletes_row(transactional_db, peer_host, monkeypatch):
    _patch_request_response(monkeypatch, network_error=True)
    result = _run(peer_mutation.create_peer_and_request(name="bob", base_url="https://bob.example.com"))
    assert not result.success
    assert result.errors[0].code == "unreachable"
    assert Peer.objects.count() == 0


def test_create_peer_validations(transactional_db, monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: {"peerBaseUrl": ""})
    result = _run(peer_mutation.create_peer_and_request(name="b", base_url="https://bob.example.com"))
    assert result.errors[0].code == "peer_host_unset"
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: {"peerBaseUrl": "https://me.example.com"})
    result = _run(peer_mutation.create_peer_and_request(name="b", base_url="nope"))
    assert result.errors[0].code == "invalid_url"
    _patch_request_response(monkeypatch)
    _run(peer_mutation.create_peer_and_request(name="b", base_url="https://bob.example.com"))
    result = _run(peer_mutation.create_peer_and_request(name="b2", base_url="https://bob.example.com"))
    assert result.errors[0].code == "duplicate"


# ── crossed handshake ───────────────────────────────────────────────────────

def test_crossed_request_merges_into_pending_received(client, transactional_db, peer_host, broadcasts):
    peer = _make_pending_sent(base_url="https://alice.example.com")
    our_token = peer.token_ours
    res = _post(client, "/peer/handshake/request/", _request_body())
    assert res.status_code == 200
    peer.refresh_from_db()
    assert Peer.objects.count() == 1
    assert peer.state == PeerState.PENDING_RECEIVED
    assert peer.token_ours == our_token  # kept
    assert peer.token_theirs == "tok-" + "a" * 40
    assert len(peer.verification_code) == 6  # code minted — no crossed exemption
    assert broadcasts[-1]["type"] == "peer_request_received"
    # The serialized row is flagged crossed (has our outbound leg) so the UI
    # offers the code entry on the incoming-request card too.
    assert broadcasts[-1]["peer"]["crossed"] is True
    from twicc.core.serializers import serialize_peer

    assert serialize_peer(_make_pending_received(base_url="https://x.example.com", token_theirs="t-x"))["crossed"] is False


def test_crossed_accept_reuses_existing_token(client, transactional_db, peer_host, monkeypatch):
    from django.utils import timezone as djtz

    peer = _make_pending_sent(base_url="https://alice.example.com")
    our_token = peer.token_ours
    _post(client, "/peer/handshake/request/", _request_body())
    peer.refresh_from_db()
    peer.verified_at = djtz.now()
    peer.save(update_fields=["verified_at"])
    calls = []
    _patch_accept_response(monkeypatch, calls=calls)
    result = _run(peer_mutation.accept_peer(peer, name="alice"))
    assert result.success
    peer.refresh_from_db()
    assert peer.token_ours == our_token  # reused, never re-minted
    assert calls[0]["token"] == our_token


def test_accept_endpoint_noop_on_crossed_row(client, transactional_db, peer_host):
    peer = _make_pending_received(token_ours=mint_token())
    res = _post(
        client, "/peer/handshake/accept/",
        {"token": "their-tok", "display_name": "alice"}, bearer=peer.token_ours,
    )
    assert res.status_code == 200
    peer.refresh_from_db()
    assert peer.state == PeerState.PENDING_RECEIVED  # activation is local-only here


# ── handshake_accept endpoint (requester side) ──────────────────────────────

def test_accept_endpoint_honest_flow_activates(client, transactional_db, peer_host, broadcasts):
    from django.utils import timezone as djtz

    peer = _make_pending_sent(code_confirmed_at=djtz.now())
    res = _post(
        client, "/peer/handshake/accept/",
        {"token": "their-token", "display_name": "bob-instance"}, bearer=peer.token_ours,
    )
    assert res.status_code == 200
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE
    assert peer.token_theirs == "their-token"
    assert peer.remote_display_name == "bob-instance"
    assert peer.accepted_at is not None
    assert broadcasts[-1]["type"] == "peer_accepted"


def test_accept_endpoint_held_without_code_confirmation(client, transactional_db, peer_host, broadcasts):
    peer = _make_pending_sent()
    res = _post(
        client, "/peer/handshake/accept/",
        {"token": "their-token", "display_name": "bob"}, bearer=peer.token_ours,
    )
    assert res.status_code == 200
    peer.refresh_from_db()
    assert peer.state == PeerState.PENDING_SENT  # held, NOT activated
    assert peer.remote_accepted_at is not None
    assert peer.token_theirs == "their-token"
    assert broadcasts[-1]["type"] == "peer_updated"


def test_held_accept_full_recovery(client, transactional_db, peer_host, monkeypatch):
    """Acceptor already active; the requester's retried verify succeeds and the
    local code submission flips the held row to active."""
    peer = _make_pending_sent()
    # Accept callback arrives before code confirmation → held.
    _post(client, "/peer/handshake/accept/",
          {"token": "their-token", "display_name": "bob"}, bearer=peer.token_ours)
    peer.refresh_from_db()
    assert peer.state == PeerState.PENDING_SENT and peer.remote_accepted_at is not None
    # The peer's row is active on their side; their verify endpoint answers 200.
    _patch_verify_response(monkeypatch, 200)
    result = _run(peer_mutation.submit_verification_code(peer, "654321"))
    assert result.success
    peer.refresh_from_db()
    assert peer.state == PeerState.ACTIVE


def test_accept_endpoint_idempotent_active_and_bad_state(client, transactional_db, peer_host):
    peer = _make_pending_sent(state=PeerState.ACTIVE, token_theirs="their-token")
    res = _post(client, "/peer/handshake/accept/",
                {"token": "their-token", "display_name": "bob"}, bearer=peer.token_ours)
    assert res.status_code == 200
    # Wrong body token on an active row → 409.
    res = _post(client, "/peer/handshake/accept/",
                {"token": "other", "display_name": "bob"}, bearer=peer.token_ours)
    assert res.status_code == 409


# ── ShareConsumer regression (security invariant) ───────────────────────────

def test_share_consumer_never_forwards_peer_events(transactional_db):
    """The whitelist in ShareConsumer.broadcast keeps verification codes away
    from share viewers. A peer_* event must produce zero outbound frames."""
    from twicc.share.consumer import ShareConsumer

    consumer = ShareConsumer()
    sent = []

    async def _send_json(data):
        sent.append(data)

    consumer.send_json = _send_json
    for mtype in ("peer_updated", "peer_request_received", "peers_updated",
                  "peer_accepted", "peer_removed", "peer_message_received"):
        _run(consumer.broadcast({"data": {"type": mtype, "peer": {"verification_code": "123456"}}}))
    assert sent == []


# ── Owner REST ──────────────────────────────────────────────────────────────

def test_owner_rest_list_and_detail(client, transactional_db, peer_host):
    peer = _make_pending_received()
    res = _run(client.get("/api/peers/"))
    assert res.status_code == 200
    peers = orjson.loads(res.content)["peers"]
    assert len(peers) == 1
    assert peers[0]["id"] == peer.id
    assert peers[0]["verification_code"] == "123456"  # owner UI shows it on pending_received
    assert "token_ours" not in peers[0] and "token_theirs" not in peers[0]
    res = _run(client.get(f"/api/peers/{peer.id}/"))
    assert res.status_code == 200


def test_owner_rest_create(client, transactional_db, peer_host, monkeypatch):
    _patch_request_response(monkeypatch)
    res = _post(client, "/api/peers/", {"name": "bob", "base_url": "https://bob.example.com"})
    assert res.status_code == 201
    data = orjson.loads(res.content)
    assert data["state"] == "pending_sent"
    assert "token_ours" not in data


def test_owner_rest_verify(client, transactional_db, peer_host, monkeypatch):
    peer = _make_pending_sent()
    _patch_verify_response(monkeypatch, 200)
    res = _post(client, f"/api/peers/{peer.id}/verify/", {"code": "123456"})
    assert res.status_code == 200
    assert orjson.loads(res.content)["code_confirmed_at"] is not None


def test_owner_rest_verify_error(client, transactional_db, peer_host, monkeypatch):
    peer = _make_pending_sent()
    _patch_verify_response(monkeypatch, 403, {"error": "bad_code"})
    res = _post(client, f"/api/peers/{peer.id}/verify/", {"code": "000000"})
    assert res.status_code == 400
    assert orjson.loads(res.content)["errors"][0]["code"] == "bad_code"


def test_owner_rest_accept_refuse_rename_delete(client, transactional_db, peer_host, monkeypatch):
    from django.utils import timezone as djtz

    peer = _make_pending_received(verified_at=djtz.now())
    _patch_accept_response(monkeypatch)
    res = _post(client, f"/api/peers/{peer.id}/accept/", {"name": "alice"})
    assert res.status_code == 200
    assert orjson.loads(res.content)["state"] == "active"

    res = _run(client.patch(f"/api/peers/{peer.id}/",
                            data=orjson.dumps({"name": "alice2"}), content_type="application/json"))
    assert orjson.loads(res.content)["name"] == "alice2"

    other = _make_pending_received(base_url="https://carol.example.com", token_theirs="tok-c")
    res = _post(client, f"/api/peers/{other.id}/refuse/", {})
    assert res.status_code == 200
    assert not Peer.objects.filter(pk=other.pk).exists()

    res = _run(client.delete(f"/api/peers/{peer.id}/"))
    assert res.status_code == 200
    assert not Peer.objects.filter(pk=peer.pk).exists()


def test_owner_rest_rejects_peer_address_changes_without_partial_rename(client, transactional_db, peer_host):
    peer = Peer.objects.create(
        name="alice",
        base_url="https://alice.example.com",
        state=PeerState.ACTIVE,
        token_ours=mint_token(),
        token_theirs=mint_token(),
    )

    response = _run(client.patch(
        f"/api/peers/{peer.id}/",
        data=orjson.dumps({"name": "renamed", "base_url": "https://mallory.example.com"}),
        content_type="application/json",
    ))

    assert response.status_code == 400
    assert orjson.loads(response.content)["errors"] == [{
        "field": "base_url",
        "code": "immutable",
        "message": "A peer address cannot be changed. Create a new peering for the new address.",
    }]
    peer.refresh_from_db()
    assert peer.name == "alice"
    assert peer.base_url == "https://alice.example.com"


def test_outbound_request_carries_configured_display_name(transactional_db, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"peerBaseUrl": "https://me.example.com", "peerDisplayName": "  Stephane (dell) "},
    )
    sent = {}

    async def _fake(base_url, *, display_name, own_base_url, token):
        sent["display_name"] = display_name
        return 201, {}

    monkeypatch.setattr("twicc.peer.outbound.post_handshake_request", _fake)
    result = _run(peer_mutation.create_peer_and_request(name="bob", base_url="https://bob.example.com"))
    assert result.success
    assert sent["display_name"] == "Stephane (dell)"  # trimmed setting wins


def test_outbound_display_name_falls_back_to_hostname(transactional_db, peer_host, monkeypatch):
    # peer_host fixture sets only peerBaseUrl → hostname fallback.
    sent = {}

    async def _fake(base_url, *, display_name, own_base_url, token):
        sent["display_name"] = display_name
        return 201, {}

    monkeypatch.setattr("twicc.peer.outbound.post_handshake_request", _fake)
    result = _run(peer_mutation.create_peer_and_request(name="bob", base_url="https://bob.example.com"))
    assert result.success
    assert sent["display_name"] == "me.example.com"


@pytest.mark.parametrize("base_url,expected", [
    ("https://me.example.com", "me.example.com"),
    ("https://me.example.com:8443", "me.example.com"),
    ("https://192.168.1.42:8443", "192.168.1.42"),
    # The canonical hostname of an IPv6 origin is unbracketed; the advertised
    # name is read as an address, so it must carry the brackets back.
    ("https://[2001:db8::1]", "[2001:db8::1]"),
    ("https://[0:0:0:0:0:0:0:1]:8443", "[::1]"),
    ("", "twicc"),
])
def test_own_display_name_hostname_fallback_brackets_ipv6(base_url, expected, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"peerBaseUrl": base_url},
    )
    assert peer_mutation.own_display_name() == expected
