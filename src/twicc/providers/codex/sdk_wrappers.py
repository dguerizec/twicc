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
into ``AsyncCodex._ensure_initialized`` / ``AsyncCodex._client``. The
goal helpers and ``inject_user_message`` additionally call
``AsyncCodex._client.request`` with raw ``thread/goal/*`` /
``thread/inject_items`` method strings — these app-server RPCs have no
generated SDK wrapper (only the goal notifications are generated). See the
memory ``reference_codex_sdk_update_procedure.md`` for the upgrade checklist
(these attribute paths must hold).
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
    ThreadGoal,
    ThreadResumeParams,
    ThreadStartParams,
    TurnStartParams,
)
from pydantic import BaseModel, ConfigDict


class _ThreadGoalEnvelope(BaseModel):
    """``thread/goal/{get,set}`` response — the thread's goal, or ``None``."""

    model_config = ConfigDict(populate_by_name=True)
    goal: ThreadGoal | None = None


class _ThreadGoalClearResponse(BaseModel):
    """``thread/goal/clear`` response — whether a goal was actually removed."""

    model_config = ConfigDict(populate_by_name=True)
    cleared: bool = False


class _ThreadInjectItemsResponse(BaseModel):
    """``thread/inject_items`` response — an empty envelope."""

    model_config = ConfigDict(populate_by_name=True)


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

    # ------------------------------------------------------------------
    # Goal RPCs (``thread/goal/{get,set,clear}``)
    #
    # Not part of the generated SDK surface — only the goal *notifications*
    # are generated. TwiCC drives the app-server RPCs directly through the
    # private client, mirroring how the SDK's own ``compact()`` reaches
    # ``thread/compact/start``. These are thin single-RPC helpers; the
    # set-vs-clear-vs-replace orchestration lives on ``CodexAgent``.
    # ------------------------------------------------------------------

    async def goal_get(self) -> ThreadGoal | None:
        """Read this thread's goal via ``thread/goal/get`` (``None`` when unset)."""
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/get",
            {"threadId": self.id},
            response_model=_ThreadGoalEnvelope,
        )
        return response.goal

    async def goal_set(self, objective: str) -> ThreadGoal | None:
        """Create or edit this thread's goal via ``thread/goal/set``.

        Sends only ``objective`` (+ ``threadId``): on a thread with no goal
        this creates one (``status=active``, no budget, counters at 0); on an
        existing goal it edits the objective in place, keeping status, budget
        and counters. Budget and status are deliberately never sent — TwiCC's
        ``/goal`` surface is objective-only.
        """
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/set",
            {"threadId": self.id, "objective": objective},
            response_model=_ThreadGoalEnvelope,
        )
        return response.goal

    async def goal_clear(self) -> bool:
        """Delete this thread's goal via ``thread/goal/clear``.

        Returns the server's ``cleared`` flag (``False`` when there was no
        goal to remove).
        """
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/clear",
            {"threadId": self.id},
            response_model=_ThreadGoalClearResponse,
        )
        return response.cleared

    async def inject_user_message(self, text: str) -> None:
        """Append a synthetic user message to the thread via ``thread/inject_items``.

        Records a ``message`` (role=user) item in the thread's rollout AND
        model-visible history WITHOUT starting or steering a turn (Codex
        ``inject_no_new_turn`` + ``flush_rollout``), so it is safe even while a
        turn runs. TwiCC uses this to give ``/goal clear`` and ``/compact`` a
        persistent transcript line — those RPCs write no "the user asked" line to
        the rollout on their own (``/goal clear`` is a wire-only notification;
        ``/compact`` writes only the ``compacted`` summary); the compute then
        relabels the injected line as a real ``user_message`` (see
        ``_injected_command_text``). Like the goal RPCs, ``thread/inject_items``
        has no generated SDK wrapper.
        """
        await self._codex._ensure_initialized()
        await self._codex._client.request(
            "thread/inject_items",
            {
                "threadId": self.id,
                "items": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                ],
            },
            response_model=_ThreadInjectItemsResponse,
        )


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
