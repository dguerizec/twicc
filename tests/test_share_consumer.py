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


def test_tool_state_forwarded_with_visibility_check(session):
    from twicc.core.models import SessionItem, ToolResultLink

    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    sid = session.id
    # Visible tool_use (level 1) at line 2; hidden one (DEBUG_ONLY) at line 4.
    SessionItem.objects.create(session=session, line_num=2, content="{}", display_level=1, kind="assistant_message")
    SessionItem.objects.create(session=session, line_num=4, content="{}", display_level=3, kind="assistant_message")
    ToolResultLink.objects.create(session=session, tool_use_line_num=2, tool_result_line_num=3,
                                  tool_use_id="tu-vis", tool_result_at=djtz.now())
    ToolResultLink.objects.create(session=session, tool_use_line_num=4, tool_result_line_num=5,
                                  tool_use_id="tu-hid", tool_result_at=djtz.now(), error="hidden detail")

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": sid, "tool_use_id": "tu-vis",
            "result_count": 1, "completed_at": None, "extra": None, "error": None,
            "tool_result_line_nums": [3],
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_tool_state"
        assert msg["tool_use_id"] == "tu-vis"
        assert msg["result_count"] == 1
        # Over-ceiling tool_use: dropped (its error text must not leak).
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": sid, "tool_use_id": "tu-hid",
            "result_count": 1, "completed_at": None, "extra": None, "error": "hidden detail",
            "tool_result_line_nums": [5],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        # Another session's tool_state: dropped.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": "some-other-session", "tool_use_id": "tu-x",
            "result_count": 1, "completed_at": None, "extra": None, "error": None,
            "tool_result_line_nums": [1],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_share_updated_refreshes_open_socket_filters(session):
    # Review finding: an owner tightening a live share's options must apply to
    # already-connected viewers — the socket's connect-time ceiling is stale.
    share = _share(session, options={"mode": "live", "max_display_mode": "debug"})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        item = {"line_num": 1, "display_level": 3, "content": "{}", "kind": "system"}
        # Debug ceiling: level-3 forwarded.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid, "items": [item]}})
        assert (await comm.receive_json_from(timeout=2))["type"] == "share_items_added"
        # Owner tightens to normal → socket refreshes its filters + pushes meta.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "share_updated",
            "share": {"id": share.id, "status": "active",
                      "options": {"mode": "live", "max_display_mode": "normal"}},
        }})
        assert (await comm.receive_json_from(timeout=2))["type"] == "share_meta"
        # Level-3 traffic is now dropped by the refreshed ceiling.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid, "items": [item]}})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_process_state_forwarded_root_only(session):
    # Live "is thinking" indicator: the root session's process_state is forwarded
    # slim (state only); another session's is dropped.
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "process_state", "session_id": sid, "state": "assistant_turn",
            "label": "compacting", "tools": ["Bash"],  # owner-only detail, must be stripped
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg == {"type": "share_process_state", "state": "assistant_turn"}
        # Another session's process_state: dropped.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "process_state", "session_id": "some-other-session", "state": "assistant_turn"}})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_agent_link_forwarded_for_live_spawned_subagent(session):
    # A subagent spawned after page load must become openable: the consumer both
    # tracks it as a descendant AND pushes a viewer link so "View Agent" resolves.
    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "agent_link_created", "parent_session_id": sid,
            "agent_session_id": "sub-1", "agent_slug": "explorer",
            "tool_use_id": "tu-agent", "tool_use_line_num": 3,
            "is_background": False, "started_at": None,
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_agent_link"
        assert msg["link"] == {
            "agent_id": "sub-1", "agent_slug": "explorer", "tool_use_id": "tu-agent",
            "tool_use_line_num": 3, "is_background": False, "started_at": None,
        }
        # The new subagent is now a tracked descendant: its items forward too.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": "sub-1",
            "items": [{"line_num": 1, "display_level": 1, "content": "{}", "kind": "assistant_message"}],
        }})
        items_msg = await comm.receive_json_from(timeout=2)
        assert items_msg["type"] == "share_items_added" and items_msg["session_id"] == "sub-1"
        await comm.disconnect()

    _run(scenario())


def test_agent_link_not_forwarded_when_subagents_disabled(session):
    share = _share(session, options={"mode": "live", "include_subagents": False})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "agent_link_created", "parent_session_id": sid,
            "agent_session_id": "sub-1", "tool_use_id": "tu-agent", "tool_use_line_num": 3,
        }})
        assert await comm.receive_nothing(timeout=0.5)
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
