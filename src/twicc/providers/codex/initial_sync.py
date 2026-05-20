"""
Synchronization logic for JSONL files from Codex sessions.

Walks :attr:`CodexHelpers.SESSIONS_DIR` (~/.codex/sessions/YYYY/MM/DD/),
groups files by project (resolved from the first JSONL line's
``payload.cwd``), and pushes initial-sync payloads onto the unified
consumer queue. Producer-side reads only — every DB write is delegated to
the unified consumer via ``CreateSessionPayload`` / ``UpdateSessionPayload`` /
``MarkSessionsStalePayload`` / ``UpdateProjectMetadataPayload``.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import orjson
from django.db.models import Max

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.paths import path_to_project_id
from twicc.providers.db_writer import (
    CreateSessionPayload,
    MarkSessionsStalePayload,
    UpdateProjectMetadataPayload,
    UpdateSessionPayload,
)
from twicc.sync_helpers import check_file_has_content, read_session_items_from_file
from .helpers import CodexHelpers

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid Codex session file (rollout-*.jsonl)."""
    return path.suffix == ".jsonl" and path.name.startswith("rollout-")


class SessionMeta(NamedTuple):
    """First-line metadata extracted from a Codex JSONL file.

    ``parent_session_id`` is set when the file is a subagent rollout
    (Codex marks the spawn through ``payload.source.subagent.thread_spawn``);
    ``None`` for top-level sessions.
    """
    session_id: str
    cwd: str
    parent_session_id: str | None = None


