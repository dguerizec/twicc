"""Regression: project registration resolves ``git_root`` eagerly.

A project registered in a freshly ``git init``-ed directory — e.g. created via
the RPC/API ``project:create`` drop-request in a never-synced repo — must expose
its ``git_root`` immediately, instead of staying ``None`` until the next startup
sync re-resolves it. The frontend gates the worktree-creation affordance
(``WorktreeButton v-if="p.git_root"``) and the project Git tab
(``hasGitRepo = !!git_root``) on ``git_root``, so a stale ``None`` there made
worktrees uncreatable until a restart — the bug this test guards against.

The resolution lives in :func:`twicc.projects.register_project_db_only` (the
single DB-only half every creation path funnels through), so exercising it
directly covers the RPC, HTTP, session and worktree creation flows at once.
"""

from __future__ import annotations

import subprocess

import pytest

from twicc.core.models import Project
from twicc.paths import path_to_project_id
from twicc.projects import _project_git_roots, register_project_db_only


def _git_init(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


@pytest.fixture(autouse=True)
def _clear_git_root_cache():
    """``register_project_db_only`` consults the module-level ``git_root``
    cache; isolate each test from any leftover entry."""
    _project_git_roots.clear()
    yield
    _project_git_roots.clear()


def test_register_resolves_git_root_on_creation(db, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    directory = str(repo)
    project_id = path_to_project_id(directory)

    project, created, _adopted = register_project_db_only(project_id, directory=directory)

    assert created is True
    # Mirrored onto the returned instance so the ``project_added`` broadcast /
    # 201 response carry the resolved root, not a stale ``None`` that the
    # frontend would write over the correct value.
    assert project.git_root == directory
    # And persisted to the DB.
    assert Project.objects.get(id=project_id).git_root == directory


def test_register_leaves_git_root_none_for_non_git_directory(db, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    directory = str(plain)
    project_id = path_to_project_id(directory)

    project, created, _adopted = register_project_db_only(project_id, directory=directory)

    assert created is True
    assert project.git_root is None
    assert Project.objects.get(id=project_id).git_root is None


def test_register_resolves_git_root_on_directory_adoption(db, tmp_path):
    """A directory-less row (the watcher's transient creation state) that later
    adopts a git directory resolves ``git_root`` at adoption time."""
    repo = tmp_path / "adopt"
    repo.mkdir()
    _git_init(repo)
    directory = str(repo)
    project_id = path_to_project_id(directory)

    # First registration without a directory — git_root cannot be resolved yet.
    register_project_db_only(project_id)
    assert Project.objects.get(id=project_id).git_root is None

    # Adopting the directory now resolves it.
    project, created, adopted = register_project_db_only(project_id, directory=directory)

    assert created is False
    assert adopted is True
    assert project.git_root == directory
    assert Project.objects.get(id=project_id).git_root == directory
