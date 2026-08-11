"""CLI share list/show behaviour (direct-DB reads). Extended later by the
redaction task; here: the §8 cross-kind filter repair."""

import asyncio

import pytest
from django.utils import timezone as djtz

from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType
from twicc.core.services import share_mutation


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-reads", directory="/tmp/reads")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-reads", project=project, provider="claude_code",
        file_path="sess-reads.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=5,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def one_share_each(session, bookmark, tmp_path, monkeypatch):
    """One session share + one artifact share of the same session/project."""
    from twicc import paths
    data_dir = tmp_path / "data"
    (data_dir / "artifacts" / session.id / "demo").mkdir(parents=True)
    (data_dir / "artifacts" / session.id / "demo" / "index.html").write_bytes(b"<html/>")
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    s1 = _run(share_mutation.create_share("session", session=session, options={}))
    s2 = _run(share_mutation.create_share("artifact", bookmark=bookmark, options={}))
    assert s1.success and s2.success
    return s1.share_id, s2.share_id


def _list(**kwargs):
    from twicc.cli import share as cli_share
    captured = []
    import twicc.cli.share
    orig = twicc.cli.share.emit_json
    twicc.cli.share.emit_json = captured.append
    try:
        cli_share.list_main(**kwargs)
    finally:
        twicc.cli.share.emit_json = orig
    return captured[0]


def test_session_filter_returns_both_kinds(one_share_each, session):
    rows = _list(session=session.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_returns_both_kinds(one_share_each, project):
    rows = _list(project=project.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_expands_downward_to_worktree_for_both_kinds(
        one_share_each, project):
    """A main project includes child worktrees; a worktree stays local."""
    from twicc import paths

    worktree = Project.objects.create(
        id="-tmp-reads-wt", directory="/tmp/reads-wt", worktree_of=project,
    )
    now = djtz.now()
    wt_session = Session.objects.create(
        id="sess-reads-wt", project=worktree, provider="claude_code",
        file_path="sess-reads-wt.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=8,
    )
    wt_bookmark = ArtifactBookmark.objects.create(
        session=wt_session, project=worktree,
        relative_path="demo/index.html", name="Worktree demo", scope=PinMode.PROJECT,
    )
    source = paths.get_data_dir() / "artifacts" / wt_session.id / "demo" / "index.html"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"<html>worktree</html>")
    wt_session_result = _run(
        share_mutation.create_share("session", session=wt_session, options={}))
    wt_artifact_result = _run(
        share_mutation.create_share("artifact", bookmark=wt_bookmark, options={}))
    assert wt_session_result.success and wt_artifact_result.success
    worktree_ids = {wt_session_result.share_id, wt_artifact_result.share_id}

    main_rows = _list(project=project.id)
    assert worktree_ids <= {row["id"] for row in main_rows}
    worktree_rows = _list(project=worktree.id)
    assert {row["id"] for row in worktree_rows} == worktree_ids
    assert {row["kind"] for row in worktree_rows} == {"session", "artifact"}


def test_unrelated_session_filter_returns_nothing(one_share_each):
    assert _list(session="other-session") == []
