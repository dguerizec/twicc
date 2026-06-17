import asyncio
from pathlib import Path

import orjson
import pytest
from django.db import IntegrityError
from django.test import AsyncClient
from django.utils import timezone

from twicc import paths
from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-ab", directory="/tmp/ab")


@pytest.fixture
def session(project):
    now = timezone.now()
    return Session.objects.create(
        id="sess-ab", project=project, provider="claude_code",
        file_path="sess-ab.jsonl", type=SessionType.SESSION, title="sess-ab",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )


def test_create_bookmark(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )
    assert bm.id is not None
    assert bm.scope == "project"


def test_unique_per_session_and_path(session, project):
    ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope=PinMode.ALL,
    )
    with pytest.raises(IntegrityError):
        ArtifactBookmark.objects.create(
            session=session, project=project, relative_path="a.md", name="B", scope=PinMode.PROJECT,
        )


def test_serialize_bookmark(session, project):
    from twicc.core.serializers import serialize_artifact_bookmark
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.WORKSPACE,
    )
    d = serialize_artifact_bookmark(bm)
    assert d["name"] == "Demo"
    assert d["scope"] == "workspace"
    assert d["session_id"] == "sess-ab"
    assert d["project_id"] == "-tmp-ab"
    assert d["relative_path"] == "demo/index.html"
    assert d["file_ext"] == "html"
    assert d["root"].endswith("/sess-ab")
    assert d["created_at"] and d["updated_at"]


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path. get_session_artifacts_dir reads from
    get_data_dir, so patching that one function reroutes the whole tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer is only started at app boot. In tests, run the
    write factory transparently so the view logic (ORM + serialize + broadcast)
    can be exercised without the writer lifecycle."""
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr("twicc.views.run_under_db_write_lock", _passthrough)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _write_artifact(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def test_create_and_list_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html", b"<html></html>")
    body = {"session_id": "sess-ab", "relative_path": "demo/index.html",
            "name": "Demo", "scope": "all"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 201
    created = orjson.loads(res.content)
    assert created["name"] == "Demo"

    res = _run(client.get("/api/artifact-bookmarks/"))
    assert res.status_code == 200
    assert len(orjson.loads(res.content)["bookmarks"]) == 1


def test_create_rejects_missing_file(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "nope.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 404


def test_create_rejects_path_escape(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "../escape.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 400


def test_patch_and_delete_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="project")
    res = _run(client.patch(f"/api/artifact-bookmarks/{bm.id}/",
                            data=orjson.dumps({"name": "B", "scope": "all"}),
                            content_type="application/json"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["name"] == "B"
    res = _run(client.delete(f"/api/artifact-bookmarks/{bm.id}/"))
    assert res.status_code == 200
    assert not ArtifactBookmark.objects.filter(id=bm.id).exists()


def test_detail_reports_availability(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    present = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{present.id}/"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["available"] is True
    # A bookmark whose file is missing (created directly, bypassing POST validation):
    missing = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="gone.md", name="Gone", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{missing.id}/"))
    assert orjson.loads(res.content)["available"] is False


def test_method_not_allowed(client, session, project, artifacts_root):
    res = _run(client.delete("/api/artifact-bookmarks/"))
    assert res.status_code == 405
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="all")
    res = _run(client.put(f"/api/artifact-bookmarks/{bm.id}/",
                          data=orjson.dumps({}), content_type="application/json"))
    assert res.status_code == 405
