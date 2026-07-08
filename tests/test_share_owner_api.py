import asyncio

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone as djtz

from twicc.core.models import Project, Session, SessionType, Share, ShareAccess
from twicc.core.services.share_tokens import mint_token


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def session(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-owner", directory="/tmp/owner")
    return Session.objects.create(
        id="sess-owner", project=project, provider="claude_code",
        file_path="sess-owner.jsonl", type=SessionType.SESSION, title="Owner",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=5,
    )


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.share_mutation.run_under_db_write_lock", _p)


@pytest.fixture
def share_host(monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings",
                        lambda: {"shareBaseUrl": "share.example.com"})


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _share(session, **kw):
    return Share.objects.create(kind="session", token=mint_token(), session=session, **kw)


def test_create_refused_without_share_host(client, session, monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: {"shareBaseUrl": ""})
    body = {"kind": "session", "session_id": "sess-owner", "options": {"mode": "live"}}
    res = _run(client.post("/api/shares/", data=orjson.dumps(body), content_type="application/json"))
    assert res.status_code == 400
    assert orjson.loads(res.content)["error"] == "share_host_unset"


def test_create_and_list(client, session, share_host):
    body = {"kind": "session", "session_id": "sess-owner", "label": "priv", "options": {"mode": "live"}}
    res = _run(client.post("/api/shares/", data=orjson.dumps(body), content_type="application/json"))
    assert res.status_code == 201
    created = orjson.loads(res.content)
    assert created["kind"] == "session"
    assert created["token"]
    assert created["url_path"].startswith("/share/")
    res = _run(client.get("/api/shares/"))
    assert len(orjson.loads(res.content)["shares"]) == 1


def test_patch_revoke_unrevoke_delete(client, session, share_host):
    share = _share(session, label="a")
    res = _run(client.patch(f"/api/shares/{share.id}/",
                            data=orjson.dumps({"label": "b"}), content_type="application/json"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["label"] == "b"
    res = _run(client.post(f"/api/shares/{share.id}/revoke/"))
    assert orjson.loads(res.content)["status"] == "revoked"
    res = _run(client.post(f"/api/shares/{share.id}/unrevoke/"))
    assert orjson.loads(res.content)["status"] == "active"
    res = _run(client.delete(f"/api/shares/{share.id}/"))
    assert res.status_code == 200
    assert not Share.objects.filter(id=share.id).exists()


def test_accesses_endpoint(client, session, share_host):
    share = _share(session)
    ShareAccess.objects.create(share=share, ip="1.2.3.4", user_agent="UA")
    res = _run(client.get(f"/api/shares/{share.id}/accesses/"))
    assert res.status_code == 200
    accesses = orjson.loads(res.content)["accesses"]
    assert len(accesses) == 1
    assert accesses[0]["ip"] == "1.2.3.4"


def test_unknown_share_404(client, session, share_host):
    assert _run(client.get("/api/shares/shr_nope/")).status_code == 404
