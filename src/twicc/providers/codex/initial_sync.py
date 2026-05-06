"""
Synchronization logic for JSONL files from Codex sessions.

Walks :attr:`CodexHelpers.SESSIONS_DIR` (~/.codex/sessions/YYYY/MM/DD/),
groups files by project (resolved from the first JSONL line's
``payload.cwd``), and inserts new lines as raw :class:`SessionItem`
rows. Codex has no subagent concept, no compute task, and no watcher
yet — this module only owns the initial sync.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import orjson
from django.db.models import Max

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.paths import path_to_project_id
from twicc.sync_helpers import check_file_has_content, sync_session_items
from .helpers import CodexHelpers

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid Codex session file (rollout-*.jsonl)."""
    return path.suffix == ".jsonl" and path.name.startswith("rollout-")


class SessionMeta(NamedTuple):
    """First-line metadata extracted from a Codex JSONL file."""
    session_id: str
    cwd: str


def extract_session_meta(file_path: Path) -> SessionMeta | None:
    """
    Read the first line of a Codex JSONL file and pull session_id + cwd.

    Codex writes a ``session_meta`` event as the first line of every
    rollout file, with the session UUID at ``payload.id`` and the working
    directory at ``payload.cwd``. The session UUID is the canonical
    session id (the filename also contains it but we trust the payload).

    Returns ``None`` if the file is empty, unreadable, or missing either
    field — such files are skipped by the sync.
    """
    if not file_path.exists():
        return None

    try:
        with open(file_path, "rb") as f:
            first_line = f.readline()
    except OSError:
        return None

    if not first_line.strip():
        return None

    try:
        parsed = orjson.loads(first_line)
    except orjson.JSONDecodeError:
        return None

    payload = parsed.get("payload") if isinstance(parsed, dict) else None
    if not isinstance(payload, dict):
        return None

    session_id = payload.get("id")
    cwd = payload.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(cwd, str) or not cwd:
        return None

    return SessionMeta(session_id=session_id, cwd=cwd)


def scan_session_files() -> list[Path]:
    """
    Walk ``SESSIONS_DIR`` recursively and return every Codex session file.

    Codex stores files under ``YYYY/MM/DD/rollout-*.jsonl``. Returns an
    empty list if the directory doesn't exist yet (fresh install).
    """
    sessions_dir = CodexHelpers.SESSIONS_DIR
    if not sessions_dir.exists():
        return []

    return [
        path
        for path in sessions_dir.rglob("rollout-*.jsonl")
        if path.is_file() and is_session_file(path)
    ]


class _NewEntry(NamedTuple):
    """A disk file with no matching DB row yet (first line was parsed)."""
    file_path: Path
    session_id: str
    cwd: str


class _ExistingEntry(NamedTuple):
    """A disk file already represented by a DB session row."""
    file_path: Path
    session: Session


