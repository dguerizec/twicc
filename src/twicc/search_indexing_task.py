"""
Cross-provider startup search-indexing task.

Walks every session whose ``search_version`` is outdated and feeds it
into the global Tantivy index. The extraction of indexable text from
``SessionItem.content`` is provider-specific and therefore delegated to
``BaseProviderHelpers.get_indexable_messages`` — exactly the same path
used by ``twicc.search.reindex_session`` for live re-indexes. Sessions
are processed in ``-mtime`` order so the user's most recent activity
becomes searchable first.

Lifecycle:
- Started by the CLI after every provider's ``compute_done`` event has
  fired (see ``twicc.cli.run``).
- Runs as an async task in the main event loop.
- Uses ``asyncio.to_thread`` for Tantivy calls (CPU/IO bound).
- Uses ``sync_to_async`` for Django ORM reads.
- Processes one session at a time, committing after each.

Hot-toggle re-trigger:
- When a provider is enabled at runtime via Settings, its historical
  sessions land in DB with ``search_version=NULL`` and would otherwise
  stay invisible to ``twicc search`` until the next process restart
  (the live JSONL watcher only indexes brand-new file writes, not the
  rows the initial sync just bulk-inserted).
- :func:`kick_off_search_indexing` is the entry point used by the
  orchestrator registry after a successful hot ``start_one`` +
  ``compute_done`` to schedule another pass. Runs are serialized through
  :data:`_indexing_run_lock` so two overlapping triggers can't race on
  the same session; every active task is exposed via
  :func:`get_active_indexing_tasks` so the CLI shutdown path can cancel
  whatever happens to be running (or queued).
"""

from __future__ import annotations

import asyncio
import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc import search
from twicc.core.enums import ItemKind
from twicc.core.models import Session, SessionType
from twicc.providers.helpers import get_provider_helpers
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_stop_event: asyncio.Event | None = None

# Serializes overlapping runs of the indexing task (boot + hot-toggle, or
# two back-to-back hot-toggles): a second call waits for the first to
# finish then starts another sweep. Lazily created on first use so the
# event loop is always the one currently running.
_indexing_run_lock: asyncio.Lock | None = None

# Every indexing task spawned via ``kick_off_search_indexing`` (and the
# boot pass) lands here. Done tasks are pruned lazily on each new kick.
# The CLI shutdown path iterates the survivors so it can cancel both an
# active run (boot pass still mid-sweep when a hot-toggle arrives) and
# every pending re-trigger queued behind it on the lock.
_indexing_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# Search-version mark batching
# ---------------------------------------------------------------------------


