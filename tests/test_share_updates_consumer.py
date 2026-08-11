import asyncio

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.asgi import WSConsumer
from twicc.core.models import Project, Session, SessionType, Share
from twicc.core.services.share_tokens import mint_token


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def attributed_share(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-share-updates", directory="/tmp/share-updates")
    target = Session.objects.create(
        id="target-updates", project=project, provider="claude_code",
        file_path="target-updates.jsonl", type=SessionType.SESSION,
        title="Target", created_at=now, last_new_content_at=now,
    )
    creator = Session.objects.create(
        id="creator-updates", project=project, provider="claude_code",
        file_path="creator-updates.jsonl", type=SessionType.SESSION,
        title="Creator", created_at=now, last_new_content_at=now,
    )
    share = Share.objects.create(
        kind="session", token=mint_token(), session=target,
        created_by_session=creator,
    )
    return share, creator


def test_initial_shares_updated_serializes_visible_creator(
        attributed_share, monkeypatch, settings):
    share, creator = attributed_share
    settings.TWICC_PASSWORD_HASH = ""

    class Registry:
        def set_broadcast_callback(self, callback):
            self.callback = callback

    registry = Registry()
    monkeypatch.setattr("twicc.asgi.scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: registry)

    async def scenario():
        comm = WebsocketCommunicator(
            WSConsumer.as_asgi(), "/ws/?subscribe=shares_updated",
        )
        connected, _ = await comm.connect()
        assert connected
        message = await comm.receive_json_from(timeout=2)
        assert message["type"] == "shares_updated"
        row = next(item for item in message["shares"] if item["id"] == share.id)
        assert row["created_by"] == {
            "kind": "agent",
            "session": {
                "id": creator.id,
                "title": "Creator",
                "project_id": creator.project_id,
            },
        }
        await comm.disconnect()

    _run(scenario())
