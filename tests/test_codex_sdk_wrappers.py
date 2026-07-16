"""Fine-grained Codex SDK wrappers preserve the approval reviewer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai_codex import TextInput
from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    CollaborationMode,
    ModeKind,
    SandboxMode,
    SandboxPolicy,
    Settings,
    WorkspaceWriteSandboxPolicy,
)

from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex, TwiccAsyncThread


def _mock_codex() -> TwiccAsyncCodex:
    codex = TwiccAsyncCodex()
    codex._ensure_initialized = AsyncMock()
    codex._client = AsyncMock()
    return codex


def test_thread_start_forwards_auto_review() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_start.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )

        await codex.thread_start_with_policy(
            sandbox=SandboxMode.workspace_write,
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.auto_review,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_start.await_args.args[0]
    assert params.approvals_reviewer is ApprovalsReviewer.auto_review


def test_thread_resume_forwards_user_reviewer() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_resume.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )

        await codex.thread_resume_with_policy(
            "thread-id",
            sandbox=SandboxMode.workspace_write,
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.user,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_resume.await_args.args[1]
    assert params.approvals_reviewer is ApprovalsReviewer.user


def test_thread_set_name_does_not_resume_rollout() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        await codex.thread_set_name("thread-id", "New title")
        return codex

    codex = asyncio.run(scenario())
    codex._client.thread_set_name.assert_awaited_once_with(
        "thread-id",
        "New title",
    )
    codex._client.thread_resume.assert_not_awaited()


def test_turn_forwards_auto_review() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.turn_start.return_value = SimpleNamespace(
            turn=SimpleNamespace(id="turn-id"),
        )
        thread = TwiccAsyncThread(codex, "thread-id")

        await thread.turn_with_policy(
            [TextInput("test")],
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.auto_review,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.turn_start.await_args.kwargs["params"]
    assert params.approvals_reviewer is ApprovalsReviewer.auto_review


def test_approve_guardian_denied_action_uses_native_rpc() -> None:
    event = {
        "id": "review-1",
        "turn_id": "turn-id",
        "started_at_ms": 1,
        "completed_at_ms": 2,
        "status": "denied",
        "action": {
            "type": "network_access",
            "target": "https://example.com",
            "host": "example.com",
            "protocol": "https",
            "port": 443,
        },
    }

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.approve_guardian_denied_action(event)
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/approveGuardianDeniedAction",
        {"threadId": "thread-id", "event": event},
    )


def test_thread_settings_update_uses_native_rpc() -> None:
    sandbox_policy = SandboxPolicy(root=WorkspaceWriteSandboxPolicy(
        type="workspaceWrite",
        network_access=True,
        writable_roots=["/data/artifacts/thread-id", "/data/scratch/thread-id"],
    ))

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.update_settings_with_policy(sandbox_policy=sandbox_policy)
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/settings/update",
        {
            "threadId": "thread-id",
            "sandboxPolicy": {
                "excludeSlashTmp": False,
                "excludeTmpdirEnvVar": False,
                "networkAccess": True,
                "type": "workspaceWrite",
                "writableRoots": [
                    "/data/artifacts/thread-id",
                    "/data/scratch/thread-id",
                ],
            },
        },
    )


def test_thread_settings_update_collaboration_mode_payload() -> None:
    """The Plan-mode payload keeps ``developer_instructions`` as explicit null.

    ``settings.developer_instructions: null`` means "use Codex's built-in
    instructions for this collaboration mode" — an ``exclude_none`` dump would
    drop the key and change the protocol meaning. Same for
    ``reasoning_effort: null`` (the mode preset / config decides).
    """
    collaboration_mode = CollaborationMode(
        mode=ModeKind.plan,
        settings=Settings(
            model="gpt-5.6",
            reasoning_effort=None,
            developer_instructions=None,
        ),
    )

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.update_settings_with_policy(
            collaboration_mode=collaboration_mode,
        )
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/settings/update",
        {
            "threadId": "thread-id",
            "collaborationMode": {
                "mode": "plan",
                "settings": {
                    "model": "gpt-5.6",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
        },
    )


def test_thread_settings_update_requires_a_setting() -> None:
    async def scenario() -> None:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        with pytest.raises(ValueError):
            await thread.update_settings_with_policy()

    asyncio.run(scenario())
