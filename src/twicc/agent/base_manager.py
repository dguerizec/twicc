"""
Base agent manager for TwiCC.

Owns the registry of running agents, the timeout monitor, and the database
lifecycle bookkeeping (``ProcessRun`` rows, ``Session`` start/stop timestamps).
Provider-specific managers subclass this and plug in their factory and
optional hooks (state-change extras, timeout policy, extra monitors).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from .base_agent import BaseAgent
from .states import AgentInfo, AgentState

logger = logging.getLogger(__name__)

# Async callback used to push agent state to clients (typically over WebSocket).
BroadcastCallback = Callable[[AgentInfo], Coroutine[Any, Any, None]]


class BaseAgentManager:
    """Provider-agnostic manager for a fleet of agents.

    Subclasses must override ``_create_agent`` to build their provider's
    agent type and typically wrap it in a higher-level method
    (``send_to_session``, ``create_session``, ...) whose signature depends on
    the provider's settings shape.
    """

    # Interval (seconds) of the timeout monitor loop. Frequent enough to
    # catch short startup timeouts accurately without excessive churn.
    TIMEOUT_MONITOR_INTERVAL = 30

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._lock = asyncio.Lock()
        self._broadcast_callback: BroadcastCallback | None = None
        self._timeout_monitor_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Public API — generic for every provider
    # ------------------------------------------------------------------

    def set_broadcast_callback(self, callback: BroadcastCallback) -> None:
        """Register the callback used to broadcast agent state changes."""
        self._broadcast_callback = callback

    def get_active_agents(self) -> list[AgentInfo]:
        """Return snapshots of all non-dead agents."""
        return [
            agent.get_info()
            for agent in self._agents.values()
            if agent.state != AgentState.DEAD
        ]

    def get_agent_info(self, session_id: str) -> AgentInfo | None:
        """Return a snapshot for a single agent, or ``None`` if unknown."""
        agent = self._agents.get(session_id)
        if agent is None:
            return None
        return agent.get_info()

    def touch_agent_activity(self, session_id: str) -> bool:
        """Refresh ``last_activity`` so the idle-timeout countdown resets.

        Useful when the user is preparing the next message (typing, attaching
        files): we don't want to auto-stop the agent during that pause.

        Returns ``True`` if the agent was found and updated.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        if agent.state not in (AgentState.USER_TURN, AgentState.ASSISTANT_TURN):
            return False
        agent.last_activity = time.time()
        logger.debug(
            "Touched last_activity for session %s (state=%s)",
            session_id, agent.state.value,
        )
        return True

    async def kill_agent(self, session_id: str, reason: str = "manual") -> bool:
        """Stop one agent. Returns ``True`` if a kill was actually issued."""
        async with self._lock:
            agent = self._agents.get(session_id)
            if agent is None:
                logger.debug("kill_agent: session %s not found", session_id)
                return False
            if agent.state == AgentState.DEAD:
                logger.debug("kill_agent: session %s already dead", session_id)
                return False
            logger.info(
                "Stopping agent for session %s (reason: %s)", session_id, reason,
            )
            await agent.interrupt_or_kill(reason=reason)
            return True

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stop all agents and cancel monitors. Best-effort, time-bounded.

        After this call returns, the manager is reset to a state equivalent
        to a freshly constructed one (modulo provider-specific extras): the
        timeout monitor and stop event are cleared, and the agent registry
        is emptied. A subsequent ``_register_and_start`` would lazily restart
        the timeout monitor.
        """
        if self._stop_event is not None:
            self._stop_event.set()

        if self._timeout_monitor_task is not None:
            self._timeout_monitor_task.cancel()
            try:
                await self._timeout_monitor_task
            except asyncio.CancelledError:
                pass
            self._timeout_monitor_task = None

        await self._stop_extra_monitors()
        await self._pre_shutdown_extra()

        async with self._lock:
            if self._agents:
                logger.info("Shutting down %d active agent(s)", len(self._agents))

                shutdown_tasks = [
                    asyncio.create_task(
                        agent.interrupt_or_kill(reason="shutdown"),
                        name=f"shutdown-{session_id}",
                    )
                    for session_id, agent in self._agents.items()
                ]
                if shutdown_tasks:
                    _, pending = await asyncio.wait(shutdown_tasks, timeout=timeout)
                    for task in pending:
                        task.cancel()

                self._agents.clear()
                logger.info("All agents shut down")

        # Clear the stop event last so a future `_ensure_timeout_monitor_running`
        # creates a fresh one tied to the new lifecycle.
        self._stop_event = None

    # ------------------------------------------------------------------
    # Lifecycle helpers (called by subclasses)
    # ------------------------------------------------------------------

    async def _register_and_start(
        self,
        agent: BaseAgent,
        text: str,
        resume: bool,
        **start_kwargs: Any,
    ) -> None:
        """Register an agent, persist its ``ProcessRun``, broadcast STARTING, start.

        Must be called while holding ``self._lock``. Failures inside
        ``agent.start`` should be reported via the DEAD state, not raised.
        """
        from django.utils import timezone as dj_timezone

        from twicc.core.models import ProcessRun as ProcessRunModel, Session

        session_id = agent.session_id
        self._agents[session_id] = agent

        now = dj_timezone.now()

        # session_id is a plain CharField on ProcessRun, so this works even
        # when no Session row exists yet (the watcher creates the Session
        # when the JSONL file appears).
        process_run = await asyncio.to_thread(
            lambda: ProcessRunModel.objects.create(
                session_id=session_id,
                started_at=now,
            )
        )
        agent.process_run = process_run

        await asyncio.to_thread(
            lambda: Session.objects.filter(id=session_id).update(
                last_started_at=now, last_updated_at=now,
            )
        )
        await self._broadcast_session_updated(session_id)

        # Initial STARTING broadcast (state was set to STARTING in __init__).
        await self._on_state_change(agent)

        await agent.start(text, self._on_state_change, resume=resume, **start_kwargs)

        self._ensure_timeout_monitor_running()

    # ------------------------------------------------------------------
    # State-change skeleton
    # ------------------------------------------------------------------

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Default skeleton: broadcast → DB lifecycle on DEAD → registry cleanup.

        Subclasses typically override this to insert provider-specific work
        before/after broadcast (titles, settings hot-reload, ...) while still
        delegating the generic bits to the helpers below.
        """
        info = agent.get_info()
        await self._broadcast_info(info)
        if info.state == AgentState.DEAD:
            await self._update_session_stopped_at(agent)
            self._cleanup_dead(agent)

    async def _broadcast_info(self, info: AgentInfo) -> None:
        """Push an ``AgentInfo`` snapshot through the broadcast callback."""
        if self._broadcast_callback is None:
            return
        try:
            await self._broadcast_callback(info)
        except Exception as e:
            logger.error("Error broadcasting state change: %s", e)

    async def _update_session_stopped_at(self, agent: BaseAgent) -> None:
        """Update ``Session.last_stopped_at`` and broadcast ``session_updated``."""
        from django.utils import timezone as dj_timezone

        from twicc.core.models import Session

        try:
            now = dj_timezone.now()
            await asyncio.to_thread(
                lambda: Session.objects.filter(id=agent.session_id).update(
                    last_stopped_at=now, last_updated_at=now,
                )
            )
            await self._broadcast_session_updated(agent.session_id)
        except Exception as e:
            logger.error(
                "Error updating last_stopped_at for session %s: %s",
                agent.session_id, e,
            )

    def _cleanup_dead(self, agent: BaseAgent) -> None:
        """Remove a dead agent from the registry (identity-checked)."""
        if (
            agent.session_id in self._agents
            and self._agents[agent.session_id] is agent
        ):
            logger.debug(
                "Cleaning up dead agent for session %s", agent.session_id,
            )
            del self._agents[agent.session_id]

    async def _broadcast_session_updated(self, session_id: str) -> None:
        """Push a ``session_updated`` message via WebSocket."""
        from channels.layers import get_channel_layer

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        try:
            session = await asyncio.to_thread(Session.objects.get, id=session_id)
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )
        except Exception as e:
            logger.error(
                "Error broadcasting session_updated for %s: %s", session_id, e,
            )

    # ------------------------------------------------------------------
    # Timeout monitor
    # ------------------------------------------------------------------

    def _ensure_timeout_monitor_running(self) -> None:
        """Start the timeout monitor (and provider-extra monitors) if idle."""
        if self._timeout_monitor_task is not None and not self._timeout_monitor_task.done():
            return
        self._stop_event = asyncio.Event()
        self._timeout_monitor_task = asyncio.create_task(
            self._run_timeout_monitor(),
            name="agent-timeout-monitor",
        )
        logger.debug("Started agent timeout monitor")

        self._start_extra_monitors()

    async def _run_timeout_monitor(self) -> None:
        """Periodically check every active agent against its timeout policy."""
        logger.info("Agent timeout monitor started")
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                killed = await self.check_and_stop_timed_out_agents()
                if killed:
                    logger.info(
                        "Auto-stopped %d timed out agent(s): %s",
                        len(killed), ", ".join(killed),
                    )
            except Exception as e:
                logger.error(
                    "Error in agent timeout monitor: %s", e, exc_info=True,
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.TIMEOUT_MONITOR_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("Agent timeout monitor stopped")

    async def check_and_stop_timed_out_agents(self) -> list[str]:
        """Kill every agent whose ``_check_agent_timeout`` returns a decision."""
        current_time = time.time()
        killed: list[str] = []

        # Snapshot to avoid mutation during iteration (kill_agent acquires the lock).
        for session_id, agent in list(self._agents.items()):
            decision = await self._check_agent_timeout(agent, current_time)
            if decision is None:
                continue
            reason, elapsed, timeout = decision

            logger.info(
                "Auto-stopping agent %s: state=%s, elapsed=%.1fs, timeout=%ds",
                session_id, agent.state.value, elapsed, timeout,
            )
            if await self.kill_agent(session_id, reason=reason):
                killed.append(session_id)

        return killed

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    async def _create_agent(self, *args: Any, **kwargs: Any) -> BaseAgent:
        """Factory hook: build a provider-specific agent instance."""
        raise NotImplementedError

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Decide whether ``agent`` has exceeded a state-specific timeout.

        Returns ``(reason, elapsed_seconds, timeout_seconds)`` if the agent
        should be killed, ``None`` otherwise. The default returns ``None`` —
        no timeouts are enforced unless the subclass opts in.
        """
        return None

    def _start_extra_monitors(self) -> None:
        """Override to launch extra background monitors."""
        return None

    async def _stop_extra_monitors(self) -> None:
        """Override to cancel extra background monitors."""
        return None

    async def _pre_shutdown_extra(self) -> None:
        """Override to perform cleanup before agents are interrupted."""
        return None