class _MarkIndexedBuffer:
    """Batch ``Session.search_version`` updates of just-indexed sessions.

    The indexing sweep used to do one ``session.save(update_fields=[...])``
    per session, which was the last high-frequency direct DB writer left in
    the runtime loop (every other big producer goes through the DB writer
    now). This buffer accumulates the session ids and submits one
    :class:`_MarkSessionsIndexedJob` per batch — either when ``MAX_SIZE``
    is reached, or after ``MAX_AGE_SECONDS`` of inactivity for the tail
    that may not fill an entire batch.

    The submission is fire-and-forget for throughput (we don't want a slow
    apply to gate the next ``_index_session``), but every in-flight job's
    Future is kept on :attr:`_pending_futures` so :meth:`drain` can await
    them all before the sweep is declared complete. Both the happy path
    end of :func:`_run_indexing` and the cancellation ``finally`` route
    through :meth:`drain` — Tantivy commit and DB mark already form a
    non-atomic pair (a crash between them re-indexes the session at next
    boot, see migration ``0066_backfill_search_version``), and the batch
    widens that window by at most ``MAX_SIZE`` sessions or ``MAX_AGE_SECONDS``.
    """

    MAX_SIZE = 50
    MAX_AGE_SECONDS = 2.0

    def __init__(self) -> None:
        self._items: list[str] = []
        self._flush_task: asyncio.Task | None = None
        self._pending_futures: list[asyncio.Future] = []

    async def add(self, session_id: str) -> None:
        """Queue a session id for the next mark batch."""
        self._items.append(session_id)
        if len(self._items) >= self.MAX_SIZE:
            await self._flush_now()
        elif self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        """Flush the buffer after ``MAX_AGE_SECONDS`` of inactivity."""
        try:
            await asyncio.sleep(self.MAX_AGE_SECONDS)
        except asyncio.CancelledError:
            return
        if self._items:
            await self._flush_now()

    async def _flush_now(self) -> None:
        """Submit the current batch as one :class:`_MarkSessionsIndexedJob`.

        Cancels the inactivity timer (if any) so a delayed flush can't
        double-submit after a size-triggered flush already drained the
        buffer.
        """
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None
        if not self._items:
            return
        batch = self._items
        self._items = []
        # Lazy import: avoids a top-level dependency on ``db_writer`` (which
        # imports Django models) at module import time.
        from twicc.providers.db_writer import _MarkSessionsIndexedJob, submit_async_job

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await submit_async_job(_MarkSessionsIndexedJob(
            session_ids=batch,
            future=future,
            provider=None,
        ))
        self._pending_futures.append(future)

    async def drain(self) -> None:
        """Flush the buffer and await every in-flight mark apply.

        Called by :func:`_run_indexing` on its happy path (after the
        loop completes) AND from its ``finally`` block on
        :class:`asyncio.CancelledError` raised by the CLI shutdown path
        (``run.py:_run_until_signal`` cancels every active indexing task
        through :func:`get_active_indexing_tasks`). Either way the goal
        is the same: nothing that already hit Tantivy should stay marked
        ``search_version=NULL`` in DB just because the loop stopped
        between ``_index_session`` and the next implicit flush.

        ``return_exceptions=True`` so a single apply failure on the last
        batch doesn't strand the others — failures are already logged
        by :func:`_settle_async_job` on the DB writer side.
        """
        await self._flush_now()
        if self._pending_futures:
            await asyncio.gather(*self._pending_futures, return_exceptions=True)
            self._pending_futures.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_active_indexing_tasks() -> list[asyncio.Task]:
    """Return every indexing task currently scheduled or running.

    Used by the CLI shutdown path so it can cancel both an active sweep
    and any hot-toggle re-trigger queued behind it on the run lock.
    Filters out already-finished tasks: callers don't need to know
    about them, and the bookkeeping list is pruned lazily on each new
    kick anyway.
    """
    return [t for t in _indexing_tasks if not t.done()]


async def kick_off_search_indexing() -> None:
    """Schedule a (re)run of the indexing task, serialized via a lock.

    Safe to call repeatedly: if a previous run is still in progress, the
    new task waits on :data:`_indexing_run_lock` before starting another
    sweep. The DB filter ``exclude(search_version=CURRENT_SEARCH_VERSION)``
    naturally scopes the sweep to sessions that actually need it, so a
    re-trigger that finds nothing left to do is cheap.

    The new task handle is appended to :data:`_indexing_tasks` so the
    CLI shutdown path can cancel every active run (boot pass + queued
    hot-toggle re-triggers). Already-done tasks are pruned here to
    keep the list bounded across long-lived processes.
    """
    global _indexing_run_lock
    if _indexing_run_lock is None:
        _indexing_run_lock = asyncio.Lock()

    async def _runner() -> None:
        assert _indexing_run_lock is not None
        async with _indexing_run_lock:
            await start_search_index_task()

    # Prune done tasks before appending so the list doesn't grow without
    # bound (each restart of the indexing task adds an entry).
    _indexing_tasks[:] = [t for t in _indexing_tasks if not t.done()]
    _indexing_tasks.append(asyncio.create_task(_runner()))


async def start_search_index_task():
    """Background task to index sessions at startup.

    Queries all sessions needing indexing (``search_version != CURRENT_SEARCH_VERSION``),
    routes their content extraction through the owning provider's
    helpers, and indexes the resulting messages into Tantivy.

    Progress is broadcast via WebSocket for the frontend startup
    progress display under the global ``search_index`` phase
    (``provider=None``).
    """
    global _stop_event
    _stop_event = asyncio.Event()

    try:
        await _run_indexing()
    except asyncio.CancelledError:
        logger.info("Search index task cancelled")
        return
    except Exception:
        logger.exception("Search index task failed with unexpected error")


