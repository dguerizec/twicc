"""Codex Auto-review denials can be handed to TwiCC's user approval flow."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openai_codex.generated.v2_all import ItemGuardianApprovalReviewCompletedNotification

from twicc.providers.codex.agent.agent import (
    CodexAgent,
    _guardian_denial_context,
)


def _network_review(status: str = "denied") -> ItemGuardianApprovalReviewCompletedNotification:
    return ItemGuardianApprovalReviewCompletedNotification.model_validate({
        "threadId": "thread-id",
        "turnId": "turn-id",
        "targetItemId": None,
        "reviewId": "review-id",
        "startedAtMs": 10,
        "completedAtMs": 20,
        "decisionSource": "agent",
        "review": {
            "status": status,
            "riskLevel": "medium",
            "userAuthorization": "low",
            "rationale": "The requested host was not authorized.",
        },
        "action": {
            "type": "networkAccess",
            "target": "https://example.com",
            "host": "example.com",
            "protocol": "https",
            "port": 443,
        },
    })


def _agent(*, decision: str = "accept", steer_error: Exception | None = None) -> CodexAgent:
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "thread-id"
    agent._thread = SimpleNamespace(approve_guardian_denied_action=AsyncMock())
    agent._await_pending_request = AsyncMock(return_value={"decision": decision})
    agent._current_turn = SimpleNamespace(steer=AsyncMock(side_effect=steer_error))
    agent._auto_review_retry_after_turn = False
    agent._auto_review_retry_action = None
    agent.last_activity = 0
    return agent


def test_denial_context_converts_public_action_to_native_event() -> None:
    event, display = _guardian_denial_context(_network_review()) or ({}, {})

    assert event == {
        "id": "review-id",
        "turn_id": "turn-id",
        "started_at_ms": 10,
        "completed_at_ms": 20,
        "status": "denied",
        "risk_level": "medium",
        "user_authorization": "low",
        "rationale": "The requested host was not authorized.",
        "decision_source": "agent",
        "action": {
            "type": "network_access",
            "target": "https://example.com",
            "host": "example.com",
            "protocol": "https",
            "port": 443,
        },
    }
    assert display["action"]["type"] == "networkAccess"
    assert display["rationale"] == "The requested host was not authorized."


def test_user_approval_records_native_override_and_steers_retry() -> None:
    agent = _agent()
    asyncio.run(agent._handle_auto_review_completed(_network_review()))

    request = agent._await_pending_request.await_args.args[0]
    assert request.tool_name == "autoReviewDenial"
    assert request.tool_input["action"]["host"] == "example.com"
    native_event = agent._thread.approve_guardian_denied_action.await_args.args[0]
    assert native_event["action"]["type"] == "network_access"
    agent._current_turn.steer.assert_awaited_once()
    assert agent._auto_review_retry_after_turn is True

    asyncio.run(agent._handle_stream_event(SimpleNamespace(
        method="item/autoApprovalReview/completed",
        payload=_network_review("approved"),
    )))
    assert agent._auto_review_retry_after_turn is False
    assert agent._auto_review_retry_action is None


def test_user_approval_schedules_continuation_when_turn_cannot_be_steered() -> None:
    agent = _agent(steer_error=RuntimeError("turn already completed"))
    asyncio.run(agent._handle_auto_review_completed(_network_review()))

    agent._thread.approve_guardian_denied_action.assert_awaited_once()
    assert agent._auto_review_retry_after_turn is True


def test_user_can_keep_auto_review_denial() -> None:
    agent = _agent(decision="decline")
    asyncio.run(agent._handle_auto_review_completed(_network_review()))

    agent._thread.approve_guardian_denied_action.assert_not_awaited()
    agent._current_turn.steer.assert_not_awaited()
    assert agent._auto_review_retry_after_turn is False
