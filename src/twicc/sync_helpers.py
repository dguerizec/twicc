"""
Cross-provider JSONL sync helpers.

Each provider stores session content as an append-only JSONL file. The
helpers in this module are the provider-agnostic plumbing every initial
sync needs: a content probe before creating an empty session, and an
incremental reader that bulk-inserts new lines as raw
:class:`~twicc.core.models.SessionItem` rows. Metadata computation
(``kind``, ``display_level``, costs, ...) stays in each provider's own
compute path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionItem

logger = logging.getLogger(__name__)


# =============================================================================
# Initial-sync payloads
# =============================================================================
#
# These NamedTuples are pushed by per-provider initial-sync producers (running
# in their ``asyncio.to_thread`` worker) onto a ``queue.Queue``, then drained
# by the cross-provider unified consumer in ``background_task.py``. They
# describe a self-contained unit of DB work the producer has prepared but
# intentionally not executed yet, so all writes happen in a single serialised
# coroutine and never race with another provider's writes.


class CreateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session that does not exist in DB.

    The consumer creates the ``Project`` (idempotent — no-op if it exists),
    saves the unsaved ``Session`` instance, bulk-creates the items, then
    persists the tracking fields. ``new_project_directory`` may be ``None``
    (Claude Code stores the cwd inside the JSONL body, so initial sync
    cannot pass it; it gets filled in later by the background compute).
    """

    provider: Provider
    project_id: str
    new_project_directory: str | None
    new_project_stale: bool
    session: Session
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float


class UpdateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session that already exists in DB.

    The consumer appends the new items (``ignore_conflicts=True`` covers the
    rare watcher-already-inserted-them race), persists the tracking fields,
    optionally clears the ``stale`` flag, and optionally resets
    ``compute_version`` to ``None`` so background compute will re-process the
    session.
    """

    provider: Provider
    session: Session
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    reset_compute_version: bool
    clear_stale: bool


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed by a producer when it has finished pushing payloads.

    The unified consumer finalises the matching entry (unblocking the
    producer task that was awaiting completion) and removes it from the
    registry. Other registered providers continue to be drained.
    """

    provider: Provider


def check_file_has_content(file_path: Path) -> bool:
    """
    Check if a JSONL file has any valid lines (non-empty, non-whitespace).

    This function performs no database operations and is used to determine
    if a session should be created before saving it.
    """
    if not file_path.exists():
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return True
    return False


@transaction.atomic
def sync_session_items(session: Session, file_path: Path) -> list[int]:
    """
    Read new lines from a JSONL file and insert them as raw SessionItem rows.

    No JSON parsing is done. All metadata computation is deferred to the
    provider's own compute path (background task or watcher).

    Used by initial sync where speed matters and metadata will be computed
    in background anyway.

    Wrapped in ``transaction.atomic`` so the pre-existing count, the
    ``SessionItem`` bulk_create, and the tracking-field save all share a
    single write-lock acquisition and one fsync per session.

    Args:
        session: The session (must already be saved to the database)
        file_path: Path to the JSONL file

    Returns:
        List of line_nums of new items added
    """
    if not file_path.exists():
        return []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed and file hasn't grown beyond last_offset, nothing to do.
    # Check file size too: mtime has ~1s resolution, so two writes within the same second
    # share the same mtime. Without the size check, the second write would be silently skipped.
    if session.mtime == file_mtime and session.last_offset >= stat.st_size:
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        # Seek to last known position
        f.seek(session.last_offset)

        # Read remaining content
        new_content = f.read()
        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return []

        # Split into lines (filter out empty lines)
        lines = [line for line in new_content.split("\n") if line.strip()]

        actually_new_count = 0

        if lines:
            # Create SessionItem objects for bulk insert (raw content only)
            current_line_num = session.last_line
            items_to_create: list[SessionItem] = []

            for line in lines:
                line = line.strip()
                if not line:
                    line = "{}"
                current_line_num += 1
                items_to_create.append(SessionItem(
                    session=session,
                    line_num=current_line_num,
                    content=line,
                ))

            # Check how many of these line_nums already exist in the DB.
            # This can happen when the watcher already inserted items (during a previous run)
            # but the session's tracking fields (last_line, last_offset, mtime) weren't saved
            # before shutdown — leaving the session state stale while items exist in the DB.
            # bulk_create(ignore_conflicts=True) silently skips duplicates, so we can't rely
            # on items_to_create to know how many were actually inserted.
            first_new_line = session.last_line + 1
            pre_existing = SessionItem.objects.filter(
                session=session,
                line_num__gte=first_new_line,
                line_num__lte=current_line_num,
            ).count()
            actually_new_count = len(items_to_create) - pre_existing

            # Bulk create all items (silently skips items that already exist)
            SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True, batch_size=50)

            # Update session tracking fields
            session.last_line = current_line_num

        # Update offset to end of file
        session.last_offset = f.tell()
        session.mtime = file_mtime
        session.save(update_fields=["last_offset", "last_line", "mtime"])

    # Return only truly new line_nums (the last actually_new_count items,
    # since pre-existing items occupy the lower line_nums in the range)
    if actually_new_count > 0:
        return [item.line_num for item in items_to_create[-actually_new_count:]]
    return []
