"""
Claude Code agent manager: tracks and manages all active Claude Code agents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from django.conf import settings

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from twicc.agent import AgentInfo, AgentState, BaseAgent, BaseAgentManager
from twicc.core.enums import Provider

from .agent import ClaudeAgent

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


def _get_session_slug_sync(session_id: str) -> str | None:
    """Retrieve the slug stored on the Session model (synchronous).

    Args:
        session_id: The session identifier to look up

    Returns:
        The slug string, or None if not set
    """
    from twicc.core.models import Session

    try:
        return Session.objects.values_list('slug', flat=True).get(id=session_id)
    except Session.DoesNotExist:
        return None


async def get_session_slug(session_id: str) -> str | None:
    """Async wrapper around _get_session_slug_sync.

    Runs the DB query in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_get_session_slug_sync, session_id)


class ClaudeAgentManager(BaseAgentManager):
    """Manages all active Claude Code agents.

    Inherits the registry, lifecycle and timeout monitor from BaseAgentManager
    and adds Claude-specific behavior: settings hot-reload, cron lifecycle,
    title flushing, subagent propagation, and a separate cron-expiry monitor
    that runs alongside the timeout monitor.

    Typical usage:
        manager = ClaudeAgentManager()

        # Send a message to an existing session
        await manager.send_to_session(session_id, project_id, cwd, "Hello")

        # Create a new session
        await manager.create_session(session_id, project_id, cwd, "Hello")

        # Get active agents
        agents = manager.get_active_agents()

        # Shutdown all
        await manager.shutdown()
    """

    # Cron expiry check cadence (seconds). Detects recurring crons auto-deleted
    # by the CLI after their 7-day window without forcing a tighter timeout loop.
    CRON_EXPIRY_MONITOR_INTERVAL = 60

    def __init__(self) -> None:
        super().__init__()
        self._cron_expiry_monitor_task: asyncio.Task[None] | None = None
        self._cron_restart_tasks: dict[str, asyncio.Task[None]] = {}  # session_id -> restart task
        # Pending content to send after a settings-triggered restart completes.
        # Stored when the user sends text + startup settings changes during USER_TURN:
        # the agent must be killed and restarted, and this content is sent after
        # cron restart (if any) or directly with the new agent.
        self._pending_after_restart: dict[str, dict] = {}  # session_id -> {text, images, documents}

    # ------------------------------------------------------------------
    # Public API (Claude-specific signatures)
    # ------------------------------------------------------------------

    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = True,
        context_max: int = 200_000,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        cancel_cron_restart: bool = True,
    ) -> None:
        """Send a message to an existing session, applying settings changes as needed.

        If no active agent exists for this session, a new one is created with
        the resume option to continue the existing conversation.

        Messages can be sent during USER_TURN (normal) or ASSISTANT_TURN
        (Claude reads them inline during work).

        Settings are classified into categories:
        - live (permission_mode): applied immediately in any state
        - idle (selected_model, context_max): applied during USER_TURN via set_model()
        - startup (effort, thinking_enabled, claude_in_chrome): require agent stop

        When startup settings change during USER_TURN, the agent is killed
        (reason="apply-settings") and restarted. During ASSISTANT_TURN, the
        changes are saved to DB and applied on the next USER_TURN transition.

        Raises:
            RuntimeError: If the agent cannot be started or message cannot be sent
        """
        from twicc.synced_settings import classify_claude_settings_changes

        async with self._lock:
            # Cancel any running cron restart task — user is taking over this session.
            # Callers that ARE the cron restart task pass cancel_cron_restart=False
            # to avoid cancelling themselves.
            if cancel_cron_restart:
                self._cancel_cron_restart_task(session_id)

            has_content = bool(text) or bool(images) or bool(documents)

            requested_settings = {
                "permission_mode": permission_mode,
                "selected_model": selected_model,
                "effort": effort,
                "thinking_enabled": thinking_enabled,
                "claude_in_chrome": claude_in_chrome,
                "context_max": context_max,
            }

            if session_id in self._agents:
                agent = self._agents[session_id]

                if agent.state == AgentState.DEAD:
                    # Dead agent, clean it up and create a new one
                    logger.debug("Removing dead agent for session %s", session_id)
                    del self._agents[session_id]

                elif agent.state == AgentState.USER_TURN:
                    changes = classify_claude_settings_changes(
                        agent.get_claude_settings(), requested_settings,
                    )
                    has_startup_changes = bool(changes["startup"])

                    if has_startup_changes:
                        # Startup settings changed → must kill and restart
                        logger.info(
                            "Startup settings changed for session %s (%s), killing agent",
                            session_id, changes["startup"],
                        )
                        has_crons = await self._session_has_crons(agent)
                        will_restart = has_content or has_crons
                        if has_content:
                            self._pending_after_restart[session_id] = {
                                "text": text, "images": images, "documents": documents,
                            }
                        # _on_state_change(DEAD) fires once DEAD is reached:
                        # - if has_crons: launches cron restart task (which will
                        #   also send pending text after success)
                        # - cleans up agent from _agents
                        await agent.interrupt_or_kill(reason="apply-settings")
                        if will_restart:
                            # Broadcast "starting" so the frontend blocks interaction
                            await self._broadcast_agent_state(
                                session_id, project_id, AgentState.STARTING,
                            )
                        # If no crons and has content → start directly
                        if not has_crons and has_content:
                            pending = self._pending_after_restart.pop(session_id, None)
                            await self._start_agent(
                                session_id, project_id, cwd, pending["text"],
                                resume=True, **requested_settings,
                                images=pending.get("images"),
                                documents=pending.get("documents"),
                            )
                        # If has_crons → cron restart task handles it
                        return
                    else:
                        # Only live/idle changes → apply on the live agent
                        await agent.apply_live_settings(
                            permission_mode, selected_model, context_max,
                        )
                        if has_content:
                            await agent.send(text, images=images, documents=documents)
                        return

                elif agent.state == AgentState.ASSISTANT_TURN:
                    # During assistant_turn: apply live (permission) immediately,
                    # send text if any. Idle/startup changes are saved to DB by
                    # the caller and will be checked on next USER_TURN transition.
                    if permission_mode != agent.permission_mode:
                        await agent.set_permission_mode(permission_mode)
                    if has_content:
                        await agent.send(text, images=images, documents=documents)
                    return

                else:
                    # Agent starting - cannot send yet
                    raise RuntimeError(
                        f"Cannot send message: agent is in state {agent.state}"
                    )

            # No live agent — text is required to start a new one
            if not text:
                raise RuntimeError(
                    "Cannot start a new agent without a message"
                )

            # Create and start new agent with resume
            await self._start_agent(
                session_id, project_id, cwd, text, resume=True,
                **requested_settings,
                images=images, documents=documents,
            )

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = True,
        context_max: int = 200_000,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create a new session with a client-provided session ID.

        Unlike send_to_session which handles existing sessions, this creates
        a brand new session. The session_id is passed to the Claude CLI via
        the --session-id flag.

        Raises:
            RuntimeError: If an agent already exists for this session_id
        """
        async with self._lock:
            if session_id in self._agents:
                agent = self._agents[session_id]
                if agent.state != AgentState.DEAD:
                    raise RuntimeError(
                        f"Session {session_id} already exists and is active"
                    )
                # Dead agent, clean it up
                logger.debug(
                    "Removing dead agent for session %s before creating new",
                    session_id,
                )
                del self._agents[session_id]

            # Create and start new agent without resume
            await self._start_agent(
                session_id, project_id, cwd, text, resume=False,
                permission_mode=permission_mode, selected_model=selected_model,
                effort=effort, thinking_enabled=thinking_enabled,
                claude_in_chrome=claude_in_chrome, context_max=context_max,
                images=images, documents=documents,
            )

    async def discard_active_tool(self, session_id: str, tool_use_id: str) -> bool:
        """Discard an active-tool entry on the matching agent. Returns True
        when an entry was actually removed. Used by the JSONL watcher to clean
        up tools that never produced a PostToolUse/PostToolUseFailure (CLI-side
        validation rejections).
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return await agent.discard_active_tool(tool_use_id)

    async def stop_agent(self, session_id: str, agent_id: str) -> bool:
        """Stop a running background agent (subagent task) by its agent ID.

        Verifies that the subagent belongs to the given session, then calls
        stop_task on the session's main agent.

        Args:
            session_id: The parent session that spawned this subagent
            agent_id: The agent ID (same as task_id for the SDK)

        Returns:
            True if stop_task was called, False if not found or not valid
        """
        from twicc.core.models import Session

        # Verify the subagent exists and belongs to the given session
        exists = await asyncio.to_thread(
            lambda: Session.objects.filter(
                agent_id=agent_id, parent_session_id=session_id
            ).exists()
        )
        if not exists:
            logger.debug(
                "stop_agent: no subagent %s found for session %s", agent_id, session_id
            )
            return False

        agent = self._agents.get(session_id)
        if not agent or agent.state == AgentState.DEAD:
            logger.debug("stop_agent: no running agent for session %s", session_id)
            return False

        try:
            await agent.stop_agent(agent_id)
            return True
        except Exception as e:
            logger.warning(
                "stop_agent: failed to stop subagent %s in session %s: %s",
                agent_id, session_id, e,
            )
            return False

    async def resolve_pending_request(
        self,
        session_id: str,
        request_id: str,
        response: PermissionResultAllow | PermissionResultDeny,
    ) -> bool:
        """Resolve a specific pending request on an agent.

        Routes the user's response to the correct agent based on session_id and
        the specific in-flight request based on request_id. Multiple permission
        asks can be concurrent within one session (parallel concurrency-safe tools),
        so request_id is required to disambiguate.

        Args:
            session_id: The session to resolve
            request_id: UUID of the pending request to resolve
            response: The permission result to send to the SDK

        Returns:
            True if resolved, False if no matching agent or no matching request
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return agent.resolve_pending_request(request_id, response)

    # ------------------------------------------------------------------
    # Factory + lifecycle helper (Claude-specific kwargs)
    # ------------------------------------------------------------------

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        permission_mode: str,
        selected_model: str | None,
        effort: str | None,
        thinking_enabled: bool | None,
        claude_in_chrome: bool,
        context_max: int,
    ) -> ClaudeAgent:
        """Build a ClaudeAgent wired to this manager's cron callbacks."""
        return ClaudeAgent(
            session_id=session_id,
            project_id=project_id,
            cwd=cwd,
            permission_mode=permission_mode,
            selected_model=selected_model,
            effort=effort,
            thinking_enabled=thinking_enabled,
            get_session_slug=get_session_slug,
            on_cron_created=self._on_cron_created,
            on_cron_deleted=self._on_cron_deleted,
            claude_in_chrome=claude_in_chrome,
            context_max=context_max,
        )

    async def _start_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = True,
        context_max: int = 200_000,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create and start a new Claude agent.

        Common implementation for both send_to_session (resume=True) and
        create_session (resume=False). Must be called while holding self._lock.
        """
        logger.debug(
            "Creating agent for session %s, project %s (resume=%s)",
            session_id, project_id, resume,
        )
        agent = await self._create_agent(
            session_id, project_id, cwd,
            permission_mode=permission_mode,
            selected_model=selected_model,
            effort=effort,
            thinking_enabled=thinking_enabled,
            claude_in_chrome=claude_in_chrome,
            context_max=context_max,
        )
        await self._register_and_start(
            agent, text, resume=resume, images=images, documents=documents,
        )

    # ------------------------------------------------------------------
    # Hooks (overrides of BaseAgentManager)
    # ------------------------------------------------------------------

    def _start_extra_monitors(self) -> None:
        """Launch the cron expiry monitor alongside the base timeout monitor."""
        if not settings.CRON_AUTO_RESTART:
            logger.debug("Cron expiry monitor skipped (TWICC_NO_CRON_RESTART is set)")
            return
        if self._cron_expiry_monitor_task is not None and not self._cron_expiry_monitor_task.done():
            return
        self._cron_expiry_monitor_task = asyncio.create_task(
            self._run_cron_expiry_monitor(),
            name="cron-expiry-monitor",
        )
        logger.debug("Started cron expiry monitor task")

    async def _stop_extra_monitors(self) -> None:
        """Cancel the cron expiry monitor."""
        if self._cron_expiry_monitor_task is not None:
            self._cron_expiry_monitor_task.cancel()
            try:
                await self._cron_expiry_monitor_task
            except asyncio.CancelledError:
                pass
            self._cron_expiry_monitor_task = None

    async def _pre_shutdown_extra(self) -> None:
        """Cancel runtime cron restart tasks before agents are interrupted.

        ProcessRuns stay in the DB so ``restart_all_session_crons`` can pick
        them up on the next TwiCC startup.
        """
        if self._cron_restart_tasks:
            logger.info("Cancelling %d cron restart task(s)", len(self._cron_restart_tasks))
            for session_id in list(self._cron_restart_tasks):
                self._cancel_cron_restart_task(session_id)

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """State-aware timeout policy with cron- and pending-request- skipping.

        Skips agents that are waiting on a user response (pending_requests) or
        that have active crons (the CLI has scheduled work that would be lost
        if we kill the agent). Otherwise applies per-state timeouts:

        - STARTING: ``PROCESS_TIMEOUT_STARTING`` (default 60s) — stuck startup.
        - USER_TURN: ``PROCESS_TIMEOUT_USER_TURN`` (default 15min) — idle.
        - ASSISTANT_TURN: ``PROCESS_TIMEOUT_ASSISTANT_TURN`` (default 2h) for
          inactivity, plus an absolute ``PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE``
          (default 6h) safety cap.
        """
        # Don't timeout agents waiting for user input.
        if agent.pending_requests:
            return None

        # Don't timeout agents with active cron jobs.
        try:
            from twicc.core.models import SessionCron
            has_crons = await asyncio.to_thread(
                lambda sid=agent.session_id: SessionCron.has_active_for_session(sid)
            )
            if has_crons:
                return None
        except Exception as e:
            logger.error(
                "Error checking active crons for session %s: %s",
                agent.session_id, e,
            )

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
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN", 2 * 60 * 60
            )
            absolute_timeout = getattr(
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE", 6 * 60 * 60
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

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Handle state changes: titles, settings hot-reload, cron lifecycle.

        Concurrency model:
        This callback is invoked in two contexts:
        1. Synchronously from send_message() when start()/send() fails - the lock is
           already held by send_message(), so we must not try to acquire it again.
        2. Asynchronously from the message loop background task when Claude finishes
           or an error occurs - the lock is NOT held.

        For dead agent cleanup, we do NOT acquire the lock because:
        - In context 1, we'd deadlock (the lock is already held)
        - In context 2, the cleanup is effectively atomic in asyncio (no await between
          the check and delete operations, so no preemption)
        - The identity check (is agent) ensures we only delete the exact instance
        """
        # Snapshot the state at entry. This is critical because _apply_pending_settings
        # may kill() the agent (changing agent.state to DEAD) while we're handling
        # a USER_TURN callback. Using the snapshot ensures each callback invocation only
        # runs the logic for the state it was called with.
        info = agent.get_info()
        state = info.state

        logger.debug(
            "State change callback triggered for session %s: state=%s",
            info.session_id, state.value,
        )

        # First USER_TURN: purge old ProcessRuns for this session (cascade deletes their crons).
        # Done BEFORE broadcasting so that _enrich_with_active_crons sees the correct cron count
        # (without this, crons from the old run + new run would both appear in the broadcast).
        # Uses _old_runs_purged flag because _first_user_turn_reached is already True at this point
        # (set in _run_message_loop before _notify_state_change is called).
        # Systematic for all agents — no-op if no old process runs exist (DELETE affects 0 rows).
        if (
            state == AgentState.USER_TURN
            and not agent._old_runs_purged
            and agent.process_run is not None
        ):
            agent._old_runs_purged = True
            current_run_id = agent.process_run.pk
            try:
                from twicc.core.models import ProcessRun as ProcessRunModel
                deleted_count, _ = await asyncio.to_thread(
                    lambda: ProcessRunModel.objects.filter(
                        session_id=agent.session_id
                    ).exclude(pk=current_run_id).delete()
                )
                if deleted_count:
                    logger.info(
                        "Purged %d old process run(s) for session %s (current: %s)",
                        deleted_count, agent.session_id, current_run_id,
                    )
            except Exception as e:
                logger.error("Error purging old process runs for session %s: %s", agent.session_id, e)

        # Broadcast state change (base helper handles missing callback)
        await self._broadcast_info(info)

        # --- Check for pending settings changes on USER_TURN transition ---
        # When settings are changed during ASSISTANT_TURN, they are saved to DB
        # but not applied to the agent. On each USER_TURN transition, we compare
        # DB settings with agent settings and apply/restart as needed.
        if state == AgentState.USER_TURN:
            try:
                await self._apply_pending_settings(agent)
            except Exception as e:
                logger.error("Error applying pending settings for session %s: %s", agent.session_id, e)

        # Flush pending title for draft→real sessions.
        # On ASSISTANT_TURN, the CLI has created the JSONL file, so we can
        # now write the title that was stored when the draft was sent.
        if state == AgentState.ASSISTANT_TURN:
            from twicc.providers.claude_code.titles import get_pending_title, pop_pending_title, protect_title, rename_session_in_jsonl

            pending = get_pending_title(agent.session_id)
            if pending:
                try:
                    await asyncio.to_thread(rename_session_in_jsonl, agent.session_id, pending)
                    pop_pending_title(agent.session_id)
                    protect_title(agent.session_id, pending)
                except Exception as e:
                    logger.error("Error flushing pending title for session %s: %s", agent.session_id, e)

        # Update last_stopped_at when agent dies, and propagate to recent subagents
        if state == AgentState.DEAD:
            from twicc.providers.claude_code.titles import clear_protected_title, get_protected_title, rename_session_in_jsonl

            # Re-write the user-set title at the end of the JSONL so the CLI's
            # tail-scan finds it on the next resume (survives server restarts).
            protected = get_protected_title(agent.session_id)
            if protected:
                try:
                    await asyncio.to_thread(rename_session_in_jsonl, agent.session_id, protected)
                except Exception as e:
                    logger.warning("Failed to re-write title on DEAD for session %s: %s", agent.session_id, e)

            clear_protected_title(agent.session_id)

            try:
                from django.utils import timezone as dj_timezone
                from twicc.core.models import Session
                now = dj_timezone.now()

                # Get the previous cutoff BEFORE updating, to find subagents started in this run
                session = await asyncio.to_thread(Session.objects.filter(id=agent.session_id).first)
                previous_cutoff = session.cutoff if session else None

                await asyncio.to_thread(
                    lambda: Session.objects.filter(id=agent.session_id).update(
                        last_stopped_at=now, last_updated_at=now
                    )
                )
                await self._broadcast_session_updated(agent.session_id)

                # Propagate last_stopped_at to subagents started after the previous cutoff
                if session is not None:
                    subagent_filter = Session.objects.filter(parent_session_id=agent.session_id)
                    if previous_cutoff is not None:
                        subagent_filter = subagent_filter.filter(last_started_at__gte=previous_cutoff)
                    subagent_ids = await asyncio.to_thread(
                        lambda: list(subagent_filter.values_list('id', flat=True))
                    )
                    if subagent_ids:
                        await asyncio.to_thread(
                            lambda: Session.objects.filter(id__in=subagent_ids).update(
                                last_stopped_at=now, last_updated_at=now
                            )
                        )
                        for subagent_id in subagent_ids:
                            await self._broadcast_session_updated(subagent_id)

            except Exception as e:
                logger.error("Error updating last_stopped_at for session %s: %s", agent.session_id, e)

            # ProcessRun lifecycle cleanup on agent death
            if agent.process_run is not None:
                should_delete_run = False

                if agent.kill_reason == "manual":
                    # User explicitly stopped → delete process run (cascade deletes crons)
                    should_delete_run = True
                elif not agent._first_user_turn_reached:
                    # Died before first USER_TURN (failed cron restart, early crash, etc.)
                    # Delete current process run to discard partial crons, keep old runs for retry
                    should_delete_run = True
                else:
                    # Died after USER_TURN (old runs already purged). Keep only if it has crons.
                    has_crons = await asyncio.to_thread(lambda: agent.process_run.crons.exists())
                    if not has_crons:
                        should_delete_run = True

                if should_delete_run:
                    try:
                        run_pk = agent.process_run.pk
                        await asyncio.to_thread(lambda: agent.process_run.delete())
                        agent.process_run = None
                        logger.info(
                            "Deleted process run %s for session %s (kill_reason=%s, user_turn_reached=%s)",
                            run_pk, agent.session_id, agent.kill_reason, agent._first_user_turn_reached,
                        )
                    except Exception as e:
                        logger.error("Error deleting process run for session %s: %s", agent.session_id, e)

                # --- Auto-restart crons for non-manual, non-shutdown deaths ---
                # should_delete_run is False ⟹ agent had active crons and died
                # after USER_TURN. Launch a background restart task.
                if (
                    not should_delete_run
                    and agent.kill_reason not in ("manual", "shutdown")
                    and settings.CRON_AUTO_RESTART
                    and agent.session_id not in self._cron_restart_tasks
                    and self._stop_event is not None
                    and not self._stop_event.is_set()
                ):
                    task = asyncio.create_task(
                        self._restart_crons_for_session(agent.session_id),
                        name=f"cron-restart-{agent.session_id}",
                    )
                    self._cron_restart_tasks[agent.session_id] = task
                    logger.info(
                        "Launched runtime cron restart task for session %s (kill_reason=%s)",
                        agent.session_id, agent.kill_reason,
                    )

        # Clean up dead agent from registry. No lock needed - see docstring for concurrency model.
        if state == AgentState.DEAD:
            self._cleanup_dead(agent)

    # ------------------------------------------------------------------
    # Cron expiry monitor
    # ------------------------------------------------------------------

    async def _run_cron_expiry_monitor(self) -> None:
        """Background task that detects recurring crons auto-deleted by the CLI after 7 days.

        Runs every 60 seconds. For each active agent, checks if any recurring crons
        have passed their computed expiry time (last_fire + jitter + margin).
        """
        logger.info("Cron expiry monitor started")

        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                await self._check_expired_crons()
            except Exception as e:
                logger.error("Error in cron expiry monitor: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.CRON_EXPIRY_MONITOR_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass

        logger.info("Cron expiry monitor stopped")

    async def _check_expired_crons(self) -> None:
        """Check all active agents for expired recurring crons.

        Only checks agents in USER_TURN state: this guarantees the last cron fire
        has completed (Claude is no longer working on the cron's prompt).

        When expired crons are found, they are deleted from the DB and a message is
        sent to Claude asking it to recreate them via CronCreate. The new crons will
        be persisted by the existing PostToolUse hooks.
        """
        from twicc.core.models import SessionCron
        from twicc.providers.claude_code.cron_restart import _build_renewal_message

        # Snapshot active agents (no lock needed — dict iteration is safe in asyncio)
        agents = list(self._agents.values())

        for agent in agents:
            if agent.state != AgentState.USER_TURN:
                continue
            try:
                expired = await asyncio.to_thread(agent.get_expired_recurring_crons)
                if not expired:
                    continue

                logger.info(
                    "Session %s has %d expired recurring cron(s): %s",
                    agent.session_id,
                    len(expired),
                    ", ".join(c.cron_id for c in expired),
                )

                # Build renewal message from expired crons data before deleting them.
                # Includes cron_id so Claude can delete the old CLI crons first.
                crons_data = [
                    {
                        "cron_id": c.cron_id,
                        "cron_expr": c.cron_expr,
                        "recurring": c.recurring,
                        "prompt": c.prompt,
                    }
                    for c in expired
                ]
                message = _build_renewal_message(crons_data)

                # Delete expired crons from DB (they're already dead in the CLI)
                expired_ids = [c.pk for c in expired]
                await asyncio.to_thread(
                    lambda: SessionCron.objects.filter(pk__in=expired_ids).delete()
                )
                logger.info(
                    "Deleted %d expired cron(s) from DB for session %s",
                    len(expired_ids), agent.session_id,
                )

                # Send message to Claude asking to recreate the crons
                try:
                    await agent.send(message)
                    logger.info(
                        "Sent cron renewal message to session %s for %d cron(s)",
                        agent.session_id, len(crons_data),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to send cron renewal message to session %s: %s",
                        agent.session_id, e,
                    )

            except Exception as e:
                logger.error(
                    "Error checking expired crons for session %s: %s",
                    agent.session_id, e,
                )

    # ------------------------------------------------------------------
    # Cron restart helpers
    # ------------------------------------------------------------------

    def _cancel_cron_restart_task(self, session_id: str) -> bool:
        """Cancel a running cron restart task for a session.

        Returns True if a task was cancelled, False if none existed.
        """
        task = self._cron_restart_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled cron restart task for session %s", session_id)
            return True
        return False

    async def _restart_crons_for_session(self, session_id: str) -> None:
        """Launch infinite cron restart loop for a session that died at runtime.

        Thin wrapper around restart_session_crons() that handles task lifecycle
        (CancelledError, cleanup from _cron_restart_tasks dict).

        After successful cron restart, sends any pending content that was queued
        during a settings-triggered restart (user sent text + startup settings changes).
        """
        from twicc.providers.claude_code.cron_restart import restart_session_crons

        RUNTIME_INITIAL_DELAY = 10  # seconds before first attempt (let API recover)

        try:
            await restart_session_crons(
                session_id,
                stop_event=self._stop_event,
                initial_delay=RUNTIME_INITIAL_DELAY,
            )
            # After successful cron restart, send pending content if any
            pending = self._pending_after_restart.pop(session_id, None)
            if pending and pending.get("text"):
                agent = self._agents.get(session_id)
                if agent and agent.state == AgentState.USER_TURN:
                    logger.info("Sending pending text after cron restart for session %s", session_id)
                    await agent.send(
                        pending["text"],
                        images=pending.get("images"),
                        documents=pending.get("documents"),
                    )
        except asyncio.CancelledError:
            logger.info("Cron restart task cancelled for session %s", session_id)
            raise
        except Exception as e:
            logger.error("Cron restart task failed for session %s: %s", session_id, e, exc_info=True)
        finally:
            self._cron_restart_tasks.pop(session_id, None)
            # Drop any queued pending content too: if we got here without
            # successfully sending it (cancel/error/no-USER_TURN), we cannot
            # send it later — keeping the entry around would just leak and
            # potentially overwrite a future legitimate queue.
            self._pending_after_restart.pop(session_id, None)

    # ------------------------------------------------------------------
    # Cron persistence callbacks (passed to ClaudeAgent)
    # ------------------------------------------------------------------

    async def _on_cron_created(
        self,
        session_id: str,
        cron_id: str,
        cron_expr: str,
        recurring: bool,
        prompt: str,
        created_at: "datetime",
        next_fire: "datetime",
    ) -> None:
        """Persist a newly created cron job to the database and broadcast the update."""
        from twicc.core.models import SessionCron

        # Associate cron with the current process run (if any)
        agent = self._agents.get(session_id)
        process_run = agent.process_run if agent else None

        await asyncio.to_thread(
            lambda: SessionCron.objects.create(
                cron_id=cron_id,
                session_id=session_id,
                process_run=process_run,
                cron_expr=cron_expr,
                recurring=recurring,
                prompt=prompt,
                created_at=created_at,
                next_fire=next_fire,
            )
        )
        logger.info(
            "Persisted cron %s for session %s (process run: %s)",
            cron_id, session_id, process_run.pk if process_run else None,
        )
        await self._broadcast_agent_state(session_id)

    async def _on_cron_deleted(self, session_id: str, cron_id: str) -> None:
        """Delete a cron job from the database and broadcast the update."""
        from twicc.core.models import SessionCron

        deleted, _ = await asyncio.to_thread(
            lambda: SessionCron.objects.filter(cron_id=cron_id).delete()
        )
        if deleted:
            logger.info("Deleted cron %s for session %s", cron_id, session_id)
        else:
            logger.debug("Cron %s not found in DB for session %s (may already be deleted)", cron_id, session_id)
        await self._broadcast_agent_state(session_id)

    @staticmethod
    async def _session_has_crons(agent: ClaudeAgent) -> bool:
        """Check if an agent has active crons (via its ProcessRun)."""
        if agent.process_run is None:
            return False
        return await asyncio.to_thread(lambda: agent.process_run.crons.exists())

    # ------------------------------------------------------------------
    # Synthetic state broadcast (for restart "starting" notifications)
    # ------------------------------------------------------------------

    async def _broadcast_agent_state(
        self,
        session_id: str,
        project_id: str | None = None,
        override_state: AgentState | None = None,
    ) -> None:
        """Trigger an agent state broadcast for a session.

        When override_state is provided, broadcasts a synthetic AgentInfo with
        that state (useful for broadcasting "starting" after a kill). Otherwise
        broadcasts the current agent state (used after cron changes).
        """
        if self._broadcast_callback is None:
            return
        try:
            if override_state is not None and project_id is not None:
                now = asyncio.get_event_loop().time()
                info = AgentInfo(
                    session_id=session_id,
                    project_id=project_id,
                    provider=ClaudeAgent.provider,
                    state=override_state,
                    previous_state=None,
                    started_at=now,
                    state_changed_at=now,
                    last_activity=now,
                )
                await self._broadcast_callback(info)
            else:
                agent = self._agents.get(session_id)
                if agent:
                    await self._broadcast_callback(agent.get_info())
        except Exception as e:
            logger.error("Error broadcasting agent state for session %s: %s", session_id, e)

    # ------------------------------------------------------------------
    # Settings hot-reload
    # ------------------------------------------------------------------

    async def _apply_pending_settings(self, agent: ClaudeAgent) -> None:
        """Check DB settings vs agent settings and apply/restart on USER_TURN.

        Called from _on_state_change when an agent transitions to USER_TURN.
        Compares the session's stored settings (potentially updated during
        ASSISTANT_TURN) with the agent's current settings.

        - Idle changes (model, context) → apply live via set_model()
        - Startup changes (effort, thinking, chrome) → kill + restart/cron restart
        - No changes → no-op
        """
        from twicc.core.models import Session
        from twicc.synced_settings import classify_claude_settings_changes, read_synced_settings

        session = await asyncio.to_thread(
            Session.objects.filter(id=agent.session_id).first
        )
        if session is None:
            return

        # Resolve effective settings (null → global default)
        defaults = read_synced_settings()
        requested = {
            "permission_mode": session.permission_mode if session.permission_mode is not None else defaults.get("claudeCodeDefaultPermissionMode", "default"),
            "selected_model": session.selected_model if session.selected_model is not None else defaults.get("claudeCodeDefaultModel", "opus"),
            "effort": session.effort if session.effort is not None else defaults.get("claudeCodeDefaultEffort", "medium"),
            "thinking_enabled": session.thinking_enabled if session.thinking_enabled is not None else defaults.get("claudeCodeDefaultThinking", True),
            "claude_in_chrome": session.claude_in_chrome if session.claude_in_chrome is not None else defaults.get("claudeCodeDefaultClaudeInChrome", True),
            "context_max": session.context_max if session.context_max is not None else defaults.get("claudeCodeDefaultContextMax", 200_000),
        }

        # Enforce 1M consistency
        from twicc.providers.claude_code.model_registry import enforce_1m_consistency
        requested["context_max"] = enforce_1m_consistency(requested["selected_model"], requested["context_max"])

        changes = classify_claude_settings_changes(agent.get_claude_settings(), requested)
        if not any(changes.values()):
            return

        if changes["startup"]:
            # Startup settings changed → kill and let cron restart / pending handle it
            logger.info(
                "Pending startup settings for session %s (%s), killing agent on USER_TURN",
                agent.session_id, changes["startup"],
            )
            has_crons = await self._session_has_crons(agent)
            has_pending = agent.session_id in self._pending_after_restart
            will_restart = has_crons or has_pending
            await agent.interrupt_or_kill(reason="apply-settings")
            if will_restart:
                await self._broadcast_agent_state(
                    agent.session_id, agent.project_id, AgentState.STARTING,
                )
            # If no crons but has pending text → start directly
            if not has_crons and has_pending:
                pending = self._pending_after_restart.pop(agent.session_id)
                await self._start_agent(
                    agent.session_id, agent.project_id, agent.cwd,
                    pending["text"], resume=True, **requested,
                    images=pending.get("images"), documents=pending.get("documents"),
                )
        else:
            # Only live/idle changes → apply via SDK methods
            logger.info(
                "Applying live/idle settings for session %s (live=%s, idle=%s) on USER_TURN",
                agent.session_id, changes["live"], changes["idle"],
            )
            await agent.apply_live_settings(
                requested["permission_mode"],
                requested["selected_model"],
                requested["context_max"],
            )


def get_claude_agent_manager() -> ClaudeAgentManager:
    """Return the Claude Code agent manager singleton from the registry.

    Thin convenience wrapper for Claude-specific call sites. The actual
    instance is owned by the global ``AgentManagerRegistry`` and shared with
    every provider-agnostic consumer.
    """
    # Lazy import to avoid an import cycle: the registry imports this module.
    from twicc.agent.registry import get_agent_manager_registry

    manager = get_agent_manager_registry().get(Provider.CLAUDE_CODE)
    return manager  # type: ignore[return-value]
