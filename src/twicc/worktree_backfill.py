"""Backfill :attr:`twicc.core.models.Project.worktree_of` from strong,
filesystem-only signals.

Live worktree detection (:func:`twicc.projects.ensure_worktree_link`) only
fires when a project is registered or recomputed, so projects created before
the feature — or worktrees whose ``.git`` is already gone — keep a null link.
This module relinks them with three strong signals, in order of certainty:

1. **live** — the worktree directory still exists and its ``.git`` pointer file
   resolves to the main repo (:func:`twicc.git.resolve_worktree_main_repo`).
2. **admin** — the worktree is still recorded in some existing repo's
   ``.git/worktrees/*/gitdir`` (read directly, no ``git`` subprocess). Survives
   deletion of the worktree directory until ``git worktree prune``.
3. **marker** — the directory is gone and the record pruned, but a worktree
   marker in the path (``.worktrees/…`` or a ``worktree-*`` folder) both proves
   it was a worktree and locates the repo
   (:func:`twicc.git.resolve_worktree_repo_from_marker`).

A bare deleted subdirectory of a repo (a session that merely ran in a subfolder,
not a worktree) matches none of these — it has no live ``.git``, no admin
record, and no marker — so it is left untouched.

Idempotent: only projects with a null ``worktree_of`` are considered. The parent
repo's project is created if missing (consistent with live detection). Run after
``migrate`` from the ``run`` startup, and exposed as the
``backfill_worktree_links`` management command for standalone testing.
"""

from __future__ import annotations

import glob
import logging
import os
from typing import Callable, NamedTuple

from asgiref.sync import sync_to_async

from twicc.core.models import Project
from twicc.git import (
    path_has_worktree_marker,
    resolve_worktree_main_repo,
    resolve_worktree_repo_from_marker,
)
from twicc.projects import link_worktree_to_repo

logger = logging.getLogger(__name__)


class WorktreeLinkPlan(NamedTuple):
    """One detected worktree → repo link the backfill will (or would) create."""
    project_id: str
    directory: str
    repo: str          # main repo root, realpath
    case: str          # 'live' | 'admin' | 'marker'


def _load_candidates() -> list[tuple[str, str]]:
    """``(project_id, directory)`` for every project not yet linked that has a
    directory. Archived projects are included on purpose — a deleted worktree is
    typically archived yet still worth linking."""
    return list(
        Project.objects.filter(worktree_of__isnull=True)
        .exclude(directory__isnull=True)
        .exclude(directory="")
        .values_list("id", "directory")
    )


def _build_admin_map() -> dict[str, str]:
    """Map ``realpath(worktree_dir) -> realpath(repo_root)`` from the
    ``.git/worktrees/*/gitdir`` records of every existing repo among the known
    projects. No ``git`` subprocess — the admin files are read directly."""
    admin: dict[str, str] = {}
    seen: set[str] = set()
    directories = Project.objects.exclude(directory__isnull=True).values_list("directory", flat=True)
    for directory in directories:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        git_dir = os.path.join(directory, ".git")
        if not os.path.isdir(git_dir):
            continue
        repo_real = os.path.realpath(directory)
        for gitdir_file in glob.glob(os.path.join(git_dir, "worktrees", "*", "gitdir")):
            try:
                with open(gitdir_file) as f:
                    content = f.read().strip()
            except OSError:
                continue
            worktree_dir = os.path.dirname(content)  # <wt>/.git -> <wt>
            if worktree_dir:
                admin[os.path.realpath(worktree_dir)] = repo_real
    return admin


def _resolve_repo(directory: str, admin_map: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve the main repo of *directory* via the three signals, in order of
    certainty. Returns ``(repo_realpath, case)`` or ``(None, None)``."""
    main = resolve_worktree_main_repo(directory)
    if main:
        return os.path.realpath(main), "live"
    recorded = admin_map.get(os.path.realpath(directory))
    if recorded:
        return recorded, "admin"
    marker_repo = resolve_worktree_repo_from_marker(directory)
    if marker_repo:
        return marker_repo, "marker"
    return None, None


def _plan() -> tuple[list[WorktreeLinkPlan], list[str]]:
    """Detect links (pure, no writes). Returns ``(plans, unresolved_marked)``:

    - *plans* — projects resolved to a repo via one of the three signals.
    - *unresolved_marked* — ids of projects that *look* like a worktree (their
      gone directory carries a worktree marker in its path) but could not be
      resolved (the repo is gone). Surfaced so the backfill can warn about them;
      they are left for the future manual-fix UI.

    Already-linked projects are out of scope entirely: :func:`_load_candidates`
    only yields ``worktree_of IS NULL`` rows, so a project that already has a
    parent is never re-resolved — for any of the three cases.
    """
    admin_map = _build_admin_map()
    plans: list[WorktreeLinkPlan] = []
    unresolved_marked: list[str] = []
    for project_id, directory in _load_candidates():
        repo, case = _resolve_repo(directory, admin_map)
        if repo and case:
            if os.path.realpath(directory) != repo:
                plans.append(WorktreeLinkPlan(project_id, directory, repo, case))
            # else: the repo root itself is never its own worktree — skip.
            continue
        # Not resolvable. Flag it only when its (gone) path looks like a
        # worktree — i.e. a pruned/lost worktree whose repo is gone too.
        if not os.path.isdir(directory) and path_has_worktree_marker(directory):
            unresolved_marked.append(project_id)
    return plans, unresolved_marked


async def backfill_worktree_links(
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = logger.info,
    warn: Callable[[str], None] = logger.warning,
) -> tuple[list[WorktreeLinkPlan], int, list[str]]:
    """Detect and (unless *dry_run*) create the worktree links.

    Logs only what changes: each newly-created link via *log* (never the
    already-linked projects — they are not even candidates), and each
    worktree-looking-but-unlinkable project via *warn*. Returns
    ``(plans, linked_count, unresolved_marked)``: every detected link, the
    number actually written (0 in dry-run), and the ids flagged as
    worktree-looking-but-unresolved.
    """
    plans, unresolved_marked = await sync_to_async(_plan)()

    for project_id in unresolved_marked:
        warn(
            "Worktree backfill: %s looks like a worktree (path marker) but its "
            "repo could not be found — left unlinked for manual fix." % project_id
        )

    linked = 0
    for plan in plans:
        if dry_run:
            log(f"Worktree backfill [dry-run]: would link [{plan.case}] {plan.project_id} -> {plan.repo}")
            continue
        parent_id = await link_worktree_to_repo(plan.project_id, plan.repo)
        if parent_id:
            linked += 1
            log(f"Worktree backfill: linked [{plan.case}] {plan.project_id} -> {parent_id}")

    return plans, linked, unresolved_marked
