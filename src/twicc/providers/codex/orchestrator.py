"""
Codex provider orchestrator.

Owns the Codex initial JSONL sync, the background metadata compute,
the JSONL sessions watcher, the Codex CLI auth check task, the ChatGPT
usage sync task, the OpenAI statuspage poll, the periodic skill
catalogue sync (``skills/list`` → ``Command`` rows under the ``$``
prefix), and the shutdown of the Codex agent manager (its construction
is lazy via the agent registry).
"""

from __future__ import annotations

import asyncio
import logging
import threading

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.codex.agent import get_codex_agent_manager
from twicc.providers.codex.agent.original_files_cache import (
    start_cleanup_task as start_original_files_cache_cleanup,
    stop_cleanup_task as stop_original_files_cache_cleanup,
)
from twicc.providers.codex.auth_task import start_auth_task, stop_auth_task
from twicc.providers.codex.commands_task import start_commands_task, stop_commands_task
from twicc.providers.codex.initial_sync import scan_session_files, sync_all
from twicc.providers.codex.plugin_install import ensure_twicc_plugin_installed
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
        self._commands_task: asyncio.Task | None = None
        self._original_files_cache_task: asyncio.Task | None = None

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

        ``shutdown_event`` is the CLI-level SIGTERM signal, used by loops
        that do non-cancellable long-duration work and need to bail out
        gracefully before the eventual ``task.cancel()`` arrives — the
        canonical use is Claude's :func:`restart_all_session_crons`,
        which retries with exponential backoff. Codex has no such loop:
        its periodic tasks (auth, usage, statuspage, commands,
        original-files cache) and its watcher all suspend on
        ``wait_for(local_stop_event.wait(), timeout=...)``, and the
        ``task.cancel()`` issued by :meth:`shutdown` unblocks every
        ``wait_for`` instantly via ``CancelledError``. The parameter
        stays in the signature to honour :meth:`BaseOrchestrator.start`.

        ``search_index_ready`` is awaited by :meth:`_dependency_orchestrator`
        before launching the JSONL watcher, since the watcher writes new
        items into the global Tantivy index as they arrive.
        """
        self.search_index_ready = search_index_ready

        # Reset stateful events in case this is a hot-restart (provider was
        # toggled off via Settings, then back on). ``shutdown()`` set them
        # all so the previous run's awaiters could finish; without this
        # reset, the new tasks would observe the leftover ``set()`` and
        # exit on the first ``is_set()`` check.
        self._sync_stop_event.clear()
        self.initial_sync_done.clear()
        self.compute_done.clear()

        # Register the TwiCC marketplace and (re)install the plugin before
        # the commands sync task starts polling for skills. The call is
        # idempotent and best-effort — failures are logged inside.
        await ensure_twicc_plugin_installed()

        self._sync_task = self._create_task(self._initial_sync_task())
        self._orch_task = self._create_task(self._dependency_orchestrator())
        self._auth_check_task = self._create_task(start_auth_task())
        self._usage_sync_task = self._create_task(start_usage_sync_task())
        self._statuspage_task = self._create_task(start_statuspage_task())
        self._commands_task = self._create_task(start_commands_task())
        self._original_files_cache_task = self._create_task(
            start_original_files_cache_cleanup()
        )

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

        if self._commands_task is not None:
            logger.info("Stopping Codex commands task...")
            stop_commands_task()
            await _cancel_task(self._commands_task, "Codex commands task")

        # Original files cache cleanup
        if self._original_files_cache_task is not None:
            stop_original_files_cache_cleanup()
            await _cancel_task(
                self._original_files_cache_task,
                "Codex original files cache cleanup",
            )

        # Stop every live Codex agent. The manager itself is owned by the
        # AgentManagerRegistry singleton, so we just ask it to drain — its
        # internal timeout monitor, agent registry and locks are reset to a
        # fresh state, mirroring the shutdown happening on the Claude side.
        logger.info("Stopping Codex agent manager...")
        await get_codex_agent_manager().shutdown(timeout=5.0)
        logger.info("Codex agent manager stopped")

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

    async def _sync_titles_at_boot(self) -> None:
        """Import Codex Thread.name into Session.title for every known thread.

        Runs once between the initial JSONL sync and the background compute.
        For each Codex Session whose title differs from Thread.name (and
        Thread.name is non-empty), we update the row and broadcast a
        session_updated event so clients connected during the boot window
        see the new title without a full reload.
        """
        from twicc.providers.codex.titles import bulk_sync_titles_from_codex
        from twicc.providers.sessions_watcher import broadcast_message
        from twicc.core.serializers import serialize_session

        titles = await bulk_sync_titles_from_codex()
        if not titles:
            logger.info("Codex title sync at boot: no titles to import")
            return

        # Pull only Codex sessions whose id is in the fetched map and whose
        # current title differs. bulk_update keeps it one SQL round-trip.
        def _apply() -> list[Session]:
            sessions = list(
                Session.objects.filter(
                    provider=Provider.CODEX,
                    id__in=titles.keys(),
                )
            )
            changed: list[Session] = []
            for s in sessions:
                new_title = titles.get(s.id)
                if new_title and s.title != new_title:
                    s.title = new_title
                    changed.append(s)
            if changed:
                Session.objects.bulk_update(changed, ["title"])
            return changed

        changed = await sync_to_async(_apply)()
        logger.info(
            "Codex title sync at boot: %d/%d titles imported",
            len(changed), len(titles),
        )

        if changed:
            channel_layer = get_channel_layer()
            for s in changed:
                # No refresh_from_db needed — title was just set on the instance.
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(s),
                })
                await asyncio.sleep(0)

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start the background compute and
        the JSONL watcher. Also imports Codex Thread names into Session titles
        before compute begins, so clients see accurate titles from the first load.

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

        # Pull thread names from the Codex state DB *before* the compute
        # task runs so newly-imported titles appear at the same time as
        # the rest of the initial UI state.
        try:
            await self._sync_titles_at_boot()
        except Exception as e:
            logger.warning("Codex title sync at boot failed: %s", e)

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
