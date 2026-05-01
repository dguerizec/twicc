"""
Claude Code provider orchestrator.

Owns the lifecycle of every async/background task that belongs to the Claude
Code provider: initial JSONL sync, sessions watcher, background metadata
compute, search index, auth/usage/statuspage/slash-commands polling, model
retirement, cron restart, original file cache cleanup, and the
ClaudeCodeAgentManager that wraps the Claude Agent SDK.

The CLI server entry point (``twicc.cli.run``) instantiates this class and
delegates start/shutdown of all Claude Code tasks to it. Cross-provider tasks
(PyPI version check, OpenRouter price sync) stay in the CLI module — pricing
in particular is shared across every provider that has declared an
``OPENROUTER_MODEL_PREFIX``, with a single fetch per cycle.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async

from twicc.providers.claude_code.agent import get_claude_code_agent_manager
from twicc.providers.claude_code.agent.original_file_cache import (
    start_cleanup_task as start_original_file_cache_cleanup,
    stop_cleanup_task as stop_original_file_cache_cleanup,
)
from twicc.core.models import Project, Session, SessionType
from twicc.providers.claude_code.auth_task import start_auth_task, stop_auth_task
from twicc.providers.claude_code.background_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.claude_code.cron_restart import restart_all_session_crons
from twicc.providers.claude_code.initial_sync import scan_projects, scan_sessions, sync_all
from twicc.providers.claude_code.model_retirement_task import (
    start_model_retirement_task,
    stop_model_retirement_task,
)
from twicc.providers.claude_code.search_indexing_task import (
    start_search_index_task,
    stop_search_index_task,
)
from twicc.providers.claude_code.sessions_watcher import start_watcher, stop_watcher
from twicc.providers.claude_code.slash_commands_task import (
    start_slash_commands_task,
    stop_slash_commands_task,
)
from twicc.providers.claude_code.statuspage_task import start_statuspage_task, stop_statuspage_task
from twicc.providers.claude_code.usage_task import start_usage_sync_task, stop_usage_sync_task
from twicc.search import init_search_index, shutdown_search_index
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


class ClaudeCodeOrchestrator:
    """Lifecycle manager for Claude Code provider tasks.

    Task dependency graph (started by ``start()``):

    - initial_sync, usage_sync, auth_check, statuspage, slash_commands,
      original_file_cache_cleanup, model_retirement: start immediately.
    - search index init + watcher + cron_restart + background_compute:
      start after initial_sync.
    - search_indexing: starts after background_compute finishes (via done
      callback), unless the server is shutting down.

    Pricing is owned by the CLI (cross-provider, single OpenRouter fetch
    shared by every provider that has an ``OPENROUTER_MODEL_PREFIX``); the
    initial price sync is awaited by the CLI before instantiating this
    orchestrator, so prices are guaranteed to be in DB by the time the
    background compute task starts.
    """

    def __init__(self) -> None:
        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()
        # Set by the host (CLI) when the server begins shutting down
        self._shutdown_event: asyncio.Event | None = None

        # Internal dependency-signaling events
        self._sync_done = asyncio.Event()

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
        self._search_indexing_task: asyncio.Task | None = None
        self._cron_restart_task: asyncio.Task | None = None

    def request_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event) -> None:
        """Launch all Claude Code tasks. Returns once tasks are scheduled."""
        self._shutdown_event = shutdown_event

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

        # Cancel startup tasks (may already be done)
        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Initial sync task")
        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Orchestrator task")

        # Watcher (may not have started yet)
        if self._watcher_task is not None:
            logger.info("Stopping watcher...")
            stop_watcher()
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

        # Search index task (may not have started yet) + final index shutdown
        if self._search_indexing_task is not None:
            logger.info("Stopping search index task...")
            stop_search_index_task()
            await _cancel_task(self._search_indexing_task, "Search index task")
        else:
            logger.info("Search index task was not started, skipping")
        logger.info("Shutting down search index...")
        await asyncio.to_thread(shutdown_search_index)

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
        loop = asyncio.get_running_loop()

        total_sessions = await asyncio.to_thread(_count_total_sessions)

        await broadcast_startup_progress("initial_sync", 0, total_sessions)

        progress = {"current": 0}

        def on_session_progress(session_id: str, idx: int, total: int):
            # idx/total are per-project; we track global progress ourselves
            progress["current"] += 1
            asyncio.run_coroutine_threadsafe(
                broadcast_startup_progress("initial_sync", progress["current"], total_sessions),
                loop,
            )

        logger.info("Starting data synchronization...")
        await asyncio.to_thread(
            sync_all,
            on_session_progress=on_session_progress,
            stop_event=self._sync_stop_event,
        )

        await broadcast_startup_progress(
            "initial_sync", total_sessions, total_sessions, completed=True
        )

        projects_count = await sync_to_async(Project.objects.filter(stale=False).count)()
        sessions_count = await sync_to_async(
            Session.objects.filter(stale=False, type=SessionType.SESSION).count
        )()
        subagents_count = await sync_to_async(
            Session.objects.filter(stale=False, type=SessionType.SUBAGENT).count
        )()
        logger.info(
            "Data synchronized (%d projects, %d sessions, %d subagents)",
            projects_count,
            sessions_count,
            subagents_count,
        )

        self._sync_done.set()

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start watcher + cron restart + compute.

        The CLI has already run the cross-provider initial price sync
        before this orchestrator was started, so prices are guaranteed
        to be in DB by the time background compute kicks in.
        """
        # Initialize search index before watcher starts, so the watcher can
        # index new items into the search index as they arrive in real time.
        await self._sync_done.wait()
        await asyncio.to_thread(init_search_index)
        logger.info("Search index initialized (after initial sync)")

        self._watcher_task = asyncio.create_task(start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Watcher started (after initial sync)")

        # Restart cron jobs from previous process runs.
        # Must run after watcher is up so that JSONL writes from restarted sessions are detected.
        assert self._shutdown_event is not None
        self._cron_restart_task = asyncio.create_task(
            restart_all_session_crons(stop_event=self._shutdown_event)
        )
        logger.info("Cron restart task launched")

        self._compute_ctx = ComputeContext()
        self._compute_task = asyncio.create_task(
            start_background_compute_task(self._compute_ctx)
        )
        logger.info("Background compute started (after initial sync)")

        # Search indexing task starts automatically when background compute finishes
        # (via done callback). Uses shutdown_event to skip if server is stopping.
        # Note: init_search_index was already called above (before watcher start).
        def _on_compute_done(task: asyncio.Task) -> None:
            if task.cancelled() or self._shutdown_event.is_set():
                return
            self._search_indexing_task = asyncio.create_task(start_search_index_task())
            logger.info("Background search indexing started (after compute)")

        self._compute_task.add_done_callback(_on_compute_done)