def extract_session_meta(file_path: Path) -> SessionMeta | None:
    """
    Read the first line of a Codex JSONL file and pull session_id + cwd
    (and a parent session id when the file represents a subagent rollout).

    Codex writes a ``session_meta`` event as the first line of every
    rollout file, with the session UUID at ``payload.id`` and the working
    directory at ``payload.cwd``. The session UUID is the canonical
    session id (the filename also contains it but we trust the payload).

    For subagent rollouts the ``payload.source`` field carries
    ``{"subagent": {"thread_spawn": {"parent_thread_id": "...", ...}}}``
    — we surface ``parent_thread_id`` so :func:`sync_project` can wire
    the subagent to its parent ``Session`` row. Codex supports nested
    subagents (a subagent can itself spawn subagents) but
    ``parent_thread_id`` always points to the *direct* parent, so a
    single field is enough to reconstruct the chain.

    Returns ``None`` if the file is empty, unreadable, or missing either
    ``id`` or ``cwd`` — such files are skipped by the sync.
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

    parent_session_id: str | None = None
    source = payload.get("source")
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            thread_spawn = subagent.get("thread_spawn")
            if isinstance(thread_spawn, dict):
                candidate = thread_spawn.get("parent_thread_id")
                if isinstance(candidate, str) and candidate:
                    parent_session_id = candidate

    return SessionMeta(
        session_id=session_id,
        cwd=cwd,
        parent_session_id=parent_session_id,
    )


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
    """A disk file with no matching DB row yet (first line was parsed).

    ``parent_session_id`` is non-``None`` for subagent rollouts; the
    sync resolves it to the parent ``Session`` row before creating the
    subagent (multi-pass topological order — see :func:`sync_project`).
    """
    file_path: Path
    session_id: str
    cwd: str
    parent_session_id: str | None = None


class _ExistingEntry(NamedTuple):
    """A disk file already represented by a DB session row."""
    file_path: Path
    session: Session


def sync_project(
    project_id: str,
    new_entries: list[_NewEntry],
    existing_entries: list[_ExistingEntry],
    sync_queue: queue.Queue,
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize one project's Codex sessions by pushing payloads on ``sync_queue``.

    Producer-side reads only. The unified consumer applies every write
    inside ``transaction.atomic`` from a single coroutine, so two
    providers (Claude Code + Codex) and two phases (initial sync +
    compute) never race on the SQLite write lock.
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
    project_will_be_created = False

    # Pass 1 — existing entries (UPDATEs only).
    for entry in existing_entries:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        to_insert = read_session_items_from_file(entry.session, entry.file_path)
        if to_insert is not None:
            stats["items_added"] += to_insert.actually_new_count
            sync_queue.put(UpdateSessionPayload(
                provider=Provider.CODEX,
                session=entry.session,
                items=to_insert.items,
                last_offset=to_insert.last_offset,
                last_line=to_insert.last_line,
                mtime=to_insert.mtime,
                reset_compute_version=(
                    to_insert.actually_new_count > 0
                    and entry.session.compute_version is not None
                ),
                clear_stale=entry.session.stale,
            ))

        if on_session_progress:
            on_session_progress(entry.session.id, idx, total_sessions)

    # Split new entries by topology: regular sessions have no parent
    # dependency and can be created in any order; subagents must wait
    # for their parent ``Session`` row to exist (the parent may itself
    # be a subagent — Codex supports nested spawns). Process the regular
    # ones first, then run a multi-pass loop over the subagents so that
    # each iteration pushes every subagent whose parent is already
    # resolvable (DB row or already-pushed CreateSessionPayload).
    regular_new = [e for e in new_entries if e.parent_session_id is None]
    subagent_new = [e for e in new_entries if e.parent_session_id is not None]

    # ``resolvable_parent_ids`` is the set of session ids the consumer
    # will have created by the time it applies a later payload — initial
    # DB content plus everything we've already pushed in this run.
    resolvable_parent_ids: set[str] = set(
        Session.objects.filter(
            project_id=project_id,
        ).values_list("id", flat=True)
    )

    def _build_create_payload(
        entry: _NewEntry,
        is_subagent: bool,
    ) -> CreateSessionPayload | None:
        """Build the CreateSessionPayload for a new entry, or None on parse failure."""
        nonlocal project_will_be_created

        relative_path = entry.file_path.relative_to(CodexHelpers.SESSIONS_DIR)
        if is_subagent:
            session = Session(
                id=entry.session_id,
                project_id=project_id,
                provider=Provider.CODEX,
                file_path=str(relative_path),
                type=SessionType.SUBAGENT,
                parent_session_id=entry.parent_session_id,
                agent_id=entry.session_id,
            )
        else:
            session = Session(
                id=entry.session_id,
                project_id=project_id,
                provider=Provider.CODEX,
                file_path=str(relative_path),
                type=SessionType.SESSION,
            )

        to_insert = read_session_items_from_file(session, entry.file_path)
        if to_insert is None:
            return None

        stats["items_added"] += to_insert.actually_new_count
        stats["sessions_created"] += 1

        # If this is the first session forcing project creation, flag for stats.
        if project is None and not project_will_be_created:
            project_will_be_created = True
            stats["project_created"] = 1

        # Codex stores cwd in the first JSONL line, so we can pass it to
        # the consumer for ``register_project_sync`` to create the project
        # with the right directory/stale right away. Subsequent payloads
        # for the same project also pass the cwd — the helper is
        # idempotent (no-op if project already exists with same directory).
        return CreateSessionPayload(
            provider=Provider.CODEX,
            project_id=project_id,
            new_project_directory=entry.cwd,
            new_project_stale=not os.path.isdir(entry.cwd),
            session=session,
            items=to_insert.items,
            last_offset=to_insert.last_offset,
            last_line=to_insert.last_line,
            mtime=to_insert.mtime,
        )

    for entry in regular_new:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        # Defensive: extract_session_meta already proved the first line
        # is parseable, but the rest of the file might be empty after a
        # failed write — skip without pushing.
        if not check_file_has_content(entry.file_path):
            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            continue

        payload = _build_create_payload(entry, is_subagent=False)
        if payload is None:
            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            continue

        sync_queue.put(payload)
        resolvable_parent_ids.add(entry.session_id)

        if on_session_progress:
            on_session_progress(entry.session_id, idx, total_sessions)

    # Subagent topology pass — repeat until no new subagent gets resolved
    # in a full sweep. Worst case: O(depth × N), with depth bounded by
    # the spawn-tree height (a handful in practice).
    remaining = list(subagent_new)
    while remaining:
        progressed = False
        next_remaining: list[_NewEntry] = []
        for entry in remaining:
            idx += 1
            if stop_event is not None and stop_event.is_set():
                logger.info(
                    "  Sync interrupted for project %s (after %d/%d sessions)",
                    project_id, idx - 1, total_sessions,
                )
                return stats

            if not check_file_has_content(entry.file_path):
                if on_session_progress:
                    on_session_progress(entry.session_id, idx, total_sessions)
                continue

            if entry.parent_session_id not in resolvable_parent_ids:
                # Parent isn't in DB yet and not in the queue yet either —
                # try again on the next pass (it might be a subagent later
                # in this batch).
                idx -= 1  # don't double-count progress for this entry
                next_remaining.append(entry)
                continue

            payload = _build_create_payload(entry, is_subagent=True)
            if payload is None:
                if on_session_progress:
                    on_session_progress(entry.session_id, idx, total_sessions)
                continue

            sync_queue.put(payload)
            resolvable_parent_ids.add(entry.session_id)

            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            progressed = True

        if not progressed:
            for entry in next_remaining:
                logger.warning(
                    "  Skipping orphan Codex subagent %s: parent %s not found "
                    "(file %s)",
                    entry.session_id, entry.parent_session_id, entry.file_path,
                )
            break
        remaining = next_remaining

    if project is None and not project_will_be_created:
        elapsed = time.monotonic() - project_start
        logger.info(
            f"  ⊘ Project {project_id} skipped in {elapsed:.1f}s — no Codex sessions with content"
        )
        return stats

    # ``sessions_count`` and ``mtime`` are cross-provider — recomputed
    # from every session of the project, not just the Codex ones we just
    # synced, so a project shared with Claude doesn't lose values the
    # other provider already wrote. Read-only here; the actual UPDATE
    # goes through the consumer.
    new_sessions_count = Session.objects.filter(
        project_id=project_id,
        type=SessionType.SESSION,
        created_at__isnull=False,
        user_message_count__gt=0,
    ).count()
    new_mtime = (
        Session.objects.filter(project_id=project_id)
        .aggregate(max_mtime=Max("mtime"))["max_mtime"]
        or 0
    )

    sync_queue.put(UpdateProjectMetadataPayload(
        provider=Provider.CODEX,
        project_id=project_id,
        new_sessions_count=new_sessions_count,
        new_mtime=new_mtime,
        # Codex sync_project leaves ``project.stale`` alone; it is handled
        # by Claude Code's sync_all (which iterates every Project and
        # recomputes stale from disk).
        new_stale=None,
        recalc_total_cost=False,
        resolve_git_root=False,
        git_root_directory=None,
    ))

    elapsed = time.monotonic() - project_start
    logger.info(
        f"  ✓ Project {project_id} done in {elapsed:.1f}s — "
        f"{stats['items_added']} items, {stats['sessions_created']} new Codex sessions"
    )

    return stats


def sync_all(
    sync_queue: queue.Queue,
    on_project_start: Callable[[str, int, int], None] | None = None,
    on_project_done: Callable[[str, dict[str, int]], None] | None = None,
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize all Codex sessions from :attr:`CodexHelpers.SESSIONS_DIR`.

    Pushes payloads onto ``sync_queue`` (the unified consumer drains them).
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
    # Include both top-level sessions and subagents — the latter share the
    # same ``rollout-*.jsonl`` layout under ``YYYY/MM/DD/`` and their
    # ``Session`` rows must be re-matched on subsequent syncs the same
    # way regular sessions are.
    db_sessions_by_path = {
        s.file_path: s
        for s in Session.objects.filter(
            provider=Provider.CODEX,
            type__in=(SessionType.SESSION, SessionType.SUBAGENT),
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
                    parent_session_id=meta.parent_session_id,
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
            sync_queue=sync_queue,
            on_session_progress=on_session_progress,
            stop_event=stop_event,
        )

        stats["projects_created"] += project_stats.get("project_created", 0)
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    interrupted = stop_event is not None and stop_event.is_set()

    # Mark stale sessions (on DB but no longer on disk). Pushed as a
    # single batch payload so the consumer issues exactly one bulk UPDATE.
    if not interrupted:
        stale_paths = (
            set(db_sessions_by_path.keys()) - set(disk_files_by_relative_path.keys())
        )
        stale_session_ids = [
            db_sessions_by_path[p].id for p in stale_paths
            if not db_sessions_by_path[p].stale
        ]
        if stale_session_ids:
            sync_queue.put(MarkSessionsStalePayload(
                provider=Provider.CODEX,
                session_ids=stale_session_ids,
            ))
            stats["sessions_stale"] = len(stale_session_ids)

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
