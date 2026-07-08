import asyncio

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.core.models import Project, Session, SessionType, Share
from twicc.core.services.share_tokens import mint_token
from twicc.share.consumer import ShareConsumer


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def session(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-cons", directory="/tmp/cons")
    return Session.objects.create(
        id="sess-cons", project=project, provider="claude_code",
        file_path="sess-cons.jsonl", type=SessionType.SESSION, title="Cons",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=5,
    )


def _share(session, **kw):
    return Share.objects.create(kind="session", token=mint_token(), session=session, **kw)


def _communicator(token):
    comm = WebsocketCommunicator(ShareConsumer.as_asgi(), f"/ws/share/{token}/")
    comm.scope["url_route"] = {"kwargs": {"token": token}}
    return comm


def test_live_share_accepts_and_filters_debug(session):
    # Share created in the sync test body (async ORM in the consumer is fine).
    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid,
            "items": [
                {"line_num": 1, "display_level": 3, "content": "{}", "kind": "system"},
                {"line_num": 2, "display_level": 1, "content": "{}", "kind": "user_message"},
            ],
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_items_added"
        # DEBUG_ONLY (level 3) filtered before it ever reaches the viewer's socket.
        assert [it["line_num"] for it in msg["items"]] == [2]
        await comm.disconnect()

    _run(scenario())


def test_snapshot_share_rejected(session):
    share = _share(session, options={"mode": "snapshot", "frozen_at_line": 3})

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert not connected  # snapshot shares never stream
        await comm.disconnect()

    _run(scenario())


def test_revoked_share_rejected(session):
    share = _share(session, options={"mode": "live"}, revoked_at=djtz.now())

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert not connected
        await comm.disconnect()

    _run(scenario())


def test_unrelated_session_items_not_forwarded(session):
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        # A different session's items must be dropped by the server-side filter.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": "some-other-session",
            "items": [{"line_num": 1, "display_level": 1, "content": "{}", "kind": "user_message"}],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())
