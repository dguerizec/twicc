"""
Base class for a running agent instance.

A ``BaseAgent`` represents one live instance of a coding-agent runtime
(Claude Code SDK, Codex, ...) for a single TwiCC session. Subclasses provide
the runtime-specific lifecycle (``start``/``send``/``interrupt_or_kill``); the
base owns the state machine, lifecycle timestamps, and process introspection
shared by every provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

from .states import AgentInfo, AgentState, get_process_memory

logger = logging.getLogger(__name__)

# Async callback invoked when the agent transitions between states.
StateChangeCallback = Callable[["BaseAgent"], Coroutine[Any, Any, None]]


class BaseAgent:
    """Skeleton for a single agent instance.

    Subclasses must implement ``start``, ``send`` and ``interrupt_or_kill``.
    They typically also override ``get_info`` to add provider-specific fields
    on top of the base snapshot (via ``AgentInfo._replace``).

    Subclasses must also set the ``provider`` class attribute to the
    provider key registered in ``AgentManagerRegistry.PROVIDER_MANAGERS``.
    """

    # Provider key (e.g. ``"claude_code"``). Subclasses must override.
    provider: ClassVar[str]

    def __init__(self, session_id: str, project_id: str, cwd: str) -> None:
        # Fail fast if the subclass forgot to set its provider key. Without
        # this guard, the missing attribute would only surface deep inside
        # the first ``get_info()`` call as an opaque ``AttributeError``.
        if not getattr(type(self), "provider", None):
            raise TypeError(
                f"{type(self).__name__}.provider must be set to a non-empty "
                "string class attribute (e.g. 'claude_code')."
            )

        self.session_id = session_id
        self.project_id = project_id
        self.cwd = cwd

        self.state: AgentState = AgentState.STARTING
        self.previous_state: AgentState | None = None
        self.started_at = time.time()
        self.state_changed_at = self.started_at
        self.last_activity = self.started_at
        self.error: str | None = None
        self.kill_reason: str | None = None

        self._dead_event = asyncio.Event()
        self._state_change_callback: StateChangeCallback | None = None

        # ProcessRun model row, populated by the manager once the agent is registered.
        self.process_run: Any = None

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_state(self, new_state: AgentState) -> None:
        """Transition into ``new_state`` and trigger the DEAD-event side-effect."""
        old_state = self.state
        self.previous_state = old_state
        self.state = new_state
        self.state_changed_at = time.time()
        if new_state == AgentState.DEAD:
            self._dead_event.set()
        logger.debug(
            "State transition for session %s: %s -> %s",
            self.session_id, old_state.value, new_state.value,
        )

    async def _notify_state_change(self) -> None:
        """Dispatch the registered state-change callback, swallowing failures."""
        if self._state_change_callback is None:
            return
        try:
            await self._state_change_callback(self)
        except Exception as e:
            logger.error(
                "Error in state change callback for session %s: %s",
                self.session_id, e, exc_info=True,
            )

    async def wait_for_dead(self, timeout: float = 30.0) -> bool:
        """Wait until the agent reaches the DEAD state.

        Returns ``True`` if it died within ``timeout``, ``False`` otherwise.
        """
        try:
            await asyncio.wait_for(self._dead_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------
    # Process introspection
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        """Return the underlying subprocess PID if applicable.

        Subclasses backed by a subprocess override this. The default returns
        ``None`` so providers that don't run a subprocess (in-process SDKs)
        can leave it untouched.
        """
        return None

    def get_memory_rss(self) -> int | None:
        """Return RSS memory of the underlying subprocess if known."""
        try:
            pid = self.get_pid()
            if pid is None:
                return None
            return get_process_memory(pid)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_info(self) -> AgentInfo:
        """Build an immutable snapshot of the current agent state.

        Subclasses can override this to populate ``pending_requests``,
        ``active_tools`` and ``last_started_tool_id`` by calling ``super()``
        and ``_replace``-ing the result.
        """
        # The subprocess no longer exists past DEAD — skip the memory lookup.
        memory_rss = None if self.state == AgentState.DEAD else self.get_memory_rss()
        return AgentInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            provider=self.provider,
            state=self.state,
            previous_state=self.previous_state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
        )

    # ------------------------------------------------------------------
    # Lifecycle (abstract)
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        **kwargs: Any,
    ) -> None:
        """Start the agent and process the first message.

        Implementations must:

        - Register ``on_state_change`` so ``_notify_state_change`` dispatches to it.
        - Drive the state machine through STARTING → ASSISTANT_TURN → USER_TURN
          (or → DEAD on failure).
        - Never raise: errors are reported via the DEAD state and ``error``.
        """
        raise NotImplementedError

    async def send(self, text: str, **kwargs: Any) -> None:
        """Send a follow-up message to the running agent."""
        raise NotImplementedError

    async def interrupt_or_kill(self, reason: str) -> None:
        """Interrupt the agent gracefully, falling back to a hard kill."""
        raise NotImplementedError