def sync_project(
    project_id: str,
    new_entries: list[_NewEntry],
    existing_entries: list[_ExistingEntry],
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize one project's Codex sessions.

    Both lists must belong to the same ``project_id``. Creates the
    :class:`Project` row lazily (with ``directory`` and ``stale`` set
    from the first new entry's ``cwd``) only when the project does not
    already exist and at least one new entry has content.
    """
    project_start = time.monotonic()

    stats = {
        "sessions_created": 0,
        "items_added": 0,
    }

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        project = None

    total_sessions = len(new_entries) + len(existing_entries)
    logger.info(f"  Syncing project {project_id} ({total_sessions} Codex sessions)")

    idx = 0

    for entry in existing_entries:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        new_line_nums = sync_session_items(entry.session, entry.file_path)
        stats["items_added"] += len(new_line_nums)

        update_fields: list[str] = []
        # The file is on disk (we just read it), so the session is not stale.
        if entry.session.stale:
            entry.session.stale = False
            update_fields.append("stale")
        # If new items were added, reset compute_version to trigger background recompute
        if new_line_nums and entry.session.compute_version is not None:
            entry.session.compute_version = None
            update_fields.append("compute_version")
        if update_fields:
            entry.session.save(update_fields=update_fields)

        if on_session_progress:
            on_session_progress(entry.session.id, idx, total_sessions)

    for entry in new_entries:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        # Defensive: extract_session_meta already proved the first line
        # is parseable, but the rest of the file might be empty after a
        # failed write — skip without creating a session row.
        if not check_file_has_content(entry.file_path):
            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            continue

        if project is None:
            project = Project.objects.create(
                id=project_id,
                directory=entry.cwd,
                stale=not os.path.isdir(entry.cwd),
            )
            stats["project_created"] = 1

        relative_path = entry.file_path.relative_to(CodexHelpers.SESSIONS_DIR)
        session = Session(
            id=entry.session_id,
            project=project,
            provider=Provider.CODEX,
            file_path=str(relative_path),
            type=SessionType.SESSION,
        )
        session.save()
        stats["sessions_created"] += 1

        new_line_nums = sync_session_items(session, entry.file_path)
        stats["items_added"] += len(new_line_nums)

        if on_session_progress:
            on_session_progress(entry.session_id, idx, total_sessions)

    if project is None:
        elapsed = time.monotonic() - project_start
        logger.info(
            f"  ⊘ Project {project_id} skipped in {elapsed:.1f}s — no Codex sessions with content"
        )
        return stats

    # ``sessions_count`` and ``mtime`` are cross-provider — recomputed
    # from every session of the project, not just the Codex ones we just
    # synced, so a project shared with Claude doesn't lose values the
    # other provider already wrote.
    project.sessions_count = Session.objects.filter(
        project=project,
        type=SessionType.SESSION,
        created_at__isnull=False,
        user_message_count__gt=0,
    ).count()
    project.mtime = (
        Session.objects.filter(project=project)
        .aggregate(max_mtime=Max("mtime"))["max_mtime"]
        or 0
    )
    project.save(update_fields=["sessions_count", "mtime"])

    elapsed = time.monotonic() - project_start
    logger.info(
        f"  ✓ Project {project_id} done in {elapsed:.1f}s — "
        f"{stats['items_added']} items, {stats['sessions_created']} new Codex sessions"
    )

    return stats


def sync_all(
    on_project_start: Callable[[str, int, int], None] | None = None,
    on_project_done: Callable[[str, dict[str, int]], None] | None = None,
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize all Codex sessions from :attr:`CodexHelpers.SESSIONS_DIR`.

    Args:
        on_project_start: Callback called before syncing a project
            with (project_id, current_index, total_projects).
        on_project_done: Callback called after syncing a project
            with (project_id, project_stats).
        on_session_progress: Callback passed to sync_project.
        stop_event: Optional threading event; when set, sync stops early.

    Returns aggregate statistics.
    """
    sync_start = time.monotonic()

    stats = {
        "projects_created": 0,
        "sessions_created": 0,
        "sessions_stale": 0,
        "items_added": 0,
    }

    sessions_dir = CodexHelpers.SESSIONS_DIR
    if not sessions_dir.exists():
        logger.info(f"Codex sessions dir not found: {sessions_dir}")
        return stats

    disk_files = scan_session_files()
    disk_files_by_relative_path = {
        str(p.relative_to(sessions_dir)): p for p in disk_files
    }

    # Match disk files to existing DB rows by ``file_path`` (unique in DB),
    # so we don't have to read the first JSON line for sessions we already
    # know about. Only brand-new files require ``extract_session_meta``.
    db_sessions_by_path = {
        s.file_path: s
        for s in Session.objects.filter(
            provider=Provider.CODEX, type=SessionType.SESSION
        )
    }

    new_by_project: dict[str, list[_NewEntry]] = {}
    existing_by_project: dict[str, list[_ExistingEntry]] = {}

    for rel_path, file_path in disk_files_by_relative_path.items():
        if rel_path in db_sessions_by_path:
            sess = db_sessions_by_path[rel_path]
            existing_by_project.setdefault(sess.project_id, []).append(
                _ExistingEntry(file_path=file_path, session=sess)
            )
        else:
            meta = extract_session_meta(file_path)
            if meta is None:
                continue
            project_id = path_to_project_id(meta.cwd)
            new_by_project.setdefault(project_id, []).append(
                _NewEntry(
                    file_path=file_path,
                    session_id=meta.session_id,
                    cwd=meta.cwd,
                )
            )

    all_project_ids = sorted(set(new_by_project) | set(existing_by_project))
    total_projects = len(all_project_ids)

    logger.info(
        f"Codex sync started — {total_projects} projects, "
        f"{len(disk_files_by_relative_path)} session files"
    )

    for idx, project_id in enumerate(all_project_ids, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "Codex sync interrupted (after %d/%d projects)",
                idx - 1, total_projects,
            )
            break

        if on_project_start:
            on_project_start(project_id, idx, total_projects)

        project_stats = sync_project(
            project_id,
            new_entries=new_by_project.get(project_id, []),
            existing_entries=existing_by_project.get(project_id, []),
            on_session_progress=on_session_progress,
            stop_event=stop_event,
        )

        stats["projects_created"] += project_stats.get("project_created", 0)
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    interrupted = stop_event is not None and stop_event.is_set()

    if not interrupted:
        stale_paths = (
            set(db_sessions_by_path.keys()) - set(disk_files_by_relative_path.keys())
        )
        if stale_paths:
            updated = Session.objects.filter(
                provider=Provider.CODEX,
                file_path__in=stale_paths,
                stale=False,
            ).update(stale=True)
            stats["sessions_stale"] = updated

    elapsed = time.monotonic() - sync_start
    if interrupted:
        logger.info(
            f"⚠ Codex sync interrupted after {elapsed:.1f}s — "
            f"{stats['sessions_created']} sessions created, "
            f"{stats['items_added']} items added"
        )
    else:
        logger.info(
            f"✓ Codex sync complete in {elapsed:.1f}s — "
            f"{stats['sessions_created']} sessions created, "
            f"{stats['items_added']} items added"
        )

    return stats
