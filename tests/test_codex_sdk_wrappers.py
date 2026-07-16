"""Fine-grained Codex SDK wrappers preserve the approval reviewer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openai_codex import TextInput
from openai_codex.generated.v2_all import ApprovalsReviewer, AskForApproval, SandboxMode

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
