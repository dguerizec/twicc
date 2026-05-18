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

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer

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

    # Update DB and cache, and set stale based on directory existence
    should_be_stale = not os.path.isdir(cwd)
    Project.objects.filter(id=project_id).update(directory=cwd, stale=should_be_stale)
    _project_directories[project_id] = cwd

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

    # Update DB and cache
    Project.objects.filter(id=project_id).update(git_root=git_root)
    _project_git_roots[project_id] = git_root


# =============================================================================
# Project creation — single entry point
# =============================================================================
#
# Every code path that creates a ``Project`` row goes through one of the two
# helpers below (``register_project`` async, ``register_project_sync`` sync).
# They wrap ``Project.objects.get_or_create`` and run the after-creation
# hooks atomically: WS broadcast of ``project_added`` and workspace
# auto-add (when the directory is already known).
#
# For callers that learn the directory only later (the watcher between
# ``parse_session_file`` and ``sync_session_items_from_file``, and the
# claude_code initial sync where the cwd is in the JSONL body), pass
# ``directory=None`` here and call :func:`auto_add_project_to_workspaces`
# (or its sync sibling) once the directory has been resolved. That second
# call is idempotent — a workspace that already lists the project is left
# untouched and no broadcast fires.


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
    callers should go through ``register_project`` /
    ``register_project_sync`` instead, which run the post-creation hooks.
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
        # so subsequent reads don't have to round-trip the DB.
        _project_directories[project_id] = directory
    return project, created


async def _broadcast_project_added(project: Project) -> None:
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "project_added", "project": serialize_project(project)},
    })


def _adopt_directory_sync(project: Project, directory: str) -> None:
    """Patch ``project.directory`` in DB + cache (sync). Caller checks that
    the project actually lacks a directory before invoking."""
    project.directory = directory
    project.save(update_fields=["directory"])
    _project_directories[project.id] = directory


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

    Sync callers use :data:`register_project_sync` (an
    ``async_to_sync(register_project)`` alias). Broadcasts targeting a
    channel layer with no subscribers (e.g. initial sync before any WS
    client is connected) are silent no-ops.
    """
    project, created = await sync_to_async(_create_or_get_project)(
        project_id,
        directory=directory, name=name, color=color, stale=stale,
    )
    if created:
        await _broadcast_project_added(project)
    elif directory and not project.directory:
        await sync_to_async(_adopt_directory_sync)(project, directory)
    if project.directory:
        await auto_add_project_to_workspaces(project.id, project.directory)

    return project, created


register_project_sync = async_to_sync(register_project)


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
    max_mtime = sessions.order_by("-mtime").values_list("mtime", flat=True).first()
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])

    update_project_total_cost(project_id)