async def _run_indexing():
    """Core indexing loop — separated for clean exception handling."""
    # Find sessions needing indexing — recent first so the user can
    # search through what they were just working on while older
    # sessions catch up in the background.
    sessions_to_index = await sync_to_async(
        lambda: list(
            Session.objects.filter(type=SessionType.SESSION)
            .exclude(search_version=settings.CURRENT_SEARCH_VERSION)
            .order_by("-mtime")
            .values_list("id", flat=True)
        )
    )()

    total = len(sessions_to_index)

    if total == 0:
        logger.info("Search index: no sessions to index")
        # Report the total session count so the frontend can show "N/N" instead of "0/0"
        total_sessions = await sync_to_async(
            Session.objects.filter(type=SessionType.SESSION).count
        )()
        await broadcast_startup_progress("search_index", total_sessions, total_sessions, completed=True)
        return

    logger.info("Search index: %d sessions to index", total)
    await broadcast_startup_progress("search_index", 0, total)

    start_time = time.monotonic()
    indexed_count = 0
    last_logged_count = 0
    buffer = _MarkIndexedBuffer()

    try:
        for session_id in sessions_to_index:
            # Check stop signal between sessions
            if _stop_event is not None and _stop_event.is_set():
                logger.info("Search index task: stop signal received, aborting after %d/%d sessions", indexed_count, total)
                break

            try:
                await _index_session(session_id, buffer)
                indexed_count += 1

                # Broadcast progress and log every 50 sessions
                await broadcast_startup_progress("search_index", indexed_count, total)

                if indexed_count - last_logged_count >= 50:
                    elapsed = time.monotonic() - start_time
                    logger.info(
                        "Search index progress: %d/%d sessions (%.1fs elapsed)",
                        indexed_count,
                        total,
                        elapsed,
                    )
                    last_logged_count = indexed_count

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Search index: error indexing session %s, skipping", session_id)
    finally:
        # Happy path and cancellation both route through here so the tail
        # batch (the < MAX_SIZE sessions queued after the last size-triggered
        # flush) hits the DB before the sweep is declared done. If the CLI
        # shutdown cancels us mid-drain we accept the loss — those sessions
        # remain ``search_version=NULL`` and get re-indexed at next boot,
        # the same baseline the batching widens by at most MAX_SIZE rows.
        try:
            await buffer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Search index: failed to drain mark buffer")

    elapsed = time.monotonic() - start_time
    logger.info("Search index task completed: %d/%d sessions indexed in %.1fs", indexed_count, total, elapsed)
    await broadcast_startup_progress("search_index", indexed_count, total, completed=True)


async def _index_session(session_id: str, buffer: _MarkIndexedBuffer):
    """Index the session title plus every indexable message via the owning provider.

    The text extraction (parsing ``item.content``, picking out role and
    body) is provider-specific and lives in
    ``BaseProviderHelpers.get_indexable_messages``. This function only
    wires Tantivy I/O around it.

    The post-indexing ``search_version`` mark is appended to ``buffer``
    rather than written through a direct ``session.save()`` so the entire
    sweep collapses into ``ceil(N / MAX_SIZE)`` batched DB writes routed
    via the DB writer instead of one direct write per session.
    """
    session = await sync_to_async(Session.objects.get)(id=session_id)

    items = await sync_to_async(
        lambda: list(
            session.items.filter(
                kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
            ).order_by("line_num")
        )
    )()

    helpers = get_provider_helpers(session.provider)

    # Delete existing documents for this session (re-indexing case)
    await asyncio.to_thread(search.delete_session_documents, session.id)

    # Index session title (if any)
    if session.title:
        await asyncio.to_thread(
            search.index_document,
            session.id,
            session.project_id,
            0,  # line_num 0 = title document
            session.title,
            "title",
            session.created_at,
            session.archived,
        )

    # Provider extracts the text + role; we just pipe it into the index.
    for msg in helpers.get_indexable_messages(items):
        await asyncio.to_thread(
            search.index_document,
            session.id,
            session.project_id,
            msg.line_num,
            msg.text,
            msg.from_role,
            msg.timestamp,
            session.archived,
        )

    # Commit after each session
    await asyncio.to_thread(search.commit)

    # Mark the session as indexed via the DB writer (batched).
    await buffer.add(session.id)


def stop_search_index_task():
    """Signal the search index task to stop."""
    if _stop_event is not None:
        _stop_event.set()
