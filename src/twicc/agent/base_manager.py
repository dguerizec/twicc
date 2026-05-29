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
import os
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async

from twicc.providers.db_writer import run_under_db_write_lock

from .base_agent import BaseAgent
from .states import AgentInfo, AgentState

if TYPE_CHECKING:
    from twicc.core.enums import Provider
    from twicc.providers.helpers import AgentSettings

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
        # Background retry tasks for pending title flushes (one per session).
        # Each task converges the provider's own store (Claude Code JSONL,
        # Codex thread name) toward the user-set title after a transient
        # failure on the first ASSISTANT_TURN attempt. See
        # :meth:`_flush_pending_title`.
        self._pending_title_retry_tasks: dict[str, asyncio.Task[None]] = {}

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

    async def stop_subagent(self, session_id: str, subagent_id: str) -> bool:
        """Stop a running subagent (Task) within ``session_id``.

        Subagents are a provider-specific concept (e.g. Claude Code's
        Task tool spawns a subagent within a parent session). The base
        implementation is a no-op returning ``False`` so generic
        consumers (e.g. the WS handler) can call this without
        provider-specific dispatch. Providers that support subagents
        override.
        """
        return False

    async def resolve_pending_request(
        self,
        session_id: str,
        request_id: str,
        response: Any,
    ) -> bool:
        """Resolve a specific pending request on an agent.

        Routes the user's response to the correct agent (by ``session_id``)
        and the correct in-flight Future on that agent (by ``request_id``).
        ``response`` is provider-specific; the caller (typically the WS
        handler) is responsible for shaping it correctly for the SDK that
        will receive it.

        Returns ``True`` if the request was resolved, ``False`` if no agent
        or no matching pending request was found.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return agent.resolve_pending_request(request_id, response)

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

        # Drop any background pending-title retries — fire-and-forget cancel,
        # we don't await them. The DB row already holds the user's title,
        # so the worst case is a missing custom-title entry in the provider's
        # own store (the watcher's next resync may or may not recover it,
        # but the user-visible title is preserved).
        self._cancel_all_pending_title_retries()

        await self._stop_extra_monitors()
        await self._pre_shutdown_extra()

        async with self._lock:
            if self._agents:
                logger.info("Shutting down %d active agent(s)", len(self._agents))

                agents_snapshot = list(self._agents.values())
                shutdown_tasks = [
                    asyncio.create_task(
                        agent.interrupt_or_kill(reason="shutdown"),
                        name=f"shutdown-{agent.session_id}",
                    )
                    for agent in agents_snapshot
                ]
                if shutdown_tasks:
                    _, pending = await asyncio.wait(shutdown_tasks, timeout=timeout)
                    for task in pending:
                        task.cancel()

                # Belt-and-suspenders: an ``interrupt_or_kill`` override is
                # free to return as soon as the agent has reached the DEAD
                # state, but the DEAD state-change callback may still be in
                # flight afterwards — and that callback is where the
                # lifecycle DB writes happen, under ``run_under_db_write_lock``.
                # ``wait_for_dead`` blocks on both ``_dead_event`` and
                # ``_dead_callback_done_event``, so this gather guarantees
                # every callback's lock-protected writes have committed
                # before we let the caller proceed to ``stop_db_writer()``.
                wait_results = await asyncio.gather(
                    *[agent.wait_for_dead(timeout=timeout) for agent in agents_snapshot],
                    return_exceptions=True,
                )
                # Surface anything that timed out or raised — the caller
                # (``run_server``) is about to call ``stop_db_writer()``,
                # and a False/exception here means a DEAD callback's
                # lock-protected writes may not have committed. We can't
                # block forever (the user pressed Ctrl-C), but a log line
                # is what makes the silent drop debuggable after the fact.
                for agent, result in zip(agents_snapshot, wait_results):
                    if isinstance(result, BaseException):
                        logger.error(
                            "Waiting for DEAD callback of session %s raised during shutdown: %s",
                            agent.session_id, result, exc_info=result,
                        )
                    elif result is False:
                        logger.warning(
                            "DEAD callback for session %s did not finish within %.1fs "
                            "during shutdown — its lifecycle DB writes may have been dropped",
                            agent.session_id, timeout,
                        )

                self._agents.clear()
                logger.info("All agents shut down")

        # Clear the stop event last so a future `_ensure_timeout_monitor_running`
        # creates a fresh one tied to the new lifecycle.
        self._stop_event = None

    # ------------------------------------------------------------------
    # Lifecycle helpers (called by subclasses)
    # ------------------------------------------------------------------

    async def _start_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        *,
        settings: AgentSettings,
        **start_kwargs: Any,
    ) -> str:
        """Build a provider agent, bind it to its canonical id, register and start.

        Common entry point for both new sessions (``resume=False``, the
        ``session_id`` argument is the frontend-side draft) and resumes
        (``resume=True``, ``session_id`` is the canonical id already known
        to the frontend).

        For brand-new sessions, the provider's ``_create_agent`` is expected
        to return an agent whose ``session_id`` is the canonical id. If the
        provider accepts a client-supplied id (Claude Code) the two are
        equal; if it mints its own (Codex) they differ — in either case,
        ``notify_session_bound`` tells the frontend which canonical id was
        bound to its local draft so it can reconcile its state.

        Provider-specific factory kwargs go through ``settings`` (universal)
        and ``_create_agent`` overrides. Provider-specific start kwargs
        (e.g. ``images``/``documents`` for Claude Code) are forwarded through
        ``start_kwargs`` to ``_register_and_start`` and ultimately to
        ``agent.start``.

        Must be called while holding ``self._lock``.
        """
        label = "session" if resume else "draft session"
        logger.debug(
            "Creating agent for %s %s, project %s",
            label, session_id, project_id,
        )
        agent = await self._create_agent(
            session_id, project_id, cwd, resume=resume, settings=settings,
        )

        # Once ``_create_agent`` returns, the agent owns external resources
        # (SDK client / subprocess). If any of the post-creation steps below
        # raise — pending re-keying, WS broadcast, DB writes in
        # ``_register_and_start``, ``agent.start`` itself — nothing else will
        # release them: the agent is at most in ``_agents`` but its state is
        # still ``STARTING``, so the DEAD-driven ``_cleanup_dead`` path never
        # fires. Tear it down explicitly via the provider's own
        # ``interrupt_or_kill`` (which is required to be safe on a not-yet-
        # started agent — see the docstring of ``_create_agent``).
        try:
            # Brand-new sessions: tell the frontend which canonical id is bound
            # to its local draft, so it can reconcile (redirect or discard).
            # On resume the frontend already knows the canonical id — skip it.
            if not resume:
                # When the provider mints its own canonical id (Codex), the WS
                # handler stored the pending agent settings under the draft id we
                # received. Re-key them under the canonical id so the watcher
                # pops them when it creates the Session row from the JSONL —
                # otherwise selected_model / effort / ... stay NULL until the
                # next user-initiated settings update. No-op when ids match
                # (Claude Code): the existing pending entry is already under the
                # canonical key.
                if session_id != agent.session_id:
                    from twicc.pending_agent_settings import (
                        pop_pending_agent_settings,
                        set_pending_agent_settings,
                    )

                    pending = pop_pending_agent_settings(session_id)
                    if pending is not None:
                        set_pending_agent_settings(agent.session_id, pending)

                    # Same rationale for pending_titles: the WS handler stored it under
                    # the draft id we received; re-key under the canonical id so the
                    # Codex manager's ASSISTANT_TURN flush actually finds it. No-op for
                    # Claude Code where draft id == canonical id.
                    from twicc.pending_titles import (
                        pop_pending_title,
                        set_pending_title,
                    )

                    pending_title = pop_pending_title(session_id)
                    if pending_title is not None:
                        set_pending_title(agent.session_id, pending_title)

                await self.notify_session_bound(
                    draft_session_id=session_id,
                    session_id=agent.session_id,
                )

            await self._register_and_start(agent, text, resume=resume, **start_kwargs)
            return agent.session_id
        except Exception:
            try:
                await agent.interrupt_or_kill(reason="startup-failed")
            except Exception:
                logger.exception(
                    "Cleanup interrupt_or_kill failed for session %s after start-up error",
                    agent.session_id,
                )
            self._agents.pop(agent.session_id, None)
            raise

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

        By the time this method runs, ``agent.session_id`` is the canonical
        provider-side id (the draft → canonical resolution, if any, happens
        earlier in ``_start_agent``). Everything below operates on that id.
        """
        from django.utils import timezone

        from twicc.core.models import ProcessRun, Session

        session_id = agent.session_id
        self._agents[session_id] = agent

        now = timezone.now()

        # Persist both DB writes (ProcessRun create + Session start-timestamps
        # update) under a single DB write lock acquire. The broadcasts that
        # follow stay outside the lock — they target the in-process Channels
        # layer and have no DB dependency. ``session_id`` is a plain
        # CharField on ProcessRun, so the create works even when no Session
        # row exists yet (the watcher creates the Session when the JSONL
        # file appears). The row is created with the model's default
        # ``state=STARTING`` and ``last_state_change_at=now``;
        # :meth:`_on_state_change` keeps both columns in sync from there.
        # ``twicc_pid`` always carries our own PID (stable for the lifetime
        # of this TwiCC process); ``agent_pid`` is whatever the provider can
        # surface right now via :meth:`BaseAgent.get_pid` — typically
        # ``None`` because ``agent.start()`` (the call that actually spawns
        # the subprocess) runs after this block.
        twicc_pid = os.getpid()
        agent_pid = agent.get_pid()

        async def _persist_run_and_start_timestamps() -> None:
            pr = await asyncio.to_thread(
                lambda: ProcessRun.objects.create(
                    provider=agent.provider.value,
                    session_id=session_id,
                    started_at=now,
                    last_state_change_at=now,
                    twicc_pid=twicc_pid,
                    agent_pid=agent_pid,
                )
            )
            agent.process_run = pr
            await asyncio.to_thread(
                lambda: Session.objects.filter(id=session_id).update(
                    last_started_at=now, last_updated_at=now,
                )
            )

        await run_under_db_write_lock(_persist_run_and_start_timestamps)
        await self._broadcast_session_updated(session_id)

        # Initial STARTING broadcast (state was set to STARTING in __init__).
        await self._on_state_change(agent)

        await agent.start(text, self._on_state_change, resume=resume, **start_kwargs)

        self._ensure_timeout_monitor_running()

    # ------------------------------------------------------------------
    # State-change skeleton
    # ------------------------------------------------------------------

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Default skeleton: persist ProcessRun state → broadcast → DB lifecycle → registry cleanup.

        Subclasses typically override this to insert provider-specific work
        before/after broadcast (titles, settings hot-reload, ...) while still
        delegating the generic bits to the helpers below.

        The ProcessRun transition is persisted **before** the broadcast: clients
        that consult the DB right after receiving a state-change message
        observe a row already in the new state. On ``DEAD``, the row is
        either UPDATEd (when the provider helper says to keep it — Claude
        Code with crons attached) or DELETEd (default for every other case);
        ``agent.process_run`` is cleared on delete so override post-DEAD
        logic can detect "row was kept" via ``agent.process_run is not None``.

        On ``ASSISTANT_TURN``, :meth:`_flush_pending_title` runs to push any
        title the CLI / WS stored for a draft session into the DB and the
        provider's own store. Provider-agnostic — both Claude Code and Codex
        share the same draft-title bridge (:mod:`twicc.pending_titles`).
        """
        info = agent.get_info()
        await self._persist_process_run_transition(agent, info.state)
        await self._broadcast_info(info)
        if info.state == AgentState.DEAD:
            # Cancel the background pending-title retry, if any: once the
            # agent is gone there's nothing left to converge on the provider
            # side; ``Session.title`` already holds the value.
            self._cancel_pending_title_retry(agent.session_id)
            await self._update_session_stopped_at(agent)
            self._cleanup_dead(agent)
        elif info.state == AgentState.ASSISTANT_TURN:
            await self._flush_pending_title(agent)

    async def _flush_pending_title(self, agent: BaseAgent) -> None:
        """Persist any pending session title once the agent reaches ASSISTANT_TURN.

        Draft sessions (CLI ``--title`` or WS ``send_message`` with a custom
        title) stash the chosen title in :mod:`twicc.pending_titles` because
        the provider's backing store does not exist yet at draft time. The
        first ``ASSISTANT_TURN`` is the contractual moment to drain it: the
        agent has signalled first activity, the WS clients have already seen
        ``serialize_session`` resolve the pending title for the frontend, and
        we now want the title to live in two stores at rest.

        Two-step flush:

        1. ``Session.title`` in the DB — what
           :func:`twicc.core.serializers.serialize_session` falls back to once
           the pending entry is gone, and what ``twicc session <id>`` reads
           on the CLI. The mirror lands first because the user-facing
           consistency only depends on this row; if it fails we bail out
           (next ``ASSISTANT_TURN`` retries — pending entry is left in place).
        2. The provider's own store via
           :meth:`BaseProviderHelpers.rename_session` — for Claude Code, a
           ``custom-title`` JSONL entry plus :func:`protect_title` against
           CLI re-appends; for Codex, a ``thread/name/set`` call to the app
           server. **This step can fail transiently**: the Claude Code SDK
           may not have written the JSONL yet at the moment we hit
           ``ASSISTANT_TURN`` (the SDK signals the state transition on
           connection-ready, before the file is created), or the Codex app
           server may be momentarily unreachable.

        When step 2 fails on the inline attempt, we schedule a background
        task (:meth:`_retry_provider_rename_session`) that keeps retrying
        with exponential backoff until success or cancellation (DEAD /
        shutdown). The pending entry is popped only when one of the
        provider attempts (inline or background) succeeds, so the
        write is converged-eventually instead of dropped on transient
        failure. ``protect_title`` is also kept honest:
        :meth:`ClaudeCodeHelpers.rename_session` registers it in a
        ``finally``, so every retry call leaves the in-memory protection
        in place even when the JSONL append still raises.

        Idempotency: if a background retry is already in flight for this
        session, the method is a no-op (the running loop owns the pop).
        """
        from twicc.core.models import Session
        from twicc.pending_titles import get_pending_title, pop_pending_title
        from twicc.providers.helpers import get_provider_helpers

        pending = get_pending_title(agent.session_id)
        if not pending:
            return

        session_id = agent.session_id

        # A background loop is already converging the provider store —
        # don't fire a parallel attempt.
        if session_id in self._pending_title_retry_tasks:
            return

        try:
            async def _persist_session_title() -> None:
                await sync_to_async(
                    Session.objects.filter(id=session_id).update
                )(title=pending)
            await run_under_db_write_lock(_persist_session_title)
        except Exception as e:
            logger.error(
                "Pending title flush — DB update failed for session %s: %s",
                session_id, e,
            )
            return

        helpers = get_provider_helpers(agent.provider)
        try:
            await helpers.rename_session(session_id, pending)
        except Exception as e:
            logger.warning(
                "Pending title flush — provider rename_session failed for session %s: %s "
                "(scheduling background retry; DB title is already up-to-date)",
                session_id, e,
            )
            task = asyncio.create_task(
                self._retry_provider_rename_session(agent.provider, session_id, pending),
                name=f"pending-title-retry-{session_id}",
            )
            self._pending_title_retry_tasks[session_id] = task
            return

        pop_pending_title(session_id)

    async def _retry_provider_rename_session(
        self, provider: "Provider", session_id: str, pending: str,
    ) -> None:
        """Background retry of :meth:`BaseProviderHelpers.rename_session` with backoff.

        Sleeps ``backoff`` seconds, attempts the provider's rename, repeats
        on failure with ``backoff = min(backoff * 2, 30)``. Pops the pending
        entry on success and exits. On cancellation (DEAD / shutdown), exits
        without popping — by then the agent is gone and there is nothing left
        to converge to; ``Session.title`` (written upstream by
        :meth:`_flush_pending_title`) remains the source of truth.

        Retry attempts are logged at DEBUG to avoid spamming a long-running
        loop; success is logged at INFO so it shows up in normal logs.

        Self-clean-up: removes itself from ``_pending_title_retry_tasks`` on
        every exit path via a ``finally``. The :meth:`_cancel_pending_title_retry`
        helper also pops eagerly so the dict never references a cancelled task.
        """
        from twicc.pending_titles import pop_pending_title
        from twicc.providers.helpers import get_provider_helpers

        backoff = 1.0
        max_backoff = 30.0
        # The inline attempt in _flush_pending_title was attempt #1.
        attempt = 1

        try:
            while True:
                await asyncio.sleep(backoff)
                attempt += 1
                try:
                    helpers = get_provider_helpers(provider)
                    await helpers.rename_session(session_id, pending)
                    pop_pending_title(session_id)
                    logger.info(
                        "Pending title flush retry succeeded for session %s on attempt %d",
                        session_id, attempt,
                    )
                    return
                except Exception as e:
                    logger.debug(
                        "Pending title retry attempt %d failed for session %s: %s",
                        attempt, session_id, e,
                    )
                backoff = min(backoff * 2, max_backoff)
        finally:
            self._pending_title_retry_tasks.pop(session_id, None)

    def _cancel_pending_title_retry(self, session_id: str) -> None:
        """Cancel a single session's background pending-title retry, if any."""
        task = self._pending_title_retry_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_pending_title_retries(self) -> None:
        """Cancel every running pending-title retry. Used on manager shutdown."""
        if not self._pending_title_retry_tasks:
            return
        logger.info(
            "Cancelling %d pending-title retry task(s)",
            len(self._pending_title_retry_tasks),
        )
        for session_id in list(self._pending_title_retry_tasks):
            self._cancel_pending_title_retry(session_id)

    async def _broadcast_info(self, info: AgentInfo) -> None:
        """Push an ``AgentInfo`` snapshot through the broadcast callback."""
        if self._broadcast_callback is None:
            return
        try:
            await self._broadcast_callback(info)
        except Exception as e:
            logger.error("Error broadcasting state change: %s", e)

    async def _persist_process_run_transition(
        self, agent: BaseAgent, state: AgentState,
    ) -> None:
        """Mirror a runtime state transition onto the agent's ProcessRun row.

        For non-``DEAD`` transitions: UPDATE ``state``,
        ``last_state_change_at`` and ``agent_pid``. For ``DEAD``: consult
        the provider helper — if it returns ``True``, UPDATE the row to
        ``state=DEAD`` (same triplet); otherwise DELETE the row (and clear
        ``agent.process_run`` so override-level post-DEAD logic can branch
        on whether the row was kept).

        ``agent_pid`` is refreshed on every UPDATE because
        :meth:`BaseAgent.get_pid` returns ``None`` until the provider has
        actually spawned its subprocess — which only happens inside
        ``agent.start()``, after ``ProcessRun.objects.create`` has already
        run. The first transition out of ``STARTING`` is therefore the
        earliest moment we can persist a usable value. On ``DEAD`` the
        subprocess is gone again, so ``get_pid()`` flips back to ``None``
        and the column reflects that.

        ``awaiting_user_input`` mirrors ``bool(agent.pending_requests)``,
        forced to ``False`` on ``DEAD`` (a dead agent is not awaiting
        anything, even if its pending-requests dict still has entries that
        will be drained in the cancellation cleanup). The column is the
        persistable view of "blocked on user click", since the runtime
        ``state`` stays in ``ASSISTANT_TURN`` while the SDK's
        ``can_use_tool`` callback blocks. This persist site fires both on
        explicit state transitions and on pending-request add/remove,
        because :meth:`BaseAgent._await_pending_request` invokes
        ``_notify_state_change`` at both moments.

        The helper read + write are grouped under a single
        ``run_under_db_write_lock`` acquire so no other writer can race
        between the keep/delete decision and the DB mutation (mirrors the
        pattern previously used in Claude Code's ``_on_state_change``).
        Every log emission goes through :func:`provider_log_context` so
        records carry the agent's provider tag — agent message loops do
        not set the context themselves, so wrapping it here is the
        canonical attribution point. No-op when the agent has no
        process_run attached (early failure before ``_register_and_start``
        completed).
        """
        if agent.process_run is None:
            return

        from django.utils import timezone

        from twicc.core.models import ProcessRun
        from twicc.logging_context import provider_log_context
        from twicc.providers.helpers import get_provider_helpers

        now = timezone.now()
        pr_pk = agent.process_run.pk
        state_value = state.value
        agent_pid = agent.get_pid()
        # ``awaiting_user_input`` is the persistable counterpart of
        # ``agent.pending_requests``. Force ``False`` on DEAD: the pending
        # requests dict may still hold entries at the moment the DEAD
        # callback runs (cleanup happens in the ``finally`` of
        # ``_await_pending_request`` AFTER the future is cancelled), but a
        # dead agent is not actually awaiting anything — the column reflects
        # the operational truth.
        awaiting = False if state == AgentState.DEAD else bool(agent.pending_requests)

        with provider_log_context(agent.provider):
            if state != AgentState.DEAD:
                async def _persist_update() -> None:
                    await asyncio.to_thread(
                        lambda: ProcessRun.objects.filter(pk=pr_pk).update(
                            state=state_value,
                            last_state_change_at=now,
                            agent_pid=agent_pid,
                            awaiting_user_input=awaiting,
                        )
                    )
                    if agent.process_run is not None:
                        agent.process_run.state = state_value
                        agent.process_run.last_state_change_at = now
                        agent.process_run.agent_pid = agent_pid
                        agent.process_run.awaiting_user_input = awaiting

                try:
                    await run_under_db_write_lock(_persist_update)
                except Exception as e:
                    logger.error(
                        "Error persisting state %s on process run %s for session %s: %s",
                        state_value, pr_pk, agent.session_id, e,
                    )
                return

            # DEAD: helper decides keep vs delete. Helper is sync (typically
            # a DB read); wrapped in ``to_thread`` so the event loop stays free.
            helper = get_provider_helpers(agent.provider)

            async def _settle_dead() -> None:
                keep = await asyncio.to_thread(
                    lambda: helper.should_keep_dead_process_run(
                        agent.process_run, agent=agent,
                    )
                )
                if keep:
                    await asyncio.to_thread(
                        lambda: ProcessRun.objects.filter(pk=pr_pk).update(
                            state=state_value,
                            last_state_change_at=now,
                            agent_pid=agent_pid,
                            awaiting_user_input=awaiting,
                        )
                    )
                    if agent.process_run is not None:
                        agent.process_run.state = state_value
                        agent.process_run.last_state_change_at = now
                        agent.process_run.agent_pid = agent_pid
                        agent.process_run.awaiting_user_input = awaiting
                else:
                    await asyncio.to_thread(lambda: agent.process_run.delete())
                    agent.process_run = None
                    logger.info(
                        "Deleted process run %s for session %s on death",
                        pr_pk, agent.session_id,
                    )

            try:
                await run_under_db_write_lock(_settle_dead)
            except Exception as e:
                logger.error(
                    "Error settling DEAD process run %s for session %s: %s",
                    pr_pk, agent.session_id, e,
                )

    async def _update_session_stopped_at(self, agent: BaseAgent) -> None:
        """Update ``Session.last_stopped_at`` and broadcast ``session_updated``."""
        from django.utils import timezone

        from twicc.core.models import Session

        try:
            now = timezone.now()

            async def _persist_stopped() -> None:
                await asyncio.to_thread(
                    lambda: Session.objects.filter(id=agent.session_id).update(
                        last_stopped_at=now, last_updated_at=now,
                    )
                )

            await run_under_db_write_lock(_persist_stopped)
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
        """Push a ``session_updated`` message via WebSocket.

        No-op when the ``Session`` row does not exist yet. That's the
        expected state for brand-new sessions between ``_register_and_start``
        and the moment the watcher inserts the row from the first JSONL
        line — the watcher then broadcasts its own ``session_updated`` so
        nothing is lost. Resume / DEAD call sites observe an existing
        row and broadcast normally.
        """
        from channels.layers import get_channel_layer

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        try:
            session = await asyncio.to_thread(
                lambda: Session.objects.filter(id=session_id).first()
            )
            if session is None:
                return
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

    async def notify_session_bound(
        self, draft_session_id: str, session_id: str,
    ) -> None:
        """Broadcast the canonical session id bound to a local draft.

        The frontend mints a ``draft_session_id`` (UUID) when the user starts a
        new conversation and uses it locally — store key, URL, IndexedDB draft.
        Providers that accept a client-supplied id (Claude Code) reuse it as
        the canonical id; providers that mint their own (Codex) return a
        different id. This broadcast tells the frontend which canonical id is
        now bound to the draft so it can reconcile its local state: redirect
        ``/sessions/{draft_session_id}`` to ``/sessions/{session_id}`` if the
        user is still on the draft, or just discard the local draft otherwise.

        Called once per new session, as early as the provider can confirm the
        canonical id. Not called on resume (the frontend already knows the id).
        When ``draft_session_id == session_id`` the frontend treats it as a
        no-op (the existing ``session_updated`` path upgrades the draft in
        place).
        """
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        try:
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_bound",
                        "draft_session_id": draft_session_id,
                        "session_id": session_id,
                    },
                },
            )
        except Exception as e:
            logger.error(
                "Error broadcasting session_bound for draft=%s, session=%s: %s",
                draft_session_id, session_id, e,
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

    def _state_based_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Shared per-state timeout policy reused by every provider.

        Returns ``(reason, elapsed_seconds, timeout_seconds)`` if ``agent``
        has exceeded the timeout for its current state, ``None`` otherwise.
        Providers call this from their own ``_check_agent_timeout`` after
        applying provider-specific skips (e.g. Claude Code's active cron
        checks). The ``pending_requests`` skip is built in here so every
        provider gets it for free. Reason strings are part of the wire
        contract with the frontend — see ``useWebSocket.js``
        ``kill_reason`` toasts.

        Per-state policy:

        - ``STARTING``: ``PROCESS_TIMEOUT_STARTING`` (default 60s) — stuck startup.
        - ``USER_TURN``: ``PROCESS_TIMEOUT_USER_TURN`` (default 15min) — idle.
        - ``ASSISTANT_TURN``: ``PROCESS_TIMEOUT_ASSISTANT_TURN`` (default 2h)
          for inactivity, plus an absolute
          ``PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE`` (default 6h) safety cap.
        """
        from django.conf import settings

        # Never time out an agent waiting on a user click. The countdown
        # resumes once the pending request resolves and last_activity is
        # touched again.
        if agent.pending_requests:
            return None

        if agent.state == AgentState.STARTING:
            timeout = getattr(settings, "PROCESS_TIMEOUT_STARTING", 60)
            elapsed = current_time - agent.state_changed_at
            if elapsed > timeout:
                return ("timeout_starting", elapsed, timeout)
            return None

        if agent.state == AgentState.USER_TURN:
            timeout = getattr(settings, "PROCESS_TIMEOUT_USER_TURN", 15 * 60)
            elapsed = current_time - agent.last_activity
            if elapsed > timeout:
                return ("timeout_user_turn", elapsed, timeout)
            return None

        if agent.state == AgentState.ASSISTANT_TURN:
            inactivity_timeout = getattr(
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN", 2 * 60 * 60,
            )
            absolute_timeout = getattr(
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE", 6 * 60 * 60,
            )

            inactivity_elapsed = current_time - agent.last_activity
            absolute_elapsed = current_time - agent.state_changed_at

            # Absolute takes precedence for the reason.
            if absolute_elapsed > absolute_timeout:
                return ("timeout_assistant_turn_absolute", absolute_elapsed, absolute_timeout)
            if inactivity_elapsed > inactivity_timeout:
                return ("timeout_assistant_turn", inactivity_elapsed, inactivity_timeout)
            return None

        return None

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        **kwargs: Any,
    ) -> BaseAgent:
        """Factory hook: build a provider-specific agent instance.

        For providers that accept a client-supplied id (Claude Code), build
        the agent with ``session_id`` as-is — that becomes the canonical id.
        For providers that mint their own id (Codex), ignore ``session_id``
        when ``resume`` is ``False`` and obtain the canonical id from the
        provider (e.g. via ``thread/start``) before constructing the agent.
        When ``resume`` is ``True``, ``session_id`` is the canonical id
        already known to the frontend in every case.

        The returned agent must carry the canonical id in ``agent.session_id``.

        Cleanup invariant: the returned agent must accept
        ``interrupt_or_kill`` immediately, even before ``start`` has been
        called. ``_start_agent`` calls it as the cleanup mechanism when the
        post-creation startup sequence (re-keying, broadcast, DB writes,
        ``agent.start``) raises — the agent owns external resources by then
        and nothing else will release them. Implementations that allocate
        resources inside ``__init__`` (or between ``__init__`` and the
        return statement) must also clean them up locally if the
        construction itself raises, since ``_start_agent`` only sees the
        agent once it has been returned.
        """
        raise NotImplementedError

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Decide whether ``agent`` has exceeded a state-specific timeout.

        Returns ``(reason, elapsed_seconds, timeout_seconds)`` if the agent
        should be killed, ``None`` otherwise. The default returns ``None`` —
        no timeouts are enforced unless the subclass opts in. Most providers
        will apply their own skips first and then delegate to
        ``_state_based_timeout`` for the shared per-state policy.
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
