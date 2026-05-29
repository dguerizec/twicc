"""
Cross-provider project metadata helpers.

A :class:`Project` row is shared across every provider that owns a session
in the same working directory (its ``id`` is derived from the cwd via
:func:`twicc.paths.path_to_project_id`). The functions in this module own
the project-level fields (``directory``, ``git_root``, ``stale``,
``sessions_count``, ``mtime``, ``total_cost``) and the in-process caches
that back them. Provider-specific code (sync, watcher, compute) calls in
here whenever it learns something new about a project from its own data
stream.
"""

from __future__ import annotations

import logging
import os

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction

from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_project
from twicc.git import resolve_git_from_path
from twicc.workspaces import auto_add_project_to_workspaces

logger = logging.getLogger(__name__)


# =============================================================================
# Project Directory / Git Root Caches
# =============================================================================

# Module-level cache: project_id -> directory (can be None)
_project_directories: dict[str, str | None] = {}
# Module-level cache: project_id -> git_root (can be None)
_project_git_roots: dict[str, str | None] = {}


def load_project_directories() -> None:
    """
    Load all project directories into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_directories.clear()
    _project_directories.update(
        Project.objects.values_list('id', 'directory')
    )


def load_project_git_roots() -> None:
    """
    Load all project git_roots into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_git_roots.clear()
    _project_git_roots.update(
        Project.objects.values_list('id', 'git_root')
    )


def get_project_directory(project_id: str) -> str | None:
    """Get cached directory for a project."""
    return _project_directories.get(project_id)


def get_project_git_root(project_id: str) -> str | None:
    """Get cached git_root for a project."""
    return _project_git_roots.get(project_id)


def ensure_project_directory(project_id: str, cwd: str) -> None:
    """
    Ensure the project's directory is set and up-to-date.

    - If project not in cache: load from DB and cache
    - If directory differs from cwd: update DB and cache
    - If directory matches cwd: do nothing

    Args:
        project_id: The project ID
        cwd: The current working directory from a session line
    """
    # Load project into cache if not present
    if project_id not in _project_directories:
        try:
            directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
        except Project.DoesNotExist:
            # Project doesn't exist yet, skip (will be handled when project is created)
            return
        _project_directories[project_id] = directory

    # Check if update needed
    if _project_directories[project_id] == cwd:
        return

    # Update DB now; refresh the cache only once the surrounding transaction
    # commits. apply_session_complete and the DB writer call this
    # inside transaction.atomic — a rollback there would otherwise leave the
    # cache ahead of the DB, and the `== cwd` check above would then suppress
    # the corrective write forever. on_commit runs immediately when there is
    # no active transaction, so non-transactional callers are unaffected.
    should_be_stale = not os.path.isdir(cwd)
    Project.objects.filter(id=project_id).update(directory=cwd, stale=should_be_stale)
    transaction.on_commit(lambda: _project_directories.update({project_id: cwd}))

    # Re-resolve git_root when directory changes
    ensure_project_git_root(project_id, cwd)


def ensure_project_git_root(project_id: str, directory: str | None = None) -> None:
    """
    Resolve and store git_root for a project.

    Called:
    - At sync_all (startup) for all projects with a directory
    - When project.directory changes (from ensure_project_directory)
    - When a session gets git info but project.git_root is still None

    Args:
        project_id: The project ID
        directory: The project directory to resolve from. If None, uses cached/DB value.
    """
    if directory is None:
        directory = _project_directories.get(project_id)
        if directory is None:
            try:
                directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
            except Project.DoesNotExist:
                return
        if not directory:
            return

    result = resolve_git_from_path(directory, use_cache=False)
    git_root = result[0] if result else None

    # Check if update needed
    if _project_git_roots.get(project_id) == git_root:
        return

    # Update DB now; refresh the cache only once the surrounding transaction
    # commits, so a rollback never leaves the cache ahead of the DB (the
    # `== git_root` check above would otherwise suppress the corrective
    # write). on_commit runs immediately when there is no active transaction.
    Project.objects.filter(id=project_id).update(git_root=git_root)
    transaction.on_commit(lambda: _project_git_roots.update({project_id: git_root}))


# =============================================================================
# Project creation — single entry point
# =============================================================================
#
# Every code path that creates a ``Project`` row goes through ``register_project``
# (async). It wraps ``Project.objects.get_or_create`` and runs the
# after-creation hooks atomically: WS broadcast of ``project_added`` and
# workspace auto-add (when the directory is already known).
#
# For callers that learn the directory only later (the watcher between
# ``parse_session_file`` and ``sync_session_items_from_file``, and the
# claude_code initial sync where the cwd is in the JSONL body), pass
# ``directory=None`` here and call :func:`auto_add_project_to_workspaces`
# once the directory has been resolved. That second call is idempotent —
# a workspace that already lists the project is left untouched and no
# broadcast fires.


