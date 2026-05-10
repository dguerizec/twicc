"""
Codex provider orchestrator.

Owns the Codex initial JSONL sync, the background metadata compute,
the JSONL sessions watcher, the Codex CLI auth check task, the ChatGPT
usage sync task, and the OpenAI statuspage poll. The agent runtime is
not wired yet — it will land alongside the Codex CLI integration.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.codex.auth_task import start_auth_task, stop_auth_task
from twicc.providers.codex.initial_sync import scan_session_files, sync_all
from twicc.providers.codex.sessions_watcher import get_watcher
from twicc.providers.codex.statuspage_task import start_statuspage_task, stop_statuspage_task
from twicc.providers.codex.usage_task import start_usage_sync_task, stop_usage_sync_task
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)


def _count_total_sessions() -> int:
    """Filesystem-only count of Codex session files (for progress reporting)."""
    return len(scan_session_files())


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


class CodexOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Codex provider tasks (initial sync + auth check + usage sync + statuspage)."""

    provider = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        # This provider runs both initial sync and background compute —
        # reset the inherited pre-set events so the CLI actually waits
        # for our broadcasts.
        self.initial_sync_done = asyncio.Event()
        self.compute_done = asyncio.Event()

        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()

        self._sync_task: asyncio.Task | None = None
        self._orch_task: asyncio.Task | None = None
        self._auth_check_task: asyncio.Task | None = None
        self._usage_sync_task: asyncio.Task | None = None
        self._statuspage_task: asyncio.Task | None = None

        # Started by the dependency orchestrator coroutine after the
        # initial sync completes (compute) or after the search index is
        # also ready (watcher). Both stay None when shutdown is requested
        # before their prerequisites complete.
        self._compute_task: asyncio.Task | None = None
        self._compute_ctx: ComputeContext | None = None
        self._watcher_task: asyncio.Task | None = None

    def request_thread_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch the initial sync, dependency orchestrator, watcher, auth
        check, usage sync, and statuspage tasks.

        ``shutdown_event`` is currently unused (Codex has no cron-style
        loops that need to watch it directly — the watcher uses its own
        per-instance stop event, set in :meth:`shutdown`). The parameter
        stays in the signature to match :meth:`BaseOrchestrator.start`.

        ``search_index_ready`` is awaited by :meth:`_dependency_orchestrator`
        before launching the JSONL watcher, since the watcher writes new
        items into the global Tantivy index as they arrive.
        """
        self.search_index_ready = search_index_ready

        self._sync_task = self._create_task(self._initial_sync_task())
        self._orch_task = self._create_task(self._dependency_orchestrator())
        self._auth_check_task = self._create_task(start_auth_task())
        self._usage_sync_task = self._create_task(start_usage_sync_task())
        self._statuspage_task = self._create_task(start_statuspage_task())

    async def shutdown(self) -> None:
        """Stop the Codex tasks (sync + compute first, then the periodic ones)."""
        # Make sure the initial sync thread cooperates if it's still running
        self._sync_stop_event.set()

        # Unblock the CLI in case it was awaiting either lifecycle event
        # before our natural ``set()`` could run. Both calls are idempotent.
        self.initial_sync_done.set()
        self.compute_done.set()

        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Codex initial sync task")

        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Codex orchestrator task")

        # Watcher (may not have started yet — depends on initial sync + search index ready)
        if self._watcher_task is not None:
            logger.info("Stopping Codex watcher...")
            get_watcher().stop_watcher()
            await _cancel_task(self._watcher_task, "Codex watcher")
        else:
            logger.info("Codex watcher was not started, skipping")

        # Background compute (may not have started yet — depends on initial sync)
        if self._compute_task is not None:
            logger.info("Stopping Codex background compute task...")
            stop_background_task(self._compute_ctx)
            await _cancel_task(self._compute_task, "Codex background compute task")
        else:
            logger.info("Codex background compute was not started, skipping")

        if self._usage_sync_task is not None:
            logger.info("Stopping Codex usage sync task...")
            stop_usage_sync_task()
            await _cancel_task(self._usage_sync_task, "Codex usage sync task")

        if self._auth_check_task is not None:
            logger.info("Stopping Codex auth check task...")
            stop_auth_task()
            await _cancel_task(self._auth_check_task, "Codex auth check task")

        if self._statuspage_task is not None:
            logger.info("Stopping Codex statuspage task...")
            stop_statuspage_task()
            await _cancel_task(self._statuspage_task, "Codex statuspage task")

    # ------------------------------------------------------------------
    # Internal task coroutines
    # ------------------------------------------------------------------

    async def _initial_sync_task(self) -> None:
        """Run sync_all() in a thread with progress broadcasting."""
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

        logger.info("Starting Codex data synchronization...")
        await asyncio.to_thread(
            sync_all,
            on_session_progress=on_session_progress,
            stop_event=self._sync_stop_event,
        )

        await broadcast_startup_progress(
            "initial_sync", total_sessions, total_sessions,
            provider=provider_value, completed=True,
        )

        sessions_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CODEX, stale=False, type=SessionType.SESSION
            ).count
        )()
        logger.info("Codex data synchronized (%d sessions)", sessions_count)

        self.initial_sync_done.set()

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start the background compute and
        the JSONL watcher.

        Background compute reads existing Codex sessions whose stored
        ``compute_version`` differs from
        :data:`settings.CODEX_COMPUTE_VERSION` and recomputes their
        kind / display_level. The done_callback signals
        :attr:`compute_done` so the CLI's global search-indexing task
        can fire — it must run on success, failure, **and** cancel,
        otherwise the CLI would block forever.

        The watcher writes new items into the global Tantivy index, so
        it must wait until the CLI has called ``init_search_index()``
        and signalled :attr:`search_index_ready`. Background compute
        does not touch the index, so it starts as soon as the initial
        sync is done.
        """
        await self.initial_sync_done.wait()

        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.CODEX_COMPUTE_VERSION,
            compute_factory="twicc.providers.codex.compute:get_compute",
        )
        self._compute_task = self._create_task(
            start_background_compute_task(self._compute_ctx)
        )
        self._compute_task.add_done_callback(lambda _t: self.compute_done.set())
        logger.info("Codex background compute started (after initial sync)")

        assert self.search_index_ready is not None, "search_index_ready must be set by start()"
        await self.search_index_ready.wait()
        self._watcher_task = self._create_task(get_watcher().start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Codex watcher started (after initial sync + search index ready)")
