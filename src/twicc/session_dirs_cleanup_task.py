"""Daily janitor for empty per-session ``artifacts``/``scratch`` directories.

TwiCC pre-creates ``<data_dir>/artifacts/<session_id>/`` and
``<data_dir>/scratch/<session_id>/`` at agent start/resume
(:meth:`twicc.agent.base_agent.BaseAgent._resolve_and_create_work_dirs`). Most
sessions never write anything into them, so over time the two roots accumulate
empty directories. This task prunes them once a day.

A directory is removed only when it is **empty** *and* stale:

- For a directory whose name matches a known :class:`~twicc.core.models.Session`,
  "stale" means the most recent of ``last_updated_at`` / ``last_started_at`` /
  ``created_at`` is older than :data:`STALE_SESSION_DIR_AGE`. ``last_started_at``
  is included on purpose: a session resumed today but with no new content yet
  has an old ``last_updated_at``, and we must not prune its freshly (re)used
  directory out from under a running agent.
- For an *orphan* directory (no matching session row — a session never synced,
  one whose JSONL was removed, a leftover test dir, ...), the directory's own
  filesystem mtime is the age reference.

Pruning is **non-destructive**: the directory is recreated on the session's next
start/resume. Removal goes through :func:`os.rmdir`, which only removes empty
directories — a hard safety net against a race where a file appears between the
emptiness check and the removal.

The whole cycle (filesystem scan + one read-only ``Session`` query + the
``rmdir`` calls) is blocking, so it runs on a worker thread via
:func:`asyncio.to_thread`. It performs **no DB writes**, so nothing is routed
through the DB writer.

Gated by ``settings.SESSION_DIRS_CLEANUP_ENABLED`` (env
``TWICC_NO_SESSION_DIRS_CLEANUP``): devctl sets the flag in worktree mode, where
``artifacts/`` and ``scratch/`` are symlinks shared with the main instance —
only the main instance owns the cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# Run once a day. The loop sleeps before its first pass (like
# ``pricing_task.start_price_sync_task``) so the initial JSONL sync has settled
# and every active session has a fresh ``last_started_at`` before we ever prune.
SESSION_DIRS_CLEANUP_INTERVAL = 24 * 60 * 60

# A session directory must be untouched for at least this long before it is
# eligible for removal.
STALE_SESSION_DIR_AGE = timedelta(days=30)

# Cap each ``id__in`` batch well below SQLite's variable limit, so a long-lived
# instance with tens of thousands of session dirs never overflows the query.
_QUERY_CHUNK = 500


def _is_stale(reference: datetime | None, now: datetime) -> bool:
    """True iff ``reference`` is known and at least :data:`STALE_SESSION_DIR_AGE` old.

    A ``None`` reference (no usable timestamp at all) is treated as *not* stale:
    we never prune a directory we cannot date.
    """
    if reference is None:
        return False
    return now - reference >= STALE_SESSION_DIR_AGE


def _dir_mtime(path: Path) -> datetime | None:
    """Return ``path``'s filesystem mtime as an aware UTC datetime, or ``None``.

    ``None`` when the directory has vanished (or cannot be stat-ed) between the
    scan and this call — the caller then treats it as not stale and skips it.
    """
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=dt_timezone.utc)


def _remove_if_empty(path: Path) -> bool:
    """Remove ``path`` iff it is an empty directory. Return ``True`` on removal.

    Uses :func:`os.rmdir`, which raises on a non-empty directory — that is the
    intended safety net: if a file slipped in since the scan, the directory is
    left intact. A vanished or otherwise non-removable directory is a no-op.
    """
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def _load_references(session_ids: set[str]) -> dict[str, datetime | None]:
    """Map each known session id to its newest lifecycle timestamp (or ``None``).

    The reference is ``max(last_updated_at, last_started_at, created_at)`` over
    the non-null values; ``None`` when a row exists but carries no timestamp yet.
    Ids with no matching row are simply absent from the result (orphans). Read
    only; chunked to stay under SQLite's variable limit.
    """
    from twicc.core.models import Session

    references: dict[str, datetime | None] = {}
    ids = list(session_ids)
    for start in range(0, len(ids), _QUERY_CHUNK):
        batch = ids[start:start + _QUERY_CHUNK]
        for row in Session.objects.filter(id__in=batch).values(
            "id", "last_updated_at", "last_started_at", "created_at"
        ):
            known = [
                ts
                for ts in (row["last_updated_at"], row["last_started_at"], row["created_at"])
                if ts is not None
            ]
            references[row["id"]] = max(known) if known else None
    return references


def _prune_stale_session_dirs() -> tuple[int, int]:
    """Run one cleanup cycle. Return ``(artifacts_removed, scratch_removed)``.

    Synchronous: a filesystem scan, read-only ``Session`` queries, and the
    ``rmdir`` calls. Meant to run on a worker thread (no event loop), so the
    sync ORM access is safe.
    """
    from django.utils import timezone

    from twicc.paths import get_artifacts_dir, get_scratch_dir

    now = timezone.now()
    roots = (("artifacts", get_artifacts_dir()), ("scratch", get_scratch_dir()))

    # (root_label, session_id, path) for every per-session subdirectory under
    # both roots, so the timestamps for all of them resolve in one batched query.
    candidates: list[tuple[str, str, Path]] = []
    for label, root in roots:
        try:
            entries = list(os.scandir(root))
        except FileNotFoundError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                candidates.append((label, entry.name, Path(entry.path)))

    if not candidates:
        return (0, 0)

    references = _load_references({sid for _, sid, _ in candidates})

    removed = {"artifacts": 0, "scratch": 0}
    for label, session_id, path in candidates:
        if session_id in references:
            # Known session; fall back to mtime only if it has no timestamp yet.
            reference = references[session_id] or _dir_mtime(path)
        else:
            # Orphan directory: no matching session → use the dir's own mtime.
            reference = _dir_mtime(path)

        if _is_stale(reference, now) and _remove_if_empty(path):
            removed[label] += 1

    total = removed["artifacts"] + removed["scratch"]
    if total:
        logger.info(
            "Session dirs cleanup: pruned %d empty dir(s) (artifacts: %d, scratch: %d)",
            total, removed["artifacts"], removed["scratch"],
        )
    return (removed["artifacts"], removed["scratch"])


async def start_session_dirs_cleanup_task(stop_event: asyncio.Event) -> None:
    """Periodic janitor loop for empty per-session ``artifacts``/``scratch`` dirs.

    Sleeps :data:`SESSION_DIRS_CLEANUP_INTERVAL` seconds, then prunes, repeating
    until ``stop_event`` is set (the shared shutdown event). No-op — logs and
    returns — when ``settings.SESSION_DIRS_CLEANUP_ENABLED`` is false.
    """
    from django.conf import settings

    if not settings.SESSION_DIRS_CLEANUP_ENABLED:
        logger.info("Session dirs cleanup disabled (TWICC_NO_SESSION_DIRS_CLEANUP is set)")
        return

    logger.info("Session dirs cleanup task started")
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SESSION_DIRS_CLEANUP_INTERVAL)
            except asyncio.TimeoutError:
                # Timeout means it's time to prune again.
                pass
            else:
                # stop_event fired — exit before another pass.
                break

            try:
                await asyncio.to_thread(_prune_stale_session_dirs)
            except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.exception("Session dirs cleanup cycle failed")
    finally:
        logger.info("Session dirs cleanup task stopped")
