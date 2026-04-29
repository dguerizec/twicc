"""
Background search indexing task.

Indexes session messages into the Tantivy search index at startup.
Sessions with an outdated or missing search_version are re-indexed.
New sessions indexed by the watcher get search_version set inline,
so they don't need background reprocessing.

Architecture:
- Runs as an async task in the main event loop
- Uses asyncio.to_thread() for Tantivy calls (which are CPU/IO bound)
- Uses sync_to_async for Django ORM reads
- Processes one session at a time, committing after each
"""

from __future__ import annotations

import asyncio
import logging
import time

import orjson
from asgiref.sync import sync_to_async
from django.conf import settings

from twicc import search
from .compute import get_message_content
from twicc.core.enums import ItemKind
from twicc.core.models import Session, SessionType
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

    Queries all sessions needing indexing (search_version != CURRENT_SEARCH_VERSION),
    reads their user/assistant message items, extracts text, and indexes them
    into the Tantivy search index.

    Progress is broadcast via WebSocket for the frontend startup progress display.
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
    # Find sessions needing indexing
    sessions_to_index = await sync_to_async(
        lambda: list(
            Session.objects.filter(type=SessionType.SESSION)
            .exclude(search_version=settings.CURRENT_SEARCH_VERSION)
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
    """Index all user/assistant messages and the session title for a single session."""
    # Load session and its message items
    session = await sync_to_async(Session.objects.get)(id=session_id)

    items = await sync_to_async(
        lambda: list(
            session.items.filter(
                kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
            ).order_by("line_num")
            .values_list("content", "kind", "line_num", "timestamp", named=True)
        )
    )()

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

    # Index each message item
    for item in items:
        try:
            parsed = orjson.loads(item.content)
        except (orjson.JSONDecodeError, TypeError):
            continue

        content = get_message_content(parsed)
        text = search.extract_indexable_text(content)

        if text:
            from_role = "user" if item.kind == ItemKind.USER_MESSAGE else "assistant"
            await asyncio.to_thread(
                search.index_document,
                session.id,
                session.project_id,
                item.line_num,
                text,
                from_role,
                item.timestamp,
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
