"""Write dispatch on the artifact-serving routes (design 2026-08-05 §3/§4):
``PUT``/``DELETE``/dir-``GET`` gated by the host-set ``X-Twicc-Artifact-Doc``
header, confined to ``<doc dir>/data/`` AND the route's own confinement root.
Exercised end-to-end through Django's ``AsyncClient`` on the standalone route
(the richest matrix), plus the project route and the bookmark route."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone

from twicc import paths
from twicc.core.models import Project, Session, SessionType

pytestmark = pytest.mark.django_db(transaction=True)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def _b64(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


@pytest.fixture
def artifact(tmp_path):
    """A demo artifact under a fake artifacts root: <root>/demo/index.html."""
    root = tmp_path / "artifacts-root"
    doc_dir = root / "demo"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.html").write_bytes(b"<html><head></head></html>")
    return {"root": root, "doc_dir": doc_dir}


def _url(artifact, rel: str) -> str:
    # doc_dir is absolute (starts with /), so "<b64>{doc_dir}" joins cleanly.
    return f"/api/file-raw/{_b64(str(artifact['root']))}{artifact['doc_dir']}/{rel}"


def _doc(artifact) -> str:
    return f"/api/file-raw/{_b64(str(artifact['root']))}{artifact['doc_dir']}/index.html"


# ── standalone route (/api/file-raw/<root_b64>/…) ─────────────────────────────


def test_put_without_header_is_405(client, artifact):
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}"))
    assert resp.status_code == 405


def test_put_creates_file(client, artifact):
    resp = _run(client.put(_url(artifact, "data/x.json"), b'{"a":1}',
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 200
    assert orjson.loads(resp.content)["ok"] is True
    assert (artifact["doc_dir"] / "data" / "x.json").read_bytes() == b'{"a":1}'


def test_put_outside_data_is_403(client, artifact):
    resp = _run(client.put(_url(artifact, "index.html"), b"pwned",
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 403
    assert (artifact["doc_dir"] / "index.html").read_bytes() == b"<html><head></head></html>"


def test_put_foreign_prefix_header_is_405(client, artifact, tmp_path):
    # A doc header under ANOTHER root_b64 fails the prefix check → treated as
    # absent (no proof the write comes from a document this route serves).
    other = tmp_path / "elsewhere"
    other.mkdir()
    bad_doc = f"/api/file-raw/{_b64(str(other))}{other}/index.html"
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}",
                           headers={"x-twicc-artifact-doc": bad_doc}))
    assert resp.status_code == 405


def test_put_doc_outside_root_is_403(client, artifact, tmp_path):
    # SAME route prefix, but the claimed doc lives outside the decoded root:
    # the doc must satisfy the same confinement as the target.
    outside = tmp_path / "outside-root" / "page"
    outside.mkdir(parents=True)
    bad_doc = f"/api/file-raw/{_b64(str(artifact['root']))}{outside}/index.html"
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}",
                           headers={"x-twicc-artifact-doc": bad_doc}))
    assert resp.status_code == 403


def test_delete_and_404(client, artifact):
    h = {"x-twicc-artifact-doc": _doc(artifact)}
    _run(client.put(_url(artifact, "data/x.json"), b"{}", headers=h))
    assert _run(client.delete(_url(artifact, "data/x.json"), headers=h)).status_code == 200
    assert _run(client.delete(_url(artifact, "data/x.json"), headers=h)).status_code == 404


def test_dir_get_with_header_lists(client, artifact):
    h = {"x-twicc-artifact-doc": _doc(artifact)}
    _run(client.put(_url(artifact, "data/sub/y.json"), b"12345", headers=h))
    resp = _run(client.get(_url(artifact, "data/"), headers=h))
    assert resp.status_code == 200
    assert [f["path"] for f in orjson.loads(resp.content)["files"]] == ["sub/y.json"]


def test_dir_get_with_header_before_first_write_is_empty(client, artifact):
    # data/ does not exist yet → empty listing, not 404 (the artifact probes
    # its store before the first write; design §4).
    resp = _run(client.get(_url(artifact, "data/"),
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 200
    assert orjson.loads(resp.content) == {"files": []}


def test_dir_get_without_header_stays_404(client, artifact):
    (artifact["doc_dir"] / "data").mkdir()
    resp = _run(client.get(_url(artifact, "data/")))
    assert resp.status_code == 404


def test_file_get_unchanged(client, artifact):
    resp = _run(client.get(_url(artifact, "index.html")))
    assert resp.status_code == 200


def test_oversize_put_is_413_with_payload(client, artifact, monkeypatch):
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_FILE_BYTES", 8)
    resp = _run(client.put(_url(artifact, "data/big"), b"123456789",
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 413
    assert orjson.loads(resp.content)["error"] == "too_large"


# ── project route (/api/projects/<id>/file-raw/…) ────────────────────────────


@pytest.fixture
def project(tmp_path, transactional_db):
    directory = tmp_path / "proj"
    directory.mkdir()
    return Project.objects.create(id="-tmp-ab-data", directory=str(directory))


@pytest.fixture
def session(project):
    now = timezone.now()
    return Session.objects.create(
        id="sess-ab-data", project=project, provider="claude_code",
        file_path="sess-ab-data.jsonl", type=SessionType.SESSION, title="sess-ab-data",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )


def test_project_route_put_creates_file(client, project):
    doc_dir = Path(project.directory) / "widget"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "index.html").write_bytes(b"<html></html>")
    url = f"/api/projects/{project.id}/file-raw{doc_dir}/data/x.json"
    doc = f"/api/projects/{project.id}/file-raw{doc_dir}/index.html"
    resp = _run(client.put(url, b"{}", headers={"x-twicc-artifact-doc": doc}))
    assert resp.status_code == 200
    assert (doc_dir / "data" / "x.json").read_bytes() == b"{}"


def test_project_route_foreign_project_header_is_405(client, project):
    doc_dir = Path(project.directory) / "widget"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "index.html").write_bytes(b"<html></html>")
    url = f"/api/projects/{project.id}/file-raw{doc_dir}/data/x.json"
    doc = f"/api/projects/-some-other-project/file-raw{doc_dir}/index.html"
    resp = _run(client.put(url, b"{}", headers={"x-twicc-artifact-doc": doc}))
    assert resp.status_code == 405


# ── bookmark route (/artifacts/<id>/…) ───────────────────────────────────────


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path. get_session_artifacts_dir reads from
    get_data_dir, so patching that one function reroutes the whole tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def bookmark(session, artifacts_root):
    from twicc.core.models import ArtifactBookmark
    target = artifacts_root / session.id / "demo" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"<html><head></head></html>")
    return ArtifactBookmark.objects.create(
        session=session, project=session.project,
        relative_path="demo/index.html", name="Demo", scope="all",
    )


def _bm_headers(bookmark):
    return {"x-twicc-artifact-doc": f"/artifacts/{bookmark.id}/__twicc_doc__"}


def test_bookmark_route_put_and_read_back(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/data/x.json", b'{"a":1}',
                           headers=_bm_headers(bookmark)))
    assert resp.status_code == 200
    resp = _run(client.get(f"/artifacts/{bookmark.id}/data/x.json"))
    assert resp.status_code == 200


def test_bookmark_route_put_without_header_is_405(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/data/x.json", b"{}"))
    assert resp.status_code == 405


def test_bookmark_route_put_outside_data_is_403(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/other.txt", b"x",
                           headers=_bm_headers(bookmark)))
    assert resp.status_code == 403


@pytest.mark.parametrize("asset", ["", "__twicc_doc__"])
def test_bookmark_route_put_on_the_document_itself_is_403(client, bookmark, artifacts_root, asset):
    # The bookmarked document is not part of the data store: a write must be
    # refused, never fall through to the (200) serving path.
    resp = _run(client.put(f"/artifacts/{bookmark.id}/{asset}", b"pwned",
                           headers=_bm_headers(bookmark)))
    assert resp.status_code == 403
    doc = artifacts_root / bookmark.session_id / "demo" / "index.html"
    assert doc.read_bytes() == b"<html><head></head></html>"


def test_bookmark_route_dir_get_with_header_lists(client, bookmark):
    _run(client.put(f"/artifacts/{bookmark.id}/data/sub/y.json", b"12345",
                    headers=_bm_headers(bookmark)))
    resp = _run(client.get(f"/artifacts/{bookmark.id}/data/", headers=_bm_headers(bookmark)))
    assert resp.status_code == 200
    assert [f["path"] for f in orjson.loads(resp.content)["files"]] == ["sub/y.json"]
