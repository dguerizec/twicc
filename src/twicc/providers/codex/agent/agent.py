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

from codex_app_server import (
    AsyncCodex,
    AsyncThread,
    AsyncTurnHandle,
    TextInput,
    TransportClosedError,
)

from twicc.agent import AgentState, BaseAgent, StateChangeCallback
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)


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
        try:
            turn_handle = await self._thread.turn(TextInput(text))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return

        self._current_turn = turn_handle

        try:
            await turn_handle.run()
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
        self._set_state(AgentState.DEAD)
        self.last_activity = time.time()
        await self._notify_state_change()
