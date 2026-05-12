"""
Codex agent: wraps a single AsyncCodex thread for one TwiCC session.

Minimal v1 implementation. Streaming partial output to the frontend is left
out: the watcher picks up the JSONL file the Codex CLI writes and pushes it
through the regular session_item path, so the UI catches up at end-of-turn
granularity. Approvals are bypassed at the server level by the manager via
``sandbox=danger_full_access`` + ``approval_policy="never"``, so the agent
itself never has to mediate one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar

from channels.layers import get_channel_layer

from codex_app_server import (
    AsyncCodex,
    AsyncThread,
    AsyncTurnHandle,
    ReasoningEffort,
    TextInput,
    TransportClosedError,
)

from twicc.agent import AgentState, BaseAgent, StateChangeCallback
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

from ..streaming_registry import get_streamed_item_registry

logger = logging.getLogger(__name__)


def _agent_message_item(payload: Any) -> Any | None:
    """Unwrap a ``ThreadItem`` payload to its ``AgentMessageThreadItem`` inner.

    ``ItemStarted`` / ``ItemCompleted`` notifications carry the freshly
    minted (or finalized) ``ThreadItem`` under ``payload.item``. That
    ``ThreadItem`` is a Pydantic ``RootModel`` whose actual variant lives
    on ``.root`` — ``item.type`` is *not* a passthrough, it returns
    ``None``. We need the real inner instance to read ``type``/``id``.

    Returns the unwrapped instance only when it's an ``agentMessage``;
    any other type (reasoning, command_execution, plan, …) flows through
    the JSONL → watcher path and isn't streamed live in this iteration.
    """
    item = getattr(payload, "item", None)
    if item is None:
        return None
    inner = getattr(item, "root", item)
    if getattr(inner, "type", None) != "agentMessage":
        return None
    return inner


class CodexAgent(BaseAgent):
    """Codex SDK agent wrapping one ``AsyncCodex`` / ``AsyncThread`` pair.

    State machine:

    - ``STARTING`` → ``ASSISTANT_TURN``: ``start(text)`` flips the state and
      schedules a background task that runs the first turn. We don't await
      the turn inside ``start`` because ``_register_and_start`` calls it
      under the manager's lock, and a turn can run for minutes.
    - ``ASSISTANT_TURN`` → ``USER_TURN``: the turn task finishes its run.
    - ``USER_TURN`` → ``ASSISTANT_TURN``: ``send(text)`` schedules a new turn.
    - any → ``DEAD``: ``interrupt_or_kill`` first attempts a clean
      ``turn/interrupt`` via :class:`AsyncTurnHandle` (when a turn is in
      flight), then closes the transport. The in-flight turn task lands in
      ``DEAD`` via :class:`TransportClosedError` either way.
    """

    provider: ClassVar[Provider] = Provider.CODEX

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        settings: AgentSettings,
        codex: AsyncCodex,
        thread: AsyncThread,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings=settings)
        self._codex = codex
        self._thread = thread
        # Tracked so ``interrupt_or_kill`` can fire ``turn/interrupt`` on the
        # active turn instead of yanking the whole transport.
        self._current_turn: AsyncTurnHandle | None = None
        self._turn_task: asyncio.Task[None] | None = None

    @staticmethod
    def _sdk_effort(effort: str | None) -> ReasoningEffort | None:
        """Map our wire effort string to the SDK enum, ``None`` for unset/unknown.

        Unknown values fall through to ``None`` so Codex CLI picks its own
        default rather than crashing the turn — the dropdown only ever
        produces validated values today, this is a defensive guard.
        """
        if not effort:
            return None
        try:
            return ReasoningEffort(effort)
        except ValueError:
            logger.warning("Unknown Codex effort %r, falling back to CLI default", effort)
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        **kwargs: Any,
    ) -> None:
        """Wire the state-change callback and schedule the first turn."""
        self._state_change_callback = on_state_change

        # Flip to ASSISTANT_TURN immediately so the UI gates the input as
        # "working" — the actual turn runs in the background task below.
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text)

    async def send(self, text: str, **kwargs: Any) -> None:
        """Schedule a new turn on the live thread."""
        if self.state == AgentState.DEAD:
            raise RuntimeError("Cannot send message: agent is dead")
        if self.state == AgentState.ASSISTANT_TURN:
            raise RuntimeError(
                "Cannot send message: agent is busy (assistant turn in progress)",
            )

        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text)

    def _schedule_turn(self, text: str) -> None:
        """Spawn the background task that drives one turn end-to-end."""
        self._turn_task = asyncio.create_task(
            self._run_turn(text),
            name=f"codex-turn-{self.session_id}",
        )

    async def _run_turn(self, text: str) -> None:
        """Open one turn, wait for it to complete, transition to USER_TURN.

        Errors raised by the SDK (transport closed, RPC errors, ...) are
        funnelled through ``_handle_error`` and surface as a ``DEAD`` state
        with an ``error`` message. ``TransportClosedError`` is treated as a
        clean shutdown when ``kill_reason`` is already set (i.e. the manager
        killed us on purpose) — no error toast in that case.
        """
        # ``Thread.turn`` expects an ``Input`` (TextInput/ImageInput/...) — only
        # ``Thread.run`` accepts a bare str via internal normalization. We don't
        # use ``run`` because it consumes the turn stream and hides the
        # ``TurnHandle`` we need for clean ``interrupt`` later on.
        #
        # ``effort`` is read off ``agent_settings`` per turn so live updates
        # via ``send_to_session`` (which refreshes the bundle just before
        # calling ``send``) take effect immediately on the next turn. ``None``
        # lets Codex CLI use the model's default (medium today).
        effort = self._sdk_effort(self.agent_settings.effort)
        try:
            turn_handle = await self._thread.turn(TextInput(text), effort=effort)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return

        self._current_turn = turn_handle

        # Consume the turn's notification stream ourselves (instead of the
        # blackbox ``turn_handle.run()``) so we can:
        #   - Broadcast ``stream_block_*`` WS events that paint the live
        #     assistant text in the frontend before the JSONL line lands.
        #   - Push each completed ``agentMessage`` item_id onto the FIFO
        #     registry so the watcher can stamp the matching SessionItem
        #     with ``stream_uuid`` and the frontend can retire the synthetic
        #     placeholder. (See ``streaming_registry.py`` for the why.)
        try:
            stream = turn_handle.stream()
            try:
                async for event in stream:
                    await self._handle_stream_event(event)
            finally:
                await stream.aclose()
        except TransportClosedError:
            # Manager closed the transport — expected during interrupt_or_kill
            # or shutdown. _handle_error already ran (or will run); avoid a
            # second transition if we're already DEAD.
            if self.state != AgentState.DEAD:
                self._set_state(AgentState.DEAD)
                self.last_activity = time.time()
                await self._notify_state_change()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Turn run failed: {e}", exc=e)
            return
        finally:
            self._current_turn = None

        # Turn completed normally → ready for the next user input.
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def interrupt_or_kill(self, reason: str) -> None:
        """Stop the agent. Tries a clean ``turn/interrupt`` first, then closes.

        Always lands in ``DEAD``. Safe to call multiple times.
        """
        if self.state == AgentState.DEAD:
            return

        self.kill_reason = reason

        # Clean turn cancellation when a turn is in flight. We don't gate this
        # on AgentState.ASSISTANT_TURN: depending on race timing, the turn
        # task may have just transitioned to USER_TURN but the next ``send``
        # could re-arm a turn before we observe DEAD. Issuing interrupt is a
        # best-effort no-op if no turn is active server-side.
        turn_handle = self._current_turn
        if turn_handle is not None:
            try:
                await turn_handle.interrupt()
            except Exception as e:
                logger.debug(
                    "turn_handle.interrupt() failed for session %s: %s — "
                    "falling back to transport close",
                    self.session_id, e,
                )

        # Close the codex transport — the turn task lands in DEAD via
        # TransportClosedError. Idempotent on the SDK side.
        try:
            await self._codex.close()
        except Exception as e:
            logger.warning(
                "codex.close() failed for session %s: %s", self.session_id, e,
            )

        # Cancel the turn task if it hasn't unwound from
        # TransportClosedError yet (e.g. it was awaiting something other
        # than ``turn_handle.run``).
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "Turn task raised on cancellation for session %s",
                    self.session_id, exc_info=True,
                )

        # Drop any item_ids buffered for this session. The agent is going
        # away, so the watcher will never get matching JSONL lines for
        # whatever was streamed and not yet completed (or whatever was
        # completed in the SDK after we tore the transport down). Keeping
        # them would corrupt the FIFO for the next agent on the same id.
        get_streamed_item_registry().clear_session(self.session_id)

        if self.state != AgentState.DEAD:
            self._set_state(AgentState.DEAD)
            self.last_activity = time.time()
            await self._notify_state_change()

    async def _handle_error(
        self, error_message: str, exc: Exception | None = None,
    ) -> None:
        """Surface a runtime error as a clean DEAD transition."""
        logger.error(
            "Codex agent for session %s died: %s",
            self.session_id, error_message,
            exc_info=exc,
        )
        self.error = error_message
        self.kill_reason = "error"
        try:
            await self._codex.close()
        except Exception:
            # Already broken — don't pile more errors on top.
            logger.debug(
                "codex.close() during error handling failed for session %s",
                self.session_id, exc_info=True,
            )
        get_streamed_item_registry().clear_session(self.session_id)
        self._set_state(AgentState.DEAD)
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # Stream event handling
    # ------------------------------------------------------------------

    async def _handle_stream_event(self, event: Any) -> None:
        """Translate one Codex SDK stream notification into TwiCC's WS protocol.

        We only handle ``agentMessage`` items in this iteration — reasoning,
        tool calls, etc. flow exclusively through the JSONL → watcher path
        for now. The mapping to the Claude-shared ``stream_block_*`` wire
        format is:

        - ``item/started`` on an ``agentMessage`` → ``stream_block_start``
          (``message_id`` carries the Codex ``item_id``; ``block_index``
          is always ``0`` because a Codex agentMessage is mono-block).
        - ``item/agentMessage/delta``             → ``stream_block_delta``
        - ``item/completed`` on an ``agentMessage`` → ``stream_block_stop``
          + ``stream_block_end`` (``uuid`` = ``item_id``, same value the
          watcher will stamp onto ``SessionItem.stream_uuid`` after popping
          the FIFO registry).
        """
        method = event.method
        payload = event.payload

        if method == "item/started":
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_start",
                "session_id": self.session_id,
                "message_id": agent_msg.id,
                "block_index": 0,
                "block_type": "text",
            })
            return

        if method == "item/agentMessage/delta":
            item_id = getattr(payload, "item_id", None)
            delta = getattr(payload, "delta", None)
            if not item_id or delta is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_delta",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "text",
                "text": delta,
            })
            return

        if method == "item/completed":
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            item_id = agent_msg.id
            await self._broadcast_stream_event({
                "type": "stream_block_stop",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "text",
            })
            await self._broadcast_stream_event({
                "type": "stream_block_end",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "text",
                "uuid": item_id,
            })
            # Hand the item_id off to the watcher so it can stamp the
            # matching SessionItem when the JSONL line lands.
            get_streamed_item_registry().push(self.session_id, item_id)
            return

    async def _broadcast_stream_event(self, data: dict[str, Any]) -> None:
        """Broadcast a streaming event to all connected WebSocket clients.

        Mirror of ``ClaudeCodeAgent._broadcast_stream_event``: pushes through
        the ``"updates"`` channel group; the consumer's ``broadcast`` handler
        forwards ``data`` verbatim as a WS message.
        """
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": data},
        )
