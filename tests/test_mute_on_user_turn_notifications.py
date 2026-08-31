import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from twicc import external_notifications
from twicc.agent.states import AgentInfo, AgentState, PendingRequest
from twicc.asgi import broadcast_process_state
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType


def _info(state, *, session_id="mute-notification", pending_requests=()):
    return AgentInfo(
        session_id=session_id,
        project_id="-mute-notification",
        provider=Provider.CODEX,
        state=state,
        previous_state=AgentState.ASSISTANT_TURN,
        started_at=1.0,
        state_changed_at=2.0,
        last_activity=2.0,
        pending_requests=pending_requests,
    )


@pytest.fixture(autouse=True)
def _reset_external_notification_baseline():
    external_notifications._last_seen.clear()
    yield
    external_notifications._last_seen.clear()


@pytest.mark.django_db(transaction=True)
def test_process_state_payload_carries_the_current_mute_flag():
    project = Project.objects.create(
        id="-mute-notification", directory="/tmp/mute-notification"
    )
    Session.objects.create(
        id="mute-notification",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        mute_on_user_turn=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    notify = Mock()
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch(
                "twicc.asgi.get_session_and_project_display",
                new=AsyncMock(return_value=("Session", "Project", None)),
            ), \
            patch("twicc.asgi.notify_agent_event", notify):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    message = layer.group_send.await_args.args[1]["data"]
    assert message["mute_on_user_turn"] is True
    assert notify.call_args.args[-1] is True


@pytest.mark.django_db(transaction=True)
def test_missing_session_row_is_explicitly_unmuted():
    layer = SimpleNamespace(group_send=AsyncMock())
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch(
                "twicc.asgi.get_session_and_project_display",
                new=AsyncMock(return_value=(None, None, None)),
            ), \
            patch("twicc.asgi.notify_agent_event"):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    message = layer.group_send.await_args.args[1]["data"]
    assert message["mute_on_user_turn"] is False


@pytest.mark.django_db(transaction=True)
def test_hidden_session_still_emits_no_process_state_or_apprise_event():
    project = Project.objects.create(
        id="-mute-notification", directory="/tmp/mute-notification"
    )
    Session.objects.create(
        id="mute-notification",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        hidden=True,
        mute_on_user_turn=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    notify = Mock()
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.notify_agent_event", notify):
        asyncio.run(broadcast_process_state(_info(AgentState.USER_TURN)))

    assert layer.group_send.await_count == 0
    assert notify.call_count == 0


def _notification_settings():
    return {
        "externalNotificationTargets": [{
            "url": "json://example.test",
            "enabled": True,
            "tested": True,
            "awayOnly": False,
            "notifyUserTurn": True,
            "notifyPendingRequest": True,
        }]
    }


def test_apprise_baseline_advances_during_a_muted_user_turn():
    spawned = []
    with patch.object(
        external_notifications, "read_synced_settings",
        return_value=_notification_settings(),
    ), patch.object(
        external_notifications, "get_provider_helpers",
        return_value=SimpleNamespace(LABEL="Codex"),
    ), patch.object(
        external_notifications,
        "_send",
        new=lambda urls, title, body: (urls, title, body),
    ), patch.object(external_notifications, "_spawn", spawned.append):
        external_notifications.notify_agent_event(
            _info(AgentState.USER_TURN), "Session", "Project", None, True,
        )
        external_notifications.notify_agent_event(
            _info(AgentState.USER_TURN), "Session", "Project", None, False,
        )

    assert spawned == []


def test_muted_session_still_sends_pending_request_apprise_event():
    request = PendingRequest(
        request_id="request-1",
        request_type="ask_user_question",
        tool_name="request_user_input",
        tool_input={},
        created_at=3.0,
    )
    spawned = []
    with patch.object(
        external_notifications, "read_synced_settings",
        return_value=_notification_settings(),
    ), patch.object(
        external_notifications, "get_provider_helpers",
        return_value=SimpleNamespace(LABEL="Codex"),
    ), patch.object(
        external_notifications,
        "_send",
        new=lambda urls, title, body: (urls, title, body),
    ), patch.object(external_notifications, "_spawn", spawned.append):
        external_notifications.notify_agent_event(
            _info(AgentState.ASSISTANT_TURN, pending_requests=(request,)),
            "Session", "Project", None, True,
        )

    assert len(spawned) == 1
    assert spawned[0][1] == "Codex has a question for you"