def _create_or_get_project(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
) -> tuple[Project, bool]:
    """Pure DB operation: get-or-create a Project row.

    Returns ``(project, was_just_created)``. No broadcasts, no auto-add —
    callers should go through ``register_project`` (async) instead, which
    runs the post-creation hooks.
    """
    defaults: dict = {}
    if directory:
        defaults["directory"] = directory
    if name is not None:
        defaults["name"] = name
    if color is not None:
        defaults["color"] = color
    if stale is not None:
        defaults["stale"] = stale
    project, created = Project.objects.get_or_create(id=project_id, defaults=defaults)
    if created and directory:
        # Mirror the cache update that ``ensure_project_directory`` would do
        # so subsequent reads don't have to round-trip the DB. Deferred to
        # post-commit so a rolled-back transaction never leaves the cache
        # ahead of the DB (on_commit runs immediately outside a transaction).
        transaction.on_commit(lambda: _project_directories.update({project_id: directory}))
    return project, created


async def _broadcast_project_added(project: Project) -> None:
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "project_added", "project": serialize_project(project)},
    })


def _adopt_directory_sync(project: Project, directory: str) -> None:
    """Patch ``project.directory`` in DB + cache (sync). Caller checks that
    the project actually lacks a directory before invoking. The cache refresh
    is deferred to post-commit so a rolled-back transaction never leaves the
    cache ahead of the DB (on_commit runs immediately outside a transaction)."""
    project.directory = directory
    project.save(update_fields=["directory"])
    project_id = project.id
    transaction.on_commit(lambda: _project_directories.update({project_id: directory}))


def register_project_db_only(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
) -> tuple[Project, bool, bool]:
    """DB-only half of project registration: get-or-create + directory adoption.

    Returns ``(project, was_just_created, adopted_directory)`` and runs **no**
    side effects — no ``project_added`` broadcast, no workspace auto-add — so
    it is safe to call from inside a ``transaction.atomic()`` block.

    Callers that need the side effects must run them themselves once the
    surrounding transaction has committed: broadcast ``project_added`` when
    ``created`` is true, and call
    :func:`twicc.workspaces.auto_add_project_to_workspaces` only when the
    project was just created or just adopted a directory
    (``created or adopted_directory``) — never on every call, since an
    existing project's workspace membership cannot change just because another
    of its sessions was synced. :func:`register_project` is the async wrapper
    that does exactly that; the DB writer
    (:mod:`twicc.providers.db_writer`) calls this directly so a project is
    never announced from inside — or despite a rollback of — its transaction.
    """
    project, created = _create_or_get_project(
        project_id, directory=directory, name=name, color=color, stale=stale,
    )
    adopted = False
    if not created and directory and not project.directory:
        _adopt_directory_sync(project, directory)
        adopted = True
    return project, created, adopted


async def register_project(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
) -> tuple[Project, bool]:
    """Single entry point for Project creation. Returns ``(project, was_just_created)``.

    On creation:

    1. Broadcasts ``project_added`` via the channel layer.
    2. If ``directory`` is provided, runs workspace auto-add (which
       broadcasts ``workspaces_updated`` on hit).

    If the project already exists *without* a directory and one is provided
    here, the directory is adopted and workspace auto-add is re-evaluated.
    Common when ``claude_code/initial_sync.py`` created the row before the
    background compute pass filled the cwd in.

    Pass ``directory=None`` for flows that resolve the directory only after
    creation (the file watcher between the bare ``get_or_create`` and the
    JSONL sync that fills in ``cwd``); call
    :func:`twicc.workspaces.auto_add_project_to_workspaces` once the
    directory has been set.

    Broadcasts targeting a channel layer with no subscribers (e.g.
    initial sync before any WS client is connected) are silent no-ops.
    """
    project, created, adopted = await sync_to_async(register_project_db_only)(
        project_id,
        directory=directory, name=name, color=color, stale=stale,
    )
    if created:
        await _broadcast_project_added(project)
    if (created or adopted) and project.directory:
        await auto_add_project_to_workspaces(project.id, project.directory)

    return project, created


def update_project_total_cost(project_id: str) -> None:
    """
    Recalculate and save a project's total_cost.

    Uses Project.recalculate_total_cost() which sums total_cost from
    non-stale SESSION-type sessions.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    project.recalculate_total_cost()
    project.save(update_fields=["total_cost"])


@transaction.atomic
def update_project_metadata(project_id: str) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    sessions = Session.objects.filter(
        project=project, type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0
    )
    project.sessions_count = sessions.count()
    # mtime excludes stale sessions (JSONL gone from disk): a stale session
    # must not keep the project's mtime -- and thus its sort position -- high.
    # sessions_count above intentionally keeps stale sessions (matching
    # recalc_sessions_count in the DB writer); only this mtime
    # aggregate filters them out. Keep this filter aligned with
    # _compute_project_mtime() in twicc.providers.db_writer.
    max_mtime = (
        sessions.filter(stale=False).order_by("-mtime").values_list("mtime", flat=True).first()
    )
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])

    update_project_total_cost(project_id)
