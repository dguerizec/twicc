"""Regression tests: SendMessage continuations must not flip agent links to background.

A foreground ``Agent`` call (``run_in_background: false``) completes with a
single tool_result. The parent can later continue that agent via
``SendMessage``: the CLI resumes it in the background and, when it stops,
emits a ``<task-notification>`` whose ``<tool-use-id>`` is the SendMessage
tool_use, not the launching one. TwiCC rewrites that notification into a
tool_result carrying ``toolUseResult = {agentId, isAsync: true}``.

Before the fix, ``create_agent_link_from_tool_result`` treated that isAsync
as an async-launch ack and upgraded the ORIGINAL (foreground, already
complete) link to background, re-broadcasting ``agent_link_created`` — which
resurrected the subagent's synthetic "running" state in the frontend with no
removal path: the second result the background rule then waits for lands on
the SendMessage tool_use, never on the link's.

The upgrade must only apply when the isAsync tool_result sits on the link's
own tool_use (the genuine async-launch-ack backfill).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from twicc.core.enums import Provider
from twicc.core.models import AgentLink, Project, Session, SessionItem
from twicc.providers.claude_code.compute import get_compute

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _tool_result_entry(tool_use_id: str, agent_id: str, *, is_async: bool) -> dict:
    """Parsed JSONL entry: a tool_result carrying an agent reference.

    ``is_async=True`` mirrors both the CLI's async launch ack and TwiCC's
    task-notification rewrite (compute's ``_transform_inline_provider``),
    which both surface ``toolUseResult = {agentId, isAsync: true}``.
    """
    tool_use_result: dict = {"agentId": agent_id}
    if is_async:
        tool_use_result["isAsync"] = True
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": "done"},
            ],
        },
        "toolUseResult": tool_use_result,
    }


@pytest.fixture
def parent_session(db):
    project = Project.objects.create(id="test-project-agent-links")
    return Session.objects.create(
        id="test-session-agent-links",
        project=project,
        provider=Provider.CLAUDE_CODE,
    )


def _make_item(session: Session, line_num: int, content: str = "{}") -> SessionItem:
    return SessionItem.objects.create(session=session, line_num=line_num, content=content)


class TestAsyncUpgradeScopedToLinkToolUse:
    def test_continuation_notification_does_not_flip_foreground_link(self, parent_session):
        """A rewritten notification on a SendMessage tool_use leaves the link alone."""
        agent_id = "agent-continuation"
        launch_tool_use = "toolu_launch_001"
        sendmessage_tool_use = "toolu_sendmessage_001"

        AgentLink.objects.create(
            session=parent_session,
            tool_use_line_num=10,
            tool_use_id=launch_tool_use,
            agent_id=agent_id,
            is_background=False,
            started_at=_NOW,
        )
        item = _make_item(parent_session, line_num=42)

        compute = get_compute()
        update = compute.create_agent_link_from_tool_result(
            parent_session.id,
            item,
            _tool_result_entry(sendmessage_tool_use, agent_id, is_async=True),
        )

        assert update is None
        link = AgentLink.objects.get(session=parent_session, agent_id=agent_id)
        assert link.is_background is False
        assert link.tool_use_id == launch_tool_use

    def test_async_ack_on_link_tool_use_still_upgrades(self, parent_session):
        """The genuine backfill — the ack lands on the link's own tool_use — keeps working."""
        agent_id = "agent-async-ack"
        launch_tool_use = "toolu_launch_002"

        AgentLink.objects.create(
            session=parent_session,
            tool_use_line_num=10,
            tool_use_id=launch_tool_use,
            agent_id=agent_id,
            is_background=False,
            started_at=_NOW,
        )
        item = _make_item(parent_session, line_num=43)

        compute = get_compute()
        update = compute.create_agent_link_from_tool_result(
            parent_session.id,
            item,
            _tool_result_entry(launch_tool_use, agent_id, is_async=True),
        )

        assert update is not None
        assert update.is_background is True
        assert update.tool_use_id == launch_tool_use
        link = AgentLink.objects.get(session=parent_session, agent_id=agent_id)
        assert link.is_background is True
