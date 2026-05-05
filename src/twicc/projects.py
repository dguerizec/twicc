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

from twicc.core.models import Project, Session, SessionType
from twicc.git import resolve_git_from_path

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
