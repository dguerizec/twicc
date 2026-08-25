"""The action-time "Re-check" behind a project whose directory went missing.

``Project.stale`` is a STORED observation ("the working directory was gone the
last time TwiCC looked"). Nothing watches the working directories themselves, so
a folder deleted — or restored — while TwiCC runs keeps the stored flag until
the next restart. :func:`twicc.projects.refresh_project_directory_state` is what
the project dialog's "Re-check" button calls to heal it live, in both
directions, and to re-resolve ``git_root`` once the directory is back.
"""

from __future__ import annotations

import subprocess

import pytest
from asgiref.sync import async_to_sync

from twicc.core.models import Project
from twicc.projects import (
    _project_git_roots,
    compute_project_stale,
    refresh_all_project_directory_states,
    refresh_project_directory_state,
)


@pytest.fixture(autouse=True)
def _clear_git_root_cache():
    """``ensure_project_git_root`` short-circuits on its module-level cache;
    isolate each test from any leftover entry."""
    _project_git_roots.clear()
    yield
    _project_git_roots.clear()


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer only starts at app boot; run the write factory
    transparently (the symbol is imported lazily from the module)."""
    async def _passthrough(coro_factory):
        return await coro_factory()

    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)


@pytest.fixture(autouse=True)
def _capture_broadcasts(monkeypatch):
    """Record ``project_updated`` broadcasts instead of hitting a channel layer."""
    calls: list[str] = []

    async def _broadcast(project_id):
        calls.append(project_id)

    monkeypatch.setattr("twicc.projects._broadcast_project_updated", _broadcast)
    return calls


def test_compute_project_stale():
    assert compute_project_stale(None) is False  # directory not known yet
    assert compute_project_stale("/definitely/not/a/directory") is True
    assert compute_project_stale("/tmp") is False


@pytest.mark.django_db(transaction=True)
def test_refresh_flags_a_directory_that_disappeared(tmp_path, _capture_broadcasts):
    directory = tmp_path / "gone"
    directory.mkdir()
    project = Project.objects.create(id="-refresh-gone", directory=str(directory), stale=False)
    directory.rmdir()

    found, refreshed = async_to_sync(refresh_project_directory_state)(project.id)

    assert found is True
    assert refreshed.stale is True
    assert Project.objects.get(id=project.id).stale is True
    assert _capture_broadcasts == [project.id]


@pytest.mark.django_db(transaction=True)
def test_refresh_clears_the_flag_and_resolves_git_root_when_restored(tmp_path, _capture_broadcasts):
    directory = tmp_path / "restored"
    project = Project.objects.create(
        id="-refresh-restored", directory=str(directory), stale=True, git_root=None
    )

    directory.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)

    found, refreshed = async_to_sync(refresh_project_directory_state)(project.id)

    assert found is True
    assert refreshed.stale is False
    # git_root is re-resolved in the same pass: a project first seen while its
    # directory was already gone would otherwise stay git-less until a restart.
    assert refreshed.git_root == str(directory)
    stored = Project.objects.get(id=project.id)
    assert stored.stale is False
    assert stored.git_root == str(directory)
    assert _capture_broadcasts == [project.id]


@pytest.mark.django_db(transaction=True)
def test_refresh_keeps_git_root_while_the_directory_is_missing(tmp_path, _capture_broadcasts):
    """Resolving from a missing directory can only yield ``None`` — that would
    destroy the last known value for no gain, so it is left alone."""
    directory = tmp_path / "vanished"
    project = Project.objects.create(
        id="-refresh-keep-git", directory=str(directory), stale=True, git_root=str(directory)
    )

    found, refreshed = async_to_sync(refresh_project_directory_state)(project.id)

    assert found is True
    assert refreshed.stale is True
    assert refreshed.git_root == str(directory)
    # Nothing changed, so no broadcast.
    assert _capture_broadcasts == []


@pytest.mark.django_db(transaction=True)
def test_boot_sweep_fixes_every_project_both_ways(tmp_path, _capture_broadcasts):
    """The sweep is provider-agnostic on purpose: it used to live inside
    claude_code's sync_all, so a Codex-only TwiCC never refreshed the flag."""
    gone = tmp_path / "gone"
    gone.mkdir()
    back = tmp_path / "back"
    back.mkdir()
    Project.objects.create(id="-sweep-gone", directory=str(gone), stale=False)
    Project.objects.create(id="-sweep-back", directory=str(back), stale=True)
    # Already correct, and a project with no known directory: neither must move.
    Project.objects.create(id="-sweep-ok", directory=str(back), stale=False)
    Project.objects.create(id="-sweep-nodir", directory=None, stale=False)
    gone.rmdir()

    changed = async_to_sync(refresh_all_project_directory_states)()

    assert changed == 2
    assert Project.objects.get(id="-sweep-gone").stale is True
    assert Project.objects.get(id="-sweep-back").stale is False
    assert Project.objects.get(id="-sweep-ok").stale is False
    assert Project.objects.get(id="-sweep-nodir").stale is False
    # Only the two rows that moved are broadcast.
    assert sorted(_capture_broadcasts) == ["-sweep-back", "-sweep-gone"]


@pytest.mark.django_db(transaction=True)
def test_boot_sweep_is_a_no_op_when_nothing_moved(tmp_path, _capture_broadcasts):
    Project.objects.create(id="-sweep-quiet", directory=str(tmp_path), stale=False)

    assert async_to_sync(refresh_all_project_directory_states)() == 0
    assert _capture_broadcasts == []


@pytest.mark.django_db(transaction=True)
def test_refresh_reports_a_missing_project():
    found, refreshed = async_to_sync(refresh_project_directory_state)("-does-not-exist")

    assert found is False
    assert refreshed is None
