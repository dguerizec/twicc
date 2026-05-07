"""
Claude Code provider orchestrator.

Owns the lifecycle of every async/background task that belongs to the Claude
Code provider: initial JSONL sync, sessions watcher, background metadata
compute, auth/usage/statuspage/slash-commands polling, model retirement,
cron restart, original file cache cleanup, and the ClaudeCodeAgentManager
that wraps the Claude Agent SDK.

The CLI server entry point (``twicc.cli.run``) instantiates this class and
delegates start/shutdown of all Claude Code tasks to it. Cross-provider tasks
(PyPI version check, OpenRouter price sync, search index lifecycle, global
startup search-indexing task) stay in the CLI module — pricing in particular
is shared across every provider that has declared an
``OPENROUTER_MODEL_PREFIX``, with a single fetch per cycle.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.claude_code.agent import get_claude_code_agent_manager
from twicc.providers.claude_code.agent.original_file_cache import (
    start_cleanup_task as start_original_file_cache_cleanup,
    stop_cleanup_task as stop_original_file_cache_cleanup,
)
from twicc.core.models import Project, Session, SessionType
from twicc.providers.claude_code.auth_task import start_auth_task, stop_auth_task
from twicc.providers.claude_code.cron_restart import restart_all_session_crons
from twicc.providers.claude_code.initial_sync import scan_projects, scan_sessions, sync_all
from twicc.providers.claude_code.model_retirement_task import (
    start_model_retirement_task,
    stop_model_retirement_task,
)
from twicc.providers.claude_code.sessions_watcher import get_watcher
from twicc.providers.claude_code.slash_commands_task import (
    start_slash_commands_task,
    stop_slash_commands_task,
)
from twicc.providers.claude_code.statuspage_task import start_statuspage_task, stop_statuspage_task
from twicc.providers.claude_code.usage_task import start_usage_sync_task, stop_usage_sync_task
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)


def _count_total_sessions() -> int:
    """Filesystem-only count of session files across all projects (for progress reporting)."""
    total = 0
    for project_id in scan_projects():
        total += len(scan_sessions(project_id))
    return total


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


def _on_watcher_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Watcher task crashed with exception — file changes will no longer be detected!",
            exc_info=exc,
        )
    else:
        logger.warning(
            "Watcher task ended unexpectedly (no exception) — file changes will no longer be detected"
        )


class ClaudeCodeOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Claude Code provider tasks.

    Task dependency graph (started by ``start()``):

    - initial_sync, usage_sync, auth_check, statuspage, slash_commands,
      original_file_cache_cleanup, model_retirement: start immediately.
    - watcher: starts after initial_sync **and** after the CLI has
      initialized the global search index (so the watcher can write into
      it as new items arrive).
    - cron_restart + background_compute: start after initial_sync.

    The cross-provider search-index init/shutdown and the global search
    indexing background task live in the CLI now — this orchestrator
    only signals its progress via :attr:`initial_sync_done` and
    :attr:`compute_done` (inherited from :class:`BaseOrchestrator`).

    Pricing is owned by the CLI (cross-provider, single OpenRouter fetch
    shared by every provider that has an ``OPENROUTER_MODEL_PREFIX``); the
    initial price sync is awaited by the CLI before instantiating this
    orchestrator, so prices are guaranteed to be in DB by the time the
    background compute task starts.
    """

    provider = Provider.CLAUDE_CODE

    def __init__(self) -> None:
        super().__init__()
        # This provider owns both phases — reset the inherited pre-set
        # events so the CLI actually waits for our broadcasts.
        self.initial_sync_done = asyncio.Event()
        self.compute_done = asyncio.Event()

        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()
        # Set by the host (CLI) when the server begins shutting down
        self._shutdown_event: asyncio.Event | None = None

        # Tasks started immediately
        self._sync_task: asyncio.Task | None = None
        self._orch_task: asyncio.Task | None = None
        self._usage_sync_task: asyncio.Task | None = None
        self._auth_check_task: asyncio.Task | None = None
        self._statuspage_task: asyncio.Task | None = None
        self._slash_commands_task: asyncio.Task | None = None
        self._original_file_cache_task: asyncio.Task | None = None
        self._retirement_task: asyncio.Task | None = None

        # Tasks started by the internal orchestrator coroutine (may stay None
        # if shutdown is requested before their prerequisites complete).
        self._watcher_task: asyncio.Task | None = None
        self._compute_task: asyncio.Task | None = None
        self._compute_ctx: ComputeContext | None = None
        self._cron_restart_task: asyncio.Task | None = None

    def request_thread_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration. Async tasks listen for the shared
        ``shutdown_event`` passed to :meth:`start`; this hook is for the
        blocking thread only.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch all Claude Code tasks. Returns once tasks are scheduled."""
        self._shutdown_event = shutdown_event
        self.search_index_ready = search_index_ready

        self._sync_task = asyncio.create_task(self._initial_sync_task())
        self._orch_task = asyncio.create_task(self._dependency_orchestrator())
        self._usage_sync_task = asyncio.create_task(start_usage_sync_task())
        self._auth_check_task = asyncio.create_task(start_auth_task())
        self._statuspage_task = asyncio.create_task(start_statuspage_task())
        self._slash_commands_task = asyncio.create_task(start_slash_commands_task())
        self._original_file_cache_task = asyncio.create_task(start_original_file_cache_cleanup())
        self._retirement_task = asyncio.create_task(start_model_retirement_task())

    async def shutdown(self) -> None:
        """Stop all Claude Code tasks in dependency-safe order."""
        # Make sure the initial sync thread cooperates if it's still running
        self._sync_stop_event.set()

        # Unblock the CLI in case it was awaiting either lifecycle event:
        # if a task was cancelled before reaching its natural ``set()``,
        # the CLI's ``wait_initial_sync_done`` / ``wait_compute_done``
        # would otherwise hang. Both calls are idempotent.
        self.initial_sync_done.set()
        self.compute_done.set()

        # Cancel startup tasks (may already be done)
        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Initial sync task")
        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Orchestrator task")

        # Watcher (may not have started yet)
        if self._watcher_task is not None:
            logger.info("Stopping watcher...")
            get_watcher().stop_watcher()
            await _cancel_task(self._watcher_task, "Watcher")
        else:
            logger.info("Watcher was not started, skipping")

        # Background compute (may not have started yet)
        if self._compute_task is not None:
            logger.info("Stopping background compute task...")
            stop_background_task(self._compute_ctx)
            await _cancel_task(self._compute_task, "Background compute task")
        else:
            logger.info("Background compute was not started, skipping")

        # Usage sync
        if self._usage_sync_task is not None:
            logger.info("Stopping usage sync task...")
            stop_usage_sync_task()
            await _cancel_task(self._usage_sync_task, "Usage sync task")

        # Auth check
        if self._auth_check_task is not None:
            logger.info("Stopping auth check task...")
            stop_auth_task()
            await _cancel_task(self._auth_check_task, "Auth check task")

        # Statuspage
        if self._statuspage_task is not None:
            logger.info("Stopping statuspage task...")
            stop_statuspage_task()
            await _cancel_task(self._statuspage_task, "Statuspage task")

        # Slash commands
        if self._slash_commands_task is not None:
            logger.info("Stopping slash commands task...")
            stop_slash_commands_task()
            await _cancel_task(self._slash_commands_task, "Slash commands task")

        # Original file cache cleanup
        if self._original_file_cache_task is not None:
            stop_original_file_cache_cleanup()
            await _cancel_task(self._original_file_cache_task, "Original file cache cleanup")

        # Model retirement
        if self._retirement_task is not None:
            logger.info("Stopping model retirement task...")
            stop_model_retirement_task()
            await _cancel_task(self._retirement_task, "Model retirement task")

        # Cron restart (may still be retrying)
        if self._cron_restart_task is not None:
            await _cancel_task(self._cron_restart_task, "Cron restart")

        # Claude Code agent manager (Claude Agent SDK)
        logger.info("Stopping Claude Code agent manager...")
        await get_claude_code_agent_manager().shutdown()
        logger.info("Claude Code agent manager stopped")

    # ------------------------------------------------------------------
    # Internal task coroutines
    # ------------------------------------------------------------------

    async def _initial_sync_task(self) -> None:
        """Run sync_all() in a thread with progress broadcasting."""
        current_provider.set(self.provider.value)

        loop = asyncio.get_running_loop()
        provider_value = self.provider.value

        total_sessions = await asyncio.to_thread(_count_total_sessions)

        await broadcast_startup_progress(
            "initial_sync", 0, total_sessions, provider=provider_value
        )

        progress = {"current": 0}

        def on_session_progress(session_id: str, idx: int, total: int):
            # idx/total are per-project; we track global progress ourselves
            progress["current"] += 1
            asyncio.run_coroutine_threadsafe(
                broadcast_startup_progress(
                    "initial_sync", progress["current"], total_sessions, provider=provider_value
                ),
                loop,
            )

        logger.info("Starting data synchronization...")
        await asyncio.to_thread(
            sync_all,
            on_session_progress=on_session_progress,
            stop_event=self._sync_stop_event,
        )

        await broadcast_startup_progress(
            "initial_sync", total_sessions, total_sessions,
            provider=provider_value, completed=True,
        )

        projects_count = await sync_to_async(
            Project.objects.filter(
                stale=False, sessions__provider=Provider.CLAUDE_CODE,
            ).distinct().count
        )()
        sessions_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CLAUDE_CODE, stale=False, type=SessionType.SESSION,
            ).count
        )()
        subagents_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CLAUDE_CODE, stale=False, type=SessionType.SUBAGENT,
            ).count
        )()
        logger.info(
            "Data synchronized (%d projects, %d sessions, %d subagents)",
            projects_count,
            sessions_count,
            subagents_count,
        )

        self.initial_sync_done.set()

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start compute + cron restart + watcher.

        Tasks created here (background compute, cron restart, watcher)
        inherit the provider tag from this task's context.

        The CLI has already run the cross-provider initial price sync
        before this orchestrator was started, so prices are guaranteed
        to be in DB by the time background compute kicks in. The CLI
        also owns ``init_search_index()`` and signals readiness via
        :attr:`search_index_ready` — the watcher waits on it so it
        never writes to an uninitialized index. The background compute
        does not touch the index, so it starts as soon as the initial
        sync is done.
        """
        current_provider.set(self.provider.value)

        await self.initial_sync_done.wait()

        # Background compute is independent of the search index and can
        # start as soon as the initial sync is done. The done_callback
        # signals completion so the CLI's global search-indexing task
        # can fire — it must run on success, failure, **and** cancel,
        # otherwise the CLI would block forever.
        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION,
            compute_factory="twicc.providers.claude_code.compute:get_compute",
        )
        self._compute_task = asyncio.create_task(
            start_background_compute_task(self._compute_ctx)
        )
        self._compute_task.add_done_callback(lambda _t: self.compute_done.set())
        logger.info("Background compute started (after initial sync)")

        # Restart cron jobs from previous process runs. Must run after
        # the watcher is up (below) so that JSONL writes from restarted
        # sessions are picked up — but we schedule it now so that
        # ``shutdown()`` can cancel it cleanly if it never gets a chance
        # to run.
        assert self._shutdown_event is not None
        self._cron_restart_task = asyncio.create_task(
            restart_all_session_crons(stop_event=self._shutdown_event)
        )
        logger.info("Cron restart task launched")

        # Watcher writes new items into the global search index, so it
        # must wait until the CLI has called ``init_search_index()``.
        assert self.search_index_ready is not None, "search_index_ready must be set by start()"
        await self.search_index_ready.wait()
        self._watcher_task = asyncio.create_task(get_watcher().start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Watcher started (after initial sync + search index ready)")
