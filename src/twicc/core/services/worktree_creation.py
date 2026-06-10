"""Create a git worktree of a project and register it as a linked Project.

Single orchestration point shared by:

- the HTTP endpoint ``POST /api/projects/<id>/worktrees/``
  (:func:`twicc.views.project_worktrees`), and
- the CLI ``create-session --worktree-branch ...`` flow, where
  :func:`twicc.core.services.session_creation.create_session_from_payload`
  calls it before creating the session so the session lands in the new
  worktree.

The function is transport-agnostic: it returns a
:class:`twicc.projects.ProjectMutationResult` (``success`` flag, the new
worktree ``project_id`` + ``Project`` instance, or a list of structured
errors). Callers map the error ``code`` to their own surface — an HTTP
status for the view, a ``rejected`` drop-status for the watcher.

The actual ``git worktree add`` (:func:`twicc.git.create_worktree`) runs
OUTSIDE the DB write lock — the subprocess can take seconds; only the
get-or-create of the source project (when the caller knows it by path
only, e.g. the CLI) and of the new worktree project run under the lock,
mirroring the granularity the HTTP endpoint used before extraction.
"""

from __future__ import annotations

import asyncio
import os

from asgiref.sync import sync_to_async
from django.db import IntegrityError

from twicc.core.models import Project
from twicc.paths import path_to_project_id
from twicc.projects import (
    ProjectMutationError,
    ProjectMutationResult,
    register_project,
)
from twicc.providers.db_writer import run_under_db_write_lock


def _error(field: str, code: str, message: str) -> ProjectMutationResult:
    return ProjectMutationResult(False, None, None, [ProjectMutationError(field, code, message)])


async def create_worktree_from_source(
    *,
    source_project_id: str,
    path: str,
    branch: str,
    start_from: str | None = None,
    source_directory: str | None = None,
) -> ProjectMutationResult:
    """Create a git worktree of ``source_project_id`` at ``path`` on ``branch``.

    An existing local ``branch`` is checked out into the worktree
    (``start_from`` ignored); a new one is created with ``-b``, from
    ``start_from`` when given, else from the source repo's current HEAD.

    ``source_directory`` lets a caller that only knows the source by path
    (the CLI) have the source Project get-or-created here, so the
    worktree's ``worktree_of`` FK is valid. The HTTP caller omits it: the
    source row already exists and a missing one is a ``project_not_found``.

    Returns the registered worktree as a
    :class:`~twicc.projects.ProjectMutationResult`. Error codes:
    ``project_not_found``, ``not_git_repo``, ``invalid_path``,
    ``branch_required``, ``project_already_exists``, ``start_from_not_found``,
    ``git_error``.
    """
    from twicc.git import create_worktree, get_branches, resolve_git_from_path

    branch = (branch or "").strip()
    path = (path or "").strip()
    start_from = (start_from or "").strip() or None

    # --- resolve the source project + its repo root -------------------
    # Order mirrors the pre-extraction HTTP endpoint: source existence,
    # then git-ness, then the requested worktree's own validation.
    source = await Project.objects.filter(id=source_project_id).afirst()
    if source is None:
        if not source_directory:
            return _error("project", "project_not_found",
                          f"Project {source_project_id!r} not found.")
        # CLI flow: get-or-create the source so the worktree_of FK resolves
        # (and the source shows up / auto-adds to workspaces from here, the
        # main process, where the broadcasts reach connected clients).
        source, _ = await run_under_db_write_lock(
            lambda: register_project(source_project_id, directory=source_directory)
        )

    repo_root = source.git_root
    if not repo_root:
        # Freshly-registered rows have no computed git_root yet; resolve it
        # live from the directory so a never-synced repo still works.
        directory = source.directory or source_directory
        git = (
            await sync_to_async(resolve_git_from_path)(directory, use_cache=False)
            if directory else None
        )
        repo_root = git[0] if git else None
    if not repo_root:
        return _error("project", "not_git_repo",
                      f"Project {source_project_id!r} is not a git repository.")

    # --- validate the requested worktree ------------------------------
    if not path or not os.path.isabs(path):
        return _error("worktree_path", "invalid_path", "Path must be an absolute path.")
    if not branch:
        return _error("worktree_branch", "branch_required", "Branch is required.")

    resolved = await asyncio.to_thread(os.path.realpath, path)
    new_project_id = path_to_project_id(resolved)
    if await Project.objects.filter(id=new_project_id).aexists():
        return ProjectMutationResult(False, new_project_id, None, [
            ProjectMutationError("worktree_path", "project_already_exists",
                                 "A project already exists for this directory."),
        ])

    local_branches = await sync_to_async(get_branches)(repo_root)
    if branch in local_branches:
        start_from = None  # meaningless for an existing-branch checkout
    elif start_from and start_from not in local_branches:
        return _error("worktree_start_from", "start_from_not_found",
                      "Start-from branch does not exist.")

    # Whether the target path exists / is an empty directory is left to git:
    # ``git worktree add`` rejects a non-empty target with a clear message.
    # Run it OUTSIDE the DB write lock — the subprocess can take seconds.
    ok, git_error = await sync_to_async(create_worktree)(repo_root, resolved, branch, start_from)
    if not ok:
        return _error("worktree", "git_error", git_error)

    # The explicit ``worktree_of_id`` sets the link at row creation: the
    # ``project_added`` broadcast and the result both carry it, and the
    # filesystem detection (``ensure_worktree_link``) is skipped. ``created``
    # is deliberately not checked: the early exists-check covers the normal
    # duplicate case, and once the worktree exists on disk, adopting a
    # directory-less row in a lost race is benign — failing here would orphan
    # a freshly created worktree.
    try:
        new_project, _created = await run_under_db_write_lock(
            lambda: register_project(
                new_project_id, directory=resolved, worktree_of_id=source_project_id,
            )
        )
    except IntegrityError:
        return ProjectMutationResult(False, new_project_id, None, [
            ProjectMutationError("worktree_path", "project_already_exists",
                                 "A project already exists for this directory."),
        ])

    return ProjectMutationResult(True, new_project_id, new_project, None)
