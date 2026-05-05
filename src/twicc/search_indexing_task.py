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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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

    for session_id in sessions_to_index:
        # Check stop signal between sessions
        if _stop_event is not None and _stop_event.is_set():
            logger.info("Search index task: stop signal received, aborting after %d/%d sessions", indexed_count, total)
            break

        try:
            await _index_session(session_id)
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

    elapsed = time.monotonic() - start_time
    logger.info("Search index task completed: %d/%d sessions indexed in %.1fs", indexed_count, total, elapsed)
    await broadcast_startup_progress("search_index", indexed_count, total, completed=True)


async def _index_session(session_id: str):
    """Index the session title plus every indexable message via the owning provider.

    The text extraction (parsing ``item.content``, picking out role and
    body) is provider-specific and lives in
    ``BaseProviderHelpers.get_indexable_messages``. This function only
    wires Tantivy I/O around it.
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

    # Update session search_version
    session.search_version = settings.CURRENT_SEARCH_VERSION
    await sync_to_async(session.save)(update_fields=["search_version"])


def stop_search_index_task():
    """Signal the search index task to stop."""
    if _stop_event is not None:
        _stop_event.set()
