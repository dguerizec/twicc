"""
Codex provider orchestrator.

Owns the Codex initial JSONL sync, the Codex CLI auth check task, the
ChatGPT usage sync task, and the OpenAI statuspage poll. There is no
JSONL watcher, agent runtime, compute task, or pricing yet — those will
land when the agent runtime is wired.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.codex.auth_task import start_auth_task, stop_auth_task
from twicc.providers.codex.initial_sync import scan_session_files, sync_all
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


class CodexOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Codex provider tasks (initial sync + auth check + usage sync + statuspage)."""

    provider = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        # This provider runs an initial sync — reset the inherited pre-set
        # event so the CLI actually waits for our broadcast.
        self.initial_sync_done = asyncio.Event()

        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()

        self._sync_task: asyncio.Task | None = None
        self._auth_check_task: asyncio.Task | None = None
        self._usage_sync_task: asyncio.Task | None = None
        self._statuspage_task: asyncio.Task | None = None

    def request_thread_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch the initial sync, auth check, usage sync, and statuspage tasks.

        ``search_index_ready`` is unused today: Codex has no JSONL watcher
        writing into the search index. The signature stays aligned with
        :meth:`BaseOrchestrator.start` so the CLI can call ``start_all``
        uniformly.
        """
        self._sync_task = asyncio.create_task(self._initial_sync_task())
        self._auth_check_task = asyncio.create_task(start_auth_task())
        self._usage_sync_task = asyncio.create_task(start_usage_sync_task())
        self._statuspage_task = asyncio.create_task(start_statuspage_task())

    async def shutdown(self) -> None:
        """Stop the Codex tasks (sync first, then the periodic ones)."""
        # Make sure the initial sync thread cooperates if it's still running
        self._sync_stop_event.set()

        # Unblock the CLI in case it was awaiting the lifecycle event
        # before our natural ``set()`` could run. Idempotent.
        self.initial_sync_done.set()

        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Codex initial sync task")

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
