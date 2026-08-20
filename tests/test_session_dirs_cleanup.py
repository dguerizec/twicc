"""Tests for the daily empty-session-dirs janitor.

Covers:
- The pure :func:`_is_stale` boundary logic (None, recent, exactly the
  threshold, well past it).
- :func:`_prune_stale_session_dirs` over a temp data dir: empty vs non-empty,
  old vs recent sessions, the ``last_started_at`` race guard, sessions with no
  usable timestamp, and orphan directories dated by their filesystem mtime.

The data dir is redirected to a ``tmp_path`` by monkeypatching
``twicc.paths.get_data_dir`` (every downstream helper reads from it), so the
tests never touch the real data directory.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest
from django.utils import timezone

from twicc import paths
from twicc.core.models import Project, Session, SessionType
from twicc.session_dirs_cleanup_task import (
    STALE_SESSION_DIR_AGE,
    _is_stale,
    _prune_stale_session_dirs,
)


# ---------------------------------------------------------------------------
# _is_stale — pure function
# ---------------------------------------------------------------------------


def test_is_stale_none_reference_is_never_stale():
    now = datetime.now(UTC)
    assert _is_stale(None, now) is False


def test_is_stale_recent_is_not_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - timedelta(days=5), now) is False


def test_is_stale_exactly_at_threshold_is_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - STALE_SESSION_DIR_AGE, now) is True


def test_is_stale_well_past_threshold_is_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - timedelta(days=40), now) is True


# ---------------------------------------------------------------------------
# _prune_stale_session_dirs — filesystem + ORM
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path and pre-create both roots."""
    data_dir = tmp_path / "data"
    artifacts = data_dir / "artifacts"
    scratch = data_dir / "scratch"
    artifacts.mkdir(parents=True)
    scratch.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return artifacts, scratch


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-session-dirs-cleanup",
        directory="/tmp/twicc-session-dirs-cleanup",
    )


def _make_session(project, session_id, *, last_updated=None, last_started=None, created=None):
    return Session.objects.create(
        id=session_id,
        project=project,
        provider="claude_code",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        title=session_id,
        last_updated_at=last_updated,
        last_started_at=last_started,
        created_at=created,
    )


def _session_dir(root: Path, session_id: str, *, empty: bool = True, mtime_days_ago: int | None = None) -> Path:
    """Create ``root/session_id``; optionally add a file and back-date its mtime."""
    d = root / session_id
    d.mkdir()
    if not empty:
        (d / "keep.txt").write_text("x")
    if mtime_days_ago is not None:
        ts = (timezone.now() - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(d, (ts, ts))
    return d


def test_prunes_empty_dir_of_old_session(data_root, project):
    artifacts, scratch = data_root
    old = timezone.now() - timedelta(days=40)
    _make_session(project, "old-sess", last_updated=old, last_started=old, created=old)
    a = _session_dir(artifacts, "old-sess")
    s = _session_dir(scratch, "old-sess")

    assert _prune_stale_session_dirs() == (1, 1)
    assert not a.exists()
    assert not s.exists()


def test_keeps_empty_dir_of_recent_session(data_root, project):
    artifacts, _ = data_root
    recent = timezone.now() - timedelta(days=5)
    _make_session(project, "recent-sess", last_updated=recent, last_started=recent, created=recent)
    a = _session_dir(artifacts, "recent-sess")

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_keeps_non_empty_dir_of_old_session(data_root, project):
    artifacts, _ = data_root
    old = timezone.now() - timedelta(days=40)
    _make_session(project, "old-busy", last_updated=old, last_started=old, created=old)
    a = _session_dir(artifacts, "old-busy", empty=False)

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()
    assert (a / "keep.txt").exists()


def test_recent_last_started_protects_old_last_updated(data_root, project):
    """A session resumed today (no new content yet) keeps its empty dir."""
    artifacts, _ = data_root
    _make_session(
        project,
        "resumed",
        last_updated=timezone.now() - timedelta(days=40),
        last_started=timezone.now() - timedelta(days=1),
        created=timezone.now() - timedelta(days=50),
    )
    a = _session_dir(artifacts, "resumed")

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_session_without_timestamps_falls_back_to_mtime(data_root, project):
    artifacts, scratch = data_root
    _make_session(project, "no-ts")  # all lifecycle timestamps null
    old = _session_dir(artifacts, "no-ts", mtime_days_ago=40)
    fresh = _session_dir(scratch, "no-ts", mtime_days_ago=2)

    assert _prune_stale_session_dirs() == (1, 0)
    assert not old.exists()
    assert fresh.exists()


def test_orphan_dir_pruned_when_old(data_root, project):
    artifacts, _ = data_root  # no Session row for this id
    a = _session_dir(artifacts, "orphan-old", mtime_days_ago=40)

    assert _prune_stale_session_dirs() == (1, 0)
    assert not a.exists()


def test_orphan_dir_kept_when_recent(data_root, project):
    artifacts, _ = data_root
    a = _session_dir(artifacts, "orphan-fresh", mtime_days_ago=2)

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_no_roots_is_noop(tmp_path, monkeypatch):
    """Missing artifacts/scratch roots must not raise."""
    monkeypatch.setattr(paths, "get_data_dir", lambda: tmp_path / "absent")
    assert _prune_stale_session_dirs() == (0, 0)
