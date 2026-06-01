"""TwiCC wrappers for the Codex SDK that preserve fine-grained approval/sandbox.

The public ``openai_codex`` SDK exposes ``AsyncCodex.thread_start``,
``AsyncCodex.thread_resume`` and ``AsyncThread.turn`` that only accept the
coarse :class:`openai_codex.ApprovalMode` enum (``deny_all`` /
``auto_review``) for permission control. TwiCC needs the full granularity
of :class:`AskForApproval` so the 5 user-facing presets
(``read_only`` / ``strict`` / ``auto`` / ``autonomous`` / ``yolo``) map
exactly to their intended wire combinations, and it must keep
``approvals_reviewer=None`` so every approval request is routed to the
user (never auto-resolved by Codex's reviewer subagent — which would
short-circuit the TwiCC approval bridge).

These subclasses add ``*_with_policy`` methods that bypass the high-level
mapping and build the typed JSON-RPC params directly. The returned
runtime objects (``AsyncThread`` subclass, ``AsyncTurnHandle``,
streaming, errors) are otherwise the upstream SDK ones — only the
parameter-build step is replaced.

PRIVATE SDK API: the implementation reaches into
``openai_codex._inputs._normalize_run_input`` / ``_to_wire_input`` and
into ``AsyncCodex._ensure_initialized`` / ``AsyncCodex._client``. See
the memory ``reference_codex_sdk_update_procedure.md`` for the upgrade
checklist (these attribute paths must hold).
"""

from __future__ import annotations

from typing import Any

from openai_codex import AsyncCodex, AsyncThread, AsyncTurnHandle, RunInput
from openai_codex._inputs import _normalize_run_input, _to_wire_input
from openai_codex.generated.v2_all import (
    AskForApproval,
    ReasoningEffort,
    SandboxMode,
    SandboxPolicy,
    ThreadResumeParams,
    ThreadStartParams,
    TurnStartParams,
)


class TwiccAsyncThread(AsyncThread):
    """``AsyncThread`` with ``turn_with_policy`` for fine-grained per-turn overrides."""

    async def turn_with_policy(
        self,
        input: RunInput,
        *,
        approval_policy: AskForApproval | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: ReasoningEffort | None = None,
        model: str | None = None,
    ) -> AsyncTurnHandle:
        """Start a turn with fine-grained approval/sandbox overrides.

        Bypasses :class:`openai_codex.ApprovalMode` so the per-turn
        override carries the same 5-preset granularity as the start /
        resume call. ``approvals_reviewer`` is hard-coded to ``None``
        (``user``) so approvals stay routed to the TwiCC bridge.
        """
        await self._codex._ensure_initialized()
        wire_input = _to_wire_input(_normalize_run_input(input))
        params = TurnStartParams(
            thread_id=self.id,
            input=wire_input,
            approval_policy=approval_policy,
            approvals_reviewer=None,
            effort=effort,
            model=model,
            sandbox_policy=sandbox_policy,
        )
        turn = await self._codex._client.turn_start(
            self.id, wire_input, params=params,
        )
        return AsyncTurnHandle(self._codex, self.id, turn.turn.id)


class TwiccAsyncCodex(AsyncCodex):
    """``AsyncCodex`` whose ``*_with_policy`` methods return :class:`TwiccAsyncThread`."""

    async def thread_start_with_policy(
        self,
        *,
        sandbox: SandboxMode | None = None,
        approval_policy: AskForApproval | None = None,
        cwd: str | None = None,
        config: dict[str, Any] | None = None,
        model: str | None = None,
        ephemeral: bool | None = None,
        developer_instructions: str | None = None,
    ) -> TwiccAsyncThread:
        """Start a new thread with fine-grained approval/sandbox.

        ``developer_instructions`` lands in the thread rollout as a
        ``developer``-role message and is replayed on every subsequent
        turn, including after ``thread_resume`` (the Codex protocol keeps
        the original block when resume does not re-pass the field).
        """
        await self._ensure_initialized()
        params = ThreadStartParams(
            approval_policy=approval_policy,
            approvals_reviewer=None,
            config=config,
            cwd=cwd,
            developer_instructions=developer_instructions,
            ephemeral=ephemeral,
            model=model,
            sandbox=sandbox,
        )
        started = await self._client.thread_start(params)
        return TwiccAsyncThread(self, started.thread.id)

    async def thread_resume_with_policy(
        self,
        thread_id: str,
        *,
        sandbox: SandboxMode | None = None,
        approval_policy: AskForApproval | None = None,
        cwd: str | None = None,
        config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> TwiccAsyncThread:
        """Resume an existing thread with fine-grained approval/sandbox.

        The resumed thread's model is sticky server-side; leave ``model``
        unset to keep whichever model the thread was started with.
        """
        await self._ensure_initialized()
        params = ThreadResumeParams(
            thread_id=thread_id,
            approval_policy=approval_policy,
            approvals_reviewer=None,
            config=config,
            cwd=cwd,
            model=model,
            sandbox=sandbox,
        )
        resumed = await self._client.thread_resume(thread_id, params)
        return TwiccAsyncThread(self, resumed.thread.id)
