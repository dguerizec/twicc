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

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionItem

logger = logging.getLogger(__name__)


# =============================================================================
# Initial-sync payloads
# =============================================================================
#
# These NamedTuples are pushed by per-provider initial-sync producers (running
# in their ``asyncio.to_thread`` worker) onto a ``queue.Queue``, then drained
# by the cross-provider unified consumer in ``background_compute_task.py``. They
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


class MarkSessionsStalePayload(NamedTuple):
    """Producer wants to flag a set of sessions as stale (no longer on disk).

    Used by initial sync at two points:

    - Per-project at the end of ``sync_project`` once it has scanned the disk
      and seen which DB sessions have no matching file.
    - Per-subagent inside ``_sync_session_subagents`` when a DB subagent has
      no matching file under the session's ``subagents/`` folder.

    The consumer issues a single ``Session.objects.filter(id__in=..., stale=False).update(stale=True)``.
    """

    provider: Provider
    session_ids: list[str]


class UpdateProjectMetadataPayload(NamedTuple):
    """Producer has computed end-of-sync metadata for one project.

    Each non-``None`` field is applied; ``None`` means "leave that field
    alone". Two boolean flags ask the consumer to invoke heavier helpers
    that themselves write to DB:

    - ``recalc_total_cost``: the consumer calls
      :func:`twicc.projects.update_project_total_cost` after the
      ``project.save`` (re-aggregates all sessions' costs).
    - ``resolve_git_root`` (with optional ``git_root_directory`` override):
      the consumer calls :func:`twicc.projects.ensure_project_git_root`
      which resolves and persists ``project.git_root`` if missing.

    Bundling them into a single payload keeps the project's
    end-of-sync writes in one transaction.
    """

    provider: Provider
    project_id: str
    new_sessions_count: int | None
    new_mtime: float | None
    new_stale: bool | None
    recalc_total_cost: bool
    resolve_git_root: bool
    git_root_directory: str | None


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed by a producer when it has finished pushing payloads.

    The unified consumer finalises the matching entry (unblocking the
    producer task that was awaiting completion) and removes it from the
    registry. Other registered providers continue to be drained.
    """

    provider: Provider


class SessionItemsToInsert(NamedTuple):
    """Result of parsing a session's JSONL file in the producer.

    The producer turns this into a :class:`CreateSessionPayload` or
    :class:`UpdateSessionPayload` (with ``items``, ``last_offset``,
    ``last_line``, ``mtime`` carried over) before pushing it onto the
    consumer queue. ``actually_new_count`` lets the caller decide whether
    to flag ``reset_compute_version`` on the payload.

    Producers receive ``None`` from :func:`read_session_items_from_file`
    when the file has not changed since last sync — nothing to push.
    """

    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    actually_new_count: int


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


def read_session_items_from_file(
    session: Session, file_path: Path
) -> SessionItemsToInsert | None:
    """
    Read new lines from a JSONL file and return what should be inserted.

    Reads-only — performs the filesystem I/O and the ``pre_existing`` count
    (a DB read, safe under WAL) but does no writes. The caller (a producer
    inside ``asyncio.to_thread``) turns the result into a
    :class:`CreateSessionPayload` or :class:`UpdateSessionPayload` and pushes
    it onto the unified consumer queue.

    Returns ``None`` when there is nothing to do (file missing, unchanged
    since last sync).
    """
    if not file_path.exists():
        return None

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed and file hasn't grown beyond last_offset, nothing to do.
    # Check file size too: mtime has ~1s resolution, so two writes within the same second
    # share the same mtime. Without the size check, the second write would be silently skipped.
    if session.mtime == file_mtime and session.last_offset >= stat.st_size:
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        f.seek(session.last_offset)
        new_content = f.read()
        last_offset = f.tell()

    if not new_content:
        # File was touched but no new content — caller still needs to persist
        # the new mtime (and the offset is unchanged).
        return SessionItemsToInsert(
            items=[],
            last_offset=last_offset,
            last_line=session.last_line,
            mtime=file_mtime,
            actually_new_count=0,
        )

    # Split into lines (filter out empty lines)
    lines = [line for line in new_content.split("\n") if line.strip()]

    if not lines:
        return SessionItemsToInsert(
            items=[],
            last_offset=last_offset,
            last_line=session.last_line,
            mtime=file_mtime,
            actually_new_count=0,
        )

    # Build (line_num, content) tuples; the consumer creates the SessionItem
    # rows so that ``session`` references are resolved on the writer side
    # (matters for the create-session case where the row is being saved
    # inside the consumer's transaction).
    current_line_num = session.last_line
    items: list[tuple[int, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            line = "{}"
        current_line_num += 1
        items.append((current_line_num, line))

    # Count how many of the new line_nums already exist in DB. Happens when
    # the watcher inserted items in a previous run but the session's
    # tracking fields weren't persisted before shutdown. The consumer's
    # ``ignore_conflicts=True`` skips duplicates silently — the count here
    # lets the caller report accurate stats and decide whether to flag
    # ``reset_compute_version``.
    first_new_line = session.last_line + 1
    pre_existing = SessionItem.objects.filter(
        session=session,
        line_num__gte=first_new_line,
        line_num__lte=current_line_num,
    ).count()
    actually_new_count = len(items) - pre_existing

    return SessionItemsToInsert(
        items=items,
        last_offset=last_offset,
        last_line=current_line_num,
        mtime=file_mtime,
        actually_new_count=actually_new_count,
    )
